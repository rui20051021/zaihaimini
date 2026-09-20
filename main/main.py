# main.py
# -*- coding: utf-8 -*-
"""
二维迷宫多人系统（可调复杂度 / 视野）
运行: python main.py
依赖: pip install -r requirements.txt
"""
import asyncio, io, json, math, os, random, socket, subprocess, sys, threading, time, webbrowser
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import List, Optional, Tuple

import segno
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response

from evac import TARGET_N, MAX_NPCS, EvacSim

# ---------------- 全局常量 ----------------
BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

BASE_W, BASE_H = 1920, 1088        # 基准世界尺寸
PLAYER_R = 7                       # 玩家半径（世界单位）
TICK = 0.02
IDLE_BROADCAST = 0.2
INPUT_TIMEOUT = 2.0
MAX_HUMANS = 80
PLAYER_SPEED = 100.0

# 复杂度 1~10 → 每格像素（越大越粗）
COMPLEXITY_CELL = {
    1: 160, 2: 120, 3: 96, 4: 72, 5: 56,
    6: 44,  7: 36,  8: 32, 9: 28, 10: 24,
}

# 视野等级 1~10 → 视野宽度（世界单位，会再按比例算高度）
VIEW_WIDTH_LIST = [80, 130, 200, 300, 440, 640, 900, 1250, 1650, 2200]

COLORS = ["#e6194b", "#3cb44b", "#ffe119", "#4363d8", "#f58231",
          "#911eb4", "#46f0f0", "#f032e6", "#bcf60c", "#fabebe",
          "#008080", "#9a6324", "#800000", "#808000", "#000075"]


def get_lan_ip() -> str:
    if os.environ.get("MAZE_LAN_IP"):
        return os.environ["MAZE_LAN_IP"]
    # On macOS the default Internet route may belong to a VPN adapter.
    if sys.platform == "darwin":
        for interface in ("en0", "en1"):
            try:
                address = subprocess.check_output(
                    ["/usr/sbin/ipconfig", "getifaddr", interface],
                    stderr=subprocess.DEVNULL, timeout=1).decode().strip()
                if address:
                    return address
            except (OSError, subprocess.SubprocessError):
                pass
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


LAN_IP = get_lan_ip()

# 演示模式：MAZE_REQUIRE_CONSENT=0 时恢复"扫码即可加入"，不做参与者校验。
REQUIRE_CONSENT = os.environ.get("MAZE_REQUIRE_CONSENT", "1").strip().lower() \
    not in ("0", "false", "no", "off")


# ---------------- 迷宫状态 ----------------
class MazeState:
    def __init__(self):
        self.cell = 32
        self.mw = 60
        self.mh = 34
        self.world_w = self.mw * self.cell
        self.world_h = self.mh * self.cell
        self.walls: List[List[int]] = []
        self.exit: Tuple[int, int] = (self.mw - 1, self.mh - 1)
        self.exits: List[Tuple[int, int]] = [self.exit]
        self.exit_count = 1
        self.exit_layout = "spread"
        self.view_w = 200
        self.view_h = 112
        self.complexity = 8
        self.view_level = 3
        self.gen = 0
        self.seed: Optional[int] = None


maze = MazeState()


def gen_maze(w: int, h: int, rng: Optional[random.Random] = None) -> List[List[int]]:
    """递归回溯。walls[y][x]：位掩码 1=N 2=E 4=S 8=W"""
    rng = rng or random
    walls = [[15] * w for _ in range(h)]
    visited = [[False] * w for _ in range(h)]
    visited[0][0] = True
    stack = [(0, 0)]
    while stack:
        x, y = stack[-1]
        nbrs = []
        if y > 0 and not visited[y - 1][x]:       nbrs.append((x, y - 1, 'N'))
        if x < w - 1 and not visited[y][x + 1]:   nbrs.append((x + 1, y, 'E'))
        if y < h - 1 and not visited[y + 1][x]:   nbrs.append((x, y + 1, 'S'))
        if x > 0 and not visited[y][x - 1]:       nbrs.append((x - 1, y, 'W'))
        if not nbrs:
            stack.pop()
            continue
        nx, ny, d = rng.choice(nbrs)
        if d == 'N':
            walls[y][x] &= ~1; walls[ny][nx] &= ~4
        elif d == 'S':
            walls[y][x] &= ~4; walls[ny][nx] &= ~1
        elif d == 'E':
            walls[y][x] &= ~2; walls[ny][nx] &= ~8
        elif d == 'W':
            walls[y][x] &= ~8; walls[ny][nx] &= ~2
        visited[ny][nx] = True
        stack.append((nx, ny))
    return walls


def choose_exits(mw: int, mh: int, count: int, layout: str) -> List[Tuple[int, int]]:
    count = max(1, min(4, int(count)))
    layout = layout if layout in {"spread", "corners", "sides"} else "spread"
    mid_x, mid_y = mw // 2, mh // 2
    if layout == "corners":
        candidates = [(mw - 1, mh - 1), (0, mh - 1), (mw - 1, 0), (0, 0)]
    elif layout == "sides":
        candidates = [(mw - 1, mid_y), (0, mid_y), (mid_x, mh - 1), (mid_x, 0)]
    else:
        # Farthest-looking perimeter cells for a balanced default layout.
        candidates = [(mw - 1, mh - 1), (0, 0), (mw - 1, 0), (0, mh - 1)]
    return candidates[:count]


def rebuild_maze(complexity: int, view_level: int, exit_count: int = 1,
                 exit_layout: str = "spread", seed: Optional[int] = None):
    complexity = max(1, min(10, int(complexity)))
    view_level = max(1, min(10, int(view_level)))

    maze.complexity = complexity
    maze.view_level = view_level
    maze.cell = COMPLEXITY_CELL[complexity]
    maze.mw = max(4, BASE_W // maze.cell)
    maze.mh = max(4, BASE_H // maze.cell)
    maze.world_w = maze.mw * maze.cell
    maze.world_h = maze.mh * maze.cell
    if seed is None:
        seed = random.randrange(1, 2_000_000_000)
    maze.seed = int(seed)
    maze.walls = gen_maze(maze.mw, maze.mh, random.Random(maze.seed))
    maze.exit_count = max(1, min(4, int(exit_count)))
    maze.exit_layout = exit_layout if exit_layout in {"spread", "corners", "sides"} else "spread"
    maze.exits = choose_exits(maze.mw, maze.mh, maze.exit_count, maze.exit_layout)
    maze.exit = maze.exits[0]

    vw = VIEW_WIDTH_LIST[view_level - 1]
    vw = max(vw, maze.cell * 3)             # 至少看到 3 格宽
    vw = min(vw, maze.world_w)              # 不超过世界
    vh = max(60, int(vw * 9 / 16))
    vh = min(vh, maze.world_h)
    maze.view_w = vw
    maze.view_h = vh

    maze.gen += 1


def get_spawn(idx: int) -> Tuple[int, int]:
    mw, mh = maze.mw, maze.mh
    spawns = [(0, 0), (mw - 1, 0), (0, mh - 1), (mw // 2, mh // 2)]
    return spawns[idx % len(spawns)]


def is_blocked(x: float, y: float, r: float = PLAYER_R) -> bool:
    world_w, world_h = maze.world_w, maze.world_h
    if x - r < 0 or x + r > world_w or y - r < 0 or y + r > world_h:
        return True
    cell = maze.cell
    walls = maze.walls
    x0 = max(0, int((x - r) // cell))
    x1 = min(maze.mw - 1, int((x + r) // cell))
    y0 = max(0, int((y - r) // cell))
    y1 = min(maze.mh - 1, int((y + r) // cell))
    for gy in range(y0, y1 + 1):
        row = walls[gy]
        ly = y - gy * cell
        for gx in range(x0, x1 + 1):
            w = row[gx]
            lx = x - gx * cell
            if (w & 1) and ly - r < 0:      return True
            if (w & 4) and ly + r > cell:   return True
            if (w & 8) and lx - r < 0:      return True
            if (w & 2) and lx + r > cell:   return True
    return False


def maze_payload() -> dict:
    return {
        "cell": maze.cell,
        "mw": maze.mw,
        "mh": maze.mh,
        "world": {"w": maze.world_w, "h": maze.world_h},
        "view": {"w": maze.view_w, "h": maze.view_h},
        "walls": maze.walls,
        "exit": list(maze.exit),
        "exits": [list(exit_cell) for exit_cell in maze.exits],
        "exit_count": maze.exit_count,
        "exit_layout": maze.exit_layout,
        "gen": maze.gen,
        "complexity": maze.complexity,
        "view_level": maze.view_level,
        "seed": maze.seed,
    }


# 初始化一次
rebuild_maze(8, 3)
sim = EvacSim(maze, DATA_DIR)
sim.require_participant_consent = REQUIRE_CONSENT
sim.ensure_population(TARGET_N)


class Hub:
    def __init__(self):
        self.hosts = set()
        self.players = {}
        self.outboxes = {}
        self.senders = {}

    def register(self, ws):
        queue = asyncio.Queue(maxsize=8)
        self.outboxes[ws] = queue
        self.senders[ws] = asyncio.create_task(self._send(ws, queue))

    def remove(self, ws):
        self.hosts.discard(ws)
        for pid, current in list(self.players.items()):
            if current is ws:
                self.players.pop(pid, None)
                sim.human_leave(pid)
        self.outboxes.pop(ws, None)
        task = self.senders.pop(ws, None)
        if task and task is not asyncio.current_task():
            task.cancel()

    async def _send(self, ws, queue):
        try:
            while True:
                await asyncio.wait_for(ws.send_text(await queue.get()), timeout=1.0)
        except Exception:
            with suppress(Exception):
                await asyncio.wait_for(ws.close(code=1013), timeout=1.0)
        finally:
            self.remove(ws)

    @property
    def client_count(self) -> int:
        return len(self.hosts) + len(self.players)

    async def broadcast(self, msg: dict):
        if not self.hosts and not self.players:
            return
        data = json.dumps(msg, ensure_ascii=False, separators=(",", ":"))
        for ws, queue in list(self.outboxes.items()):
            if queue.full():
                # Disconnect a stalled client without blocking physics or other clients.
                self.remove(ws)
                asyncio.create_task(self._close_slow(ws))
            else:
                queue.put_nowait(data)

    async def _close_slow(self, ws):
        with suppress(Exception):
            await asyncio.wait_for(ws.close(code=1013), timeout=1.0)


hub = Hub()


# ---------------- 主循环 ----------------
def evac_state_msg(now: float) -> dict:
    st = sim.public_state()
    st.update({
        "type": "state",
        "t": now,
        "agents": sim.snapshot_agents(),
    })
    return st


async def game_loop():
    last = time.monotonic()
    broadcast_acc = 0.0
    prev_clients = 0
    prev_phase = sim.phase
    while True:
        running = sim.phase == "running"
        await asyncio.sleep(TICK if running else 0.05)
        clock = time.monotonic()
        dt = min(clock - last, 0.25)
        last = clock
        now = time.time()
        for agent in sim.agents.values():
            if agent.is_human and now - agent.last_seen > INPUT_TIMEOUT:
                agent.ix = agent.iy = 0.0
        sim.advance(dt)

        n_clients = hub.client_count
        dirty = running or n_clients != prev_clients or sim.phase != prev_phase
        prev_clients = n_clients
        prev_phase = sim.phase
        if not n_clients:
            continue

        broadcast_acc += dt
        if broadcast_acc < (0.05 if dirty else IDLE_BROADCAST):
            continue
        broadcast_acc = 0.0
        await hub.broadcast(evac_state_msg(now))


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(game_loop())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        senders = list(hub.senders.values())
        for ws in list(hub.outboxes):
            hub.remove(ws)
        await asyncio.gather(*senders, return_exceptions=True)
        save_tracks()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def save_tracks():
    sim.save_outputs(stamped=False)


async def apply_restart(complexity: int, view_level: int, exit_count: int = 1,
                        exit_layout: str = "spread", seed: Optional[int] = None):
    """重建迷宫 + 重置人群 + 广播"""
    # 必须在改动 maze 之前归档：否则汇总里的地图尺寸会写成新地图的。
    sim.archive_trial(reason="maze_rebuild")
    rebuild_maze(complexity, view_level, exit_count, exit_layout, seed=seed)
    sim.on_maze_rebuild()
    await hub.broadcast({"type": "maze", **maze_payload(), **sim.public_state(),
                         "agents": sim.snapshot_agents()})


def apply_loaded_scenario(data: dict):
    if not isinstance(data, dict):
        raise HTTPException(400, "场景必须是 JSON 对象")
    # 加载场景同样会替换地图，先归档当前场次。
    sim.archive_trial(reason="scenario_load")
    try:
        mw, mh, cell = int(data["mw"]), int(data["mh"]), int(data["cell"])
        walls = data["walls"]
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "场景缺少地图尺寸或墙壁")
    if not (4 <= mw <= 80 and 4 <= mh <= 80 and 8 <= cell <= 200):
        raise HTTPException(400, "地图尺寸不合法")
    if not isinstance(walls, list) or len(walls) != mh:
        raise HTTPException(400, "墙壁数据与尺寸不符")
    if any(not isinstance(row, list) or len(row) != mw for row in walls):
        raise HTTPException(400, "墙壁数据与尺寸不符")
    maze.mw, maze.mh, maze.cell = mw, mh, cell
    maze.world_w = mw * cell
    maze.world_h = mh * cell
    maze.walls = [[int(v) & 15 for v in row] for row in walls]
    maze.complexity = max(1, min(10, int(data.get("complexity", maze.complexity))))
    maze.view_level = max(1, min(10, int(data.get("view_level", maze.view_level))))
    layout = data.get("exit_layout", "spread")
    maze.exit_layout = layout if layout in {"spread", "corners", "sides"} else "spread"
    exits = data.get("exits") or []
    maze.exits = [tuple(int(v) for v in item[:2]) for item in exits]
    maze.exits = [item for item in maze.exits if 0 <= item[0] < mw and 0 <= item[1] < mh]
    if not maze.exits:
        maze.exits = choose_exits(mw, mh, 1, maze.exit_layout)
    maze.exit = maze.exits[0]
    maze.exit_count = len(maze.exits)
    maze.seed = None if data.get("seed") is None else int(data["seed"])
    vw = VIEW_WIDTH_LIST[maze.view_level - 1]
    vw = max(vw, maze.cell * 3)
    vw = min(vw, maze.world_w)
    vh = max(60, int(vw * 9 / 16))
    maze.view_w = vw
    maze.view_h = min(vh, maze.world_h)
    maze.gen += 1
    try:
        sim.apply_scenario_state(data)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------------- WebSocket ----------------
@app.websocket("/ws/host")
async def ws_host(ws: WebSocket):
    await ws.accept()
    try:
        await ws.send_json({"type": "init", "role": "host", "lan_ip": LAN_IP,
                            **maze_payload(), **sim.public_state(),
                            "agents": sim.snapshot_agents()})
        hub.hosts.add(ws)
        hub.register(ws)
        while True:
            await ws.receive_text()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hub.remove(ws)


@app.websocket("/ws/player")
async def ws_player(ws: WebSocket,
                    pid: str = Query(..., min_length=2, max_length=64, pattern=r"^p[A-Za-z0-9_-]+$"),
                    name: str = Query("", max_length=64),
                    participant: str = Query("", max_length=64),
                    consent: Optional[bool] = Query(None)):
    await ws.accept()
    old_ws = hub.players.get(pid)
    if old_ws:
        # Keep one owner per player; old handlers must not remove the new connection.
        hub.remove(old_ws)
        with suppress(Exception):
            await asyncio.wait_for(old_ws.close(code=4001), timeout=1.0)
    # 直接调用（测试/内部）时默认值是 Query 对象，这里统一归一化。
    participant = participant if isinstance(participant, str) else ""
    consent = consent if isinstance(consent, bool) else None
    enforce = getattr(sim, "require_participant_consent", True)
    humans = [a for a in sim.agents.values() if a.is_human]
    if len(humans) >= MAX_HUMANS and pid not in sim.agents:
        await ws.close(code=4003, reason="人数已满")
        return
    # 同一位参与者只允许占用一个身份：换设备/换标签页重复加入时，
    # 沿用其原有 pid，而不是产生第二条样本。
    participant = participant if isinstance(participant, str) else ""
    if participant:
        existing = sim.agent_by_participant(participant)
        if existing is not None and existing.pid != pid:
            pid = existing.pid
            old_ws = hub.players.get(pid)
            if old_ws and old_ws is not ws:
                hub.remove(old_ws)
                with suppress(Exception):
                    await asyncio.wait_for(old_ws.close(code=4001), timeout=1.0)
    if enforce and not participant:
        with suppress(Exception):
            await ws.close(code=4004, reason="缺少参与者编号")
        return
    if enforce and consent is not True:
        with suppress(Exception):
            await ws.close(code=4005, reason="未确认知情同意")
        return
    color = COLORS[len(humans) % len(COLORS)]
    p = sim.add_human(pid, name or f"玩家{len(humans)+1}", color,
                      participant_id=participant, consent=consent)
    hub.players[pid] = ws

    try:
        await ws.send_json({"type": "init", "you": {"pid": pid, "color": p.color, "name": p.name},
                            **maze_payload(), **sim.public_state(), "agents": sim.snapshot_agents()})
        hub.register(ws)
        while True:
            raw = await ws.receive_text()
            if hub.players.get(pid) is not ws:
                break
            if len(raw) > 2048:
                continue
            try:
                data = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if not isinstance(data, dict):
                continue
            t = data.get("type")
            p.last_seen = time.time()
            p.online = True
            if t == "input":
                try:
                    x, y = float(data.get("x", 0)), float(data.get("y", 0))
                except (ValueError, TypeError, OverflowError):
                    continue
                if not math.isfinite(x) or not math.isfinite(y):
                    continue
                p.ix, p.iy = max(-1.0, min(1.0, x)), max(-1.0, min(1.0, y))
            elif t == "rename":
                p.name = str(data.get("name", ""))[:12] or p.name
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        hub.remove(ws)


# ---------------- 页面 ----------------
@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "host.html",
                        headers={"Cache-Control": "no-store"})


@app.get("/play")
async def play():
    return FileResponse(STATIC_DIR / "play.html",
                        headers={"Cache-Control": "no-store"})


# ---------------- API ----------------
@app.get("/api/qr")
async def api_qr(request: Request):
    port = request.url.port or 8000
    url = f"http://{LAN_IP}:{port}/play"
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="svg", scale=8, border=2)
    return Response(buf.getvalue(), media_type="image/svg+xml",
                    headers={"Cache-Control": "no-store"})


@app.get("/api/info")
async def api_info(request: Request):
    port = request.url.port or 8000
    return {
        "lan_ip": LAN_IP, "port": port,
        "play_url": f"http://{LAN_IP}:{port}/play",
        "world": {"w": maze.world_w, "h": maze.world_h},
        "view": {"w": maze.view_w, "h": maze.view_h},
        "maze": {"w": maze.mw, "h": maze.mh, "cell": maze.cell},
        "exit": list(maze.exit),
        "exits": [list(exit_cell) for exit_cell in maze.exits],
        "exit_count": maze.exit_count,
        "exit_layout": maze.exit_layout,
        "complexity": maze.complexity,
        "view_level": maze.view_level,
        **sim.public_state(),
    }


@app.get("/api/tracks")
async def api_tracks():
    return {"tracks": {a.pid: a.track for a in sim.agents.values()}}


@app.get("/api/players")
async def api_players():
    return {"players": sim.snapshot_agents(), **sim.public_state()}


@app.post("/api/reset")
async def api_reset(pos: int = Query(0, ge=0, le=1), fire: int = Query(0, ge=0, le=1)):
    sim.reset_crowd(keep_humans=True, clear_fire=bool(fire))
    await hub.broadcast({"type": "maze", **maze_payload(), **sim.public_state(),
                         "agents": sim.snapshot_agents()})
    return {"ok": True, **sim.public_state()}


@app.post("/api/restart")
async def api_restart(complexity: int = Query(8, ge=1, le=10),
                      view: int = Query(3, ge=1, le=10),
                      exit_count: int = Query(1, ge=1, le=4),
                      exit_layout: str = Query("spread", pattern="^(spread|corners|sides)$"),
                      seed: Optional[int] = Query(None)):
    """按新的复杂度和视野重建迷宫"""
    await apply_restart(complexity, view, exit_count, exit_layout, seed=seed)
    return {"ok": True, "gen": maze.gen,
            "complexity": maze.complexity, "view_level": maze.view_level,
            "cell": maze.cell, "mw": maze.mw, "mh": maze.mh,
            "exits": [list(exit_cell) for exit_cell in maze.exits],
            "exit_count": maze.exit_count, "exit_layout": maze.exit_layout,
            "seed": maze.seed}


@app.post("/api/evac/start")
async def api_evac_start():
    sim.start()
    await hub.broadcast(evac_state_msg(time.time()))
    return {"ok": True, **sim.public_state()}


@app.post("/api/evac/pause")
async def api_evac_pause():
    sim.pause()
    await hub.broadcast(evac_state_msg(time.time()))
    return {"ok": True, **sim.public_state()}


@app.post("/api/evac/fire")
async def api_evac_fire(gx: int = -1, gy: int = -1):
    if (gx, gy) != (-1, -1) and not (0 <= gx < maze.mw and 0 <= gy < maze.mh):
        raise HTTPException(422, "点火位置超出地图范围")
    ok = sim.ignite(None if gx < 0 else gx, None if gy < 0 else gy)
    await hub.broadcast(evac_state_msg(time.time()))
    return {"ok": ok, **sim.public_state()}


@app.post("/api/evac/panic")
async def api_evac_panic(v: float = Query(0.4, ge=0, le=1, allow_inf_nan=False)):
    sim.panic = max(0.0, min(1.0, float(v)))
    return {"ok": True, "panic": sim.panic}


@app.post("/api/evac/npcs")
async def api_evac_npcs(n: int = Query(..., ge=0, le=MAX_NPCS)):
    sim.set_npc_count(n)
    await hub.broadcast({"type": "maze", **maze_payload(), **sim.public_state(),
                         "agents": sim.snapshot_agents()})
    return {"ok": True, **sim.public_state()}


@app.post("/api/evac/speed")
async def api_evac_speed(v: float = Query(1.0, ge=0.25, le=4.0, allow_inf_nan=False)):
    sim.set_time_scale(v)
    await hub.broadcast(evac_state_msg(time.time()))
    return {"ok": True, "time_scale": sim.time_scale}


@app.post("/api/evac/wind")
async def api_evac_wind(x: float = Query(0.0, ge=-8.0, le=8.0, allow_inf_nan=False),
                        y: float = Query(0.0, ge=-8.0, le=8.0, allow_inf_nan=False)):
    sim.set_wind(x, y)
    await hub.broadcast(evac_state_msg(time.time()))
    return {"ok": True, **sim.public_state()}


@app.post("/api/evac/spawn_zone")
async def api_evac_spawn_zone(x0: Optional[int] = None, y0: Optional[int] = None,
                              x1: Optional[int] = None, y1: Optional[int] = None):
    """限制人群起始区（格坐标）。全部省略表示恢复全图随机。

    设置后需重置人群才会对在场个体生效，用于控制初始到出口距离这一协变量。
    """
    if x0 is None and y0 is None and x1 is None and y1 is None:
        sim.set_spawn_zone(None)
    else:
        if None in (x0, y0, x1, y1):
            raise HTTPException(400, "起始区需要同时提供 x0, y0, x1, y1")
        try:
            sim.set_spawn_zone((x0, y0, x1, y1))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    await hub.broadcast(evac_state_msg(time.time()))
    return {"ok": True, "spawn_zone": list(sim.spawn_zone) if sim.spawn_zone else None}


@app.get("/api/experiment")
async def api_experiment_get():
    """查看当前实验元数据（场次分组、条件标签等）。"""
    return {"ok": True, "experiment": sim.build_summary()["experiment"]}


@app.post("/api/experiment")
async def api_experiment_set(request: Request):
    """设置实验元数据。这些字段会写进汇总与轨迹文件，用于多场次分组统计。"""
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "实验元数据必须是 JSON")
    if not isinstance(data, dict):
        raise HTTPException(400, "实验元数据必须是 JSON 对象")
    fields = ("experiment_id", "session_id", "condition", "group", "notes", "operator")
    for key in fields:
        if key in data:
            value = data[key]
            if value is None:
                value = ""
            if not isinstance(value, (str, int, float)) or isinstance(value, bool):
                raise HTTPException(400, f"{key} 必须是字符串")
            setattr(sim, key, str(value)[:200])
    return {"ok": True, "experiment": sim.build_summary()["experiment"]}


@app.post("/api/experiment/archive")
async def api_experiment_archive():
    """把当前场次立即落盘为独立文件，不影响运行中的仿真。"""
    names = sim.archive_trial(reason="manual")
    return {"ok": True, "saved": names, "trial_seq": sim.trial_seq}


@app.get("/api/evac/summary")
async def api_evac_summary():
    return sim.summary or sim.build_summary()


@app.get("/api/scenario")
async def api_scenario_get():
    return JSONResponse(sim.export_scenario(), headers={
        "Content-Disposition": "attachment; filename=evac_scenario.json"})


@app.post("/api/scenario")
async def api_scenario_load(request: Request):
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "场景必须是 JSON")
    apply_loaded_scenario(data)
    await hub.broadcast({"type": "maze", **maze_payload(), **sim.public_state(),
                         "agents": sim.snapshot_agents()})
    return {"ok": True, "gen": maze.gen, **sim.public_state()}


@app.get("/api/export")
async def api_export(fmt: str = "csv"):
    if fmt == "summary" or fmt == "json":
        data = sim.summary or sim.build_summary()
        return JSONResponse(data, headers={
            "Content-Disposition": "attachment; filename=evac_summary.json"})

    return PlainTextResponse(sim.export_csv(), media_type="text/csv",
                             headers={"Content-Disposition":
                                      "attachment; filename=evac_tracks.csv"})


# ---------------- 启动 ----------------
def find_free_port(start=8000, end=9100) -> int:
    for p in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("0.0.0.0", p))
                return p
            except OSError:
                continue
    raise RuntimeError("没有找到可用端口")


if __name__ == "__main__":
    import uvicorn

    port = find_free_port()
    host_url = f"http://127.0.0.1:{port}/"
    print("=" * 60)
    print("  迷宫多人系统已启动")
    print(f"  主机大屏:  {host_url}")
    print(f"  手机控制:  http://{LAN_IP}:{port}/play")
    print(f"  初始迷宫:  {maze.mw} × {maze.mh} 格（每格 {maze.cell}px）")
    print(f"  初始视野:  {maze.view_w} × {maze.view_h} 世界单位")
    print("  提示: 手机建议横屏，视野最佳")
    print("=" * 60)

    threading.Timer(1.5, lambda: webbrowser.open(host_url)).start()
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
