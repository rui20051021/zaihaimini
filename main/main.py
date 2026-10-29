# main.py
# -*- coding: utf-8 -*-
"""
二维迷宫多人系统（可调复杂度 / 视野）
运行: python main.py
依赖: pip install -r requirements.txt
"""
import asyncio, copy, hashlib, io, json, math, mimetypes, os, random, re, secrets, shutil, socket, struct, subprocess, sys, tempfile, threading, time, uuid, webbrowser
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import segno
from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect, Query, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from starlette.concurrency import run_in_threadpool

from evac import TARGET_N, MAX_NPCS, SCENARIO_VERSION, DataPersistenceError, EvacSim
from nav3d import EvacSim3D, SceneValidationError, validate_scene

def _restrict_file_descriptor(fd: int) -> None:
    """Restrict newly-created files where POSIX descriptor permissions exist."""
    if os.name != "nt":
        os.fchmod(fd, 0o600)


def _sync_directory(path: Path) -> None:
    """Persist directory metadata where the platform supports directory handles."""
    if os.name == "nt":
        # Windows does not permit opening directories as file descriptors.
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


# ---------------- 全局常量 ----------------
BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
# Python's stdlib MIME table does not consistently include GLB/GLTF on macOS
# and slim Linux images. Register the formats before mounting StaticFiles so
# browsers and reverse proxies receive an accurate content type.
mimetypes.add_type("model/gltf-binary", ".glb")
mimetypes.add_type("model/gltf+json", ".gltf")
mimetypes.add_type("text/javascript", ".mjs")
DATA_DIR = Path(os.environ.get("MAZE_DATA_DIR", str(BASE_DIR / "data"))).expanduser()
DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
AUDIT_LOG_PATH = Path(os.environ.get("MAZE_AUDIT_LOG", str(DATA_DIR / "audit.jsonl"))).expanduser()
AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
AUDIT_LOCK = threading.Lock()

BASE_W, BASE_H = 1920, 1088        # 基准世界尺寸
PLAYER_R = 7                       # 玩家半径（世界单位）
TICK = 0.02
IDLE_BROADCAST = 0.2
INPUT_TIMEOUT = 2.0
MAX_HUMANS = 80
PLAYER_SPEED = 100.0

def configured_default_npcs() -> int:
    raw = os.environ.get("MAZE_DEFAULT_NPCS", "0").strip()
    try:
        value = int(raw or "0")
    except ValueError as exc:
        raise RuntimeError("MAZE_DEFAULT_NPCS 必须是 0 到 300 的整数") from exc
    if not 0 <= value <= 300:
        raise RuntimeError("MAZE_DEFAULT_NPCS 必须是 0 到 300 的整数")
    return value

DEFAULT_SCENE_NPCS = configured_default_npcs()
APP_VERSION = os.environ.get("MAZE_APP_VERSION", "dev")
OPEN_BROWSER = os.environ.get("MAZE_OPEN_BROWSER", "1").strip().lower() not in ("0", "false", "no", "off")

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


def configured_public_url(port: int) -> str:
    """Return the URL that should be shared with players.

    MAZE_PUBLIC_URL may be a site root (https://example.test) or a complete
    player URL (https://example.test/play). When unset, use the LAN address.
    """
    raw = os.environ.get("MAZE_PUBLIC_URL", "").strip().rstrip("/")
    if raw:
        return raw if raw.endswith("/play") else f"{raw}/play"
    return f"http://{LAN_IP}:{port}/play"


def configured_port() -> Optional[int]:
    raw = os.environ.get("MAZE_PORT", "").strip()
    if not raw:
        return None
    try:
        port = int(raw)
    except ValueError as exc:
        raise RuntimeError("MAZE_PORT 必须是 1 到 65535 的整数") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("MAZE_PORT 必须是 1 到 65535 的整数")
    return port


# 演示模式：MAZE_REQUIRE_CONSENT=0 时恢复"扫码即可加入"，不做参与者校验。
REQUIRE_CONSENT = os.environ.get("MAZE_REQUIRE_CONSENT", "1").strip().lower() \
    not in ("0", "false", "no", "off")
# 可选的主机/管理端令牌。未设置时保持局域网演示的无令牌行为；
# 设置后，主机 WebSocket、实验控制、导出和参与者数据接口都需要令牌。
ADMIN_TOKEN = os.environ.get("MAZE_ADMIN_TOKEN", "").strip()
ADMIN_SESSION_COOKIE = "maze_admin_session"
ADMIN_SESSION_TTL = 12 * 60 * 60
# Session ids survive a service restart so the operator token only has to be
# typed once per device.  The map is persisted lazily on login/logout and on
# a periodic sweep; a missing or unreadable file silently degrades to
# in-memory sessions.
ADMIN_SESSIONS: Dict[str, float] = {}
ADMIN_SESSIONS_LOADED = False
ADMIN_LOGIN_ATTEMPTS: Dict[str, List[float]] = {}
ADMIN_LOGIN_LOCK = threading.Lock()
ADMIN_LOGIN_WINDOW = 5 * 60
ADMIN_LOGIN_LIMIT = 10
MAX_EXPERIMENT_JSON_BYTES = 16 * 1024
MAX_SCENARIO_JSON_BYTES = 1 * 1024 * 1024
# Calibration drafts carry every walkable polygon, stair and exit, so they
# are allowed to be larger than a runtime scenario document.
MAX_SCENE_JSON_BYTES = 8 * 1024 * 1024
MAX_GLB_UPLOAD_BYTES = 1024 * 1024 * 1024
MAX_PARTICIPANT_ID_LENGTH = 64
MAX_PLAYER_NAME_LENGTH = 12
TRUST_PROXY_HEADERS = os.environ.get("MAZE_TRUST_PROXY", "0").strip().lower() \
    in ("1", "true", "yes", "on")


def external_scheme_host(headers, fallback_scheme: str, fallback_host: str) -> Tuple[str, str]:
    """Resolve the browser-visible scheme/host for same-origin checks.

    Forwarded headers are intentionally opt-in.  Without an explicitly
    trusted reverse proxy, accepting them would let a client spoof the
    origin check.  When enabled, only the first comma-separated hop is used
    and the scheme is restricted to HTTP(S).
    """
    scheme = fallback_scheme
    host = fallback_host
    if TRUST_PROXY_HEADERS:
        forwarded_proto = str(headers.get("x-forwarded-proto", "")).split(",", 1)[0].strip().lower()
        if forwarded_proto in {"http", "https"}:
            scheme = forwarded_proto
        forwarded_host = str(headers.get("x-forwarded-host", "")).split(",", 1)[0].strip()
        if forwarded_host and all(ord(ch) >= 32 and ch not in {"\"", "'"} for ch in forwarded_host):
            host = forwarded_host
    return scheme, host


ADMIN_SESSIONS_PATH = DATA_DIR / "admin-sessions.json"


def _persist_admin_sessions() -> None:
    """Store absolute expiry timestamps so sessions survive a restart."""
    now = time.time()
    payload = {"schema": 1, "sessions": {
        session_id: now + max(0.0, expires_at - time.monotonic())
        for session_id, expires_at in ADMIN_SESSIONS.items() if expires_at > time.monotonic()
    }}
    try:
        _atomic_json(ADMIN_SESSIONS_PATH, payload)
    except OSError:
        pass


def load_admin_sessions() -> None:
    """Restore unexpired sessions once per process, ignoring corrupt files."""
    global ADMIN_SESSIONS_LOADED
    if ADMIN_SESSIONS_LOADED:
        return
    ADMIN_SESSIONS_LOADED = True
    try:
        raw = json.loads(ADMIN_SESSIONS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    sessions = raw.get("sessions") if isinstance(raw, dict) else None
    if not isinstance(sessions, dict):
        return
    now = time.time()
    for session_id, expires_at in sessions.items():
        try:
            remaining = float(expires_at) - now
        except (TypeError, ValueError):
            continue
        if isinstance(session_id, str) and session_id and remaining > 0:
            ADMIN_SESSIONS[session_id] = time.monotonic() + remaining


def valid_admin_session(session_id: str) -> bool:
    """Validate and lazily expire an opaque operator session."""
    load_admin_sessions()
    expires_at = ADMIN_SESSIONS.get(session_id, 0.0)
    if expires_at <= time.monotonic():
        if ADMIN_SESSIONS.pop(session_id, None) is not None:
            _persist_admin_sessions()
        return False
    return True


def admin_token_matches(supplied: str, expected: Optional[str] = None) -> bool:
    expected = ADMIN_TOKEN if expected is None else expected
    if not isinstance(supplied, str) or not expected:
        return False
    return secrets.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


def require_same_origin(request: Request):
    origin = request.headers.get("origin", "")
    scheme, host = external_scheme_host(
        request.headers, request.url.scheme, request.headers.get("host", request.url.netloc)
    )
    expected = f"{scheme}://{host}"
    if not origin or origin != expected:
        raise HTTPException(403, "跨源管理请求已拒绝")


def allow_admin_login_attempt(client_ip: str) -> bool:
    now = time.monotonic()
    with ADMIN_LOGIN_LOCK:
        expired = [ip for ip, attempts in ADMIN_LOGIN_ATTEMPTS.items()
                   if not attempts or now - attempts[-1] > ADMIN_LOGIN_WINDOW]
        for ip in expired:
            ADMIN_LOGIN_ATTEMPTS.pop(ip, None)
        attempts = ADMIN_LOGIN_ATTEMPTS.setdefault(client_ip or "unknown", [])
        attempts[:] = [stamp for stamp in attempts if now - stamp <= ADMIN_LOGIN_WINDOW]
        if len(attempts) >= ADMIN_LOGIN_LIMIT:
            return False
        attempts.append(now)
        return True


async def read_json_body(request: Request, max_bytes: int, label: str, allow_empty: bool = False):
    """Read a bounded JSON request body without buffering untrusted input first."""
    # Keep small dependency-free unit doubles usable; real Starlette Request
    # objects always expose both ``headers`` and ``stream`` below.
    if not hasattr(request, "headers") or not hasattr(request, "stream"):
        try:
            return await request.json()
        except Exception:
            raise HTTPException(400, f"{label}必须是 JSON")
    content_length = request.headers.get("content-length", "")
    if content_length:
        try:
            declared = int(content_length)
        except (TypeError, ValueError):
            raise HTTPException(400, f"{label}请求长度不合法")
        if declared < 0:
            raise HTTPException(400, f"{label}请求长度不合法")
        if declared > max_bytes:
            raise HTTPException(413, f"{label}请求过大")

    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > max_bytes:
            raise HTTPException(413, f"{label}请求过大")
    if not raw and allow_empty:
        return {}
    try:
        return json.loads(bytes(raw).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(400, f"{label}必须是 JSON")


async def require_admin(request: Request):
    """Protect operator/data endpoints when MAZE_ADMIN_TOKEN is configured."""
    if not ADMIN_TOKEN:
        return
    supplied = request.headers.get("X-Maze-Admin-Token", "")
    if not supplied:
        authorization = request.headers.get("Authorization", "")
        if authorization.lower().startswith("bearer "):
            supplied = authorization[7:].strip()
    if admin_token_matches(supplied):
        return

    session_id = request.cookies.get(ADMIN_SESSION_COOKIE, "")
    if session_id and valid_admin_session(session_id):
        # SameSite cookies reduce CSRF exposure; require a same-origin browser
        # Origin as an additional check for browser-authenticated mutations.
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            require_same_origin(request)
        return

    raise HTTPException(
        status_code=401,
        detail="需要主机管理令牌",
        headers={"WWW-Authenticate": "Bearer"},
    )


def websocket_admin_token(ws: WebSocket) -> bool:
    if not ADMIN_TOKEN:
        return True
    session_id = ws.cookies.get(ADMIN_SESSION_COOKIE, "")
    if not session_id or not valid_admin_session(session_id):
        return False
    origin = ws.headers.get("origin", "")
    fallback_scheme = "https" if ws.url.scheme == "wss" else "http"
    scheme, host = external_scheme_host(
        ws.headers, fallback_scheme, ws.headers.get("host", ws.url.netloc)
    )
    expected = f"{scheme}://{host}"
    return bool(origin) and origin == expected


def audit_event(action: str, request: Optional[Request] = None, **details):
    """Append a redacted operator/audit event without affecting the request path.

    The log deliberately stores route-level metadata only: never record bearer
    tokens, participant identifiers, request bodies, or exported data. A failed
    audit write must not make an otherwise successful experiment control call
    fail, but is reported on stderr for the operator.
    """
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    client = getattr(request, "client", None) if request is not None else None
    record = {
        "timestamp": now,
        "action": str(action)[:80],
        "request_id": getattr(getattr(request, "state", None), "request_id", "")[:80],
        "method": getattr(request, "method", ""),
        "path": getattr(getattr(request, "url", None), "path", ""),
        "client": getattr(client, "host", "") if client else "",
    }
    for key, value in details.items():
        if key in {"token", "authorization", "body", "participant", "participant_id"}:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            record[str(key)[:40]] = value
    try:
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with AUDIT_LOCK:
            fd = os.open(AUDIT_LOG_PATH, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
            _restrict_file_descriptor(fd)
            with os.fdopen(fd, "a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
    except Exception as exc:  # pragma: no cover - filesystem failure is environment-specific
        print(f"审计日志写入失败: {exc}", file=sys.stderr)


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
                # Waiting for the next broadcast is not a stalled network send.
                # Keep idle connections alive; apply the timeout only to send_text.
                payload = await queue.get()
                await asyncio.wait_for(ws.send_text(payload), timeout=5.0)
        except asyncio.CancelledError:
            raise
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
    v2_task = asyncio.create_task(scene_game_loop())
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
        # v1 files are immutable historical datasets; the new active UI writes
        # only v2-named outputs and must never replace evac_latest.json/CSV.
        v2_task.cancel()
        with suppress(asyncio.CancelledError):
            await v2_task


app = FastAPI(
    title="Maze Evacuation Simulation",
    version=APP_VERSION,
    lifespan=lifespan,
)


@app.exception_handler(DataPersistenceError)
async def data_persistence_error_handler(request: Request, exc: DataPersistenceError):
    return JSONResponse(
        status_code=507,
        content={
            "detail": "实验数据无法写入磁盘；当前场次未重置，请检查磁盘空间和目录权限"
        },
        headers={"Cache-Control": "no-store"},
    )


@app.middleware("http")
async def add_request_and_security_headers(request: Request, call_next):
    """Add lightweight production defaults without changing the UI contract."""
    candidate_id = request.headers.get("X-Request-ID", "")
    valid_id = (candidate_id.isascii() and 1 <= len(candidate_id) <= 80
                and all(ch.isalnum() or ch in "-_." for ch in candidate_id))
    request_id = candidate_id if valid_id else uuid.uuid4().hex
    request.state.request_id = request_id
    try:
        response = await call_next(request)
    except Exception:
        # Let FastAPI/Uvicorn render the 500, but keep a correlation id in logs/headers.
        await run_in_threadpool(audit_event, "http_exception", request, status=500)
        raise
    # Persist route-level operator activity for reproducibility and incident review.
    # GET data reads are included only for sensitive/export endpoints.
    audited_gets = {"/api/export", "/api/scenario", "/api/tracks", "/api/players", "/api/evac/summary"}
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} or request.url.path in audited_gets:
        await run_in_threadpool(audit_event, "http_request", request, status=response.status_code)
    response.headers.setdefault("X-Request-ID", request_id)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), xr-spatial-tracking=(self)")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; base-uri 'self'; object-src 'none'; "
        "frame-ancestors 'self'; form-action 'self'; "
        # meshopt is a local, pinned WASM decoder used by the GLB scene loader.
        # Keep the permission narrow to WebAssembly rather than enabling eval.
        "script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; media-src 'self' blob:; "
        # GLTFLoader materializes embedded GLB textures as blob: URLs and fetches
        # them through connect-src before decoding them.
        "connect-src 'self' blob: ws: wss:; worker-src 'self' blob:"
    )
    no_store_paths = audited_gets | {
        "/healthz", "/readyz", "/api/version", "/api/info", "/api/scene-models",
        "/", "/play", "/calibrate",
    }
    if request.url.path in no_store_paths or request.url.path.startswith("/api/admin/"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
    # StaticFiles only emits ETag/Last-Modified, which makes a 30 MB GLB a
    # conditional revalidation on every reload and on every mobile page.  GLB
    # scene files are immutable in practice (a replacement ships under a new
    # name or a new ?v=<sha256> query), so they are safe to cache for a week.
    elif request.url.path.startswith("/static/scenes/") and request.url.path.lower().endswith(".glb"):
        response.headers["Cache-Control"] = "public, max-age=604800"
    elif request.url.path.startswith("/static/"):
        revision = request.query_params.get("v", "")
        response.headers["Cache-Control"] = ("public, max-age=604800, immutable"
            if re.fullmatch(r"[a-fA-F0-9]{8,64}", revision) else "no-cache")
    return response


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def save_tracks():
    try:
        sim.archive_trial(reason="shutdown")
        sim.save_outputs(stamped=False)
    except DataPersistenceError as exc:
        print(f"关闭时保存数据失败: {exc}", file=sys.stderr)


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
    """Validate a scenario completely before archiving or mutating live state."""
    if not isinstance(data, dict):
        raise HTTPException(400, "场景必须是 JSON 对象")
    version = data.get("version", SCENARIO_VERSION)
    if isinstance(version, bool) or not isinstance(version, int) \
            or version != SCENARIO_VERSION:
        raise HTTPException(400, f"不支持的场景版本: {version}")
    try:
        mw, mh, cell = data["mw"], data["mh"], data["cell"]
        walls = data["walls"]
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "场景缺少地图尺寸或墙壁")
    if any(isinstance(value, bool) or not isinstance(value, int)
           for value in (mw, mh, cell)):
        raise HTTPException(400, "地图尺寸必须是整数")
    if not (4 <= mw <= 80 and 4 <= mh <= 80 and 8 <= cell <= 200):
        raise HTTPException(400, "地图尺寸不合法")
    if not isinstance(walls, list) or len(walls) != mh:
        raise HTTPException(400, "墙壁数据与尺寸不符")
    if any(not isinstance(row, list) or len(row) != mw for row in walls):
        raise HTTPException(400, "墙壁数据与尺寸不符")
    try:
        normalized_walls = []
        for row in walls:
            normalized_row = []
            for value in row:
                if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 15:
                    raise ValueError("墙壁位掩码必须是 0 到 15 之间的整数")
                normalized_row.append(value)
            normalized_walls.append(normalized_row)

        for y, row in enumerate(normalized_walls):
            for x, mask in enumerate(row):
                if y == 0 and not mask & 1 or y == mh - 1 and not mask & 4 \
                        or x == 0 and not mask & 8 or x == mw - 1 and not mask & 2:
                    raise ValueError("地图边界必须封闭")
                if x + 1 < mw and bool(mask & 2) != bool(normalized_walls[y][x + 1] & 8):
                    raise ValueError("相邻格的东西墙定义不一致")
                if y + 1 < mh and bool(mask & 4) != bool(normalized_walls[y + 1][x] & 1):
                    raise ValueError("相邻格的南北墙定义不一致")

        complexity = data.get("complexity", maze.complexity)
        view_level = data.get("view_level", maze.view_level)
        if any(isinstance(value, bool) or not isinstance(value, int)
               for value in (complexity, view_level)):
            raise ValueError("迷宫复杂度和视野必须是整数")
        if not 1 <= complexity <= 10 or not 1 <= view_level <= 10:
            raise ValueError("迷宫复杂度和视野必须在 1 到 10 之间")

        layout = data.get("exit_layout", "spread")
        if layout not in {"spread", "corners", "sides"}:
            raise ValueError("出口布局不合法")
        raw_exits = data.get("exits") or []
        if not isinstance(raw_exits, list):
            raise ValueError("出口数据格式不合法")
        exits = []
        for item in raw_exits:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                raise ValueError("出口坐标格式不合法")
            gx, gy = item[:2]
            if (isinstance(gx, bool) or isinstance(gy, bool)
                    or not isinstance(gx, int) or not isinstance(gy, int)
                    or not 0 <= gx < mw or not 0 <= gy < mh):
                raise ValueError("出口坐标超出地图范围或不是整数")
            if (gx, gy) not in exits:
                exits.append((gx, gy))
        if len(exits) > 4:
            raise ValueError("出口数量不能超过 4 个")
        if not exits:
            exits = choose_exits(mw, mh, 1, layout)

        seed = data.get("seed")
        if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
            raise ValueError("种子必须是整数")

        npc_target = data.get("npc_target", sim.npc_target)
        if (isinstance(npc_target, bool) or not isinstance(npc_target, int)
                or not 0 <= npc_target <= MAX_NPCS):
            raise ValueError(f"NPC 人数必须是 0 到 {MAX_NPCS} 之间的整数")
        panic = float(data.get("panic", sim.panic))
        scale = float(data.get("time_scale", sim.time_scale))
        if not math.isfinite(panic) or not 0 <= panic <= 1:
            raise ValueError("恐慌系数必须在 0 到 1 之间")
        if not math.isfinite(scale) or not 0.25 <= scale <= 4:
            raise ValueError("倍速必须在 0.25 到 4 之间")

        wind = data.get("wind") or {}
        if not isinstance(wind, dict):
            raise ValueError("风场数据格式不合法")
        wind_x, wind_y = float(wind.get("x", 0.0)), float(wind.get("y", 0.0))
        if not math.isfinite(wind_x) or not math.isfinite(wind_y) or math.hypot(wind_x, wind_y) > 8:
            raise ValueError("风速合速度不能超过 8 m/s")

        zone = data.get("spawn_zone")
        if zone is not None:
            if not isinstance(zone, (list, tuple)) or len(zone) != 4:
                raise ValueError("起始区必须是 4 个整数 (x0, y0, x1, y1)")
            if any(isinstance(v, bool) or not isinstance(v, int) for v in zone):
                raise ValueError("起始区必须是 4 个整数 (x0, y0, x1, y1)")
            if (min(zone[0], zone[2]) < 0 or max(zone[0], zone[2]) >= mw
                    or min(zone[1], zone[3]) < 0 or max(zone[1], zone[3]) >= mh):
                raise ValueError("起始区超出地图范围")

        fire = data.get("fire") or []
        if not isinstance(fire, list):
            raise ValueError("火源数据格式不合法")
        for item in fire:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                raise ValueError("火源坐标格式不合法")
            gx, gy = item[:2]
            if (isinstance(gx, bool) or isinstance(gy, bool)
                    or not isinstance(gx, int) or not isinstance(gy, int)
                    or not 0 <= gx < mw or not 0 <= gy < mh):
                raise ValueError("火源坐标超出地图范围或不是整数")

        heating = data.get("heating") or []
        if not isinstance(heating, list):
            raise ValueError("加热数据格式不合法")
        for item in heating:
            if not isinstance(item, (list, tuple)) or len(item) < 3:
                raise ValueError("加热数据格式不合法")
            gx, gy, value = item[:3]
            if (isinstance(gx, bool) or isinstance(gy, bool)
                    or not isinstance(gx, int) or not isinstance(gy, int)
                    or not 0 <= int(gx) < mw or not 0 <= int(gy) < mh
                    or not isinstance(value, (int, float)) or isinstance(value, bool)
                    or not math.isfinite(float(value)) or float(value) < 0):
                raise ValueError("加热数据超出有效范围")
    except (TypeError, ValueError, OverflowError) as exc:
        raise HTTPException(400, str(exc) or "场景数据格式不合法")

    # 到这里才会产生归档和状态变更；非法上传不会额外制造空场次。
    sim.archive_trial(reason="scenario_load")

    # ``EvacSim.apply_scenario_state`` also resets agents and rebuilds fields.
    # Keep a detached snapshot so an unexpected post-validation failure cannot
    # leave the map and simulation pointing at different scenarios.
    maze_before = copy.deepcopy(maze.__dict__)
    sim_before = {
        key: copy.deepcopy(value)
        for key, value in sim.__dict__.items()
        if key != "maze"
    }
    try:
        maze.mw, maze.mh, maze.cell = mw, mh, cell
        maze.world_w = mw * cell
        maze.world_h = mh * cell
        maze.walls = normalized_walls
        maze.complexity = complexity
        maze.view_level = view_level
        maze.exit_layout = layout
        maze.exits = exits
        maze.exit = maze.exits[0]
        maze.exit_count = len(maze.exits)
        maze.seed = seed
        vw = VIEW_WIDTH_LIST[maze.view_level - 1]
        vw = max(vw, maze.cell * 3)
        vw = min(vw, maze.world_w)
        vh = max(60, int(vw * 9 / 16))
        maze.view_w = vw
        maze.view_h = min(vh, maze.world_h)
        maze.gen += 1
        sim.apply_scenario_state(data)
    except (TypeError, ValueError, OverflowError) as exc:
        maze.__dict__.clear()
        maze.__dict__.update(maze_before)
        sim.__dict__.clear()
        sim.__dict__.update(sim_before)
        sim.maze = maze
        raise HTTPException(400, str(exc) or "场景应用失败")
    except Exception:
        maze.__dict__.clear()
        maze.__dict__.update(maze_before)
        sim.__dict__.clear()
        sim.__dict__.update(sim_before)
        sim.maze = maze
        raise


# ---------------- WebSocket ----------------
@app.websocket("/ws/host")
async def ws_host(ws: WebSocket):
    if not websocket_admin_token(ws):
        await ws.close(code=4003, reason="需要主机管理令牌")
        return
    ws.scope["maze_admin_session_id"] = ws.cookies.get(ADMIN_SESSION_COOKIE, "")
    await ws.accept()
    try:
        await ws.send_json({"type": "init", "role": "host", "lan_ip": LAN_IP,
                            **maze_payload(), **sim.public_state(),
                            "agents": sim.snapshot_agents()})
        hub.hosts.add(ws)
        hub.register(ws)
        while True:
            try:
                await asyncio.wait_for(ws.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                if not websocket_admin_token(ws):
                    await ws.close(code=4003, reason="管理会话已过期")
                    break
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hub.remove(ws)


@app.websocket("/ws/player")
async def ws_player(ws: WebSocket,
                    pid: str = Query(..., min_length=2, max_length=64, pattern=r"^p[A-Za-z0-9_-]+$"),
                    name: str = Query("", max_length=64),
                    participant: str = Query("", max_length=MAX_PARTICIPANT_ID_LENGTH),
                    consent: Optional[bool] = Query(None)):
    await ws.accept()
    # 直接调用（测试/内部）时默认值是 Query 对象，这里统一归一化。
    participant = participant if isinstance(participant, str) else ""
    consent = consent if isinstance(consent, bool) else None
    name = name if isinstance(name, str) else ""
    # Keep identifiers stable and easy to export. Whitespace-only values are
    # treated as missing; the consent gate below then rejects them explicitly.
    participant = participant.strip()[:MAX_PARTICIPANT_ID_LENGTH]
    name = name.strip()[:MAX_PLAYER_NAME_LENGTH]
    enforce = getattr(sim, "require_participant_consent", True)

    # 先校验新连接，再断开旧连接；否则一个无效重连请求可能把有效连接踢下线。
    if enforce and not participant:
        await ws.close(code=4004, reason="缺少参与者编号")
        return
    if enforce and consent is not True:
        await ws.close(code=4005, reason="未确认知情同意")
        return

    # 同一位参与者只允许占用一个身份：换设备/换标签页重复加入时，
    # 沿用其原有 pid，而不是产生第二条样本。容量检查必须放在身份合并之后。
    existing = sim.agent_by_participant(participant) if participant else None
    if existing is not None and existing.pid != pid:
        pid = existing.pid

    # Do not let a late participant silently enter an already-finished trial;
    # otherwise the next summary would mix a new person into the old sample.
    # Reconnecting an identity that already belongs to this trial remains safe.
    if sim.phase == "ended" and pid not in sim.agents:
        await ws.close(code=4006, reason="本场已结束，请等待主机开始新场")
        return

    humans = [a for a in sim.agents.values() if a.is_human]
    if len(humans) >= MAX_HUMANS and pid not in sim.agents:
        await ws.close(code=4003, reason="人数已满")
        return

    old_ws = hub.players.get(pid)
    if old_ws and old_ws is not ws:
        hub.remove(old_ws)
        with suppress(Exception):
            await asyncio.wait_for(old_ws.close(code=4001), timeout=1.0)

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


# ---------------- 运行状态与页面 ----------------
@app.get("/healthz")
async def healthz():
    """Liveness probe: the process and event loop are serving requests."""
    return {"status": "ok", "service": "maze-evacuation", "version": APP_VERSION}


def data_dir_is_writable() -> bool:
    """Check the actual data volume, not just its permission bits.

    ``os.access`` can report a false positive when the process runs as root
    (common in containers). A short create/write/fsync/delete probe catches a
    read-only bind mount before an experiment is started.
    """
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=".maze-ready-", suffix=".tmp", dir=DATA_DIR,
            delete=True,
        ) as handle:
            handle.write(b"ready\n")
            handle.flush()
            os.fsync(handle.fileno())
        return True
    except (OSError, ValueError):
        return False


@app.get("/readyz")
async def readyz():
    """Readiness probe for a reverse proxy or container orchestrator."""
    checks = {
        "maze": bool(maze.walls) and maze.mw > 0 and maze.mh > 0,
        "simulation": sim is not None,
        # A missing external scene directory is the portable legacy-test mode;
        # when a scene directory exists but is invalid, report not-ready.
        "scene_3d": globals().get("scene_sim") is not None or not SCENE_DIR.exists(),
        "data_dir": data_dir_is_writable(),
    }
    ready = all(checks.values())
    payload = {"status": "ready" if ready else "not_ready",
               "version": APP_VERSION, "checks": checks}
    if not ready:
        return JSONResponse(payload, status_code=503)
    return payload


@app.get("/api/version")
async def api_version():
    return {"version": APP_VERSION, "python": sys.version.split()[0]}


@app.get("/")
async def index(legacy: int = Query(0, ge=0, le=1)):
    # The 3D scene is the default runtime.  Explicit legacy=1 keeps the old
    # grid UI available for historical regression tests and comparisons.
    # The modern 3D page remains the default even when a model is missing so
    # operators see an actionable loading error instead of silently falling
    # back to the historical grid UI. Use legacy=1 explicitly for that UI.
    page = STATIC_DIR / ("host.html" if legacy else "host3d.html")
    if not page.is_file(): page = STATIC_DIR / "host.html"
    return FileResponse(page, headers={"Cache-Control": "no-store"})


@app.get("/play")
async def play(legacy: int = Query(0, ge=0, le=1)):
    # Do not silently turn a missing 3D asset into a different experiment.
    # Participants can still opt into the historical client with legacy=1.
    page = STATIC_DIR / ("play.html" if legacy else "play3d.html")
    if not page.is_file(): page = STATIC_DIR / "play.html"
    return FileResponse(page, headers={"Cache-Control": "no-store"})


@app.get("/calibrate")
async def calibrate_page():
    page = STATIC_DIR / "calibrate3d.html"
    if not page.is_file(): raise HTTPException(404, "3D 校准器尚未安装")
    return FileResponse(page, headers={"Cache-Control": "no-store"})


@app.get("/api/admin/session")
async def admin_session_status(request: Request):
    """Report whether the optional operator gate is active and authenticated."""
    session_id = request.cookies.get(ADMIN_SESSION_COOKIE, "")
    return {
        "required": bool(ADMIN_TOKEN),
        "authenticated": not ADMIN_TOKEN or valid_admin_session(session_id),
    }


@app.post("/api/admin/session")
async def admin_session_login(request: Request):
    """Exchange an operator token for a short-lived HttpOnly browser session."""
    if not ADMIN_TOKEN:
        return {"ok": True, "required": False}
    require_same_origin(request)
    client_ip = request.client.host if request.client else "unknown"
    if not allow_admin_login_attempt(client_ip):
        raise HTTPException(429, "登录尝试过多，请稍后再试", headers={"Retry-After": "300"})
    raw_body = bytearray()
    async for chunk in request.stream():
        raw_body.extend(chunk)
        if len(raw_body) > 4096:
            raise HTTPException(413, "管理令牌请求过大")
    try:
        data = json.loads(bytes(raw_body))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(400, "管理令牌请求格式不合法")
    supplied = data.get("token", "") if isinstance(data, dict) else ""
    if not admin_token_matches(supplied):
        raise HTTPException(401, "管理令牌不正确")
    with ADMIN_LOGIN_LOCK:
        ADMIN_LOGIN_ATTEMPTS.pop(client_ip or "unknown", None)

    now = time.monotonic()
    if len(ADMIN_SESSIONS) >= 256:
        for existing_id, expires_at in list(ADMIN_SESSIONS.items()):
            if expires_at <= now:
                ADMIN_SESSIONS.pop(existing_id, None)
        if len(ADMIN_SESSIONS) >= 256:
            oldest_id = min(ADMIN_SESSIONS, key=ADMIN_SESSIONS.get)
            ADMIN_SESSIONS.pop(oldest_id, None)
    session_id = secrets.token_urlsafe(32)
    ADMIN_SESSIONS[session_id] = time.monotonic() + ADMIN_SESSION_TTL
    _persist_admin_sessions()
    response = JSONResponse({"ok": True, "required": True})
    response.set_cookie(
        ADMIN_SESSION_COOKIE,
        session_id,
        max_age=ADMIN_SESSION_TTL,
        httponly=True,
        secure=external_scheme_host(
            request.headers, request.url.scheme, request.headers.get("host", request.url.netloc)
        )[0] == "https",
        samesite="strict",
        path="/",
    )
    return response


@app.delete("/api/admin/session")
async def admin_session_logout(request: Request):
    require_same_origin(request)
    session_id = request.cookies.get(ADMIN_SESSION_COOKIE, "")
    ADMIN_SESSIONS.pop(session_id, None)
    _persist_admin_sessions()
    for ws in list(hub.hosts):
        if ws.scope.get("maze_admin_session_id") == session_id:
            hub.remove(ws)
            with suppress(Exception):
                await asyncio.wait_for(ws.close(code=4003, reason="管理会话已注销"), timeout=1.0)
    response = JSONResponse({"ok": True})
    response.delete_cookie(ADMIN_SESSION_COOKIE, path="/", httponly=True, samesite="strict")
    return response


# ---------------- API ----------------
@app.get("/api/qr")
async def api_qr(request: Request):
    port = request.url.port or 8000
    url = configured_public_url(port)
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="svg", scale=8, border=2)
    return Response(buf.getvalue(), media_type="image/svg+xml",
                    headers={"Cache-Control": "no-store"})


DEFAULT_SCENE_DIR = STATIC_DIR / "scenes"
SCENE_DIR = Path(os.environ.get("MAZE_SCENE_DIR", str(DEFAULT_SCENE_DIR))).expanduser()
SCENE_ASSET_PREFIX = "/static/scenes" if SCENE_DIR.resolve() == DEFAULT_SCENE_DIR.resolve() else "/scene-assets"
if SCENE_ASSET_PREFIX == "/scene-assets" and SCENE_DIR.is_dir():
    app.mount(SCENE_ASSET_PREFIX, StaticFiles(directory=SCENE_DIR), name="scene-assets")

SCENE_CONFIG_PATH = SCENE_DIR / "scene-config.json"


def load_scene_config() -> dict:
    """Read the operator-editable visual scene manifest.

    The evacuation simulation remains grid based.  This manifest only defines
    which GLB is shown as the visual shell and how it is fitted to that grid.
    Keeping it as data lets an operator swap a building without touching the
    simulation or the two web clients.
    """
    try:
        with SCENE_CONFIG_PATH.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict) and isinstance(data.get("scenes"), list):
            return data
    except (OSError, ValueError, TypeError):
        pass
    return {"active": "school", "scenes": []}

@app.get("/api/scene-models")
async def api_scene_models(legacy: int = Query(0, ge=0, le=1)):
    """列出本地 3D 场景文件；仅 legacy=1 返回旧二维页面所需 placement。"""
    config = load_scene_config()
    models = []
    scenes = []
    for scene in config.get("scenes", []):
        if not isinstance(scene, dict) or not scene.get("id"):
            continue
        scene_out = {
            "id": scene["id"],
            "name": scene.get("name", scene["id"]),
            "description": scene.get("description", ""),
            "models": [],
        }
        if legacy:
            scene_out["placement"] = scene.get("placement", {})
        for entry in scene.get("models", []):
            if not isinstance(entry, dict) or not entry.get("file"):
                continue
            filename = str(entry["file"])
            if Path(filename).name != filename or filename in {".", ".."}:
                continue
            path = SCENE_DIR / filename
            try:
                if not path.is_file():
                    continue
                size = path.stat().st_size
            except OSError:
                continue
            scene_out["models"].append({
                "name": filename,
                "url": f"{SCENE_ASSET_PREFIX}/{filename}",
                "bytes": size,
                "optimized": bool(entry.get("optimized", entry.get("meshopt", False))),
                "meshopt": bool(entry.get("meshopt", False)),
                "preferred": bool(entry.get("preferred", False)),
                "fallback": bool(entry.get("fallback", False)),
            })
            models.append({
                "name": filename,
                "url": f"{SCENE_ASSET_PREFIX}/{filename}",
                "bytes": size,
                "optimized": bool(entry.get("optimized", entry.get("meshopt", False))),
                "meshopt": bool(entry.get("meshopt", False)),
                "preferred": bool(entry.get("preferred", False)),
                "fallback": bool(entry.get("fallback", False)),
                "scene": scene["id"],
            })
        scenes.append(scene_out)
    return {"active": config.get("active", "school"), "scenes": scenes, "models": models}


# ---------------- 可校准 3D 场景与连续空间仿真 ----------------
SCENE_V2_PATH = SCENE_DIR / "scene-v2.json"
SCENE_ACTIVE_PATH = SCENE_DIR / "active-v2.json"
SCENE_LOCK = threading.RLock()
SCENE_HUB = None
ACTIVE_SCENE_ID = "school"
SCENE_SIM: Optional[EvacSim3D] = None
SCENE_EXPERIMENT = {"experiment_id": "", "condition": "", "group": "", "operator": "", "notes": ""}


def _scene_path(scene_id: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,47}", scene_id):
        raise HTTPException(400, "场景编号不合法")
    if scene_id == "school": return SCENE_V2_PATH
    return SCENE_DIR / f"{scene_id}.scene-v2.json"


def _read_scene(scene_id: str) -> dict:
    path = _scene_path(scene_id)
    try:
        with path.open("r", encoding="utf-8") as handle: raw = json.load(handle)
    except FileNotFoundError:
        raise HTTPException(404, "场景不存在")
    except (OSError, ValueError):
        raise HTTPException(422, "场景文件损坏")
    try: return validate_scene(raw)
    except SceneValidationError as exc: raise HTTPException(422, str(exc))


def _raw_scene(scene_id: str) -> dict:
    path = _scene_path(scene_id)
    try:
        with path.open("r", encoding="utf-8") as handle: return json.load(handle)
    except FileNotFoundError: raise HTTPException(404, "场景不存在")
    except (OSError, ValueError): raise HTTPException(422, "场景文件损坏")


def _atomic_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        _restrict_file_descriptor(fd)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush(); os.fsync(handle.fileno())
        os.replace(temp_path, path)
        _sync_directory(path.parent)
    except Exception:
        with suppress(OSError): os.unlink(temp_path)
        raise


def _scene_model_file(scene: dict) -> Path:
    filename = scene["model"]["file"]
    path = (SCENE_DIR / filename).resolve()
    if path.parent != SCENE_DIR.resolve() or not path.is_file():
        raise HTTPException(404, "场景模型文件不存在")
    return path


def _scene_mobile_model_url(scene: dict) -> Optional[str]:
    """Optional lossless spatial render chunks; never change navigation data."""
    if SCENE_ASSET_PREFIX != "/static/scenes":
        return None
    mobile=(scene.get("model") or {}).get("mobile") or {}
    filename=str(mobile.get("file") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*\.glb",filename):
        return None
    path=(SCENE_DIR/filename).resolve()
    if path.parent!=SCENE_DIR.resolve() or not path.is_file():
        return None
    revision=str(mobile.get("sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}",revision):
        return None
    return f"{SCENE_ASSET_PREFIX}/{filename}?v={revision[:16]}"


def _scene_model_url(scene: dict) -> str:
    """Serve the active GLB from the cacheable static route when possible.

    ``/api/`` is proxied with ``Cache-Control: no-store`` so the browser would
    re-download the whole model on every reload.  When the scene directory is
    the bundled one, point at ``/static/scenes/...`` which nginx (and the dev
    server) serve with a long ``max-age``.  A ``?v=<sha256 prefix>`` query
    makes the URL content-addressed, so replacing a model busts the cache
    while an unchanged model is loaded straight from disk cache.
    """
    filename = str((scene.get("model") or {}).get("file") or "")
    if not filename or Path(filename).name != filename:
        return f"/api/scenes/{scene['id']}/model"
    if SCENE_ASSET_PREFIX != "/static/scenes":
        return f"/api/scenes/{scene['id']}/model"
    revision = str((scene.get("model") or {}).get("sha256") or "")[:16]
    suffix = f"?v={revision}" if revision else ""
    return f"{SCENE_ASSET_PREFIX}/{filename}{suffix}"


def _verify_scene_model_hash(scene: dict, path: Path):
    expected = str((scene.get("model") or {}).get("sha256", "") or "").lower()
    if not expected:
        return
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise HTTPException(422, "场景模型 SHA-256 配置不合法")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise HTTPException(422, "无法读取场景模型") from exc
    if not secrets.compare_digest(digest.hexdigest(), expected):
        raise HTTPException(422, "场景模型 SHA-256 校验失败，请重新上传或更新标注")


def _validate_glb_file(path: Path, expected_size: Optional[int] = None) -> dict:
    """Validate the GLB container before it becomes an addressable scene asset."""
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            header = handle.read(12)
            if len(header) != 12 or header[:4] != b"glTF":
                raise ValueError("文件不是有效的 GLB")
            version, total_length = struct.unpack("<II", header[4:12])
            if version != 2 or total_length != size or (expected_size is not None and total_length != expected_size):
                raise ValueError("GLB 版本或文件长度不匹配")
            offset = 12
            json_chunk = None
            chunk_count = 0
            while offset < size:
                chunk_header = handle.read(8)
                if len(chunk_header) != 8:
                    raise ValueError("GLB chunk 头不完整")
                chunk_length, chunk_type = struct.unpack("<II", chunk_header)
                if chunk_length < 0 or offset + 8 + chunk_length > size:
                    raise ValueError("GLB chunk 长度越界")
                payload = handle.read(chunk_length)
                if len(payload) != chunk_length:
                    raise ValueError("GLB chunk 数据不完整")
                offset += 8 + chunk_length
                chunk_count += 1
                if chunk_type == 0x4E4F534A and json_chunk is None:
                    json_chunk = payload.rstrip(b" \t\r\n\x00")
            if offset != size or not json_chunk:
                raise ValueError("GLB 缺少 JSON chunk")
            document = json.loads(json_chunk.decode("utf-8"))
            if not isinstance(document, dict) or not isinstance(document.get("asset"), dict):
                raise ValueError("GLB JSON 缺少 asset 元数据")
            if str(document["asset"].get("version", "")) != "2.0":
                raise ValueError("GLB asset.version 必须为 2.0")
            return {"bytes": size, "chunks": chunk_count, "json": document}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, struct.error) as exc:
        raise ValueError("GLB 文件损坏或无法读取") from exc


def _scene_compact_state() -> dict:
    """Running snapshots skip disk fallback, never omit stairs or exits.

    Hosts and participants replace their state on every message.  An incomplete
    scene would erase calibration warnings, connector focus and exit markers.
    """
    return _scene_sim_state(include_saved_replay=False)


def _scene_sim_state(include_saved_replay: bool = True) -> dict:
    """Build a compact v2 snapshot for 20 FPS WebSocket delivery.

    The browser needs floor elevations and connector markers, not the full
    triangulated navigation geometry.  Keeping polygons server-side prevents
    a large GLB scene from turning every state update into a multi-megabyte
    message; calibration reads the complete JSON through its dedicated API.
    """
    scene = SCENE_SIM.scene
    floors = [{"id": f["id"], "name": f.get("name", f["id"]),
               "elevation_m": f["elevation_m"]} for f in scene["floors"]]
    stairs = [{"id": s["id"], "from_floor": s["from_floor"],
               "to_floor": s["to_floor"], "bottom": s["bottom"],
               "top": s["top"], "width_m": s["width_m"],
               "capacity": s.get("capacity")} for s in scene["stairs"]]
    exits = [{"id": e["id"], "name": e.get("name", e["id"]),
              "floor": e["floor"], "position": e["position"],
              "width_m": e["width_m"], "kind": e.get("kind", "exit")} for e in scene["exits"]]
    snapshot = {"type": "state", **SCENE_SIM.public_state()}
    if include_saved_replay and not snapshot.get("replay_available") and not getattr(SCENE_SIM, "replay_invalidated", False):
        try:
            summary = json.loads((DATA_DIR / "evac_latest_v2.json").read_text(encoding="utf-8"))
            if summary.get("scene_id") == scene["id"] and summary.get("phase") == "ended":
                snapshot["replay_available"] = True
                snapshot["replay_duration"] = round(float(summary.get("elapsed") or 0), 2)
        except (OSError, ValueError, TypeError):
            pass
    return {**snapshot,
            "connected_human_count": sum(a.human and pid in SCENE_HUB.players
                                         for pid, a in SCENE_SIM.agents.items()),
            "scene": {"id": scene["id"], "name": scene.get("name", ""),
                      "description": scene.get("description", ""),
                      "model_url": _scene_model_url(scene),
                      "model_url_mobile": _scene_mobile_model_url(scene),
                      "navigation_review": scene.get("navigation_review", {}),
                      "transform": scene.get("transform", {}),
                      "floors": floors, "stairs": stairs, "exits": exits,
                      "scale": scene["scale"]},
            "experiment": dict(SCENE_EXPERIMENT)}


class SceneHub:
    def __init__(self):
        self.hosts = set(); self.players = {}; self.outboxes = {}; self.senders = {}
        self.compact_clients = set(); self.sent_scene_keys = {}
    def register(self, ws, compact=False):
        if compact: self.compact_clients.add(ws)
        # All hub messages are replaceable snapshots; never accumulate old poses.
        queue = asyncio.Queue(maxsize=1); self.outboxes[ws] = queue
        self.senders[ws] = asyncio.create_task(self._send(ws, queue))
    def remove(self, ws):
        self.hosts.discard(ws)
        for pid, sock in list(self.players.items()):
            if sock is ws:
                self.players.pop(pid, None)
                if SCENE_SIM: SCENE_SIM.human_disconnect(pid)
        self.outboxes.pop(ws, None)
        self.compact_clients.discard(ws); self.sent_scene_keys.pop(ws, None)
        task = self.senders.pop(ws, None)
        if task and task is not asyncio.current_task(): task.cancel()
    async def _send(self, ws, queue):
        try:
            while True:
                payload, scene_key = await queue.get()
                await asyncio.wait_for(ws.send_text(payload), timeout=5.0)
                # A queued full snapshot may be replaced before delivery.
                # Mark its metadata known only once it has actually been sent.
                if scene_key is not None: self.sent_scene_keys[ws] = scene_key
        except asyncio.CancelledError: raise
        except Exception:
            with suppress(Exception): await asyncio.wait_for(ws.close(code=1013), timeout=1.0)
        finally: self.remove(ws)
    @property
    def client_count(self):
        return len(self.hosts) + len(self.players)

    async def broadcast(self, payload):
        raw = compact_raw = None
        scene_key = json.dumps(payload.get("scene"), ensure_ascii=False, separators=(",", ":"), sort_keys=True) if "scene" in payload else None
        for ws, queue in list(self.outboxes.items()):
            omit_scene = ws in self.compact_clients and scene_key is not None and self.sent_scene_keys.get(ws) == scene_key
            if omit_scene:
                if compact_raw is None:
                    compact_raw = json.dumps({k: v for k, v in payload.items() if k != "scene"}, ensure_ascii=False, separators=(",", ":"))
                packet = (compact_raw, None)
            else:
                if raw is None: raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                packet = (raw, scene_key)
            if queue.full():
                with suppress(asyncio.QueueEmpty): queue.get_nowait()
            try: queue.put_nowait(packet)
            except asyncio.QueueFull:
                self.remove(ws); asyncio.create_task(self._close(ws))
    async def _close(self, ws):
        with suppress(Exception): await asyncio.wait_for(ws.close(code=1013), timeout=1.0)


SCENE_HUB = SceneHub()

def _init_scene_sim():
    global SCENE_SIM, ACTIVE_SCENE_ID
    try:
        try: active = json.loads(SCENE_ACTIVE_PATH.read_text(encoding="utf-8")).get("active", "school")
        except (OSError, ValueError, AttributeError): active = "school"
        if not _scene_path(active).is_file(): active = "school"
        scene = _read_scene(active)
        model = _scene_model_file(scene)
        _verify_scene_model_hash(scene, model)
        SCENE_SIM = EvacSim3D(scene, DATA_DIR, default_npcs=DEFAULT_SCENE_NPCS)
        SCENE_SIM.experiment = dict(SCENE_EXPERIMENT)
        ACTIVE_SCENE_ID = active
    except (HTTPException, SceneValidationError, OSError) as exc:
        print(f"3D 场景未就绪: {exc}", file=sys.stderr)
        SCENE_SIM = None


_init_scene_sim()
scene_sim = SCENE_SIM  # readable public alias for readiness/tests
scene_hub = SCENE_HUB


async def scene_game_loop():
    last = time.monotonic(); accumulator = 0.0
    while True:
        await asyncio.sleep(.02 if SCENE_SIM and SCENE_SIM.phase == "running" else .05)
        now = time.monotonic(); dt = min(.25, max(0.0, now-last)); last = now
        if not SCENE_SIM: continue
        SCENE_SIM.advance(dt)
        if SCENE_HUB.client_count:
            accumulator += dt
            # 20 Hz is plenty for smooth interpolated actors; idle screens stay
            # on the cheap IDLE_BROADCAST cadence to avoid radio wakeups.
            threshold = .05 if SCENE_SIM.phase == "running" else IDLE_BROADCAST
            if accumulator >= threshold:
                accumulator = min(threshold, accumulator - threshold)
                await SCENE_HUB.broadcast(_scene_compact_state() if SCENE_SIM.phase == "running" else _scene_sim_state())


@app.get("/api/scenes")
async def api_scenes():
    files = [SCENE_V2_PATH, *sorted(SCENE_DIR.glob("*.scene-v2.json"))]
    result = []
    for path in files:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            scene_id = raw.get("id", "")
            model = raw.get("model", {})
            model_path = SCENE_DIR / str(model.get("file", ""))
            ready = model_path.is_file()
            try:
                validate_scene(raw); valid = True
            except SceneValidationError: valid = False
            # Do not hash potentially 1 GB assets on every catalog request.
            # The authoritative SHA-256 check remains on startup/activation.
            result.append({"id": scene_id, "name": raw.get("name", scene_id),
                           "model": model.get("file", ""), "ready": bool(ready and valid),
                           "scale_status": (raw.get("scale") or {}).get("status", "provisional"),
                           "active": scene_id == ACTIVE_SCENE_ID})
        except (OSError, ValueError, AttributeError): continue
    return {"active": ACTIVE_SCENE_ID, "scenes": result}


@app.post("/api/scenes/upload")
async def api_scene_upload(request: Request, filename: str = Query(..., max_length=180),
                           _: None = Depends(require_admin)):
    if not filename.lower().endswith(".glb") or Path(filename).name != filename or any(ord(c) < 32 for c in filename):
        raise HTTPException(400, "只接受安全文件名的单文件 .glb")
    declared = request.headers.get("content-length", "")
    if declared:
        try: declared_size = int(declared)
        except ValueError: raise HTTPException(400, "上传长度无效")
        if declared_size <= 0 or declared_size > MAX_GLB_UPLOAD_BYTES:
            raise HTTPException(413, "GLB 上传上限为 1 GB")
    else: declared_size = None
    SCENE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    available = shutil.disk_usage(SCENE_DIR).free
    reserve = 64 * 1024 * 1024
    if declared_size and available < declared_size + reserve:
        raise HTTPException(507, "模型目录剩余空间不足")
    # Content-Length is optional for chunked uploads. Keep a fixed reserve and
    # re-check the real free space while streaming so an honest client cannot
    # fill the volume merely by omitting the header.
    upload_budget = max(0, available - reserve)
    safe_stem = re.sub(r"[^a-z0-9_-]+", "-", Path(filename).stem.lower()).strip("-_")[:40] or "scene"
    scene_id = safe_stem
    suffix = 2
    while _scene_path(scene_id).exists():
        if scene_id == "school": scene_id = f"{safe_stem}-imported"
        else: scene_id = f"{safe_stem}-{suffix}"; suffix += 1
    model_name = f"{scene_id}.glb"
    fd, temp_name = tempfile.mkstemp(prefix=f".{scene_id}.", suffix=".upload", dir=SCENE_DIR)
    total = 0; digest = hashlib.sha256(); header = bytearray()
    try:
        with os.fdopen(fd, "wb") as target:
            async for chunk in request.stream():
                if not chunk: continue
                total += len(chunk)
                if total > MAX_GLB_UPLOAD_BYTES: raise HTTPException(413, "GLB 上传上限为 1 GB")
                if total > upload_budget: raise HTTPException(507, "模型目录剩余空间不足")
                if total % (8 * 1024 * 1024) < len(chunk):
                    free_now = shutil.disk_usage(SCENE_DIR).free
                    if free_now < reserve:
                        raise HTTPException(507, "模型目录剩余空间不足")
                if len(header) < 12: header.extend(chunk[:12-len(header)])
                target.write(chunk); digest.update(chunk)
            target.flush(); os.fsync(target.fileno())
        if total < 20 or len(header) != 12 or header[:4] != b"glTF":
            raise HTTPException(400, "文件不是有效的 GLB")
        try:
            _validate_glb_file(Path(temp_name), total)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        model_path = SCENE_DIR / model_name
        os.replace(temp_name, model_path); os.chmod(model_path, 0o600)
        draft = {"schema": 2, "id": scene_id, "name": Path(filename).stem,
                 "description": "待校准场景", "model": {"file": model_name, "sha256": digest.hexdigest(),
                 "license": "", "source": "", "author": ""},
                 "transform": {"scale_m_per_unit": 0.01, "rotation": [0,0,0], "translation": [0,0,0]},
                 "scale": {"status": "provisional", "reference_length_m": None},
                 "floors": [], "stairs": [], "exits": []}
        _atomic_json(_scene_path(scene_id), draft)
    except Exception:
        with suppress(OSError): os.unlink(temp_name)
        raise
    return {"ok": True, "id": scene_id, "filename": model_name,
            "bytes": total, "sha256": digest.hexdigest(), "next": f"/calibrate?scene={scene_id}"}


@app.get("/api/scenes/{scene_id}/calibration")
async def api_scene_calibration_get(scene_id: str, _: None = Depends(require_admin)):
    return _raw_scene(scene_id)


@app.put("/api/scenes/{scene_id}/calibration")
async def api_scene_calibration_put(scene_id: str, request: Request, _: None = Depends(require_admin)):
    data = await read_json_body(request, MAX_SCENE_JSON_BYTES, "场景校准")
    if not isinstance(data, dict) or data.get("id") != scene_id:
        raise HTTPException(400, "校准数据场景编号不匹配")
    try: normalized = validate_scene(data)
    except SceneValidationError as exc: raise HTTPException(422, str(exc))
    _scene_model_file(normalized)
    _atomic_json(_scene_path(scene_id), data)
    return {"ok": True, "id": scene_id, "scale_status": normalized["scale"]["status"],
            "floors": len(normalized["floors"]), "stairs": len(normalized["stairs"]),
            "regions": sum(len(f["walkable"]) for f in normalized["floors"])}


@app.post("/api/scenes/{scene_id}/preview/navigation")
async def api_scene_preview_navigation(scene_id: str, request: Request, _: None = Depends(require_admin)):
    """Validate a calibration draft and return a real hidden-nav test route."""
    data = await read_json_body(request, MAX_SCENE_JSON_BYTES, "导航预览")
    if not isinstance(data, dict) or data.get("id") != scene_id:
        raise HTTPException(400, "预览数据场景编号不匹配")
    try:
        preview = EvacSim3D(data, DATA_DIR, require_route=False)
        return {"ok": True, **preview.preview_navigation()}
    except (SceneValidationError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/scenes/{scene_id}/preview/smoke")
async def api_scene_preview_smoke(scene_id: str, request: Request, _: None = Depends(require_admin)):
    """Return a deterministic layered smoke-flow preview for a draft."""
    data = await read_json_body(request, MAX_SCENE_JSON_BYTES, "烟雾预览")
    if not isinstance(data, dict) or data.get("id") != scene_id:
        raise HTTPException(400, "预览数据场景编号不匹配")
    try:
        preview = EvacSim3D(data, DATA_DIR, require_route=False)
        source_floor = data.get("preview_source_floor")
        return {"ok": True, **preview.preview_smoke(source_floor if isinstance(source_floor, str) else None)}
    except (SceneValidationError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/scenes/{scene_id}/activate")
async def api_scene_activate(scene_id: str, _: None = Depends(require_admin)):
    global SCENE_SIM, scene_sim, ACTIVE_SCENE_ID
    if SCENE_SIM and SCENE_SIM.phase in {"running", "paused"}:
        raise HTTPException(409, "请先结束或重置当前疏散再切换场景")
    scene = _read_scene(scene_id); model_path = _scene_model_file(scene); _verify_scene_model_hash(scene, model_path)
    if SCENE_SIM and any(a.track for a in SCENE_SIM.agents.values()): SCENE_SIM.save_outputs()
    try: replacement = EvacSim3D(scene, DATA_DIR, default_npcs=DEFAULT_SCENE_NPCS)
    except (SceneValidationError, ValueError) as exc: raise HTTPException(422, str(exc))
    replacement.ensure_population(SCENE_SIM.npc_target if SCENE_SIM else 80)
    SCENE_SIM = scene_sim = replacement; SCENE_SIM.experiment = dict(SCENE_EXPERIMENT); ACTIVE_SCENE_ID = scene_id
    _atomic_json(SCENE_ACTIVE_PATH, {"active": scene_id})
    await SCENE_HUB.broadcast(_scene_sim_state())
    return {"ok": True, "active": scene_id, **SCENE_SIM.public_state()}


@app.get("/api/scenes/{scene_id}/model")
async def api_scene_model(scene_id: str):
    # Calibration drafts intentionally have no floors yet, but their model
    # must still be viewable.  Read the raw manifest, then apply the same
    # filename/path containment check used by validated runtime scenes.
    scene = _raw_scene(scene_id)
    if not isinstance(scene.get("model"), dict):
        raise HTTPException(422, "场景模型配置损坏")
    path = _scene_model_file(scene)
    return FileResponse(path, media_type="model/gltf-binary", headers={"Cache-Control": "public, max-age=3600"})


@app.get("/api/v2/state")
async def api_v2_state():
    if not SCENE_SIM: raise HTTPException(503, "没有可用的 3D 场景")
    return _scene_sim_state()


def _scene_replay_payload() -> dict:
    """Return the current in-memory v2 replay, or the last persisted v2 CSV."""
    if SCENE_SIM is None:
        raise HTTPException(503, "没有可用的 3D 场景")
    if any(agent.track for agent in SCENE_SIM.agents.values()):
        return SCENE_SIM.replay_payload()

    summary_path = DATA_DIR / "evac_latest_v2.json"
    tracks_path = DATA_DIR / "evac_latest_tracks_v2.csv"
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        summary = {}
    if summary.get("scene_id") != SCENE_SIM.scene["id"]:
        return {"schema": 2, "scene_id": SCENE_SIM.scene["id"], "duration": 0,
                "sample_interval": .2, "agents": []}

    by_pid = {}
    try:
        with tracks_path.open("r", encoding="utf-8", newline="") as handle:
            reader = __import__("csv").DictReader(handle)
            for row in reader:
                pid = row.get("pid", "")
                if not pid:
                    continue
                item = by_pid.setdefault(pid, {"pid": pid, "name": pid,
                    "human": pid in SCENE_SIM.agents and SCENE_SIM.agents[pid].human,
                    "color": SCENE_SIM.agents[pid].color if pid in SCENE_SIM.agents else "#8ba4ca",
                    "samples": []})
                item["samples"].append({
                    "t_sim_s": float(row.get("t_sim_s") or 0),
                    "t_real_s": float(row.get("t_real_s") or 0),
                    "x_m": float(row.get("x_m") or 0), "y_m": float(row.get("y_m") or 0),
                    "z_m": float(row.get("z_m") or 0), "floor_id": row.get("floor_id", ""),
                    "nav_region": row.get("nav_region", ""), "vx_mps": float(row.get("vx_mps") or 0),
                    "vy_mps": float(row.get("vy_mps") or 0), "vz_mps": float(row.get("vz_mps") or 0),
                    "smoke": float(row.get("smoke") or 0), "smoke_dose": float(row.get("smoke_dose") or 0),
                    "health": float(row.get("health") or 1), "pressure": float(row.get("pressure") or 0),
                    "density": int(float(row.get("density") or 0)), "state": row.get("state", ""),
                    "escaped": row.get("escaped") == "1", "failed": row.get("failed") == "1",
                })
    except OSError:
        pass
    agents = list(by_pid.values())
    duration = max((sample["t_sim_s"] for agent in agents for sample in agent["samples"]), default=0)
    return {"schema": 2, "scene_id": SCENE_SIM.scene["id"],
            "duration": round(duration, 2), "sample_interval": .2, "agents": agents}


@app.get("/api/v2/replay")
async def api_v2_replay(_: None = Depends(require_admin)):
    return _scene_replay_payload()


@app.get("/api/v2/doors")
async def api_v2_doors(_: None = Depends(require_admin)):
    if not SCENE_SIM: raise HTTPException(503, "没有可用的 3D 场景")
    return {"scene_id": SCENE_SIM.scene["id"], "doors": SCENE_SIM.door_state_payload(),
            "events": list(SCENE_SIM.door_events[-200:])}


@app.post("/api/v2/control/{action}")
async def api_v2_control(action: str, request: Request, _: None = Depends(require_admin)):
    if not SCENE_SIM: raise HTTPException(503, "没有可用的 3D 场景")
    content_length = request.headers.get("content-length", "")
    data = await read_json_body(request, 32 * 1024, "3D 控制", allow_empty=True)
    if not isinstance(data, dict): raise HTTPException(400, "控制参数必须是 JSON 对象")
    if action in {"start", "restart"} and data.get("demo") is not True:
        if SCENE_SIM.scene.get("navigation_review", {}).get("status") == "needs-review":
            raise HTTPException(409, "模型与导航标注尚未完成一致性复核，不能开始正式采集；请重新校准通路")
    if action == "start":
        if SCENE_SIM.npc_target == 0 and SCENE_SIM.active_human_count() == 0:
            joined = sum(agent.human and not agent.escaped and not agent.failed for agent in SCENE_SIM.agents.values())
            if joined:
                raise HTTPException(409, "参与者已离线，请让手机端重新打开页面后再开始")
            raise HTTPException(409, "真人实验模式请先等待至少一名参与者加入")
        if SCENE_SIM.scene["scale"]["status"] != "verified" and data.get("demo") is not True:
            raise HTTPException(409, "当前场景使用临时比例；请显式选择演示模式，或完成真实长度校准")
        SCENE_SIM.start()
    elif action == "restart":
        # Validate BEFORE resetting: a rejected restart must leave this trial intact.
        # Completed participants still connected by WebSocket may join the next run.
        connected = {pid for pid, agent in SCENE_SIM.agents.items()
                     if agent.human and pid in SCENE_HUB.players}
        if SCENE_SIM.npc_target == 0 and not connected:
            raise HTTPException(409, "请先让至少一名真人参与者连接手机端，再重新开始一场")
        if SCENE_SIM.scene["scale"]["status"] != "verified" and data.get("demo") is not True:
            raise HTTPException(409, "当前场景使用临时比例；请显式选择演示模式，或完成真实长度校准")
        # Keep the online Agent3D objects: the player WebSocket holds references
        # to them. Do not resurrect disconnected escaped/down participants.
        for pid, agent in list(SCENE_SIM.agents.items()):
            if agent.human and pid not in connected:
                SCENE_SIM.agents.pop(pid)
        SCENE_SIM.reset(clear_fire=True)
        SCENE_SIM.start()
    elif action == "pause": SCENE_SIM.pause()
    elif action == "reset": SCENE_SIM.reset(bool(data.get("clear_fire", False)))
    elif action == "npcs":
        count=data.get("count")
        if isinstance(count,bool) or not isinstance(count,int) or not 0 <= count <= MAX_NPCS:
            raise HTTPException(422, f"NPC 数量须为 0 到 {MAX_NPCS} 的整数")
        if SCENE_SIM.phase in {"running","paused"}: raise HTTPException(409,"运行中不能重设 NPC 数量")
        SCENE_SIM.ensure_population(count)
    elif action == "fire":
        if not SCENE_SIM.ignite(data.get("floor"), data.get("x"), data.get("z")):
            raise HTTPException(422,"没有找到可点燃楼层")
    elif action == "door":
        door_id, door_action = data.get("door_id"), data.get("action", "open")
        if not isinstance(door_id, str) or door_action not in {"open", "close", "lock", "unlock", "fire-block", "clear-block"}:
            raise HTTPException(422, "门控制参数不合法")
        result = SCENE_SIM.request_door(door_id, None, door_action, "host")
        if not result.get("ok"): raise HTTPException(409, result.get("message") or f"门操作失败：{result.get('reason', 'unknown')}")
    elif action == "panic":
        value=data.get("value",.4)
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<=value<=1:
            raise HTTPException(422,"恐慌值范围为 0 到 1")
        SCENE_SIM.panic=float(value)
    elif action == "speed":
        value=data.get("value",1)
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not .25<=value<=4:
            raise HTTPException(422,"倍速范围为 0.25 到 4")
        SCENE_SIM.time_scale=float(value)
    elif action == "wind":
        x,z=data.get("x",0),data.get("z",0)
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in (x,z)) or math.hypot(x,z)>8:
            raise HTTPException(422,"风速矢量合速度不能超过 8 m/s")
        SCENE_SIM.set_wind(x,z)
    elif action == "experiment":
        if SCENE_SIM.phase in {"running","paused"}: raise HTTPException(409,"场次运行时不能修改实验信息")
        for key in SCENE_EXPERIMENT:
            if key in data: SCENE_EXPERIMENT[key]=str(data[key] or "")[:200]
        SCENE_SIM.experiment = dict(SCENE_EXPERIMENT)
    elif action == "archive":
        SCENE_SIM.save_outputs()
    else: raise HTTPException(404,"未知的 3D 控制操作")
    state = _scene_sim_state()
    await SCENE_HUB.broadcast(state)
    return {"ok":True, **state}


@app.get("/api/v2/export")
async def api_v2_export(fmt: str = Query("summary", pattern="^(summary|csv)$"),
                        _: None = Depends(require_admin)):
    if not SCENE_SIM: raise HTTPException(503,"没有可用的 3D 场景")
    if fmt == "csv":
        output=io.StringIO(newline="")
        writer=__import__("csv").writer(output)
        writer.writerow(["pid","t_sim_s","t_real_s","x_m","y_m","z_m","floor_id","nav_region","vx_mps","vy_mps","vz_mps","smoke","smoke_dose","health","pressure","density","state","escaped","failed"])
        if any(agent.track for agent in SCENE_SIM.agents.values()):
            for agent in SCENE_SIM.agents.values():
                for row in agent.track: writer.writerow([agent.pid,*row])
        else:
            # After a service restart the current simulator intentionally has
            # no live track buffers. Export the persisted v2 replay instead of
            # silently returning a header-only CSV.
            replay = _scene_replay_payload()
            for record in replay.get("agents", []):
                for sample in record.get("samples", []):
                    writer.writerow([record.get("pid", ""), sample.get("t_sim_s", 0), sample.get("t_real_s", 0),
                        sample.get("x_m", 0), sample.get("y_m", 0), sample.get("z_m", 0), sample.get("floor_id", ""),
                        sample.get("nav_region", ""), sample.get("vx_mps", 0), sample.get("vy_mps", 0), sample.get("vz_mps", 0),
                        sample.get("smoke", 0), sample.get("smoke_dose", 0), sample.get("health", 1), sample.get("pressure", 0),
                        sample.get("density", 0), sample.get("state", ""), int(bool(sample.get("escaped"))), int(bool(sample.get("failed")))])
        return PlainTextResponse(output.getvalue(),media_type="text/csv",headers={"Content-Disposition":"attachment; filename=evac_tracks_v2.csv"})
    # After a service restart, the simulator starts a fresh idle population but
    # the latest completed v2 trial is still available through the replay
    # fallback. Export that persisted summary instead of returning a misleading
    # new-session summary with zero track rows.
    has_live_tracks=any(agent.track for agent in SCENE_SIM.agents.values())
    if not has_live_tracks:
        persisted_path=DATA_DIR / "evac_latest_v2.json"
        try:
            persisted=json.loads(persisted_path.read_text(encoding="utf-8"))
            if persisted.get("schema")==2 and persisted.get("scene_id")==SCENE_SIM.scene["id"]:
                # Older v2 summaries were written before track metadata was
                # added. Enrich only the response; never rewrite historical
                # files just because an operator requested an export.
                persisted=dict(persisted)
                persisted.setdefault("track_file", "evac_latest_tracks_v2.csv")
                persisted.setdefault("sample_interval", .2)
                if not isinstance(persisted.get("track_rows"), int):
                    track_path=DATA_DIR / "evac_latest_tracks_v2.csv"
                    try:
                        with track_path.open("r", encoding="utf-8", newline="") as handle:
                            persisted["track_rows"]=max(0, sum(1 for _ in handle)-1)
                    except OSError:
                        persisted["track_rows"]=0
                return JSONResponse(persisted,headers={"Content-Disposition":"attachment; filename=evac_summary_v2.json"})
        except (OSError, ValueError, TypeError):
            pass
    track_rows=sum(len(agent.track) for agent in SCENE_SIM.agents.values())
    summary={"schema":2,"scene_id":SCENE_SIM.scene["id"],"scale_status":SCENE_SIM.scene["scale"]["status"],
             "movement_model":"swept-disc-1",
             "navigation_review_status":SCENE_SIM.scene.get("navigation_review",{}).get("status","unspecified"),
             "phase":SCENE_SIM.phase,"elapsed":SCENE_SIM.elapsed,"real_elapsed":SCENE_SIM.real_elapsed,
             "experiment":dict(SCENE_EXPERIMENT),"agents":len(SCENE_SIM.agents),
             "escaped":sum(a.escaped for a in SCENE_SIM.agents.values()),"failed":sum(a.failed for a in SCENE_SIM.agents.values()),
             "floors":SCENE_SIM.public_state()["floors"],"sample_interval":.2,
             "track_file":"evac_latest_tracks_v2.csv","track_rows":track_rows}
    return JSONResponse(summary,headers={"Content-Disposition":"attachment; filename=evac_summary_v2.json"})


@app.websocket("/ws/host-v2")
async def ws_host_v2(ws: WebSocket, compact: bool = Query(False)):
    if not websocket_admin_token(ws): await ws.close(code=4003,reason="需要主机管理令牌"); return
    if not SCENE_SIM:
        # The default entry point is still the 3D host page, so a missing model
        # must produce a clean, observable close rather than an ASGI traceback.
        await ws.accept()
        await ws.close(code=1013, reason="3D 场景未就绪，请检查模型和场景清单")
        return
    ws.scope["maze_admin_session_id"]=ws.cookies.get(ADMIN_SESSION_COOKIE,"")
    await ws.accept()
    try:
        await ws.send_json({"type":"init",**_scene_sim_state()})
        SCENE_HUB.hosts.add(ws); SCENE_HUB.register(ws, compact=compact is True)
        while True:
            try: await asyncio.wait_for(ws.receive_text(),timeout=30)
            except asyncio.TimeoutError:
                if not websocket_admin_token(ws): await ws.close(code=4003,reason="管理会话已过期"); break
    except (WebSocketDisconnect,RuntimeError): pass
    finally: SCENE_HUB.remove(ws)


@app.websocket("/ws/player-v2")
async def ws_player_v2(ws: WebSocket,
        pid: str = Query(...,min_length=2,max_length=64,pattern=r"^p[A-Za-z0-9_-]+$"),
        name: str = Query("",max_length=64), participant: str = Query("",max_length=MAX_PARTICIPANT_ID_LENGTH),
        consent: Optional[bool] = Query(None), compact: bool = Query(False)):
    await ws.accept()
    if not SCENE_SIM: await ws.close(code=1013,reason="3D 场景未就绪"); return
    participant=participant.strip()[:MAX_PARTICIPANT_ID_LENGTH] if isinstance(participant,str) else ""
    if REQUIRE_CONSENT and not participant: await ws.close(code=4004,reason="缺少参与者编号"); return
    if REQUIRE_CONSENT and consent is not True: await ws.close(code=4005,reason="未确认知情同意"); return
    if SCENE_SIM.phase=="ended" and pid not in SCENE_SIM.agents:
        await ws.close(code=4006,reason="本场已结束，请等待主机开始新场"); return
    old=SCENE_HUB.players.get(pid)
    if old and old is not ws:
        SCENE_HUB.remove(old)
        with suppress(Exception): await old.close(code=4001)
    human_count=sum(a.human for a in SCENE_SIM.agents.values())
    if human_count>=MAX_HUMANS and pid not in SCENE_SIM.agents:
        await ws.close(code=4003,reason="参与人数已满"); return
    agent=SCENE_SIM.add_human(pid,(name or f"玩家{human_count+1}")[:MAX_PLAYER_NAME_LENGTH],COLORS[human_count%len(COLORS)])
    SCENE_HUB.players[pid]=ws
    try:
        await ws.send_json({"type":"init","you":{"pid":agent.pid,"name":agent.name},**_scene_sim_state()})
        SCENE_HUB.register(ws, compact=compact is True)
        while True:
            raw=await ws.receive_text()
            if SCENE_HUB.players.get(pid) is not ws: break
            if len(raw)>2048: continue
            try: data=json.loads(raw)
            except (ValueError,TypeError): continue
            if not isinstance(data,dict): continue
            if data.get("type")=="input":
                try: x=float(data.get("x",0)); z=float(data.get("z",0))
                except (TypeError,ValueError,OverflowError): continue
                if math.isfinite(x) and math.isfinite(z):
                    agent.input=(max(-1,min(1,x)),max(-1,min(1,z))) if SCENE_SIM.phase=="running" and not agent.escaped and not agent.failed and not agent.completed else (0.0,0.0)
                    agent.input_received_at=time.monotonic()
            elif data.get("type")=="door":
                door_id = data.get("door_id")
                if isinstance(door_id, str) and SCENE_SIM.phase=="running" and not agent.escaped and not agent.failed and not agent.completed:
                    SCENE_SIM.request_door(door_id, agent, "open", "participant")
            elif data.get("type")=="rename": agent.name=str(data.get("name","")).strip()[:MAX_PLAYER_NAME_LENGTH] or agent.name
    except (WebSocketDisconnect,RuntimeError): pass
    except Exception: pass
    finally: SCENE_HUB.remove(ws)


@app.get("/api/info")
async def api_info(request: Request):
    port = request.url.port or 8000
    return {
        "lan_ip": LAN_IP, "port": port,
        "version": APP_VERSION,
        "require_consent": REQUIRE_CONSENT,
        "admin_protection": bool(ADMIN_TOKEN),
        "play_url": configured_public_url(port),
        "lan_play_url": f"http://{LAN_IP}:{port}/play",
        "world": {"w": maze.world_w, "h": maze.world_h},
        "view": {"w": maze.view_w, "h": maze.view_h},
        "protocol": 2,
        "scene_3d": ({"id": SCENE_SIM.scene["id"], "name": SCENE_SIM.scene.get("name", ""),
                      "scale_status": SCENE_SIM.scene["scale"]["status"]} if SCENE_SIM else None),
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
async def api_tracks(_: None = Depends(require_admin)):
    return {"tracks": {a.pid: a.track for a in sim.agents.values()}}


@app.get("/api/players")
async def api_players(_: None = Depends(require_admin)):
    return {"players": sim.snapshot_agents(), **sim.public_state()}


@app.post("/api/reset")
async def api_reset(pos: int = Query(0, ge=0, le=1), fire: int = Query(0, ge=0, le=1),
                    _: None = Depends(require_admin)):
    sim.reset_crowd(keep_humans=True, clear_fire=bool(fire))
    await hub.broadcast({"type": "maze", **maze_payload(), **sim.public_state(),
                         "agents": sim.snapshot_agents()})
    return {"ok": True, **sim.public_state()}


@app.post("/api/restart")
async def api_restart(complexity: int = Query(8, ge=1, le=10),
                      view: int = Query(3, ge=1, le=10),
                      exit_count: int = Query(1, ge=1, le=4),
                      exit_layout: str = Query("spread", pattern="^(spread|corners|sides)$"),
                      seed: Optional[int] = Query(None),
                      _: None = Depends(require_admin)):
    """按新的复杂度和视野重建迷宫"""
    await apply_restart(complexity, view, exit_count, exit_layout, seed=seed)
    return {"ok": True, "gen": maze.gen,
            "complexity": maze.complexity, "view_level": maze.view_level,
            "cell": maze.cell, "mw": maze.mw, "mh": maze.mh,
            "exits": [list(exit_cell) for exit_cell in maze.exits],
            "exit_count": maze.exit_count, "exit_layout": maze.exit_layout,
            "seed": maze.seed}


@app.post("/api/evac/start")
async def api_evac_start(_: None = Depends(require_admin)):
    sim.start()
    await hub.broadcast(evac_state_msg(time.time()))
    return {"ok": True, **sim.public_state()}


@app.post("/api/evac/pause")
async def api_evac_pause(_: None = Depends(require_admin)):
    sim.pause()
    await hub.broadcast(evac_state_msg(time.time()))
    return {"ok": True, **sim.public_state()}


@app.post("/api/evac/end")
async def api_evac_end(_: None = Depends(require_admin)):
    """Finish the current trial and archive it without changing the crowd.

    This is intentionally separate from reset: operators can close a trial
    at the planned stopping point while preserving all outcomes and tracks.
    A subsequent Start begins a fresh trial through the existing reset path.
    """
    names = sim.end(reason="manual_end")
    if names is None:
        return {"ok": True, "changed": False, **sim.public_state()}
    await hub.broadcast(evac_state_msg(time.time()))
    return {"ok": True, "changed": True, "saved": names, **sim.public_state()}


@app.post("/api/evac/fire")
async def api_evac_fire(gx: int = -1, gy: int = -1,
                         _: None = Depends(require_admin)):
    if (gx, gy) != (-1, -1) and not (0 <= gx < maze.mw and 0 <= gy < maze.mh):
        raise HTTPException(422, "点火位置超出地图范围")
    ok = sim.ignite(None if gx < 0 else gx, None if gy < 0 else gy)
    await hub.broadcast(evac_state_msg(time.time()))
    return {"ok": ok, **sim.public_state()}


@app.post("/api/evac/panic")
async def api_evac_panic(v: float = Query(0.4, ge=0, le=1, allow_inf_nan=False),
                          _: None = Depends(require_admin)):
    sim.panic = max(0.0, min(1.0, float(v)))
    return {"ok": True, "panic": sim.panic}


@app.post("/api/evac/npcs")
async def api_evac_npcs(n: int = Query(..., ge=0, le=MAX_NPCS),
                         _: None = Depends(require_admin)):
    sim.set_npc_count(n)
    await hub.broadcast({"type": "maze", **maze_payload(), **sim.public_state(),
                         "agents": sim.snapshot_agents()})
    return {"ok": True, **sim.public_state()}


@app.post("/api/evac/speed")
async def api_evac_speed(v: float = Query(1.0, ge=0.25, le=4.0, allow_inf_nan=False),
                          _: None = Depends(require_admin)):
    sim.set_time_scale(v)
    await hub.broadcast(evac_state_msg(time.time()))
    return {"ok": True, "time_scale": sim.time_scale}


@app.post("/api/evac/wind")
async def api_evac_wind(x: float = Query(0.0, ge=-8.0, le=8.0, allow_inf_nan=False),
                        y: float = Query(0.0, ge=-8.0, le=8.0, allow_inf_nan=False),
                        _: None = Depends(require_admin)):
    sim.set_wind(x, y)
    await hub.broadcast(evac_state_msg(time.time()))
    return {"ok": True, **sim.public_state()}


@app.post("/api/evac/spawn_zone")
async def api_evac_spawn_zone(x0: Optional[int] = None, y0: Optional[int] = None,
                              x1: Optional[int] = None, y1: Optional[int] = None,
                              _: None = Depends(require_admin)):
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
async def api_experiment_get(_: None = Depends(require_admin)):
    """查看当前实验元数据（场次分组、条件标签等）。"""
    return {"ok": True, "experiment": sim.build_summary()["experiment"]}


@app.post("/api/experiment")
async def api_experiment_set(request: Request, _: None = Depends(require_admin)):
    """设置实验元数据。这些字段会写进汇总与轨迹文件，用于多场次分组统计。"""
    data = await read_json_body(request, MAX_EXPERIMENT_JSON_BYTES, "实验元数据")
    if not isinstance(data, dict):
        raise HTTPException(400, "实验元数据必须是 JSON 对象")
    fields = ("experiment_id", "session_id", "condition", "group", "notes", "operator")
    updates = {}
    for key in fields:
        if key in data:
            value = data[key]
            if value is None:
                value = ""
            if not isinstance(value, (str, int, float)) or isinstance(value, bool) \
                    or (isinstance(value, float) and not math.isfinite(value)):
                raise HTTPException(400, f"{key} 必须是字符串")
            updates[key] = str(value)[:200]
    if sim.phase in {"running", "paused"} and any(
            getattr(sim, key) != value for key, value in updates.items()):
        raise HTTPException(409, "场次运行或暂停期间不能修改实验信息")
    for key, value in updates.items():
        setattr(sim, key, value)
    return {"ok": True, "experiment": sim.build_summary()["experiment"]}


@app.post("/api/experiment/archive")
async def api_experiment_archive(_: None = Depends(require_admin)):
    """把当前场次立即落盘为独立文件，不影响运行中的仿真。"""
    names = sim.archive_trial(reason="manual", finalize=False)
    return {"ok": True, "saved": names, "trial_seq": sim.trial_seq}


@app.get("/api/evac/summary")
async def api_evac_summary(_: None = Depends(require_admin)):
    return sim.summary or sim.build_summary()


@app.get("/api/scenario")
async def api_scenario_get(_: None = Depends(require_admin)):
    return JSONResponse(sim.export_scenario(), headers={
        "Content-Disposition": "attachment; filename=evac_scenario.json"})


@app.post("/api/scenario")
async def api_scenario_load(request: Request, _: None = Depends(require_admin)):
    data = await read_json_body(request, MAX_SCENARIO_JSON_BYTES, "场景")
    apply_loaded_scenario(data)
    await hub.broadcast({"type": "maze", **maze_payload(), **sim.public_state(),
                         "agents": sim.snapshot_agents()})
    return {"ok": True, "gen": maze.gen, **sim.public_state()}


@app.get("/api/export")
async def api_export(fmt: str = "csv", _: None = Depends(require_admin)):
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

    port = configured_port() or find_free_port()
    host_url = f"http://127.0.0.1:{port}/"
    public_play_url = configured_public_url(port)
    print("=" * 60)
    print("  迷宫多人系统已启动")
    print(f"  版本:      {APP_VERSION}")
    print(f"  管理保护:  {'开启' if ADMIN_TOKEN else '关闭（可信局域网模式）'}")
    print(f"  主机大屏:  {host_url}")
    print(f"  手机控制:  {public_play_url}")
    print(f"  场景校准:  {host_url.rstrip('/')}/calibrate")
    if SCENE_SIM is not None:
        floors = len(SCENE_SIM.scene.get("floors", []))
        stairs = len(SCENE_SIM.scene.get("stairs", []))
        scale = SCENE_SIM.scene.get("scale", {}).get("status", "provisional")
        print(f"  3D 场景:   {ACTIVE_SCENE_ID} · {floors} 层 · {stairs} 段楼梯 · "
              f"{'正式尺度' if scale == 'verified' else '临时比例'}")
        print(f"  运行模式:  {'真人实验' if SCENE_SIM.npc_target == 0 else 'NPC+真人演示'}")
        print(f"  NPC 人数:  {SCENE_SIM.npc_target}（真人不占名额）")
    else:
        print(f"  初始迷宫:  {maze.mw} × {maze.mh} 格（每格 {maze.cell}px）")
        print(f"  初始视野:  {maze.view_w} × {maze.view_h} 世界单位")
    print("  提示: 手机建议横屏，视野最佳")
    print("=" * 60)

    if OPEN_BROWSER:
        threading.Timer(1.5, lambda: webbrowser.open(host_url)).start()
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
