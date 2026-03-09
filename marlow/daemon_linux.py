"""Marlow Daemon — persistent HTTP API for the autonomous agent.

Runs as a systemd service or standalone process. Exposes an HTTP API
on localhost:8420 for submitting goals, checking status, and history.

Architecture:
    ALL user interaction (sidebar, console, telegram) -> Gemini API (with tools)
    Voice -> Gemini Live (streaming audio, separate daemon)
    Gemini decides: greet, answer, or call desktop tools via function calling.
    Fallback: GoalEngine + templates if Gemini unavailable.

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
    """Persistent daemon wrapping AutonomousMarlow with an HTTP API.

    Primary path: ALL text -> Gemini API (with function calling tools).
    Fallback path: GoalEngine with templates (if Gemini unavailable).
    """

    def __init__(self):
        self._marlow = None
        self._start_time: float = 0.0
        self._state: str = "starting"
        self._current_goal: Optional[str] = None
        self._current_task: Optional[asyncio.Task] = None
        self._goal_queue: asyncio.Queue = asyncio.Queue()
        self._history: deque[GoalRecord] = deque(maxlen=MAX_HISTORY)
        self._tools_count: int = 0
        self._stop_requested: bool = False
        self._shutdown_event: asyncio.Event = asyncio.Event()
        self._ws_clients: list = []
        self._transcripts: list[dict] = []
        self._telegram = None
        self._queue_worker: Optional[asyncio.Task] = None
        self._gemini_text = None  # GeminiTextBridge for all text interaction
        self._user_name: str = ""
        self._language: str = "es"

    # ── Lifecycle ──

    def _init_marlow(self) -> dict:
        """Initialize AutonomousMarlow (tools + GoalEngine for fallback)."""
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
        self._load_user_prefs()
        logger.info(
            "AutonomousMarlow ready: %d tools, user=%s, lang=%s",
            self._tools_count, self._user_name, self._language,
        )
        return result

    def _load_user_prefs(self):
        """Load user name and language from settings."""
        try:
            from marlow.core.settings import get_settings
            s = get_settings()
            self._user_name = s.user.name or "User"
            self._language = getattr(s.user, "language", "es")
        except Exception:
            self._user_name = "User"
            self._language = "es"

    def _init_gemini_text(self):
        """Initialize GeminiTextBridge for all text interaction."""
        try:
            from marlow.core.settings import get_settings
            settings = get_settings()
        except Exception:
            logger.warning("Cannot load settings for Gemini text bridge")
            return

        # Get API key
        api_key = settings.secrets.gemini_api_key
        if not api_key:
            api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            api_key = os.environ.get("MARLOW_GEMINI_API_KEY", "")

        if not api_key:
            logger.warning(
                "No Gemini API key configured — text will use fallback "
                "(GoalEngine + templates). Set gemini.api_key in secrets.toml."
            )
            return

        # Get text model (separate from audio model)
        text_model = getattr(settings.gemini, "text_model", "") or "gemini-2.5-flash"

        try:
            from marlow.bridges.gemini_text import GeminiTextBridge

            self._gemini_text = GeminiTextBridge(
                api_key=api_key,
                tool_executor=self._execute_tool_direct,
                user_name=self._user_name,
                language=self._language,
                model=text_model,
            )
            logger.info(
                "Gemini text bridge ready: model=%s, user=%s",
                text_model, self._user_name,
            )
        except Exception as e:
            logger.error("Failed to init Gemini text bridge: %s", e)
            self._gemini_text = None

    async def _execute_tool_direct(self, tool_name: str, args: dict) -> dict:
        """Execute a tool directly from the daemon tool map.

        Used by GeminiTextBridge as tool_executor callback.
        Same tools available as the voice path (via /tool endpoint).
        """
        if not self._marlow or tool_name not in self._marlow._tool_map:
            return {"success": False, "error": f"Unknown tool: {tool_name}"}

        try:
            func = self._marlow._tool_map[tool_name]
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: func(**args))
            if isinstance(result, dict):
                # Fallback: if launch_in_shadow failed (no compositor),
                # try open_application instead so Sway mode still works.
                if (tool_name == "launch_in_shadow"
                        and not result.get("success")
                        and "open_application" in self._marlow._tool_map):
                    command = args.get("command", "")
                    logger.info(
                        "launch_in_shadow failed, falling back to "
                        "open_application: %s", command,
                    )
                    func2 = self._marlow._tool_map["open_application"]
                    result = await loop.run_in_executor(
                        None, lambda: func2(app_name=command),
                    )
                    if isinstance(result, dict):
                        result["note"] = "Opened visibly (shadow mode unavailable)"
                        return result
                return result
            return {"success": True, "result": str(result)}
        except Exception as e:
            logger.error("Tool execution error (%s): %s", tool_name, e)
            return {"success": False, "error": str(e)}

    @staticmethod
    def _is_gemini_active() -> bool:
        """Check if a Gemini Live voice session is currently active."""
        state_file = "/tmp/marlow-voice-state"
        try:
            if os.path.exists(state_file):
                with open(state_file) as f:
                    return f.read().strip() == "gemini-active"
        except Exception:
            pass
        return False

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

    # ── Goal execution (fallback path) ──

    async def _execute_goal(self, record: GoalRecord):
        """Execute a single goal via AutonomousMarlow (fallback path)."""
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
        """Process goals from the queue sequentially (fallback path)."""
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

    # ── Fallback handling (no Gemini) ──

    async def _handle_fallback(self, goal_text: str, channel: str) -> dict:
        """Handle a goal without Gemini — use GoalEngine + templates.

        Reduced capabilities: template planner for known actions,
        generic responses for everything else.
        """
        logger.info("Fallback path for: %s (channel=%s)", goal_text[:60], channel)

        # Queue the goal for GoalEngine execution
        record = GoalRecord(goal=goal_text, channel=channel)
        queue_size = self._goal_queue.qsize()
        await self._goal_queue.put(record)

        if queue_size > 0:
            return {
                "success": True,
                "status": "queued",
                "goal": goal_text,
                "response": f"En cola, posicion {queue_size + 1}.",
                "result_summary": f"Queued at position {queue_size + 1}.",
            }

        # Wait for execution to complete
        for _ in range(600):  # 10 min max
            await asyncio.sleep(1.0)
            if record.status in ("completed", "failed", "stopped"):
                result = record.to_dict()
                # Simple response formatting (no LLM)
                if record.success:
                    response = record.result_summary or "Listo."
                else:
                    response = self._sanitize_error(record.errors)
                result["response"] = response
                if not result.get("result_summary"):
                    result["result_summary"] = response
                return result

        return {
            "status": "timeout",
            "goal": goal_text,
            "response": "La tarea sigue ejecutandose despues de 10 minutos.",
        }


    @staticmethod
    def _sanitize_error(errors: list[str]) -> str:
        """Convert internal errors to user-friendly messages.

        NEVER show raw technical messages (plan validation, tool names,
        JSON, stack traces) to the user. Always natural language.
        """
        if not errors:
            return "No pude completar la tarea. ¿Quieres intentar de otra forma?"

        raw = errors[0].lower()

        # Plan validation failures — tools not available
        if "plan validation failed" in raw or "unknown tool" in raw:
            return ("No tengo todas las herramientas necesarias para eso "
                    "en este momento. ¿Quieres que lo intente de otra forma?")

        # No plan found
        if "no plan" in raw or "no template" in raw:
            return ("No encontré una forma de hacer eso. "
                    "¿Puedes describir lo que necesitas de otra manera?")

        # Timeout
        if "timeout" in raw or "timed out" in raw:
            return "La tarea tardó demasiado. ¿Quieres que lo intente de nuevo?"

        # Connection / network errors
        if "connection" in raw or "network" in raw or "socket" in raw:
            return "Hubo un problema de conexión. ¿Quieres que lo intente de nuevo?"

        # Generic fallback — never show the raw error
        return "No pude completar eso. ¿Quieres intentar de otra forma?"

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
                                # Route through Gemini (same as POST /goal)
                                response = await self._process_text(
                                    goal_text, "sidebar",
                                )
                                await ws.send_json({
                                    "type": "response",
                                    "text": response.get("response", ""),
                                    "success": response.get("success", False),
                                })
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

    async def _process_text(self, goal_text: str, channel: str) -> dict:
        """Process text through Gemini (primary) or fallback.

        This is the unified entry point for ALL text interaction.
        Gemini decides everything: greet, answer, or call tools.
        """
        logger.info("Processing text: '%s' (channel=%s)", goal_text[:60], channel)

        # Primary path: Gemini with tools
        if self._gemini_text:
            try:
                response_text = await self._gemini_text.send_message(goal_text)
                logger.info("Gemini response: %s", response_text[:100])

                result = {
                    "success": True,
                    "status": "completed",
                    "goal": goal_text,
                    "response": response_text,
                    "result_summary": response_text,
                    "engine": "gemini",
                }

                # Add to history
                record = GoalRecord(
                    goal=goal_text, channel=channel,
                    status="completed", success=True,
                    result_summary=response_text,
                    started_at=time.time(),
                    finished_at=time.time(),
                )
                self._history.append(record)

                return result

            except Exception as e:
                logger.error("Gemini text error: %s", e)
                # Fall through to fallback
                logger.info("Falling back to GoalEngine for: %s", goal_text[:60])

        # Fallback path: GoalEngine + templates
        return await self._handle_fallback(goal_text, channel)

    async def handle_goal(self, request: web.Request) -> web.Response:
        """POST /goal — Submit text for processing.

        All text goes to Gemini (with tools). Gemini decides:
        greet naturally, answer questions, or call tools for desktop actions.
        Falls back to GoalEngine if Gemini unavailable.
        """
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
        result = await self._process_text(goal_text, channel)
        return web.json_response(result)

    async def handle_status(self, request: web.Request) -> web.Response:
        """GET /status — Current agent status."""
        recent = list(self._history)[-5:]
        return web.json_response({
            "state": self._state,
            "current_goal": self._current_goal,
            "queue_size": self._goal_queue.qsize(),
            "uptime_s": round(time.time() - self._start_time, 1),
            "tools_registered": self._tools_count,
            "gemini_active": self._gemini_text is not None,
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
            "gemini": self._gemini_text is not None,
        })

    async def handle_tool(self, request: web.Request) -> web.Response:
        """POST /tool — Execute a single tool directly (for Gemini function calls)."""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        tool_name = body.get("tool", "")
        params = body.get("params", {})

        if not tool_name:
            return web.json_response({"error": "Missing 'tool' field"}, status=400)

        # Resolve aliases
        from marlow.bridges.tools_schema import resolve_tool_call
        real_name, real_params = resolve_tool_call(tool_name, params)

        result = await self._execute_tool_direct(real_name, real_params)
        if isinstance(result, dict):
            return web.json_response(result)
        return web.json_response({"success": True, "result": str(result)})

    async def handle_transcript(self, request: web.Request) -> web.Response:
        """POST /transcript — Add a voice conversation transcript entry."""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        entry = {
            "role": body.get("role", "user"),
            "text": body.get("text", ""),
            "time": time.time(),
        }
        self._transcripts.append(entry)
        if len(self._transcripts) > 50:
            self._transcripts = self._transcripts[-50:]

        # Broadcast to WebSocket clients
        await self._broadcast_ws({
            "type": "transcript",
            "role": entry["role"],
            "text": entry["text"],
        })

        return web.json_response({"ok": True})

    async def handle_get_transcripts(self, request: web.Request) -> web.Response:
        """GET /transcripts — Get recent voice transcripts."""
        since = float(request.query.get("since", 0))
        recent = [t for t in self._transcripts if t["time"] > since]
        return web.json_response({"transcripts": recent})

    async def handle_history(self, request: web.Request) -> web.Response:
        """GET /history — Last 20 executed goals."""
        return web.json_response({
            "history": [r.to_dict() for r in self._history],
            "total": len(self._history),
        })

    # ── Server ──

    async def run(self):
        """Start the daemon: init Marlow, init Gemini, serve HTTP."""
        _setup_logging()
        _ensure_sway_env()

        logger.info("Marlow Daemon starting on %s:%d", HOST, PORT)

        # Initialize AutonomousMarlow (tools + GoalEngine for fallback)
        try:
            setup_result = self._init_marlow()
            failed = len(setup_result.get("failed", []))
            if failed:
                logger.warning("%d tools failed to register", failed)
        except Exception as e:
            logger.error("Failed to initialize AutonomousMarlow: %s", e)
            sys.exit(1)

        # Initialize Gemini text bridge (primary path for all text)
        self._init_gemini_text()

        # Start goal queue worker (for fallback path)
        self._queue_worker = asyncio.create_task(self._queue_worker_loop())

        # Start Telegram bridge if configured
        try:
            from marlow.core.settings import get_settings
            settings = get_settings()
            if settings.telegram.enabled and settings.secrets.telegram_bot_token:
                from marlow.bridges.telegram.bridge import TelegramBridge
                self._telegram = TelegramBridge()

                async def _tg_goal(text, channel):
                    return await self._process_text(text, channel)

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
        app.router.add_post("/tool", self.handle_tool)
        app.router.add_post("/transcript", self.handle_transcript)
        app.router.add_get("/transcripts", self.handle_get_transcripts)
        app.router.add_get("/ws", self.handle_ws)

        # Setup signal handlers
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._handle_signal)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, HOST, PORT)
        await site.start()

        engine = "Gemini" if self._gemini_text else "fallback (GoalEngine)"
        logger.info(
            "Marlow Daemon ready — %d tools, engine=%s, http://%s:%d",
            self._tools_count, engine, HOST, PORT,
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
