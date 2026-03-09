"""Marlow Daemon — persistent HTTP API for the autonomous agent.

Runs as a systemd service or standalone process. Exposes an HTTP API
on localhost:8420 for submitting goals, checking status, and history.

Usage:
    python3 -m marlow.daemon_linux

/ Daemon persistente — HTTP API en localhost:8420 para el agente autonomo.
"""

from __future__ import annotations

import asyncio
import glob as _glob
import json
import logging
import os
import signal
import sys
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Optional

from aiohttp import web

# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

PORT = 8420
HOST = "127.0.0.1"
MAX_HISTORY = 20
MARLOW_DIR = os.path.expanduser("~/.marlow")
LOG_FILE = os.path.join(MARLOW_DIR, "daemon.log")


# ─────────────────────────────────────────────────────────────
# Data types
# ─────────────────────────────────────────────────────────────

@dataclass
class GoalRecord:
    goal: str
    channel: str = "console"  # voice | sidebar | telegram | console
    status: str = "queued"  # queued | executing | completed | failed | stopped
    success: bool = False
    steps_completed: int = 0
    steps_total: int = 0
    avg_score: float = 0.0
    errors: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    started_at: float = 0.0
    finished_at: float = 0.0
    result_summary: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ─────────────────────────────────────────────────────────────
# Environment auto-detection for Sway/Wayland
# ─────────────────────────────────────────────────────────────

def _ensure_sway_env():
    """Auto-detect Sway environment variables if not set."""
    try:
        uid = os.getuid()
    except AttributeError:
        return  # Windows — skip

    runtime_dir = f"/run/user/{uid}"

    if "XDG_RUNTIME_DIR" not in os.environ:
        if os.path.isdir(runtime_dir):
            os.environ["XDG_RUNTIME_DIR"] = runtime_dir

    if "SWAYSOCK" not in os.environ:
        socks = _glob.glob(f"{runtime_dir}/sway-ipc.*.sock")
        if socks:
            os.environ["SWAYSOCK"] = socks[0]

    if "WAYLAND_DISPLAY" not in os.environ:
        # Check for wayland socket in runtime dir
        for name in ("wayland-1", "wayland-0"):
            if os.path.exists(os.path.join(runtime_dir, name)):
                os.environ["WAYLAND_DISPLAY"] = name
                break
        else:
            os.environ["WAYLAND_DISPLAY"] = "wayland-1"

    if "DBUS_SESSION_BUS_ADDRESS" not in os.environ:
        bus_path = os.path.join(runtime_dir, "bus")
        if os.path.exists(bus_path):
            os.environ["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus_path}"


# ─────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────

def _setup_logging():
    os.makedirs(MARLOW_DIR, exist_ok=True)
    fmt = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ]
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


logger = logging.getLogger("marlow.daemon")


# ─────────────────────────────────────────────────────────────
# Daemon
# ─────────────────────────────────────────────────────────────

class MarlowDaemon:
    """Persistent daemon wrapping AutonomousMarlow with an HTTP API."""

    def __init__(self):
        self._marlow = None
        self._start_time: float = 0.0
        self._state: str = "starting"  # starting | idle | executing | planning | stopped
        self._current_goal: Optional[str] = None
        self._current_task: Optional[asyncio.Task] = None
        self._goal_queue: asyncio.Queue = asyncio.Queue()
        self._history: deque[GoalRecord] = deque(maxlen=MAX_HISTORY)
        self._tools_count: int = 0
        self._stop_requested: bool = False
        self._shutdown_event: asyncio.Event = asyncio.Event()
        self._ws_clients: list = []
        self._telegram = None  # WebSocket connections from sidebar
        self._queue_worker: Optional[asyncio.Task] = None

    # ── Lifecycle ──

    def _init_marlow(self) -> dict:
        """Initialize AutonomousMarlow with LLM provider from environment."""
        from marlow.kernel.integration_linux import AutonomousMarlow

        provider = os.environ.get("MARLOW_LLM_PROVIDER", "anthropic")
        model = os.environ.get("MARLOW_LLM_MODEL", "")

        self._marlow = AutonomousMarlow(
            llm_provider=provider,
            llm_model=model,
            auto_confirm=True,
            timeout=30.0,
        )

        result = self._marlow.setup()
        self._tools_count = result["total_tools"]
        self._start_time = time.time()
        self._state = "idle"
        logger.info(
            "AutonomousMarlow ready: %d tools, provider=%s",
            self._tools_count, provider,
        )
        return result

    def _teardown(self):
        """Clean shutdown of AutonomousMarlow."""
        self._state = "stopped"
        if self._marlow:
            try:
                self._marlow.teardown()
            except Exception as e:
                logger.error("Teardown error: %s", e)
            self._marlow = None
        logger.info("Marlow daemon stopped.")

    # ── Goal execution ──

    async def _execute_goal(self, record: GoalRecord):
        """Execute a single goal via AutonomousMarlow."""
        self._state = "executing"
        self._current_goal = record.goal
        self._stop_requested = False
        record.status = "executing"
        record.started_at = time.time()

        try:
            result = await self._marlow.execute(record.goal)

            if self._stop_requested:
                record.status = "stopped"
                record.errors = ["Stopped by user"]
            else:
                record.success = result.success
                record.status = "completed" if result.success else "failed"
                record.steps_completed = result.steps_completed
                record.steps_total = result.steps_total
                record.avg_score = result.avg_score
                record.errors = result.errors
                record.duration_s = result.duration_s
                record.result_summary = getattr(result, "result_summary", "")

        except asyncio.CancelledError:
            record.status = "stopped"
            record.errors = ["Cancelled"]
        except Exception as e:
            record.status = "failed"
            record.errors = [str(e)]
            logger.error("Goal execution error: %s", e)
        finally:
            record.finished_at = time.time()
            if record.duration_s == 0.0:
                record.duration_s = record.finished_at - record.started_at
            self._history.append(record)
            self._current_goal = None
            self._current_task = None
            self._state = "idle"
            logger.info(
                "Goal %s: %s (%.1fs)",
                record.status, record.goal[:60], record.duration_s,
            )

    async def _queue_worker_loop(self):
        """Process goals from the queue sequentially."""
        while not self._shutdown_event.is_set():
            try:
                record = await asyncio.wait_for(
                    self._goal_queue.get(), timeout=1.0,
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            task = asyncio.create_task(self._execute_goal(record))
            self._current_task = task
            try:
                await task
            except asyncio.CancelledError:
                pass

    # ── HTTP Handlers ──


    async def handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        """WebSocket /ws — real-time updates for sidebar."""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._ws_clients.append(ws)
        logger.info("Sidebar WebSocket connected (%d clients)", len(self._ws_clients))

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        if data.get("type") == "goal":
                            goal_text = data.get("text", "").strip()
                            if goal_text:
                                record = GoalRecord(goal=goal_text, channel="sidebar")
                                await self._goal_queue.put(record)
                                await ws.send_json({"type": "ack", "goal": goal_text})
                    except json.JSONDecodeError:
                        pass
                elif msg.type == web.WSMsgType.ERROR:
                    break
        finally:
            if ws in self._ws_clients:
                self._ws_clients.remove(ws)
            logger.info("Sidebar WebSocket disconnected (%d clients)", len(self._ws_clients))

        return ws

    async def _broadcast_ws(self, event: dict):
        """Send event to all connected WebSocket clients."""
        if not self._ws_clients:
            return
        data = json.dumps(event)
        for ws in list(self._ws_clients):
            try:
                await ws.send_str(data)
            except Exception:
                if ws in self._ws_clients:
                    self._ws_clients.remove(ws)

    async def handle_goal(self, request: web.Request) -> web.Response:
        """POST /goal — Submit a goal for execution."""
        try:
            body = await request.json()
        except (json.JSONDecodeError, Exception):
            return web.json_response(
                {"error": "Invalid JSON. Expected: {\"goal\": \"text\"}"},
                status=400,
            )

        goal_text = body.get("goal", "").strip()
        if not goal_text:
            return web.json_response(
                {"error": "Missing 'goal' field"},
                status=400,
            )

        channel = body.get("channel", "console")
        record = GoalRecord(goal=goal_text, channel=channel)
        queue_size = self._goal_queue.qsize()

        await self._goal_queue.put(record)

        if queue_size > 0:
            return web.json_response({
                "status": "queued",
                "goal": goal_text,
                "position": queue_size + 1,
                "message": f"Goal queued (position {queue_size + 1}). "
                           f"Another goal is currently executing.",
            })

        # Wait for execution to complete (but with timeout for very long goals)
        # Give it a moment to start, then poll for completion
        for _ in range(600):  # 10 min max
            await asyncio.sleep(1.0)
            if record.status in ("completed", "failed", "stopped"):
                return web.json_response(record.to_dict())

        return web.json_response({
            "status": "timeout",
            "goal": goal_text,
            "message": "Goal still executing after 10 minutes. Check /status.",
        })

    async def handle_status(self, request: web.Request) -> web.Response:
        """GET /status — Current agent status."""
        recent = list(self._history)[-5:]
        return web.json_response({
            "state": self._state,
            "current_goal": self._current_goal,
            "queue_size": self._goal_queue.qsize(),
            "uptime_s": round(time.time() - self._start_time, 1),
            "tools_registered": self._tools_count,
            "recent_goals": [r.to_dict() for r in recent],
        })

    async def handle_stop(self, request: web.Request) -> web.Response:
        """POST /stop — Stop the currently executing goal."""
        if self._current_task and not self._current_task.done():
            self._stop_requested = True
            self._current_task.cancel()
            return web.json_response({
                "status": "stopping",
                "goal": self._current_goal,
            })
        return web.json_response({
            "status": "idle",
            "message": "No goal currently executing.",
        })

    async def handle_health(self, request: web.Request) -> web.Response:
        """GET /health — Simple health check."""
        return web.json_response({
            "status": "ok",
            "uptime": round(time.time() - self._start_time, 1),
        })

    async def handle_history(self, request: web.Request) -> web.Response:
        """GET /history — Last 20 executed goals."""
        return web.json_response({
            "history": [r.to_dict() for r in self._history],
            "total": len(self._history),
        })

    # ── Server ──

    async def run(self):
        """Start the daemon: init Marlow, start queue worker, serve HTTP."""
        _setup_logging()
        _ensure_sway_env()

        logger.info("Marlow Daemon starting on %s:%d", HOST, PORT)

        # Initialize AutonomousMarlow
        try:
            setup_result = self._init_marlow()
            failed = len(setup_result.get("failed", []))
            if failed:
                logger.warning("%d tools failed to register", failed)
        except Exception as e:
            logger.error("Failed to initialize AutonomousMarlow: %s", e)
            sys.exit(1)

        # Start goal queue worker
        self._queue_worker = asyncio.create_task(self._queue_worker_loop())


        # Start Telegram bridge if configured
        try:
            from marlow.core.settings import get_settings
            settings = get_settings()
            if settings.telegram.enabled and settings.secrets.telegram_bot_token:
                from marlow.bridges.telegram.bridge import TelegramBridge
                self._telegram = TelegramBridge()

                async def _tg_goal(text, channel):
                    record = GoalRecord(goal=text, channel=channel)
                    await self._goal_queue.put(record)
                    # Wait for result (simplified)
                    for _ in range(600):
                        await asyncio.sleep(1.0)
                        if record.status in ("completed", "failed", "stopped"):
                            return record.to_dict()
                    return {"success": False, "errors": ["timeout"]}

                asyncio.create_task(self._telegram.start(_tg_goal))
                logger.info("Telegram bridge started")
        except Exception as e:
            logger.warning("Telegram bridge not started: %s", e)

        # Setup HTTP routes
        app = web.Application()
        app.router.add_post("/goal", self.handle_goal)
        app.router.add_get("/status", self.handle_status)
        app.router.add_post("/stop", self.handle_stop)
        app.router.add_get("/health", self.handle_health)
        app.router.add_get("/history", self.handle_history)
        app.router.add_get("/ws", self.handle_ws)

        # Setup signal handlers
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._handle_signal)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, HOST, PORT)
        await site.start()

        logger.info(
            "Marlow Daemon ready — %d tools, listening on http://%s:%d",
            self._tools_count, HOST, PORT,
        )

        # Wait for shutdown signal
        await self._shutdown_event.wait()

        # Cleanup
        logger.info("Shutting down...")
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()
            try:
                await asyncio.wait_for(self._current_task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        if self._queue_worker and not self._queue_worker.done():
            self._queue_worker.cancel()
            try:
                await asyncio.wait_for(self._queue_worker, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        await runner.cleanup()
        self._teardown()

    def _handle_signal(self):
        """Handle SIGTERM/SIGINT for clean shutdown."""
        logger.info("Signal received, initiating shutdown...")
        self._shutdown_event.set()


# ─────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────

def main():
    daemon = MarlowDaemon()
    try:
        asyncio.run(daemon.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
