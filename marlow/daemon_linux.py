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

from marlow.kernel.cognition import create_provider, LLMProviderError

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
# Intent classification — keyword routing only
# ─────────────────────────────────────────────────────────────

# Default routing keywords. Override via [intent] in config.toml.
_DEFAULT_KEYWORDS = {
    "greetings": [
        "hola", "hey", "hi", "hello", "buenos dias", "buenas tardes",
        "buenas noches", "que tal", "que onda", "saludos",
    ],
    "questions": [
        "qué", "que es", "cómo", "como", "por qué", "porque",
        "cuánto", "cuanto", "dónde", "donde", "cuándo", "cuando",
        "quién", "quien", "what", "how", "why", "where", "when", "who",
        "puedes", "sabes", "conoces", "explica", "dime",
    ],
    "actions": [
        "busca", "search", "abre", "open", "cierra", "close",
        "captura", "screenshot", "muestra", "show", "escribe", "write",
        "ejecuta", "run", "instala", "install", "mueve", "move",
        "minimiza", "minimize", "maximiza", "maximize", "mata", "kill",
        "crea", "create", "borra", "delete", "copia", "copy",
    ],
}

# Map ISO codes to LLM-friendly language names
_LANG_NAMES = {
    "es": "Spanish", "en": "English", "pt": "Portuguese",
    "fr": "French", "de": "German", "it": "Italian",
    "ja": "Japanese", "zh": "Chinese", "ko": "Korean",
}


def _load_intent_keywords() -> dict[str, list[str]]:
    """Load intent routing keywords from config, with defaults."""
    keywords = dict(_DEFAULT_KEYWORDS)
    try:
        import tomllib
        config_path = os.path.expanduser("~/.config/marlow/config.toml")
        if os.path.exists(config_path):
            with open(config_path, "rb") as f:
                config = tomllib.load(f)
            intent = config.get("intent", {})
            for key in ("greetings", "questions", "actions"):
                if key in intent:
                    keywords[key] = intent[key]
    except Exception:
        pass
    return keywords


def classify_intent(text: str) -> str:
    """Classify user input as 'greeting', 'question', or 'action'.

    Only for routing — the LLM handles all natural language generation.
    """
    keywords = _load_intent_keywords()
    lower = text.lower().strip().rstrip("?!.,")
    words = lower.split()

    if not words:
        return "greeting"

    # Check greetings (usually at the start)
    for g in keywords["greetings"]:
        if lower == g or lower.startswith(g + " ") or lower.startswith(g + ","):
            return "greeting"

    # Check action keywords (anywhere in text)
    for a in keywords["actions"]:
        if a in words:
            return "action"

    # Check question words (usually at start)
    first = words[0]
    for q in keywords["questions"]:
        if first == q or lower.startswith(q + " "):
            return "question"

    # Trailing ? is likely a question
    if text.strip().endswith("?"):
        return "question"

    # Default to conversational (let LLM handle it)
    return "question"


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
        self._transcripts: list[dict] = []  # Voice conversation transcripts
        self._telegram = None  # WebSocket connections from sidebar
        self._queue_worker: Optional[asyncio.Task] = None
        self._llm = None  # LLM provider for conversational responses
        self._user_name: str = ""
        self._language: str = "Spanish"  # Full name for LLM prompts

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
        self._load_user_prefs()
        logger.info(
            "AutonomousMarlow ready: %d tools, provider=%s, user=%s, lang=%s",
            self._tools_count, provider, self._user_name, self._language,
        )
        return result

    def _load_user_prefs(self):
        """Load user name and language from settings."""
        try:
            from marlow.core.settings import get_settings
            s = get_settings()
            self._user_name = s.user.name or "User"
            lang_code = getattr(s.user, "language", "es")
            self._language = _LANG_NAMES.get(lang_code, lang_code)
        except Exception:
            self._user_name = "User"
            self._language = "Spanish"

    def _ensure_llm(self):
        """Lazy-init LLM provider for conversational responses."""
        if self._llm is not None:
            return
        provider = os.environ.get("MARLOW_LLM_PROVIDER", "anthropic")
        model = os.environ.get("MARLOW_LLM_MODEL", "")
        try:
            self._llm = create_provider(provider, model=model, timeout=30.0)
            logger.info("Conversational LLM ready: %s", provider)
        except Exception as e:
            logger.warning("Failed to init conversational LLM: %s", e)

    async def _llm_chat(self, text: str) -> str:
        """Generate a conversational response via LLM."""
        self._ensure_llm()
        if not self._llm:
            return ""

        system = (
            f"You are Marlow, a desktop assistant. "
            f"The user's name is {self._user_name}. "
            f"Respond in {self._language}. "
            f"Be concise, natural, and helpful. Max 1-2 sentences."
        )
        try:
            return (await self._llm.generate(
                messages=[{"role": "user", "content": text}],
                system=system,
                max_tokens=150,
                temperature=0.7,
            )).strip()
        except Exception as e:
            logger.error("LLM chat error: %s", e)
            return ""

    async def _llm_format_result(self, original_message: str, result: dict) -> str:
        """Format a GoalEngine result into natural language via LLM."""
        self._ensure_llm()
        if not self._llm:
            # Fallback without LLM
            if result.get("success"):
                return result.get("result_summary") or "Done."
            errs = result.get("errors", [])
            return errs[0][:150] if errs else "Failed."

        compact = {
            "success": result.get("success"),
            "steps": result.get("steps_completed"),
            "errors": result.get("errors", [])[:2],
            "summary": result.get("result_summary", ""),
        }

        system = (
            f"The user asked: \"{original_message}\"\n"
            f"The result was: {json.dumps(compact)}\n"
            f"Summarize the outcome naturally in {self._language} in 1 sentence. "
            f"Do not include technical details like JSON or field names."
        )
        try:
            return (await self._llm.generate(
                messages=[{"role": "user", "content": "Summarize this result."}],
                system=system,
                max_tokens=100,
                temperature=0.5,
            )).strip()
        except Exception as e:
            logger.error("LLM format error: %s", e)
            if result.get("success"):
                return result.get("result_summary") or ""
            errs = result.get("errors", [])
            return errs[0][:150] if errs else ""

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
        """POST /goal — Submit a goal for execution.

        Routes greetings/questions to LLM, actions to GoalEngine.
        All natural language is generated by the LLM (language from config).
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
        intent = classify_intent(goal_text)
        logger.info("Intent: %s for '%s' (channel=%s)", intent, goal_text[:60], channel)

        # ── Conversational intents: LLM responds directly ──
        if intent in ("greeting", "question"):
            response_text = await self._llm_chat(goal_text)
            if not response_text:
                response_text = goal_text  # Echo if LLM unavailable
            return web.json_response({
                "success": True,
                "status": "completed",
                "goal": goal_text,
                "response": response_text,
                "result_summary": response_text,
                "intent": intent,
            })

        # ── Action intent: GoalEngine executes, LLM formats result ──
        record = GoalRecord(goal=goal_text, channel=channel)
        queue_size = self._goal_queue.qsize()

        await self._goal_queue.put(record)

        if queue_size > 0:
            return web.json_response({
                "status": "queued",
                "goal": goal_text,
                "position": queue_size + 1,
                "response": f"Queued at position {queue_size + 1}.",
            })

        # Wait for execution to complete
        for _ in range(600):  # 10 min max
            await asyncio.sleep(1.0)
            if record.status in ("completed", "failed", "stopped"):
                result = record.to_dict()
                # Format result with LLM
                response_text = await self._llm_format_result(goal_text, result)
                result["response"] = response_text
                if not result.get("result_summary"):
                    result["result_summary"] = response_text
                return web.json_response(result)

        return web.json_response({
            "status": "timeout",
            "goal": goal_text,
            "response": "Still executing after 10 minutes.",
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

        if not self._marlow or tool_name not in self._marlow._tool_map:
            return web.json_response(
                {"error": f"Unknown tool: {tool_name}"}, status=400,
            )

        try:
            func = self._marlow._tool_map[tool_name]
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: func(**params))
            if isinstance(result, dict):
                return web.json_response(result)
            return web.json_response({"success": True, "result": str(result)})
        except Exception as e:
            logger.error("Tool execution error (%s): %s", tool_name, e)
            return web.json_response({"success": False, "error": str(e)})

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
        """GET /transcripts — Get recent voice transcripts (with ?since= filter)."""
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
