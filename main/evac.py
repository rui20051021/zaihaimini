# -*- coding: utf-8 -*-
"""火灾疏散：社会力 + 距离场导航 + 烟气蔓延 + 数据采集。"""
from __future__ import annotations

import csv, io, json, math, random, time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# 1 世界单位 = 0.1 m（与 3D 端一致）
UNIT_M = 0.1
TARGET_N = 80
MAX_NPCS = 300
R = 2.4                      # 半径 ≈ 0.24 m
TAU = 0.5                    # 加速反应时间 s
A_PED = 250.0                # 接触时人-人斥力加速度
B_PED = 0.8                  # 0.08 m
A_WALL = 320.0
B_WALL = 0.7
K_BODY = 1800.0              # 重叠体积排斥
KAPPA = 400.0                # 切向摩擦
A_FIRE = 90.0
B_FIRE = 36.0
NEIGH_R = 18.0               # 1.8 m 邻域（世界单位换算后）
V0_BASE = 14.0               # 1.4 m/s
V_MAX = 32.0                 # 3.2 m/s
TRACK_DT = 0.2
MAX_DURATION = 180.0
BIN = 20.0

NPC_COLORS = [
    "#7a8bab", "#6e86b0", "#8090a8", "#748aa4",
    "#8a96b4", "#6a809c", "#7e8ca8", "#7088a8",
]


@dataclass
class Agent:
    pid: str
    name: str
    color: str
    x: float
    y: float
    is_human: bool = False
    vx: float = 0.0
    vy: float = 0.0
    r: float = R
    v0: float = V0_BASE
    ix: float = 0.0
    iy: float = 0.0
    delay: float = 0.0
    health: float = 1.0
    escaped: bool = False
    failed: bool = False
    escaped_at: float = 0.0
    failed_at: float = 0.0
    started_at: float = 0.0
    path_len: float = 0.0
    pressure: float = 0.0
    pressure_ped: float = 0.0
    pressure_wall: float = 0.0
    density: float = 0.0
    density_m2: float = 0.0
    max_pressure: float = 0.0
    max_density: float = 0.0
    max_density_m2: float = 0.0
    # 灾害暴露指标：烟气浓度按仿真时间积分，火源距离取最近值。
    smoke_dose: float = 0.0
    max_smoke: float = 0.0
    min_fire_distance: float = float("inf")
    injury_time: float = 0.0
    fail_cause: Optional[str] = None  # fire | smoke
    fail_smoke: float = 0.0
    last_seen: float = 0.0
    online: bool = False
    track: List[list] = field(default_factory=list)
    last_track_t: float = 0.0
    # 真实时间轴（不受仿真倍速影响，秒）。倍速只压缩仿真时间，
    # 人类反应/逃生用时必须用这一组字段分析，才能跨倍速比较。
    real_started_at: float = 0.0
    real_escaped_at: float = 0.0
    real_failed_at: float = 0.0
    real_injury_time: float = 0.0
    # 实验参与者元数据（真人有效；NPC 恒为空）。
    participant_id: str = ""
    consent: Optional[bool] = None
    # 起始位置指标：记录生成时到最近出口的网格距离与到火源的米数。
    # 用于检验各条件下起始位置是否均衡，是反应/逃生时间的关键协变量。
    initial_exit_distance: Optional[int] = None
    initial_fire_distance: Optional[float] = None


class EvacSim:
    def __init__(self, maze, data_dir: Path):
        self.maze = maze
        self.data_dir = data_dir
        self.agents: Dict[str, Agent] = {}
        self.npc_target = TARGET_N
        self.phase = "idle"          # idle / running / paused / ended
        self.t0 = 0.0
        self.now = time.time()
        self.panic = 0.4
        self.time_scale = 1.0
        # 环境边界条件：m/s；默认无风。烟气模型仍是二维启发式近似。
        self.wind_x = 0.0
        self.wind_y = 0.0
        self.speed_changes = []
        self.paused = False
        self.dist: List[List[int]] = []
        self.burning: Set[Tuple[int, int]] = set()
        self.heating: Dict[Tuple[int, int], float] = {}
        self.smoke: List[List[float]] = []
        self.npc_seq = 0
        self.flow: List[list] = []    # [t_rel, escaped]
        self.last_flow_t = -1.0
        self.last_spread_t = 0.0
        # 真实时间累计（秒）：只统计运行中的真实时长，不受倍速影响。
        self.real_t = 0.0
        # 已归档的 trial 序号，用于避免同一场被重复落盘。
        self._archived_trial = -1
        # 点火事件历史：[仿真秒, 格x, 格y, 来源]。来源为 manual/auto/spread/scenario。
        self.ignition_events: List[list] = []
        self.summary: Optional[dict] = None
        self.seed: Optional[int] = getattr(maze, "seed", None)
        self.rng = random.Random(self.seed)
        self._smoke_pub: Optional[List[list]] = None
        self.last_saved: Optional[dict] = None
        # 实验元数据层：把每一场变成可追溯、可分组、可统计的 trial。
        self.experiment_id = ""
        self.session_id = ""
        self.trial_seq = 1
        self.condition = ""
        self.group = ""
        self.notes = ""
        self.operator = ""
        self.t0_iso = ""
        self.run_id = ""
        # 实验完整性开关：开启时要求参与者编号 + 知情同意，且一人只占一个身份。
        # 演示模式可置 False，恢复"扫码即可加入"。
        self.require_participant_consent = True
        # 起始区限制：None 表示全图随机；设为 (x0, y0, x1, y1) 格矩形后，
        # 所有人只在矩形内生成。用于控制"初始到出口距离"这一关键混淆变量。
        self.spawn_zone: Optional[Tuple[int, int, int, int]] = None
        self._rebuild_field()

    def _exit_cells(self) -> Set[Tuple[int, int]]:
        exits = getattr(self.maze, "exits", None)
        return set(exits or [self.maze.exit])

    # ---------- 场 ----------
    def _passable(self, x: int, y: int, nx: int, ny: int) -> bool:
        w = self.maze.walls
        if not (0 <= nx < self.maze.mw and 0 <= ny < self.maze.mh):
            return False
        if (nx, ny) in self.burning:
            return False
        if ny == y - 1:
            return not (w[y][x] & 1)
        if nx == x + 1:
            return not (w[y][x] & 2)
        if ny == y + 1:
            return not (w[y][x] & 4)
        if nx == x - 1:
            return not (w[y][x] & 8)
        return False

    def _rebuild_field(self):
        mw, mh = self.maze.mw, self.maze.mh
        dist = [[-1] * mw for _ in range(mh)]
        exits = self._exit_cells()
        # 出口格不允许被火占据（否则出口不可达）。若发生这种情况，
        # 显式记录而不是静默丢弃，方便实验后核查场景或蔓延是否异常。
        on_fire_exits = self.burning & exits
        if on_fire_exits:
            for cx, cy in sorted(on_fire_exits):
                self.ignition_events.append([
                    round(self.elapsed, 2), int(cx), int(cy), "removed_on_exit",
                ])
            self.burning.difference_update(exits)
        q = deque()
        for ex, ey in exits:
            if dist[ey][ex] < 0:
                dist[ey][ex] = 0
                q.append((ex, ey))
        while q:
            x, y = q.popleft()
            d = dist[y][x] + 1
            for nx, ny in ((x, y - 1), (x + 1, y), (x, y + 1), (x - 1, y)):
                if self._passable(x, y, nx, ny) and dist[ny][nx] < 0:
                    dist[ny][nx] = d
                    q.append((nx, ny))
        self.dist = dist
        if not self.smoke or len(self.smoke) != mh or len(self.smoke[0]) != mw:
            self.smoke = [[0.0] * mw for _ in range(mh)]

    def desired_dir(self, x: float, y: float) -> Tuple[float, float]:
        cell = self.maze.cell
        mw, mh = self.maze.mw, self.maze.mh
        gx = min(mw - 1, max(0, int(x // cell)))
        gy = min(mh - 1, max(0, int(y // cell)))
        dist = self.dist
        if not dist:
            return 0.0, 0.0
        here = dist[gy][gx]
        if here < 0 or (gx, gy) in self.burning:
            # 被火封死或人已在火格：只走向未着火邻格，绝不朝火走。
            return self._safe_step_dir(x, y)
        if here == 0:
            exits = self._exit_cells()
            ex, ey = min(
                exits,
                key=lambda item: (x - (item[0] + 0.5) * cell) ** 2
                + (y - (item[1] + 0.5) * cell) ** 2,
            )
            cx = (ex + 0.5) * cell
            cy = (ey + 0.5) * cell
            dx, dy = cx - x, cy - y
            n = math.hypot(dx, dy)
            return (dx / n, dy / n) if n > 1e-6 else (0.0, 0.0)

        best = here
        cands = []
        for nx, ny in ((gx, gy - 1), (gx + 1, gy), (gx, gy + 1), (gx - 1, gy)):
            if not (0 <= nx < mw and 0 <= ny < mh):
                continue
            if not self._passable(gx, gy, nx, ny):
                continue
            d = dist[ny][nx]
            if d < 0 or d >= best:
                continue
            cands.append((d, nx, ny))
        if not cands:
            return 0.0, 0.0
        cands.sort()
        _, nx, ny = cands[0]
        # 对准开口中心，减少拐角卡死
        if nx == gx + 1:
            tx, ty = (gx + 1) * cell, (gy + 0.5) * cell
        elif nx == gx - 1:
            tx, ty = gx * cell, (gy + 0.5) * cell
        elif ny == gy + 1:
            tx, ty = (gx + 0.5) * cell, (gy + 1) * cell
        else:
            tx, ty = (gx + 0.5) * cell, gy * cell
        dx, dy = tx - x, ty - y
        n = math.hypot(dx, dy)
        if n < cell * 0.12:
            dx = (nx + 0.5) * cell - x
            dy = (ny + 0.5) * cell - y
            n = math.hypot(dx, dy)
        if n < 1e-6:
            return 0.0, 0.0
        return dx / n, dy / n

    def _away_from_fire(self, x: float, y: float) -> Tuple[float, float]:
        if not self.burning:
            return 0.0, 0.0
        cell = self.maze.cell
        fx = fy = 0.0
        for gx, gy in self.burning:
            dx = x - (gx + 0.5) * cell
            dy = y - (gy + 0.5) * cell
            d2 = dx * dx + dy * dy + 1.0
            fx += dx / d2
            fy += dy / d2
        n = math.hypot(fx, fy)
        return (fx / n, fy / n) if n > 1e-6 else (0.0, 0.0)

    def _safe_step_dir(self, x: float, y: float) -> Tuple[float, float]:
        """Step into a non-burning neighbor. Prefer a cell that still reaches an exit."""
        cell = self.maze.cell
        mw, mh = self.maze.mw, self.maze.mh
        gx = min(mw - 1, max(0, int(x // cell)))
        gy = min(mh - 1, max(0, int(y // cell)))
        dist = self.dist
        scored = []
        for nx, ny in ((gx, gy - 1), (gx + 1, gy), (gx, gy + 1), (gx - 1, gy)):
            if not (0 <= nx < mw and 0 <= ny < mh):
                continue
            if (nx, ny) in self.burning:
                continue
            if not self._passable(gx, gy, nx, ny):
                continue
            d = dist[ny][nx] if dist else -1
            if d >= 0:
                scored.append((0, d, nx, ny))
            else:
                fire_d = self._nearest_fire_distance((nx + 0.5) * cell, (ny + 0.5) * cell)
                scored.append((1, -fire_d, nx, ny))
        if not scored:
            return 0.0, 0.0
        scored.sort()
        _, _, nx, ny = scored[0]
        dx = (nx + 0.5) * cell - x
        dy = (ny + 0.5) * cell - y
        n = math.hypot(dx, dy)
        return (dx / n, dy / n) if n > 1e-6 else (0.0, 0.0)

    # ---------- 人群 ----------
    def _spawn_bounds(self) -> Tuple[int, int, int, int]:
        """返回可生成格子的 [gx0, gy0, gx1, gy1]（含端点），受 spawn_zone 限制。"""
        mw, mh = self.maze.mw, self.maze.mh
        zone = self.spawn_zone
        if not zone:
            return 0, 0, mw - 1, mh - 1
        zx0, zy0, zx1, zy1 = zone
        gx0 = max(0, min(zx0, mw - 1))
        gy0 = max(0, min(zy0, mh - 1))
        gx1 = max(gx0, min(zx1, mw - 1))
        gy1 = max(gy0, min(zy1, mh - 1))
        return gx0, gy0, gx1, gy1

    def _find_spawn(self, preferred: bool = False) -> Tuple[float, float]:
        """在允许的起始区内找一个不撞人、可达、非出口/非火的落点。

        preferred=True 时（真人入场）优先返回离出口较远的格子，减少真人
        恰好站在出口上"秒退"导致的数据不可用；NPC 仍均匀随机以保持人群自然。
        """
        cell = self.maze.cell
        mw, mh = self.maze.mw, self.maze.mh
        gx0, gy0, gx1, gy1 = self._spawn_bounds()
        exits = self._exit_cells()
        others = [a for a in self.agents.values() if not a.escaped and not a.failed]
        candidates: List[Tuple[float, int, int]] = []
        for _ in range(300):
            gx = self.rng.randint(gx0, gx1)
            gy = self.rng.randint(gy0, gy1)
            if (gx, gy) in exits or (gx, gy) in self.burning:
                continue
            if self.dist and self.dist[gy][gx] < 0:
                continue
            x = (gx + 0.5) * cell + self.rng.uniform(-cell * 0.22, cell * 0.22)
            y = (gy + 0.5) * cell + self.rng.uniform(-cell * 0.22, cell * 0.22)
            ok = True
            for a in others:
                if math.hypot(a.x - x, a.y - y) < a.r + R + 0.8:
                    ok = False
                    break
            if not ok:
                continue
            if not preferred:
                return x, y
            d = self.dist[gy][gx] if self.dist else 0
            candidates.append((-d, gx, gy))
        if preferred and candidates:
            # 离出口最远的那一批里随机挑一个，避免每次都同一格。
            candidates.sort()
            far = [c for c in candidates if c[0] == candidates[0][0]]
            _, gx, gy = self.rng.choice(far)
            return ((gx + 0.5) * cell, (gy + 0.5) * cell)
        return cell * 1.5, cell * 1.5

    def _stamp_spawn_metrics(self, a: "Agent"):
        """记录个体生成时刻的"到出口 / 到火源"距离，作为起始位置的协变量。"""
        cell = self.maze.cell
        gx = min(self.maze.mw - 1, max(0, int(a.x // cell)))
        gy = min(self.maze.mh - 1, max(0, int(a.y // cell)))
        if self.dist and self.dist[gy][gx] >= 0:
            a.initial_exit_distance = int(self.dist[gy][gx])
        else:
            a.initial_exit_distance = None
        if self.burning:
            d = self._nearest_fire_distance(a.x, a.y)
            a.initial_fire_distance = (
                round(d * UNIT_M, 2) if math.isfinite(d) else None
            )
        else:
            a.initial_fire_distance = None

    def _new_delay(self) -> float:
        # 预动作时间：偏真实的右偏分布，约 0.5–10 s
        d = self.rng.gauss(2.2, 1.4)
        return float(min(12.0, max(0.4, d)))

    def spawn_npc(self) -> Agent:
        self.npc_seq += 1
        pid = f"n{self.npc_seq:03d}"
        x, y = self._find_spawn()
        a = Agent(
            pid=pid,
            name=f"NPC{self.npc_seq}",
            color=NPC_COLORS[self.npc_seq % len(NPC_COLORS)],
            x=x, y=y,
            v0=V0_BASE * self.rng.uniform(0.88, 1.12),
            delay=self._new_delay(),
            last_seen=time.time(),
        )
        self._stamp_spawn_metrics(a)
        self.agents[pid] = a
        return a

    def ensure_population(self, n: Optional[int] = None):
        if n is not None:
            if isinstance(n, bool) or not isinstance(n, int) or not 0 <= n <= MAX_NPCS:
                raise ValueError(f"NPC 人数必须是 0 到 {MAX_NPCS} 之间的整数")
            self.npc_target = n
        npcs = [a for a in self.agents.values() if not a.is_human]
        while len(npcs) > self.npc_target:
            gone = npcs.pop()
            self.agents.pop(gone.pid, None)
        for _ in range(self.npc_target - len(npcs)):
            self.spawn_npc()

    def set_npc_count(self, n: int):
        if isinstance(n, bool) or not isinstance(n, int) or not 0 <= n <= MAX_NPCS:
            raise ValueError(f"NPC 人数必须是 0 到 {MAX_NPCS} 之间的整数")
        if n == self.npc_target:
            return
        self.npc_target = n
        # A changed population defines a new experiment; do not mix denominators/tracks.
        self.reset_crowd(keep_humans=True)

    def add_human(self, pid: str, name: str, color: str,
                  participant_id: str = "", consent: Optional[bool] = None) -> Agent:
        now = time.time()
        if pid in self.agents:
            a = self.agents[pid]
            a.is_human = True
            a.name = (name or a.name)[:12]
            a.color = color
            a.last_seen = now
            a.online = True
            # 重连时允许补录/修正参与者信息，空值不覆盖已有值。
            if participant_id:
                a.participant_id = participant_id[:64]
            if consent is not None:
                a.consent = bool(consent)
            self.ensure_population()
            return a

        # Human slots are independent of the configured NPC population.
        # 真人优先放到离出口较远处，且记录起始距离作为协变量。
        x, y = self._find_spawn(preferred=True)
        a = Agent(
            pid=pid, name=(name or "玩家")[:12], color=color,
            x=x, y=y, is_human=True, v0=V0_BASE,
            # 真人由输入驱动，不依赖 delay；保持 0 以免污染反应时间统计。
            delay=0.0, last_seen=now, online=True,
            participant_id=(participant_id or "")[:64],
            consent=None if consent is None else bool(consent),
        )
        self._stamp_spawn_metrics(a)
        self.agents[pid] = a
        self.ensure_population()
        return a

    def agent_by_participant(self, participant_id: str) -> Optional[Agent]:
        """按参与者编号查找真人，用于防止同一人重复占位。"""
        if not participant_id:
            return None
        for a in self.agents.values():
            if a.is_human and a.participant_id == participant_id:
                return a
        return None

    def human_leave(self, pid: str):
        a = self.agents.get(pid)
        if not a:
            return
        a.online = False
        a.ix = a.iy = 0.0
        # Keep the identity/outcome for reconnect and statistics, without creating an NPC.

    # ---------- 实验控制 ----------
    def reset_crowd(self, keep_humans: bool = True, clear_fire: bool = False):
        # 重置会清空内存中的轨迹；先落盘，避免未导出的数据被静默丢弃。
        self.archive_trial(reason="reset_crowd")
        humans = [a for a in self.agents.values() if a.is_human and a.online] if keep_humans else []
        self.agents = {a.pid: a for a in humans}
        if clear_fire:
            self.burning.clear()
            self.heating.clear()
        self._rebuild_field()
        mw, mh = self.maze.mw, self.maze.mh
        self.smoke = [[0.0] * mw for _ in range(mh)]
        self._smoke_pub = None
        for a in humans:
            x, y = self._find_spawn(preferred=True)
            a.x, a.y = x, y
            a.vx = a.vy = 0.0
            a.ix = a.iy = 0.0
            a.health = 1.0
            a.escaped = a.failed = False
            a.escaped_at = a.failed_at = a.started_at = 0.0
            a.real_started_at = a.real_escaped_at = a.real_failed_at = 0.0
            a.real_injury_time = 0.0
            a.path_len = 0.0
            a.pressure = a.pressure_ped = a.pressure_wall = 0.0
            a.density = a.density_m2 = 0.0
            a.max_pressure = a.max_density = a.max_density_m2 = 0.0
            a.smoke_dose = a.max_smoke = 0.0
            a.min_fire_distance = float("inf")
            a.injury_time = 0.0
            a.fail_cause = None
            a.fail_smoke = 0.0
            a.track.clear()
            a.last_track_t = 0.0
            # 真人不用 delay 驱动，置 0 以免被误读为该人的反应时间。
            a.delay = 0.0 if a.is_human else self._new_delay()
        # 重置后重新记录起始位置指标（地形/火源可能已变化）。
        for a in humans:
            self._stamp_spawn_metrics(a)
        self.ensure_population()
        self.phase = "idle"
        self.t0 = 0.0
        self.t0_iso = ""
        # 进入新的一场 trial：序号自增，真实时间轴清零。
        self.trial_seq += 1
        self.real_t = 0.0
        self.run_id = ""
        self.ignition_events = []
        self.speed_changes = []
        self.flow.clear()
        self.last_flow_t = -1.0
        self.summary = None
        self.paused = False

    def on_maze_rebuild(self):
        # 重建迷宫会替换地图与人群，先把当前场次存下来。
        self.archive_trial(reason="maze_rebuild")
        self.burning.clear()
        self.heating.clear()
        seed = getattr(self.maze, "seed", None)
        if seed is not None:
            self.seed = int(seed)
            self.rng = random.Random(self.seed)
        self.reset_crowd(keep_humans=True, clear_fire=True)

    def set_seed(self, seed: Optional[int]):
        if seed is None:
            self.seed = None
            self.rng = random.Random()
            return
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError("种子必须是整数")
        self.seed = seed
        self.rng = random.Random(seed)

    def start(self):
        if self.phase == "running":
            return
        if self.phase == "paused":
            self.pause(False)
            return
        if self.phase == "ended":
            self.reset_crowd(keep_humans=True)
        now = time.time()
        if self.phase in ("ended", "idle"):
            for a in self.agents.values():
                if not a.escaped and not a.failed:
                    a.track.clear()
                    a.last_track_t = 0.0
                    a.started_at = 0.0
                    a.real_started_at = 0.0
                    a.real_escaped_at = 0.0
                    a.real_failed_at = 0.0
                    a.real_injury_time = 0.0
                    # 真人不用 delay 驱动；标记为 0 以免被误当成反应时间。
                    a.delay = self._new_delay() if not a.is_human else 0.0
        self.t0 = now
        self.now = now
        self.t0_iso = datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds")
        self.real_t = 0.0
        self.run_id = f"{datetime.fromtimestamp(now).strftime('%H%M%S')}{self.trial_seq:03d}"
        if self.seed is not None:
            self.rng = random.Random(self.seed + 104729)
        self.speed_changes = [[0.0, self.time_scale]]
        self.phase = "running"
        self.paused = False
        self.flow = [[0.0, self.escaped_count]]
        self.last_flow_t = 0.0
        self.last_spread_t = now
        self.summary = None

    def pause(self, on: Optional[bool] = None):
        if self.phase != "running" and not self.paused:
            return
        self.paused = (not self.paused) if on is None else bool(on)
        self.phase = "paused" if self.paused else "running"

    def ignite(self, gx: Optional[int] = None, gy: Optional[int] = None):
        mw, mh = self.maze.mw, self.maze.mh
        exits = self._exit_cells()
        if gx is None or gy is None:
            for _ in range(80):
                gx = self.rng.randrange(mw)
                gy = self.rng.randrange(mh)
                if all(math.hypot(gx - ex, gy - ey) > 8 for ex, ey in exits):
                    break
        gx = int(max(0, min(mw - 1, gx)))
        gy = int(max(0, min(mh - 1, gy)))
        if (gx, gy) in exits:
            return False
        self.burning.add((gx, gy))
        self.heating[(gx, gy)] = 1.0
        self.ignition_events.append([
            round(self.elapsed, 2), int(gx), int(gy), "manual",
        ])
        self._rebuild_field()
        self._seed_smoke(gx, gy)
        return True

    def _seed_smoke(self, gx: int, gy: int, amount: float = 0.55):
        """Put visible smoke on a new fire so clients can render it immediately."""
        if not self.smoke:
            return
        mw, mh = self.maze.mw, self.maze.mh
        self.smoke[gy][gx] = min(1.0, max(self.smoke[gy][gx], amount))
        for nx, ny in ((gx, gy - 1), (gx + 1, gy), (gx, gy + 1), (gx - 1, gy)):
            if 0 <= nx < mw and 0 <= ny < mh and self._passable(gx, gy, nx, ny):
                self.smoke[ny][nx] = min(1.0, self.smoke[ny][nx] + amount * 0.28)
        self._smoke_pub = None

    @property
    def escaped_count(self) -> int:
        return sum(1 for a in self.agents.values() if a.escaped)

    @property
    def failed_count(self) -> int:
        return sum(1 for a in self.agents.values() if a.failed)

    @property
    def elapsed(self) -> float:
        if not self.t0:
            return 0.0
        return max(0.0, self.now - self.t0)

    # ---------- 物理 ----------
    def set_time_scale(self, value: float):
        if not math.isfinite(value) or not 0.25 <= value <= 4.0:
            raise ValueError("倍速必须在 0.25 到 4 之间")
        if value != self.time_scale:
            self.time_scale = value
            if self.phase in ("running", "paused"):
                self.speed_changes.append([round(self.elapsed, 2), value])

    def set_wind(self, wind_x: float, wind_y: float):
        if not math.isfinite(wind_x) or not math.isfinite(wind_y):
            raise ValueError("风速必须是有限数值")
        if math.hypot(wind_x, wind_y) > 8.0:
            raise ValueError("风速合速度不能超过 8 m/s")
        self.wind_x = float(wind_x)
        self.wind_y = float(wind_y)

    def set_spawn_zone(self, zone):
        """限制人群起始区。zone 为 (x0, y0, x1, y1) 格坐标或 None（全图随机）。

        控制起始区可让不同条件下的"初始到出口距离"分布可比，避免起始位置
        成为逃生时间的混淆变量。设置后需重置人群才生效于已在场个体。
        """
        if zone is None:
            self.spawn_zone = None
            return
        try:
            zx0, zy0, zx1, zy1 = (int(v) for v in zone)
        except (TypeError, ValueError):
            raise ValueError("起始区必须是 4 个整数 (x0, y0, x1, y1)")
        mw, mh = self.maze.mw, self.maze.mh
        zx0, zx1 = sorted((max(0, zx0), min(zx1, mw - 1)))
        zy0, zy1 = sorted((max(0, zy0), min(zy1, mh - 1)))
        if zx1 < zx0 or zy1 < zy0:
            raise ValueError("起始区超出地图范围")
        self.spawn_zone = (zx0, zy0, zx1, zy1)

    def advance(self, real_dt: float):
        """Advance scaled simulation time in small, stable physics steps."""
        if self.phase != "running":
            return
        real_dt = max(0.0, real_dt)
        self.real_t += real_dt
        remaining = real_dt * self.time_scale
        while remaining > 1e-9 and self.phase == "running":
            dt = min(remaining, 0.02)
            self.step(dt, self.now)
            remaining -= dt

    def step(self, dt: float, now: float):
        # Only active simulation time advances. Pausing and ending freeze all state.
        if self.phase != "running":
            return
        dt = min(max(dt, 0.0), 0.05, max(0.0, MAX_DURATION - self.elapsed))
        self.now += dt
        now = self.now
        if self.phase == "running":
            self._spread_fire(now)
            self._step_smoke(dt)
        self._integrate(dt, now)
        if self.phase == "running":
            trel = now - self.t0
            if trel - self.last_flow_t >= 0.5:
                self.flow.append([round(trel, 2), self.escaped_count])
                self.last_flow_t = trel
            if self._should_end(trel):
                self._finish()

    def _should_end(self, trel: float) -> bool:
        if trel >= MAX_DURATION:
            return True
        left = [a for a in self.agents.values() if not a.escaped and not a.failed]
        return not left

    def _finish(self):
        self.phase = "ended"
        self.summary = self.build_summary()
        self.save_outputs(stamped=True)
        # 本场已落盘；随后若发生重置，不要重复归档同一场。
        self._archived_trial = self.trial_seq

    def save_outputs(self, stamped: bool = False):
        try:
            summary = self.summary or self.build_summary()
            self.summary = summary
            latest_json = self.data_dir / "evac_latest.json"
            latest_csv = self.data_dir / "evac_latest_tracks.csv"
            payload = json.dumps(summary, ensure_ascii=False)
            latest_json.write_text(payload, encoding="utf-8")
            latest_csv.write_text(self.export_csv(), encoding="utf-8")
            names = {"json": latest_json.name, "csv": latest_csv.name}
            if stamped:
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                # 追加 trial/run 标识：同一秒内的多次重置不会互相覆盖。
                tag = f"_t{self.trial_seq:03d}" if self.trial_seq else ""
                if self.run_id:
                    tag += f"_{self.run_id}"
                json_path = self.data_dir / f"evac_{stamp}{tag}.json"
                csv_path = self.data_dir / f"evac_{stamp}{tag}_tracks.csv"
                json_path.write_text(payload, encoding="utf-8")
                csv_path.write_text(latest_csv.read_text(encoding="utf-8"), encoding="utf-8")
                names = {"json": json_path.name, "csv": csv_path.name}
            self.last_saved = names
            print(f"[疏散数据已保存] {self.data_dir / names['json']}")
        except Exception as e:
            print("保存疏散数据失败:", e)

    def _spread_fire(self, now: float):
        if not self.burning:
            return
        if now - self.last_spread_t < 1.2:
            return
        self.last_spread_t = now
        mw, mh = self.maze.mw, self.maze.mh
        exits = self._exit_cells()
        newborn = []
        for gx, gy in list(self.burning):
            for nx, ny in ((gx, gy - 1), (gx + 1, gy), (gx, gy + 1), (gx - 1, gy)):
                if not (0 <= nx < mw and 0 <= ny < mh):
                    continue
                if (nx, ny) in self.burning or (nx, ny) in exits:
                    continue
                if not self._passable(gx, gy, nx, ny):
                    continue
                self.heating[(nx, ny)] = self.heating.get((nx, ny), 0.0) + 0.34
                if self.heating[(nx, ny)] >= 1.0 and self.rng.random() < 0.55:
                    newborn.append((nx, ny))
        if newborn:
            for c in newborn:
                self.burning.add(c)
                self.ignition_events.append([
                    round(self.elapsed, 2), int(c[0]), int(c[1]), "spread",
                ])
            self._rebuild_field()

    def _step_smoke(self, dt: float):
        mw, mh = self.maze.mw, self.maze.mh
        s = self.smoke
        if not s:
            return
        if not self.burning and not any(any(row) for row in s):
            return
        add = [[0.0] * mw for _ in range(mh)]
        for gx, gy in self.burning:
            s[gy][gx] = min(1.0, s[gy][gx] + 0.35 * dt)
            for nx, ny in ((gx, gy - 1), (gx + 1, gy), (gx, gy + 1), (gx - 1, gy)):
                if 0 <= nx < mw and 0 <= ny < mh and self._passable(gx, gy, nx, ny):
                    add[ny][nx] += 0.12 * dt
        # 简单扩散 + 衰减
        new = [row[:] for row in s]
        for y in range(mh):
            for x in range(mw):
                v = s[y][x] + add[y][x]
                acc = 0.0
                n = 0
                for nx, ny in ((x, y - 1), (x + 1, y), (x, y + 1), (x - 1, y)):
                    if 0 <= nx < mw and 0 <= ny < mh and self._passable(x, y, nx, ny):
                        acc += s[ny][nx]
                        n += 1
                if n:
                    # Mix with neighbors in real simulation time, not once per physics tick.
                    mix = min(0.4, 2.0 * dt)
                    v = v * (1.0 - mix) + (acc / n) * mix
                # 用上风侧采样做一阶平流；这是可解释的二维近似，不等同 CFD。
                wind_cells_x = self.wind_x * dt / max(UNIT_M * self.maze.cell, 1e-6)
                wind_cells_y = self.wind_y * dt / max(UNIT_M * self.maze.cell, 1e-6)
                advect = min(0.35, math.hypot(wind_cells_x, wind_cells_y))
                if advect > 1e-6:
                    ux = x - (1 if self.wind_x > 0 else -1 if self.wind_x < 0 else 0)
                    uy = y - (1 if self.wind_y > 0 else -1 if self.wind_y < 0 else 0)
                    if 0 <= ux < mw and 0 <= uy < mh and self._passable(ux, uy, x, y):
                        v = v * (1.0 - advect) + s[uy][ux] * advect
                v = max(0.0, v * (1.0 - 0.015 * dt) )
                new[y][x] = min(1.0, v)
        self.smoke = new
        self._smoke_pub = None

    def _bins(self) -> Dict[Tuple[int, int], List[Agent]]:
        b = defaultdict(list)
        inv = 1.0 / BIN
        for a in self.agents.values():
            if a.escaped or a.failed:
                continue
            b[(int(a.x * inv), int(a.y * inv))].append(a)
        return b

    def _integrate(self, dt: float, now: float):
        maze = self.maze
        cell = maze.cell
        exit_cells = self._exit_cells()
        bins = self._bins()
        inv = 1.0 / BIN
        nbr_r2 = NEIGH_R * NEIGH_R
        trel = (now - self.t0) if self.t0 else 0.0
        running = self.phase == "running"
        panic = self.panic
        v0_scale = 1.0 + 0.7 * panic

        acc = {}
        for a in self.agents.values():
            if a.escaped or a.failed:
                continue
            ax = ay = 0.0
            pressure_ped = 0.0

            # 邻域
            bx, by = int(a.x * inv), int(a.y * inv)
            dens = 0
            for i in range(bx - 1, bx + 2):
                for j in range(by - 1, by + 2):
                    for o in bins.get((i, j), ()):
                        if o is a:
                            continue
                        dx = a.x - o.x
                        dy = a.y - o.y
                        d2 = dx * dx + dy * dy
                        if d2 > nbr_r2 or d2 < 1e-10:
                            continue
                        d = math.sqrt(d2)
                        dens += 1
                        nx, ny = dx / d, dy / d
                        overlap = a.r + o.r - d
                        f = A_PED * math.exp(overlap / B_PED)
                        if overlap > 0:
                            f += K_BODY * overlap
                            tx, ty = -ny, nx
                            dv = (o.vx - a.vx) * tx + (o.vy - a.vy) * ty
                            ftx = KAPPA * overlap * dv * tx
                            fty = KAPPA * overlap * dv * ty
                            ax += ftx
                            ay += fty
                        ax += f * nx
                        ay += f * ny
                        pressure_ped += f
            density_area = math.pi * (NEIGH_R * UNIT_M) ** 2
            a.density = dens
            a.density_m2 = dens / density_area
            a.max_density_m2 = max(a.max_density_m2, a.density_m2)
            a.max_density = max(a.max_density, dens)
            a.pressure_ped = pressure_ped

            # 墙
            wx, wy = self._wall_force(a)
            ax += wx
            ay += wy
            a.pressure_wall = math.hypot(wx, wy)
            a.pressure = a.pressure_ped + a.pressure_wall
            a.max_pressure = max(a.max_pressure, a.pressure)

            # 火斥力。真人主动操作时可顶着斥力走进火场。
            if self.burning:
                fx, fy = self._fire_force(a)
                if a.is_human and (abs(a.ix) > 0.12 or abs(a.iy) > 0.12):
                    fx *= 0.18
                    fy *= 0.18
                ax += fx
                ay += fy

            # 期望速度
            gx = min(maze.mw - 1, max(0, int(a.x // cell)))
            gy = min(maze.mh - 1, max(0, int(a.y // cell)))
            sm = self.smoke[gy][gx] if self.smoke else 0.0
            if (gx, gy) in self.burning:
                a.health -= 0.28 * dt
                sm = 1.0
            elif sm > 0.15:
                a.health -= 0.10 * sm * dt
            a.health = max(0.0, a.health)
            # 暴露量使用仿真秒累计，不受界面刷新频率影响；浓度 0~1。
            if running:
                a.smoke_dose += sm * dt
                a.max_smoke = max(a.max_smoke, sm)
                if self.burning:
                    a.min_fire_distance = min(
                        a.min_fire_distance,
                        self._nearest_fire_distance(a.x, a.y),
                    )
            if running and a.health < 0.999 and a.injury_time <= 0.0:
                a.injury_time = trel
                a.real_injury_time = self.real_t

            v0 = 0.0
            ex = ey = 0.0
            if running:
                steering = a.is_human and (abs(a.ix) > 0.12 or abs(a.iy) > 0.12)
                # Humans move only on input; NPCs navigate after their reaction delay.
                ready = steering if a.is_human else trel >= a.delay
                if ready:
                    if a.started_at <= 0:
                        a.started_at = now
                        a.real_started_at = self.real_t
                    v0 = a.v0 * v0_scale * max(0.22, 1.0 - 0.65 * sm)
                    if steering:
                        n = math.hypot(a.ix, a.iy)
                        ex, ey = a.ix / n, a.iy / n
                    else:
                        ex, ey = self.desired_dir(a.x, a.y)
            ax += (v0 * ex - a.vx) / TAU
            ay += (v0 * ey - a.vy) / TAU
            acc[a.pid] = (ax, ay)

            if running and a.health <= 0:
                a.failed = True
                a.failed_at = now
                a.real_failed_at = self.real_t
                a.fail_cause = "fire" if (gx, gy) in self.burning else "smoke"
                a.fail_smoke = sm
                a.vx = a.vy = 0.0

        for a in self.agents.values():
            if a.escaped or a.failed or a.pid not in acc:
                continue
            ax, ay = acc[a.pid]
            a.vx += ax * dt
            a.vy += ay * dt
            sp = math.hypot(a.vx, a.vy)
            vmax = V_MAX * (1.0 + 0.3 * panic)
            if sp > vmax:
                a.vx *= vmax / sp
                a.vy *= vmax / sp
                sp = vmax
            ox, oy = a.x, a.y
            nx = a.x + a.vx * dt
            ny = a.y + a.vy * dt
            avoid_fire = not a.is_human
            if not self._blocked(nx, a.y, a.r, avoid_fire=avoid_fire, origin=(ox, oy)):
                a.x = nx
            else:
                a.vx *= 0.25
            if not self._blocked(a.x, ny, a.r, avoid_fire=avoid_fire, origin=(a.x, oy)):
                a.y = ny
            else:
                a.vy *= 0.25
            moved = math.hypot(a.x - ox, a.y - oy)
            a.path_len += moved

            gx = int(a.x // cell)
            gy = int(a.y // cell)
            if (gx, gy) in exit_cells:
                a.escaped = True
                a.escaped_at = now
                a.real_escaped_at = self.real_t
                a.vx = a.vy = 0.0

            if running and now - a.last_track_t >= TRACK_DT:
                current_smoke = self.smoke[gy][gx] if self.smoke else 0.0
                if (gx, gy) in self.burning:
                    current_smoke = 1.0
                cur_fire_dist = (
                    self._nearest_fire_distance(a.x, a.y) if self.burning
                    else float("inf")
                )
                a.track.append([
                    round(trel, 2), round(a.x, 1), round(a.y, 1),
                    round(a.vx, 2), round(a.vy, 2), round(sp, 2),
                    round(a.pressure, 1), round(a.density, 1),
                    round(a.health, 3), round(current_smoke, 3),
                    round(a.smoke_dose, 3),
                    # 历史最小火距（单调不增），保留原语义。
                    round(a.min_fire_distance * UNIT_M, 2)
                    if math.isfinite(a.min_fire_distance) else None,
                    # 该时刻瞬时火距（可为 None：本场尚未点火）。
                    round(cur_fire_dist * UNIT_M, 2)
                    if math.isfinite(cur_fire_dist) else None,
                    # 真实秒：倍速下唯一可跨场比较的时间轴。
                    round(self.real_t, 2),
                ])
                a.last_track_t = now

    def _wall_force(self, a: Agent) -> Tuple[float, float]:
        maze = self.maze
        cell = maze.cell
        r = a.r
        fx = fy = 0.0
        x0 = max(0, int((a.x - r) // cell))
        x1 = min(maze.mw - 1, int((a.x + r) // cell))
        y0 = max(0, int((a.y - r) // cell))
        y1 = min(maze.mh - 1, int((a.y + r) // cell))
        reach = r + 4 * B_WALL
        for gy in range(y0, y1 + 1):
            row = maze.walls[gy]
            oy = gy * cell
            for gx in range(x0, x1 + 1):
                w = row[gx]
                ox = gx * cell
                lx = a.x - ox
                ly = a.y - oy
                if w & 1:
                    fx, fy = self._add_wall(fx, fy, ly, 0.0, 1.0, r, reach, a)
                if w & 4:
                    fx, fy = self._add_wall(fx, fy, cell - ly, 0.0, -1.0, r, reach, a)
                if w & 8:
                    fx, fy = self._add_wall(fx, fy, lx, 1.0, 0.0, r, reach, a)
                if w & 2:
                    fx, fy = self._add_wall(fx, fy, cell - lx, -1.0, 0.0, r, reach, a)
        return fx, fy

    def _add_wall(self, fx, fy, dist, nx, ny, r, reach, a: Agent):
        if dist > reach:
            return fx, fy
        if dist < 0.05:
            dist = 0.05
        overlap = r - dist
        f = A_WALL * math.exp(overlap / B_WALL)
        if overlap > 0:
            f += K_BODY * overlap
            tx, ty = -ny, nx
            dv = a.vx * tx + a.vy * ty
            fx -= KAPPA * overlap * dv * tx
            fy -= KAPPA * overlap * dv * ty
        return fx + f * nx, fy + f * ny

    def _fire_force(self, a: Agent) -> Tuple[float, float]:
        cell = self.maze.cell
        fx = fy = 0.0
        gx0 = int(a.x // cell)
        gy0 = int(a.y // cell)
        for gx, gy in self.burning:
            if abs(gx - gx0) > 6 or abs(gy - gy0) > 6:
                continue
            dx = a.x - (gx + 0.5) * cell
            dy = a.y - (gy + 0.5) * cell
            d = math.hypot(dx, dy)
            if d < 1e-3:
                d = 1e-3
                dx, dy = 1.0, 0.0
            f = A_FIRE * math.exp(-d / B_FIRE)
            fx += f * dx / d
            fy += f * dy / d
        return fx, fy

    def _nearest_fire_distance(self, x: float, y: float) -> float:
        """Return distance to the nearest burning cell in world units."""
        cell = self.maze.cell
        return min(
            (math.hypot(x - (gx + 0.5) * cell, y - (gy + 0.5) * cell)
             for gx, gy in self.burning),
            default=float("inf"),
        )

    def _blocked(self, x: float, y: float, r: float,
                 avoid_fire: bool = False, origin: Optional[Tuple[float, float]] = None) -> bool:
        maze = self.maze
        if x - r < 0 or x + r > maze.world_w or y - r < 0 or y + r > maze.world_h:
            return True
        cell = maze.cell
        if avoid_fire and self.burning:
            gx, gy = int(x // cell), int(y // cell)
            if (gx, gy) in self.burning:
                if origin is None:
                    return True
                ogx, ogy = int(origin[0] // cell), int(origin[1] // cell)
                if (gx, gy) != (ogx, ogy):
                    return True
        x0 = max(0, int((x - r) // cell))
        x1 = min(maze.mw - 1, int((x + r) // cell))
        y0 = max(0, int((y - r) // cell))
        y1 = min(maze.mh - 1, int((y + r) // cell))
        for gy in range(y0, y1 + 1):
            row = maze.walls[gy]
            ly = y - gy * cell
            for gx in range(x0, x1 + 1):
                w = row[gx]
                lx = x - gx * cell
                if (w & 1) and ly - r < 0:
                    return True
                if (w & 4) and ly + r > cell:
                    return True
                if (w & 8) and lx - r < 0:
                    return True
                if (w & 2) and lx + r > cell:
                    return True
        return False

    # ---------- 输出 ----------
    def snapshot_agents(self) -> List[dict]:
        out = []
        cell = self.maze.cell
        mw, mh = self.maze.mw, self.maze.mh
        for a in self.agents.values():
            gx = min(mw - 1, max(0, int(a.x // cell)))
            gy = min(mh - 1, max(0, int(a.y // cell)))
            current_sm = self.smoke[gy][gx] if self.smoke else 0.0
            if (gx, gy) in self.burning:
                current_sm = 1.0
            item = {
                "pid": a.pid,
                "c": a.color,
                "x": round(a.x, 1),
                "y": round(a.y, 1),
                "vx": round(a.vx, 1),
                "vy": round(a.vy, 1),
                "h": 1 if a.is_human else 0,
                "e": 1 if a.escaped else 0,
                "f": 1 if a.failed else 0,
                # 实时暴露指标，供主机端做烟气/健康度颜色分级。
                "hp": round(a.health, 3),
                "sm": round(a.max_smoke, 3),
                "sd": round(a.smoke_dose, 3),
                "pr": round(a.pressure, 1),
                "dn": int(a.density),
                "dm": round(a.density_m2, 2),
                "pp": round(a.pressure_ped, 1),
                "pw": round(a.pressure_wall, 1),
                "sc": round(current_sm, 3),
                "cause": a.fail_cause or "",
                "n": a.name,
            }
            if a.is_human:
                item["on"] = 1 if a.online else 0
            out.append(item)
        return out

    def public_state(self) -> dict:
        active = [a for a in self.agents.values() if not a.escaped and not a.failed]
        return {
            "phase": self.phase,
            "elapsed": round(self.elapsed, 2),
            "real_t": round(self.real_t, 2),
            "trial_seq": self.trial_seq,
            "experiment_id": self.experiment_id,
            "session_id": self.session_id,
            "condition": self.condition,
            "group": self.group,
            "t0_iso": self.t0_iso,
            "escaped": self.escaped_count,
            "failed": self.failed_count,
            "alive": sum(1 for a in self.agents.values() if not a.escaped and not a.failed),
            "n": len(self.agents),
            "npc_target": self.npc_target,
            "npc_count": sum(1 for a in self.agents.values() if not a.is_human),
            "human_count": sum(1 for a in self.agents.values() if a.is_human),
            "panic": self.panic,
            "time_scale": self.time_scale,
            "seed": self.seed,
            "exits": [list(exit_cell) for exit_cell in self._exit_cells()],
            "exit_count": len(self._exit_cells()),
            "exit_layout": getattr(self.maze, "exit_layout", "spread"),
            "wind": {"x": round(self.wind_x, 2), "y": round(self.wind_y, 2), "speed": round(math.hypot(self.wind_x, self.wind_y), 2)},
            "spawn_zone": list(self.spawn_zone) if self.spawn_zone else None,
            "outflow_per_min": self.outflow_per_min(),
            "fire": [list(c) for c in self.burning],
            "smoke": self.smoke_cells(),
            "smoke_dose_mean": round(
                sum(a.smoke_dose for a in self.agents.values()) / max(1, len(self.agents)), 3
            ),
            "smoke_peak": round(max((a.max_smoke for a in self.agents.values()), default=0.0), 3),
            "pressure_peak": round(max((a.max_pressure for a in self.agents.values()), default=0.0), 1),
            "density_active_mean": round(
                sum(a.density_m2 for a in active) / max(1, len(active)), 2
            ),
        }

    def smoke_cells(self) -> List[list]:
        """Sparse [x, y, concentration] for rendering. Omit near-empty cells."""
        if self._smoke_pub is not None:
            return self._smoke_pub
        grid = self.smoke
        if not grid:
            self._smoke_pub = []
            return self._smoke_pub
        out = []
        for y, row in enumerate(grid):
            for x, value in enumerate(row):
                if value >= 0.03:
                    out.append([x, y, round(value, 2)])
        if len(out) > 900:
            out.sort(key=lambda item: item[2], reverse=True)
            out = out[:900]
        self._smoke_pub = out
        return out

    def outflow_per_min(self) -> float:
        if len(self.flow) < 2:
            return 0.0
        now_t = self.flow[-1][0]
        base_t, base_n = self.flow[0]
        for point_t, point_n in reversed(self.flow):
            if now_t - point_t >= 30.0:
                base_t, base_n = point_t, point_n
                break
        dt = now_t - base_t
        return round((self.flow[-1][1] - base_n) * 60.0 / dt, 2) if dt > 1e-6 else 0.0

    def _spawn_summary(self, everyone) -> dict:
        """汇总起始区与初始距离，供分析时核查各条件起点是否可比。"""
        exits = [a.initial_exit_distance for a in everyone
                 if a.initial_exit_distance is not None]
        fires = [a.initial_fire_distance for a in everyone
                 if a.initial_fire_distance is not None]

        def stats(vals):
            if not vals:
                return None
            vals = sorted(vals)
            n = len(vals)
            mid = n // 2
            median = vals[mid] if n % 2 else round((vals[mid - 1] + vals[mid]) / 2, 2)

            def percentile(q):
                idx = min(n - 1, max(0, int(round((n - 1) * q))))
                return vals[idx]

            return {
                "n": n,
                "min": vals[0],
                "max": vals[-1],
                "mean": round(sum(vals) / n, 2),
                "median": median,
                "p25": percentile(0.25),
                "p75": percentile(0.75),
            }

        return {
            "zone": list(self.spawn_zone) if self.spawn_zone else None,
            "note": "zone=None 表示全图随机；zone 为格坐标 [x0, y0, x1, y1]",
            "initial_exit_distance_cells": stats(exits),
            "initial_fire_distance_m": stats(fires),
        }

    def build_summary(self) -> dict:
        t0 = self.t0
        dur = self.elapsed
        everyone = self._all_agents()
        agents = []
        times = []
        real_times = []
        for a in everyone:
            te = (a.escaped_at - t0) if a.escaped and t0 else None
            if te is not None:
                times.append(te)
                if a.real_escaped_at > 0:
                    real_times.append(a.real_escaped_at)
            agents.append({
                "pid": a.pid,
                "name": a.name,
                "human": a.is_human,
                # 参与者元数据：只有真人会填，NPC 恒为空。
                "participant_id": a.participant_id,
                "consent": a.consent,
                "escaped": a.escaped,
                "failed": a.failed,
                "delay": round(a.delay, 2),
                # 仿真秒（倍速下会被压缩，跨场比较不可用）。
                "t_escape": None if te is None else round(te, 2),
                "t_fail": (
                    None if not a.failed or not t0 else round(a.failed_at - t0, 2)
                ),
                # 真实秒（不受倍速影响）：跨场/跨倍速比较请用这一组。
                "real_reaction_time": (
                    None if a.real_started_at <= 0 else round(a.real_started_at, 2)
                ),
                "real_t_escape": (
                    None if a.real_escaped_at <= 0 else round(a.real_escaped_at, 2)
                ),
                "real_t_fail": (
                    None if a.real_failed_at <= 0 else round(a.real_failed_at, 2)
                ),
                "real_injury_time": (
                    None if a.real_injury_time <= 0 else round(a.real_injury_time, 2)
                ),
                "fail_cause": a.fail_cause,
                "fail_smoke": round(a.fail_smoke, 3),
                "path_len": round(a.path_len, 1),
                "max_pressure": round(a.max_pressure, 1),
                "max_density": int(a.max_density),
                "max_density_m2": round(a.max_density_m2, 2),
                "health": round(a.health, 3),
                "reaction_time": None if not a.started_at or not t0 else round(a.started_at - t0, 2),
                "injury_time": None if a.injury_time <= 0 else round(a.injury_time, 2),
                "smoke_dose": round(a.smoke_dose, 3),
                "max_smoke": round(a.max_smoke, 3),
                "min_fire_distance_m": (
                    None if not math.isfinite(a.min_fire_distance)
                    else round(a.min_fire_distance * UNIT_M, 2)
                ),
                # 起始位置协变量：用于检验各条件下初始位置是否均衡。
                "initial_exit_distance": a.initial_exit_distance,
                "initial_fire_distance_m": a.initial_fire_distance,
                "track_points": len(a.track),
            })
        times.sort()
        real_times.sort()
        humans = [a for a in everyone if a.is_human]
        npcs = [a for a in everyone if not a.is_human]
        # 分母按"实际参与过的人"计算，含已离线者，避免 escape_rate 被低估。
        n = max(1, len(everyone))
        n_humans = max(1, len(humans))
        n_npcs = max(1, len(npcs))
        return {
            # ---- 实验元数据：用于多场次分组、追溯与统计 ----
            "experiment": {
                "experiment_id": self.experiment_id,
                "session_id": self.session_id,
                "run_id": self.run_id,
                "trial_seq": self.trial_seq,
                "condition": self.condition,
                "group": self.group,
                "operator": self.operator,
                "notes": self.notes,
                "t0_iso": self.t0_iso,
            },
            "timebase": {
                "sim_seconds_per_real_second": self.time_scale,
                "real_duration": round(self.real_t, 2),
                "sim_duration": round(dur, 2),
                "note": "real_* fields use wall-clock seconds and stay comparable across time_scale; t_* and duration use scaled simulation seconds",
            },
            "model": {
                "name": "2D social-force evacuation prototype",
                "coordinate_unit_m": UNIT_M,
                "density_unit": "persons/m2",
                "pressure_note": "force-like simulation score; requires calibration",
                "smoke_note": "2D grid concentration with optional horizontal advection; no CFD, buoyancy, layer height, or CO/FED model",
            },
            "t0": t0,
            "duration": round(dur, 2),
            "n": len(everyone),
            "npc_target": self.npc_target,
            "npc_count": len(npcs),
            "human_count": len(humans),
            "human_online_count": sum(1 for a in humans if a.online),
            "escaped": sum(1 for a in everyone if a.escaped),
            "failed": sum(1 for a in everyone if a.failed),
            "escape_rate": round(sum(1 for a in everyone if a.escaped) / n, 3),
            "escape_rate_human": round(sum(1 for a in humans if a.escaped) / n_humans, 3),
            "escape_rate_npc": round(sum(1 for a in npcs if a.escaped) / n_npcs, 3),
            "panic": self.panic,
            "time_scale": self.time_scale,
            "seed": self.seed,
            "exits": [list(exit_cell) for exit_cell in self._exit_cells()],
            "wind": {"x": round(self.wind_x, 2), "y": round(self.wind_y, 2), "speed": round(math.hypot(self.wind_x, self.wind_y), 2)},
            "spawn": self._spawn_summary(everyone),
            "outflow_per_min": self.outflow_per_min(),
            "speed_changes": [change[:] for change in self.speed_changes],
            # 火源暴露条件：实验后必须能还原本场烧了哪些格、何时点火。
            "fire": {
                "burning_cells": [list(c) for c in sorted(self.burning)],
                "burning_count": len(self.burning),
                "manual_ignitions": sum(1 for e in self.ignition_events if e[3] == "manual"),
                "total_ignitions": len(self.ignition_events),
                "events": [e[:] for e in self.ignition_events],
            },
            "complexity": self.maze.complexity,
            "mw": self.maze.mw,
            "mh": self.maze.mh,
            "t_first": None if not times else round(times[0], 2),
            "t_last": None if not times else round(times[-1], 2),
            "t_median": None if not times else round(times[len(times) // 2], 2),
            # 真实时间版本的首/末/中位逃生用时，可直接跨倍速比较。
            "real_t_first": None if not real_times else round(real_times[0], 2),
            "real_t_last": None if not real_times else round(real_times[-1], 2),
            "real_t_median": None if not real_times else round(real_times[len(real_times) // 2], 2),
            "flow": self.flow,
            "agents": agents,
            "mean_smoke_dose": round(sum(a.smoke_dose for a in everyone) / n, 3),
            "max_smoke_dose": round(max((a.smoke_dose for a in everyone), default=0.0), 3),
            "mean_max_pressure": round(sum(a.max_pressure for a in everyone) / n, 1),
        }

    # CSV 列语义（分析前务必按此解读，否则会得出错误结论）：
    #   逐时刻列  : t, real_t, x, y, vx, vy, speed, pressure, density, health,
    #               smoke, smoke_dose, fire_distance_m, min_fire_distance_m, state
    #   常量列    : pid, name, human, participant_id, consent, t_escape, t_fail,
    #               fail_cause, escaped_total, failed_total
    #   - real_t 使用真实秒（不受仿真倍速影响），跨倍速比较必须用它。
    #   - fire_distance_m 是"该时刻"到最近火源的距离；min_fire_distance_m
    #     是该 agent 的历史最小值（单调不增），两者不可混用。
    #   - escaped_total / failed_total 是整场最终结局；逐时刻状态请看 state。
    CSV_HEADER = [
        "pid", "name", "human", "participant_id", "consent",
        "t", "real_t", "x", "y", "vx", "vy", "speed", "pressure", "density",
        "health", "smoke", "smoke_dose",
        "fire_distance_m", "min_fire_distance_m", "state",
        "initial_exit_distance", "initial_fire_distance",
        "t_escape", "t_fail", "fail_cause", "escaped_total", "failed_total",
    ]

    def _all_agents(self) -> List[Agent]:
        """本场参与的全部 agent（含中途掉线但仍保留记录者）。"""
        return list(self.agents.values())

    def has_trial_data(self) -> bool:
        """本场是否已经产生值得归档的数据。"""
        if self.phase in ("running", "paused", "ended") and self.t0:
            return True
        return any(a.track for a in self.agents.values())

    def archive_trial(self, reason: str = "reset") -> Optional[dict]:
        """把当前场次落盘为带时间戳的独立文件。

        重置/重建会清空内存状态，若此前的数据尚未落盘就会永久丢失；
        因此在这些破坏性操作之前先调用本方法。
        """
        if not self.has_trial_data() or self._archived_trial == self.trial_seq:
            return None
        summary = self.summary or self.build_summary()
        self.summary = summary
        try:
            self.save_outputs(stamped=True)
        except Exception as e:      # save_outputs 自身已兜底，这里再保一层。
            print("归档当前场次失败:", e)
            return None
        self._archived_trial = self.trial_seq
        print(f"[场次已归档] trial={self.trial_seq} reason={reason}")
        return self.last_saved

    @staticmethod
    def _emit(value):
        # 电子表格会把 = + - @ 开头的单元格当公式执行，统一加前缀中和。
        if isinstance(value, str):
            return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value
        return value

    def export_csv(self) -> str:
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(self.CSV_HEADER)
        t0 = self.t0
        for a in self._all_agents():
            h = int(a.is_human)
            te = round(a.escaped_at - t0, 2) if (a.escaped and t0) else ""
            tf = round(a.failed_at - t0, 2) if (a.failed and t0) else ""
            for row in a.track:
                if len(row) >= 13:
                    (t, x, y, vx, vy, sp, pr, dens, hp, smoke, dose,
                     min_fire, fire_dist, real_t) = row[:14]
                elif len(row) >= 12:
                    (t, x, y, vx, vy, sp, pr, dens, hp, smoke, dose,
                     min_fire) = row[:12]
                    fire_dist = real_t = ""
                elif len(row) == 9:
                    t, x, y, vx, vy, sp, pr, dens, hp = row
                    smoke = dose = min_fire = fire_dist = real_t = ""
                else:
                    continue
                # 逐时刻结局：以该点仿真时刻与逃生/失败时刻比较得出。
                if a.escaped and te != "" and t >= te - 1e-6:
                    state = "escaped"
                elif a.failed and tf != "" and t >= tf - 1e-6:
                    state = "failed"
                else:
                    state = "active"
                writer.writerow([
                    self._emit(a.pid), self._emit(a.name), h,
                    self._emit(a.participant_id), "" if a.consent is None else int(a.consent),
                    t, real_t, x, y, vx, vy, sp, pr, dens, hp, smoke, dose,
                    fire_dist, min_fire, state,
                    "" if a.initial_exit_distance is None else a.initial_exit_distance,
                    "" if a.initial_fire_distance is None else a.initial_fire_distance,
                    te, tf, self._emit(a.fail_cause or ""),
                    int(a.escaped), int(a.failed),
                ])
        return output.getvalue()

    def export_scenario(self) -> dict:
        maze = self.maze
        return {
            "version": 1,
            "seed": getattr(maze, "seed", self.seed),
            "complexity": maze.complexity,
            "view_level": getattr(maze, "view_level", 3),
            "cell": maze.cell,
            "mw": maze.mw,
            "mh": maze.mh,
            "walls": maze.walls,
            "exits": [list(item) for item in self._exit_cells()],
            "exit_count": len(self._exit_cells()),
            "exit_layout": getattr(maze, "exit_layout", "spread"),
            "npc_target": self.npc_target,
            "panic": self.panic,
            "time_scale": self.time_scale,
            "wind": {"x": self.wind_x, "y": self.wind_y},
            "spawn_zone": list(self.spawn_zone) if self.spawn_zone else None,
            "fire": [list(c) for c in sorted(self.burning)],
            "heating": [[x, y, round(v, 3)] for (x, y), v in self.heating.items()],
        }

    def apply_scenario_state(self, data: dict):
        n = int(data.get("npc_target", self.npc_target))
        if isinstance(n, bool) or not 0 <= n <= MAX_NPCS:
            raise ValueError(f"NPC 人数必须是 0 到 {MAX_NPCS} 之间的整数")
        self.npc_target = n
        self.panic = max(0.0, min(1.0, float(data.get("panic", self.panic))))
        scale = float(data.get("time_scale", self.time_scale))
        if not 0.25 <= scale <= 4.0:
            raise ValueError("倍速必须在 0.25 到 4 之间")
        self.time_scale = scale
        wind = data.get("wind") or {}
        self.set_wind(float(wind.get("x", 0.0)), float(wind.get("y", 0.0)))
        zone = data.get("spawn_zone")
        self.set_spawn_zone(zone)
        self.burning = {tuple(cell) for cell in data.get("fire") or []}
        self.heating = {}
        for item in data.get("heating") or []:
            if len(item) >= 3:
                self.heating[(int(item[0]), int(item[1]))] = float(item[2])
        seed = data.get("seed")
        if seed is not None:
            self.set_seed(int(seed))
        self.reset_crowd(keep_humans=True, clear_fire=False)
        for gx, gy in list(self.burning):
            self._seed_smoke(gx, gy)
