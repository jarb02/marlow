"""Marlow Daemon — persistent HTTP API for the autonomous agent.

Runs as a systemd service or standalone process. Exposes an HTTP API
on localhost:8420 for submitting goals, checking status, and history.

Architecture:
    ALL user interaction (sidebar, console, telegram) -> Gemini API (with tools)
    Voice -> Gemini Live (streaming audio, separate daemon)
    Gemini decides: greet, answer, or call desktop tools via function calling.
    Fallback chain: Gemini (3 retries) -> Claude Sonnet -> clean error.

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
    Fallback: Claude Sonnet with same tools (if Gemini unavailable).
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
        self._claude_client = None  # Anthropic fallback
        self._context_builder = None  # Dynamic context for LLM prompts
        self._db = None              # DatabaseManager
        self._memory_system = None   # MemorySystem (3-tier)
        self._app_knowledge = None   # AppKnowledgeManager
        self._log_repo = None        # LogRepository
        self._maintenance = None     # DatabaseMaintenance

    # ── Lifecycle ──

    def _init_marlow(self) -> dict:
        """Initialize AutonomousMarlow (tools + GoalEngine for complex goals)."""
        from marlow.kernel.integration_linux import AutonomousMarlow

        provider = os.environ.get("MARLOW_LLM_PROVIDER", "anthropic")
        model = os.environ.get("MARLOW_LLM_MODEL", "")

        self._marlow = AutonomousMarlow(
            llm_provider=provider,
            llm_model=model,
            auto_confirm=True,
            timeout=30.0,
            memory=self._memory_system,
            knowledge=self._app_knowledge,
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

    def _init_context_builder(self):
        """Initialize the dynamic context builder for LLM prompts."""
        try:
            from marlow.kernel.context_builder import ContextBuilder
            from marlow.core.settings import get_settings

            settings = get_settings()
            location = {}
            if hasattr(settings, "location"):
                loc = settings.location
                location = {
                    "city": getattr(loc, "city", ""),
                    "state": getattr(loc, "state", ""),
                    "country": getattr(loc, "country", ""),
                    "timezone": getattr(loc, "timezone", ""),
                }

            # Get platform from AutonomousMarlow if available
            platform = None
            blackboard = None
            weather = None
            if self._marlow:
                platform = getattr(self._marlow, "_platform", None)
                blackboard = getattr(self._marlow, "_blackboard", None)
                weather = getattr(self._marlow, "_weather", None)

            self._context_builder = ContextBuilder(
                platform=platform,
                blackboard=blackboard,
                desktop_weather=weather,
                location=location,
            )
            logger.info("Context builder initialized")
        except Exception as e:
            logger.warning("Failed to init context builder: %s", e)

    def _get_dynamic_context(self) -> str:
        """Get current dynamic context string (safe to call anytime)."""
        if self._context_builder:
            try:
                return self._context_builder.build()
            except Exception as e:
                logger.debug("Context build error: %s", e)
        return ""

    async def _init_database(self):
        """Initialize SQLite persistence layer.

        Creates state.db and logs.db, repositories, MemorySystem,
        AppKnowledgeManager, and runs one-time JSON migration.
        """
        try:
            from marlow.kernel.db.manager import DatabaseManager
            from marlow.kernel.db.repositories import (
                MemoryRepository, KnowledgeRepository, LogRepository,
            )
            from marlow.kernel.memory import MemorySystem
            from marlow.kernel.knowledge import AppKnowledgeManager

            db_dir = os.path.join(MARLOW_DIR, "db")
            self._db = DatabaseManager(data_dir=db_dir)
            await self._db.initialize()

            # Create repositories
            memory_repo = MemoryRepository(self._db.state)
            knowledge_repo = KnowledgeRepository(self._db.state)
            self._log_repo = LogRepository(self._db.logs)

            # Create high-level managers
            self._memory_system = MemorySystem(memory_repo)
            self._app_knowledge = AppKnowledgeManager(knowledge_repo)

            # Switch ErrorJournal to SQLite
            from marlow.core import error_journal
            state_db_path = os.path.join(db_dir, "state.db")
            error_journal.init_sqlite(state_db_path)

            # Switch memory tools to SQLite
            from marlow.tools import memory as memory_tools
            memory_tools.init_sqlite(state_db_path)

            # One-time JSON migration
            await self._migrate_json_data(state_db_path)

            logger.info(
                "Database initialized: %s (state.db + logs.db)",
                db_dir,
            )
        except Exception as e:
            logger.error("Failed to init database: %s", e)
            # Non-fatal: daemon still works with JSON fallbacks

    async def _migrate_json_data(self, state_db_path: str):
        """One-time migration of JSON data to SQLite.

        Imports existing JSON files and renames them to .json.migrated.
        """
        from pathlib import Path
        migrated = 0

        # 1. Error journal JSON
        journal_json = Path(MARLOW_DIR) / "memory" / "error_journal.json"
        if journal_json.exists():
            try:
                from marlow.core.error_journal import _journal
                import json
                data = json.loads(journal_json.read_text(encoding="utf-8"))
                if isinstance(data, list) and data:
                    count = _journal.import_json_entries(data)
                    journal_json.rename(
                        journal_json.with_suffix(".json.migrated"),
                    )
                    logger.info(
                        "Migrated error_journal.json: %d entries", count,
                    )
                    migrated += count
            except Exception as e:
                logger.warning("Error journal migration failed: %s", e)

        # 2. Memory category JSON files
        from marlow.tools.memory import import_json_to_sqlite, MEMORY_DIR
        for cat in ("general", "preferences", "projects", "tasks"):
            cat_file = MEMORY_DIR / f"{cat}.json"
            if cat_file.exists():
                try:
                    count = import_json_to_sqlite(state_db_path)
                    # Rename all category files after successful import
                    for c2 in ("general", "preferences", "projects", "tasks"):
                        f2 = MEMORY_DIR / f"{c2}.json"
                        if f2.exists():
                            f2.rename(f2.with_suffix(".json.migrated"))
                    logger.info(
                        "Migrated memory JSON files: %d entries", count,
                    )
                    migrated += count
                    break  # import_json_to_sqlite handles all categories
                except Exception as e:
                    logger.warning("Memory migration failed: %s", e)
                    break

        # 3. CDP knowledge JSON
        cdp_json = Path(MARLOW_DIR) / "cdp_knowledge.json"
        if cdp_json.exists() and self._app_knowledge:
            try:
                import json
                cdp_data = json.loads(cdp_json.read_text(encoding="utf-8"))
                if cdp_data:
                    count = await self._app_knowledge.import_from_cdp_knowledge(
                        cdp_data,
                    )
                    cdp_json.rename(
                        cdp_json.with_suffix(".json.migrated"),
                    )
                    logger.info(
                        "Migrated cdp_knowledge.json: %d apps", count,
                    )
                    migrated += count
            except Exception as e:
                logger.warning("CDP knowledge migration failed: %s", e)

        if migrated:
            logger.info("Total JSON->SQLite migration: %d entries", migrated)

    async def _start_maintenance(self):
        """Start periodic database maintenance background task."""
        if not self._db or not self._db.is_initialized:
            return
        try:
            from marlow.kernel.db.maintenance import DatabaseMaintenance
            self._maintenance = DatabaseMaintenance(self._db)
            await self._maintenance.start_background(interval_minutes=5)
            logger.info("Database maintenance started (every 5 min)")
        except Exception as e:
            logger.warning("Failed to start DB maintenance: %s", e)

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
                context_builder=self._get_dynamic_context,
            )
            logger.info(
                "Gemini text bridge ready: model=%s, user=%s",
                text_model, self._user_name,
            )
        except Exception as e:
            logger.error("Failed to init Gemini text bridge: %s", e)
            self._gemini_text = None

    def _init_claude_fallback(self):
        """Initialize Claude Sonnet as fallback when Gemini is unavailable."""
        try:
            from marlow.core.settings import get_settings
            settings = get_settings()
        except Exception:
            logger.warning("Cannot load settings for Claude fallback")
            return

        api_key = settings.secrets.anthropic_api_key
        if not api_key:
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            logger.info(
                "No Anthropic API key — Claude fallback disabled"
            )
            return

        try:
            import anthropic
            self._claude_client = anthropic.Anthropic(api_key=api_key)
            logger.info("Claude fallback ready (claude-sonnet-4-20250514)")
        except Exception as e:
            logger.error("Failed to init Claude fallback: %s", e)
            self._claude_client = None

    async def _execute_tool_direct(self, tool_name: str, args: dict) -> dict:
        """Execute a tool directly from the daemon tool map.

        Used by GeminiTextBridge as tool_executor callback.
        Same tools available as the voice path (via /tool endpoint).
        """
        # execute_complex_goal delegates to GoalEngine
        if tool_name == "execute_complex_goal":
            return await self._execute_complex_goal(args.get("goal", ""))

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

    async def _execute_complex_goal(self, goal_text: str) -> dict:
        """Delegate a complex goal to GoalEngine (Claude planner)."""
        if not self._marlow:
            return {"success": False, "error": "Agent not initialized"}
        if not goal_text:
            return {"success": False, "error": "No goal provided"}

        logger.info("Delegating to GoalEngine: %s", goal_text[:80])
        try:
            result = await self._marlow.execute(goal_text)
            return {
                "success": result.success,
                "steps_completed": result.steps_completed,
                "steps_total": result.steps_total,
                "duration_s": round(result.duration_s, 1),
                "summary": result.result_summary or (
                    "Task completed." if result.success else "Task failed."
                ),
                "errors": result.errors[:3] if result.errors else [],
            }
        except Exception as e:
            logger.error("GoalEngine delegation error: %s", e)
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
        """Process text: Gemini (3 retries) -> Claude Sonnet -> clean error.

        Unified entry point for ALL text interaction.
        Never returns raw JSON or GoalEngine output to the user.
        """
        logger.info("Processing text: '%s' (channel=%s)", goal_text[:60], channel)

        # ── Primary: Gemini with tools (3 attempts with backoff) ──
        if self._gemini_text:
            backoff = [1, 3, 6]
            for attempt in range(3):
                try:
                    response_text = await self._gemini_text.send_message(goal_text)
                    if response_text:
                        logger.info(
                            "Gemini response (attempt %d): %s",
                            attempt + 1, response_text[:100],
                        )
                        self._history.append(GoalRecord(
                            goal=goal_text, channel=channel,
                            status="completed", success=True,
                            result_summary=response_text,
                            started_at=time.time(), finished_at=time.time(),
                        ))
                        return {
                            "success": True, "status": "completed",
                            "goal": goal_text, "response": response_text,
                            "result_summary": response_text, "engine": "gemini",
                        }
                except Exception as e:
                    err_str = str(e).lower()
                    transient = any(k in err_str for k in (
                        "503", "unavailable", "overloaded",
                        "429", "resource_exhausted", "timeout", "connection",
                    ))
                    if transient and attempt < 2:
                        wait = backoff[attempt]
                        logger.warning(
                            "Gemini attempt %d/3 failed (%s), retry in %ds...",
                            attempt + 1, type(e).__name__, wait,
                        )
                        await asyncio.sleep(wait)
                    else:
                        logger.error(
                            "Gemini error (attempt %d): %s", attempt + 1, e,
                        )
                        break

            logger.info("Gemini exhausted, trying Claude fallback...")

        # ── Fallback: Claude Sonnet with function calling ──
        if self._claude_client:
            try:
                response_text = await self._claude_fallback(goal_text)
                if response_text:
                    logger.info("Claude response: %s", response_text[:100])
                    self._history.append(GoalRecord(
                        goal=goal_text, channel=channel,
                        status="completed", success=True,
                        result_summary=response_text,
                        started_at=time.time(), finished_at=time.time(),
                    ))
                    return {
                        "success": True, "status": "completed",
                        "goal": goal_text, "response": response_text,
                        "result_summary": response_text, "engine": "claude",
                    }
            except Exception as e:
                logger.error("Claude fallback failed: %s", e)

        # ── Last resort: clean error (never JSON, never GoalEngine) ──
        error_msg = {
            "es": ("Lo siento, no puedo procesar tu solicitud en este momento. "
                   "Intenta de nuevo en unos segundos."),
            "en": ("Sorry, I can't process your request right now. "
                   "Please try again in a few seconds."),
        }.get(self._language, "Sorry, I can't process your request right now.")

        self._history.append(GoalRecord(
            goal=goal_text, channel=channel,
            status="failed", success=False,
            result_summary=error_msg,
            started_at=time.time(), finished_at=time.time(),
        ))
        return {
            "success": False, "status": "failed",
            "goal": goal_text, "response": error_msg, "engine": "none",
        }

    async def _claude_fallback(self, text: str) -> str:
        """Fallback to Claude Sonnet with full function calling.

        Same tools and system prompt as Gemini. Max 8 rounds.
        """
        from marlow.bridges.tools_schema import (
            build_system_prompt, build_anthropic_tools, resolve_tool_call,
        )
        from marlow.kernel.adapters import inject_context_anthropic

        system_prompt = build_system_prompt(self._user_name, self._language)
        dynamic_ctx = self._get_dynamic_context()
        if dynamic_ctx:
            system_prompt = inject_context_anthropic(dynamic_ctx, system_prompt)
        tools = build_anthropic_tools()
        messages = [{"role": "user", "content": text}]

        for round_num in range(8):
            response = await asyncio.to_thread(
                self._claude_client.messages.create,
                model="claude-sonnet-4-20250514",
                max_tokens=500,
                system=system_prompt,
                tools=tools,
                messages=messages,
            )

            # Check for tool use
            tool_blocks = [b for b in response.content if b.type == "tool_use"]
            if not tool_blocks:
                parts = [
                    b.text for b in response.content
                    if hasattr(b, "text") and b.text
                ]
                return " ".join(parts) or (
                    "Listo." if self._language == "es" else "Done."
                )

            # Execute tools and continue conversation
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []

            for tb in tool_blocks:
                real_name, real_args = resolve_tool_call(
                    tb.name, dict(tb.input or {}),
                )
                logger.info(
                    "Claude tool [round %d]: %s(%s)",
                    round_num + 1, tb.name, tb.input,
                )
                try:
                    result = await self._execute_tool_direct(real_name, real_args)
                except Exception as e:
                    result = {"success": False, "error": str(e)}

                compact = self._compact_tool_result(result)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tb.id,
                    "content": json.dumps(compact),
                })

            messages.append({"role": "user", "content": tool_results})

        return "Listo." if self._language == "es" else "Done."

    @staticmethod
    def _compact_tool_result(result: dict) -> dict:
        """Compact a tool result for LLM consumption."""
        compact = {"success": result.get("success", False)}
        for key in ("error", "pid", "output", "windows", "result",
                     "window_id", "launched", "note", "text"):
            if key in result:
                val = result[key]
                if isinstance(val, str) and len(val) > 500:
                    val = val[:500] + "..."
                compact[key] = val
        if "windows" in compact and isinstance(compact["windows"], list):
            compact["windows"] = [
                {"id": w.get("id"), "title": w.get("title", "")[:80],
                 "app": w.get("app_id", "")}
                for w in compact["windows"][:20]
            ]
        return compact

    async def handle_goal(self, request: web.Request) -> web.Response:
        """POST /goal — Submit text for processing.

        All text goes to Gemini (with tools). Gemini decides:
        greet naturally, answer questions, or call tools for desktop actions.
        Falls back to Claude Sonnet if Gemini unavailable.
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

        # Initialize SQLite persistence layer
        await self._init_database()

        # Initialize AutonomousMarlow (tools + GoalEngine for complex goals)
        try:
            setup_result = self._init_marlow()
            failed = len(setup_result.get("failed", []))
            if failed:
                logger.warning("%d tools failed to register", failed)
        except Exception as e:
            logger.error("Failed to initialize AutonomousMarlow: %s", e)
            sys.exit(1)

        # Initialize dynamic context builder (feeds live state to LLM)
        self._init_context_builder()

        # Initialize Gemini text bridge (primary path for all text)
        self._init_gemini_text()

        # Initialize Claude Sonnet fallback
        self._init_claude_fallback()

        # Start database maintenance background task
        await self._start_maintenance()

        # Start goal queue worker (for execute_complex_goal)
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

        engine = "Gemini" if self._gemini_text else ("Claude" if self._claude_client else "none")
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

        # Close database connections
        if self._maintenance:
            try:
                await self._maintenance.stop()
            except Exception:
                pass
        if self._db:
            try:
                await self._db.close()
            except Exception as e:
                logger.warning("DB close error: %s", e)

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
