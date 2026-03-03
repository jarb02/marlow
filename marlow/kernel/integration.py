"""AutonomousMarlow — wires GoalEngine + Executor + real Marlow tools.

Connects the kernel intelligence layer to the actual 96 MCP tool
implementations, enabling end-to-end goal execution on the real desktop.

Usage::

    marlow = AutonomousMarlow()
    marlow.setup()
    result = await marlow.execute("open Notepad")
    marlow.teardown()
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from .executor import SmartExecutor
from .goal_engine import GoalEngine, GoalResult, Plan, PlanStep
from .plan_validator import PlanValidator
from .planning.template_planner import TemplatePlanner
from .planning.tool_filter import ToolFilter
from .success_checker import SuccessChecker
from .types import ToolResult

logger = logging.getLogger("marlow.integration")

# Tools that need the target window focused before execution
_INPUT_TOOLS = frozenset({
    "type_text", "press_key", "hotkey", "click", "som_click",
})

# Post-launch settle time (seconds)
_APP_LAUNCH_DELAY = 2.0

# Window titles that indicate an active dialog (case-insensitive substrings)
_DIALOG_TITLE_HINTS = (
    "save as", "save file", "open", "print", "browse",
    "dialog", "upload", "download", "export", "import",
)


async def _safe_hotkey(keyboard_mod, **kw):
    """Hotkey wrapper: normalizes keys and rejects empty calls."""
    keys = kw.get("keys")
    if not keys:
        return {"error": "No keys specified"}
    # LLM may send "ctrl+s" (string) instead of ["ctrl", "s"] (list)
    if isinstance(keys, str):
        keys = keys.split("+")
    return await keyboard_mod.hotkey(*keys)


class AutonomousMarlow:
    """End-to-end autonomous desktop agent.

    Wires together:
    - **TemplatePlanner** for common goals (no LLM needed)
    - **GoalEngine** for plan lifecycle (validate, execute, verify, replan)
    - **SmartExecutor** for async tool dispatch with timeout and ToolResult wrapping
    - **Real Marlow tools** imported from marlow/tools/ and marlow/core/

    Parameters
    ----------
    * **timeout** (float):
        Per-tool execution timeout in seconds. Default 30.
    * **auto_confirm** (bool):
        If True, auto-approve plans requiring confirmation. Default True.
    * **llm_provider** (str or None):
        LLM provider name (``"anthropic"``, ``"openai"``, ``"gemini"``,
        ``"ollama"``).  When set, enables LLM-backed plan generation
        for goals that don't match any template.
    * **llm_model** (str):
        Override the provider's default model name.
    """

    def __init__(
        self,
        timeout: float = 30.0,
        auto_confirm: bool = True,
        llm_provider: str = None,
        llm_model: str = "",
    ):
        self._timeout = timeout
        self._auto_confirm = auto_confirm
        self._llm_provider_name = llm_provider
        self._llm_model = llm_model

        # Components (initialized in setup())
        self._executor: Optional[SmartExecutor] = None
        self._engine: Optional[GoalEngine] = None
        self._planner: Optional[TemplatePlanner] = None
        self._tool_filter: Optional[ToolFilter] = None
        self._ready = False

    def setup(self) -> dict:
        """Initialize all components and register real tools.

        Returns dict with registration summary.
        """
        # 1. Create executor
        self._executor = SmartExecutor(default_timeout=self._timeout)

        # 2. Register real Marlow tools
        registered, failed = self._register_tools()

        # 3. Create supporting components
        self._planner = TemplatePlanner()
        self._tool_filter = ToolFilter(
            all_tools=self._executor.available_tools,
        )
        checker = SuccessChecker()
        validator = PlanValidator(
            available_tools=self._executor.available_tools,
        )

        # 4. Optionally create LLM-backed plan generator
        plan_generator = None
        if self._llm_provider_name:
            try:
                from .cognition.providers import create_provider
                from .cognition.planner import LLMPlanner

                provider = create_provider(
                    self._llm_provider_name,
                    model=self._llm_model,
                )
                plan_generator = LLMPlanner(
                    provider=provider,
                    tool_filter=self._tool_filter,
                )
                logger.info(
                    "LLM planner enabled: %s (model=%s)",
                    self._llm_provider_name,
                    provider._config.model,
                )
            except Exception as e:
                logger.warning("Failed to init LLM planner: %s", e)

        # 5. Create GoalEngine (with orchestration wrapper)
        self._engine = GoalEngine(
            plan_generator=plan_generator,
            tool_executor=self._execute_tool,
            success_checker=checker,
            plan_validator=validator,
            confirmation_handler=self._confirm if self._auto_confirm else None,
            progress_callback=self._on_progress,
            available_tools=self._executor.available_tools,
        )

        self._ready = True
        return {
            "success": True,
            "registered": registered,
            "failed": failed,
            "total_tools": len(self._executor.available_tools),
        }

    def teardown(self):
        """Clean up resources."""
        if self._executor:
            self._executor.shutdown()
        self._ready = False

    async def execute(
        self, goal_text: str, context: dict = None,
    ) -> GoalResult:
        """Execute a goal end-to-end.

        1. Try TemplatePlanner for common patterns (no LLM)
        2. If matched, feed pre-built plan to GoalEngine
        3. GoalEngine validates, confirms, executes, verifies

        Returns GoalResult with success/failure details.
        """
        if not self._ready:
            raise RuntimeError("Call setup() before execute()")

        logger.info(f"Goal: {goal_text}")

        # Try template planner first (no LLM needed)
        plan = self._planner.match(goal_text, context)

        if plan:
            logger.info(
                f"Template match: {len(plan.steps)} steps "
                f"({', '.join(s.tool_name for s in plan.steps)})",
            )
            result = await self._engine.execute_goal(
                goal_text=goal_text,
                context=context,
                pre_built_plan=plan,
            )
        elif self._engine._plan_generator:
            # No template match — use LLM planner
            logger.info("No template match; using LLM planner")
            result = await self._engine.execute_goal(
                goal_text=goal_text,
                context=context,
            )
        else:
            # No template match and no LLM planner
            logger.warning(f"No template match for: {goal_text}")
            result = GoalResult(
                goal_id="no_plan",
                goal_text=goal_text,
                success=False,
                errors=["No template match and no LLM planner configured"],
            )

        logger.info(
            f"Result: success={result.success}, "
            f"steps={result.steps_completed}/{result.steps_total}, "
            f"score={result.avg_score}, errors={result.errors}",
        )
        return result

    async def execute_plan(
        self, goal_text: str, steps: list[dict],
    ) -> GoalResult:
        """Execute a manually constructed plan.

        Parameters
        ----------
        * **goal_text** (str): Human-readable goal description.
        * **steps** (list of dict): Each dict has:
            - tool_name (str): Tool to call
            - params (dict): Parameters for the tool
            - description (str, optional): Human-readable description
        """
        if not self._ready:
            raise RuntimeError("Call setup() before execute_plan()")

        plan_steps = [
            PlanStep(
                id=f"step_{i + 1}",
                tool_name=s["tool_name"],
                params=s.get("params", {}),
                description=s.get("description", f"Execute {s['tool_name']}"),
                risk=s.get("risk", "medium"),
                estimated_duration_ms=s.get("estimated_duration_ms", 3000),
            )
            for i, s in enumerate(steps)
        ]

        plan = Plan(
            goal_id="",
            goal_text=goal_text,
            steps=plan_steps,
        )

        return await self._engine.execute_goal(
            goal_text=goal_text,
            pre_built_plan=plan,
        )

    # ── Tool Registration ──

    def _register_tools(self) -> tuple[list[str], list[str]]:
        """Register real Marlow tool functions in the executor.

        Returns (registered_names, failed_names).
        """
        registered = []
        failed = []

        # Build tool map matching server.py's dispatch pattern.
        # Each tool is an async function — executor handles them natively.
        tool_map = self._build_tool_map()

        for name, func in tool_map.items():
            try:
                self._executor.register_tool(name, func)
                registered.append(name)
            except Exception as e:
                logger.warning(f"Failed to register {name}: {e}")
                failed.append(name)

        return registered, failed

    def _build_tool_map(self) -> dict:
        """Build tool_name -> async callable map from real Marlow modules.

        Imports are wrapped in try/except so missing optional deps
        don't break the entire integration.
        """
        tools = {}

        # ── Phase 1: Core ──
        try:
            from marlow.tools import ui_tree
            tools["get_ui_tree"] = lambda **kw: ui_tree.get_ui_tree(
                window_title=kw.get("window_title"),
                max_depth=kw.get("max_depth", "auto"),
                include_invisible=kw.get("include_invisible", False),
            )
        except ImportError:
            logger.warning("ui_tree not available")

        try:
            from marlow.tools import screenshot
            tools["take_screenshot"] = lambda **kw: screenshot.take_screenshot(
                window_title=kw.get("window_title"),
                region=kw.get("region"),
                quality=kw.get("quality", 85),
            )
        except ImportError:
            logger.warning("screenshot not available")

        try:
            from marlow.tools import mouse
            tools["click"] = lambda **kw: mouse.click(
                element_name=kw.get("element_name"),
                window_title=kw.get("window_title"),
                x=kw.get("x"),
                y=kw.get("y"),
                button=kw.get("button", "left"),
                double_click=kw.get("double_click", False),
            )
        except ImportError:
            logger.warning("mouse not available")

        try:
            from marlow.tools import keyboard
            tools["type_text"] = lambda **kw: keyboard.type_text(
                text=kw.get("text", ""),
                element_name=kw.get("element_name"),
                window_title=kw.get("window_title"),
                clear_first=kw.get("clear_first", False),
            )
            tools["press_key"] = lambda **kw: keyboard.press_key(
                key=kw.get("key", ""),
                times=kw.get("times", 1),
            )
            tools["hotkey"] = lambda **kw: _safe_hotkey(keyboard, **kw)
        except ImportError:
            logger.warning("keyboard not available")

        try:
            from marlow.tools import windows
            tools["list_windows"] = lambda **kw: windows.list_windows(
                include_minimized=kw.get("include_minimized", True),
            )
            tools["focus_window"] = lambda **kw: windows.focus_window(
                window_title=kw.get("window_title", ""),
            )
            tools["manage_window"] = lambda **kw: windows.manage_window(
                window_title=kw.get("window_title", ""),
                action=kw.get("action", ""),
                x=kw.get("x"),
                y=kw.get("y"),
                width=kw.get("width"),
                height=kw.get("height"),
            )
        except ImportError:
            logger.warning("windows not available")

        try:
            from marlow.tools import system
            tools["run_command"] = lambda **kw: system.run_command(
                command=kw.get("command", ""),
                shell=kw.get("shell", "powershell"),
                timeout=kw.get("timeout", 30),
            )
            tools["open_application"] = lambda **kw: system.open_application(
                app_name=kw.get("app_name") or kw.get("name"),
                app_path=kw.get("app_path"),
            )
            tools["clipboard"] = lambda **kw: system.clipboard(
                action=kw.get("action", "read"),
                text=kw.get("text"),
            )
            tools["system_info"] = lambda **kw: system.system_info()
        except ImportError:
            logger.warning("system not available")

        # ── Phase 2: Advanced ──
        try:
            from marlow.tools import ocr
            tools["ocr_region"] = lambda **kw: ocr.ocr_region(
                window_title=kw.get("window_title"),
                region=kw.get("region"),
                language=kw.get("language"),
                engine=kw.get("engine"),
            )
        except ImportError:
            logger.warning("ocr not available")

        try:
            from marlow.core import escalation
            tools["smart_find"] = lambda **kw: escalation.smart_find(
                target=kw.get("target", ""),
                window_title=kw.get("window_title"),
                click_if_found=kw.get("click_if_found", False),
            )
            tools["find_elements"] = lambda **kw: escalation.find_elements(
                query=kw.get("query", ""),
                window_title=kw.get("window_title"),
                control_type=kw.get("control_type"),
            )
        except ImportError:
            logger.warning("escalation not available")

        try:
            from marlow.core import cascade_recovery
            tools["cascade_find"] = lambda **kw: cascade_recovery.cascade_find(
                target=kw.get("target", ""),
                window_title=kw.get("window_title"),
                timeout=kw.get("timeout", 10),
            )
        except ImportError:
            logger.warning("cascade_recovery not available")

        try:
            from marlow.core import som
            tools["get_annotated_screenshot"] = (
                lambda **kw: som.get_annotated_screenshot(
                    window_title=kw.get("window_title"),
                    interactive_only=kw.get("interactive_only", True),
                )
            )
            tools["som_click"] = lambda **kw: som.som_click(
                index=kw.get("index", 0),
                window_title=kw.get("window_title"),
            )
        except ImportError:
            logger.warning("som not available")

        try:
            from marlow.core import focus
            tools["restore_user_focus"] = (
                lambda **kw: focus.restore_user_focus_tool()
            )
        except ImportError:
            logger.warning("focus not available")

        try:
            from marlow.core import app_detector
            tools["detect_app_framework"] = (
                lambda **kw: app_detector.detect_app_framework(
                    window_title=kw.get("window_title"),
                )
            )
        except ImportError:
            logger.warning("app_detector not available")

        # ── Smart Wait ──
        try:
            from marlow.tools import wait
            tools["wait_for_element"] = lambda **kw: wait.wait_for_element(
                name=kw.get("name", ""),
                window_title=kw.get("window_title"),
                timeout=kw.get("timeout", 30),
                interval=kw.get("interval", 1),
            )
            tools["wait_for_text"] = lambda **kw: wait.wait_for_text(
                text=kw.get("text", ""),
                window_title=kw.get("window_title"),
                timeout=kw.get("timeout", 30),
                interval=kw.get("interval", 2),
            )
            tools["wait_for_window"] = lambda **kw: wait.wait_for_window(
                title=kw.get("title", ""),
                timeout=kw.get("timeout", 30),
                interval=kw.get("interval", 1),
            )
            tools["wait_for_idle"] = lambda **kw: wait.wait_for_idle(
                window_title=kw.get("window_title"),
                timeout=kw.get("timeout", 30),
                stable_seconds=kw.get("stable_seconds", 2),
            )
        except ImportError:
            logger.warning("wait not available")

        # ── Phase 2: Background ──
        try:
            from marlow.tools import background
            tools["setup_background_mode"] = (
                lambda **kw: background.setup_background_mode(
                    preferred_mode=kw.get("preferred_mode"),
                )
            )
            tools["move_to_agent_screen"] = (
                lambda **kw: background.move_to_agent_screen(
                    window_title=kw.get("window_title", ""),
                )
            )
            tools["move_to_user_screen"] = (
                lambda **kw: background.move_to_user_screen(
                    window_title=kw.get("window_title", ""),
                )
            )
            tools["get_agent_screen_state"] = (
                lambda **kw: background.get_agent_screen_state()
            )
            tools["set_agent_screen_only"] = (
                lambda **kw: background.set_agent_screen_only(
                    enabled=kw.get("enabled", True),
                )
            )
        except ImportError:
            logger.warning("background not available")

        # ── Phase 2: Audio ──
        try:
            from marlow.tools import audio
            tools["capture_system_audio"] = (
                lambda **kw: audio.capture_system_audio(
                    duration_seconds=kw.get("duration_seconds", 10),
                )
            )
            tools["capture_mic_audio"] = (
                lambda **kw: audio.capture_mic_audio(
                    duration_seconds=kw.get("duration_seconds", 10),
                )
            )
            tools["transcribe_audio"] = (
                lambda **kw: audio.transcribe_audio(
                    audio_path=kw.get("audio_path", ""),
                    language=kw.get("language", "auto"),
                    model_size=kw.get("model_size", "base"),
                )
            )
            tools["download_whisper_model"] = (
                lambda **kw: audio.download_whisper_model(
                    model_size=kw.get("model_size", "base"),
                )
            )
        except ImportError:
            logger.warning("audio not available")

        # ── Phase 2: Voice ──
        try:
            from marlow.tools import voice
            tools["listen_for_command"] = (
                lambda **kw: voice.listen_for_command(
                    duration_seconds=kw.get("duration_seconds", 10),
                    language=kw.get("language", "auto"),
                    model_size=kw.get("model_size", "base"),
                )
            )
        except ImportError:
            logger.warning("voice not available")

        # ── Phase 2: COM Automation ──
        try:
            from marlow.tools import app_script
            tools["run_app_script"] = lambda **kw: app_script.run_app_script(
                app_name=kw.get("app_name", ""),
                script=kw.get("script", ""),
                timeout=kw.get("timeout", 30),
                visible=kw.get("visible", False),
            )
        except ImportError:
            logger.warning("app_script not available")

        # ── Phase 3: Visual Diff ──
        try:
            from marlow.tools import visual_diff
            tools["visual_diff"] = lambda **kw: visual_diff.visual_diff(
                window_title=kw.get("window_title"),
                description=kw.get("description", ""),
            )
            tools["visual_diff_compare"] = (
                lambda **kw: visual_diff.visual_diff_compare(
                    diff_id=kw.get("diff_id", ""),
                )
            )
        except ImportError:
            logger.warning("visual_diff not available")

        # ── Phase 3: Memory ──
        try:
            from marlow.tools import memory
            tools["memory_save"] = lambda **kw: memory.memory_save(
                key=kw.get("key", ""),
                value=kw.get("value", ""),
                category=kw.get("category", "general"),
            )
            tools["memory_recall"] = lambda **kw: memory.memory_recall(
                key=kw.get("key"),
                category=kw.get("category"),
            )
            tools["memory_delete"] = lambda **kw: memory.memory_delete(
                key=kw.get("key", ""),
                category=kw.get("category", "general"),
            )
            tools["memory_list"] = lambda **kw: memory.memory_list()
        except ImportError:
            logger.warning("memory not available")

        # ── Phase 3: Clipboard History ──
        try:
            from marlow.tools import clipboard_ext
            tools["clipboard_history"] = (
                lambda **kw: clipboard_ext.clipboard_history(
                    action=kw.get("action", "list"),
                    search=kw.get("search"),
                    limit=kw.get("limit", 20),
                )
            )
        except ImportError:
            logger.warning("clipboard_ext not available")

        # ── Phase 3: Scraper ──
        try:
            from marlow.tools import scraper
            tools["scrape_url"] = lambda **kw: scraper.scrape_url(
                url=kw.get("url", ""),
                selector=kw.get("selector"),
                format=kw.get("format", "text"),
            )
        except ImportError:
            logger.warning("scraper not available")

        # ── Phase 4: Watcher ──
        try:
            from marlow.tools import watcher
            tools["watch_folder"] = lambda **kw: watcher.watch_folder(
                path=kw.get("path", ""),
                events=kw.get("events"),
                recursive=kw.get("recursive", False),
            )
            tools["unwatch_folder"] = lambda **kw: watcher.unwatch_folder(
                watch_id=kw.get("watch_id", ""),
            )
            tools["get_watch_events"] = lambda **kw: watcher.get_watch_events(
                watch_id=kw.get("watch_id"),
                limit=kw.get("limit", 50),
                since=kw.get("since"),
            )
            tools["list_watchers"] = lambda **kw: watcher.list_watchers()
        except ImportError:
            logger.warning("watcher not available")

        # ── Phase 4: Scheduler ──
        try:
            from marlow.tools import scheduler
            tools["schedule_task"] = lambda **kw: scheduler.schedule_task(
                name=kw.get("name", ""),
                command=kw.get("command", ""),
                interval_seconds=kw.get("interval_seconds", 300),
                shell=kw.get("shell", "powershell"),
                max_runs=kw.get("max_runs"),
            )
            tools["list_scheduled_tasks"] = (
                lambda **kw: scheduler.list_scheduled_tasks()
            )
            tools["remove_task"] = lambda **kw: scheduler.remove_task(
                task_name=kw.get("task_name", ""),
            )
            tools["get_task_history"] = lambda **kw: scheduler.get_task_history(
                task_name=kw.get("task_name"),
                limit=kw.get("limit", 20),
            )
        except ImportError:
            logger.warning("scheduler not available")

        # ── Phase 5: TTS ──
        try:
            from marlow.tools import tts
            tools["speak"] = lambda **kw: tts.speak(
                text=kw.get("text", ""),
                language=kw.get("language", "auto"),
                voice=kw.get("voice"),
                rate=kw.get("rate", 175),
            )
            tools["speak_and_listen"] = (
                lambda **kw: tts.speak_and_listen(
                    text=kw.get("text", ""),
                    timeout=kw.get("timeout", 10),
                    language=kw.get("language", "auto"),
                    voice=kw.get("voice"),
                )
            )
        except ImportError:
            logger.warning("tts not available")

        # ── Voice Hotkey ──
        try:
            from marlow.core import voice_hotkey
            tools["get_voice_hotkey_status"] = (
                lambda **kw: voice_hotkey.get_voice_hotkey_status()
            )
        except ImportError:
            logger.warning("voice_hotkey not available")

        # ── Adaptive + Workflows ──
        try:
            from marlow.core import adaptive
            tools["get_suggestions"] = (
                lambda **kw: adaptive.get_suggestions()
            )
            tools["accept_suggestion"] = (
                lambda **kw: adaptive.accept_suggestion(
                    pattern_id=kw.get("pattern_id", ""),
                )
            )
            tools["dismiss_suggestion"] = (
                lambda **kw: adaptive.dismiss_suggestion(
                    pattern_id=kw.get("pattern_id", ""),
                )
            )
        except ImportError:
            logger.warning("adaptive not available")

        try:
            from marlow.core import workflows
            tools["workflow_record"] = lambda **kw: workflows.workflow_record(
                name=kw.get("name", ""),
            )
            tools["workflow_stop"] = lambda **kw: workflows.workflow_stop()
            tools["workflow_list"] = lambda **kw: workflows.workflow_list()
            tools["workflow_delete"] = lambda **kw: workflows.workflow_delete(
                name=kw.get("name", ""),
            )
        except ImportError:
            logger.warning("workflows not available")

        # ── Error Journal ──
        try:
            from marlow.core import error_journal
            tools["get_error_journal"] = (
                lambda **kw: error_journal.get_error_journal(
                    window=kw.get("window"),
                )
            )
            tools["clear_error_journal"] = (
                lambda **kw: error_journal.clear_error_journal(
                    window=kw.get("window"),
                )
            )
        except ImportError:
            logger.warning("error_journal not available")

        # ── Monitor (UIA Events + Dialog Handler) ──
        try:
            from marlow.core import uia_events
            tools["start_ui_monitor"] = (
                lambda **kw: uia_events.start_ui_monitor()
            )
            tools["stop_ui_monitor"] = (
                lambda **kw: uia_events.stop_ui_monitor()
            )
            tools["get_ui_events"] = lambda **kw: uia_events.get_ui_events(
                event_type=kw.get("event_type"),
                limit=kw.get("limit", 20),
                since=kw.get("since"),
            )
        except ImportError:
            logger.warning("uia_events not available")

        try:
            from marlow.core import dialog_handler
            tools["handle_dialog"] = lambda **kw: dialog_handler.handle_dialog(
                action=kw.get("action", "report"),
                window_title=kw.get("window_title"),
            )
            tools["get_dialog_info"] = (
                lambda **kw: dialog_handler.get_dialog_info(
                    window_title=kw.get("window_title", ""),
                )
            )
        except ImportError:
            logger.warning("dialog_handler not available")

        # ── CDP ──
        try:
            from marlow.core import cdp_manager
            tools["cdp_discover"] = lambda **kw: cdp_manager.cdp_discover(
                port_start=kw.get("port_start", 9222),
                port_end=kw.get("port_end", 9250),
            )
            tools["cdp_connect"] = lambda **kw: cdp_manager.cdp_connect(
                port=kw.get("port", 9222),
            )
            tools["cdp_disconnect"] = lambda **kw: cdp_manager.cdp_disconnect(
                port=kw.get("port", 9222),
            )
            tools["cdp_list_connections"] = (
                lambda **kw: cdp_manager.cdp_list_connections()
            )
            tools["cdp_send"] = lambda **kw: cdp_manager.cdp_send(
                port=kw.get("port", 9222),
                method=kw.get("method", ""),
                params=kw.get("params"),
            )
            tools["cdp_click"] = lambda **kw: cdp_manager.cdp_click(
                port=kw.get("port", 9222),
                x=kw.get("x", 0),
                y=kw.get("y", 0),
            )
            tools["cdp_type_text"] = lambda **kw: cdp_manager.cdp_type_text(
                port=kw.get("port", 9222),
                text=kw.get("text", ""),
            )
            tools["cdp_key_combo"] = lambda **kw: cdp_manager.cdp_key_combo(
                port=kw.get("port", 9222),
                key=kw.get("key", ""),
                modifiers=kw.get("modifiers"),
            )
            tools["cdp_screenshot"] = lambda **kw: cdp_manager.cdp_screenshot(
                port=kw.get("port", 9222),
                format=kw.get("format", "png"),
            )
            tools["cdp_evaluate"] = lambda **kw: cdp_manager.cdp_evaluate(
                port=kw.get("port", 9222),
                expression=kw.get("expression", ""),
            )
            tools["cdp_get_dom"] = lambda **kw: cdp_manager.cdp_get_dom(
                port=kw.get("port", 9222),
                depth=kw.get("depth", -1),
            )
            tools["cdp_click_selector"] = (
                lambda **kw: cdp_manager.cdp_click_selector(
                    port=kw.get("port", 9222),
                    css_selector=kw.get("css_selector", ""),
                )
            )
            tools["cdp_ensure"] = lambda **kw: cdp_manager.cdp_ensure(
                app_name=kw.get("app_name", ""),
                preferred_port=kw.get("preferred_port"),
            )
            tools["cdp_restart_confirmed"] = (
                lambda **kw: cdp_manager.cdp_restart_confirmed(
                    app_name=kw.get("app_name", ""),
                    port=kw.get("port"),
                )
            )
            tools["cdp_get_knowledge_base"] = (
                lambda **kw: cdp_manager.cdp_get_knowledge_base()
            )
        except ImportError:
            logger.warning("cdp_manager not available")

        # ── Voice Overlay ──
        try:
            from marlow.core import voice_overlay
            tools["toggle_voice_overlay"] = (
                lambda **kw: voice_overlay.toggle_voice_overlay(
                    visible=kw.get("visible", True),
                )
            )
        except ImportError:
            logger.warning("voice_overlay not available")

        # ── Diagnostics ──
        try:
            from marlow.core import setup_wizard
            tools["run_diagnostics"] = (
                lambda **kw: setup_wizard.run_diagnostics()
            )
        except ImportError:
            logger.warning("setup_wizard not available")

        return tools

    # ── Orchestration ──

    async def _execute_tool(
        self, tool_name: str, params: dict,
    ) -> ToolResult:
        """Execute a tool with focus management, post-launch wait, and
        active verification.

        This wraps ``SmartExecutor.execute`` to add:
        1. Auto-focus the target app before input tools
        2. Post-launch delay after ``open_application``
        3. Post-action check for unexpected dialogs (Tier 7A)
        """
        # Pre-execution: focus target window for input tools
        if tool_name in _INPUT_TOOLS:
            await self._ensure_focus(tool_name, params)

        # Execute
        result = await self._executor.execute(tool_name, params)

        # Post-execution: wait after launching an app
        if (
            tool_name == "open_application"
            and result.success
        ):
            logger.info(
                "Waiting %.1fs for app to launch...", _APP_LAUNCH_DELAY,
            )
            await asyncio.sleep(_APP_LAUNCH_DELAY)

        # Post-action: active verification (Tier 7A)
        post_check = await self._post_action_check(tool_name, params, result)
        if post_check.get("error_dialog"):
            logger.warning(
                "Dialog detected after %s: %s",
                tool_name, post_check.get("dialog_title", ""),
            )
            handled = await self._handle_unexpected_dialog(post_check)
            if handled.get("retry"):
                result = await self._executor.execute(tool_name, params)

        return result

    async def _ensure_focus(
        self, tool_name: str, params: dict,
    ) -> None:
        """Focus the expected target window before input tools.

        Skips focusing when a dialog is likely active (e.g. after
        Ctrl+S opens Save As) — re-focusing the parent app would
        steal focus from the dialog.

        Resolves window_title from:
        1. ``params["window_title"]`` (explicit)
        2. Current plan step's ``expected_app``
        3. Plan context's ``target_app`` / ``target_window``

        If ``focus_window`` fails with the raw value (e.g. ``"notepad.exe"``),
        falls back to listing windows and matching the app name against
        real window titles.
        """
        # Already has an explicit target — tool handles it
        if params.get("window_title") or params.get("element_name"):
            return

        # Skip if a dialog window is currently open
        dialog_title = await self._detect_active_dialog()
        if dialog_title:
            logger.info(
                "Dialog detected ('%s'), skipping focus before %s",
                dialog_title, tool_name,
            )
            return

        # Find the target from plan context
        target = None
        if self._engine and self._engine.plan:
            step_idx = self._engine.current_step
            steps = self._engine.plan.steps
            if 0 <= step_idx < len(steps):
                target = steps[step_idx].expected_app
            if not target:
                target = (
                    self._engine.plan.context.get("target_window")
                    or self._engine.plan.context.get("target_app")
                )

        if not target:
            return

        try:
            # Attempt 1: try the value as-is (works for real titles)
            focus_result = await self._executor.execute(
                "focus_window", {"window_title": target},
            )
            if focus_result.success:
                logger.info("Focused '%s' before %s", target, tool_name)
                return

            # Attempt 2: strip ".exe" and search open windows
            app_stem = target.rsplit(".", 1)[0].lower()
            list_result = await self._executor.execute(
                "list_windows", {"include_minimized": False},
            )
            if not list_result.success:
                return

            windows = list_result.data.get("windows", [])
            for w in windows:
                title = w.get("title", "")
                if app_stem in title.lower():
                    focus_result = await self._executor.execute(
                        "focus_window", {"window_title": title},
                    )
                    if focus_result.success:
                        logger.info(
                            "Focused '%s' (matched '%s') before %s",
                            title, target, tool_name,
                        )
                        return

            logger.warning(
                "Could not find window for '%s'", target,
            )
        except Exception as e:
            logger.warning("Focus attempt failed: %s", e)

    async def _detect_active_dialog(self) -> str:
        """Check open windows for an active dialog.

        Returns the dialog title if found, empty string otherwise.
        """
        try:
            list_result = await self._executor.execute(
                "list_windows", {"include_minimized": False},
            )
            if not list_result.success:
                return ""

            for w in list_result.data.get("windows", []):
                title = w.get("title", "")
                title_lower = title.lower()
                for hint in _DIALOG_TITLE_HINTS:
                    if hint in title_lower:
                        return title
        except Exception:
            pass
        return ""

    # ── Active Verification (Tier 7A) ──

    # Tools that don't change desktop state — no need to verify after them
    _READ_ONLY_TOOLS = frozenset({
        "list_windows", "take_screenshot", "system_info", "get_dialog_info",
        "ocr_region", "get_ui_tree", "get_annotated_screenshot", "find_elements",
        "get_ui_events", "get_agent_screen_state", "memory_recall", "memory_list",
        "clipboard_history", "get_suggestions", "workflow_list", "get_error_journal",
        "list_watchers", "get_watch_events", "list_scheduled_tasks", "get_task_history",
        "cdp_list_connections", "cdp_get_dom", "cdp_get_knowledge_base",
        "get_voice_hotkey_status", "detect_app_framework", "run_diagnostics",
    })

    async def _post_action_check(
        self, tool_name: str, params: dict, result: ToolResult,
    ) -> dict:
        """Look at the screen after every action to see what happened.

        Returns dict with findings.  Skips checks for read-only tools
        (they don't change state).
        """
        check: dict = {
            "error_dialog": False,
            "dialog_title": "",
            "dialog_message": "",
            "dialog_buttons": [],
        }

        if tool_name in self._READ_ONLY_TOOLS:
            return check

        # Small wait for UI to settle
        await asyncio.sleep(0.3)

        # Check: did an error/confirmation dialog appear?
        try:
            dialog_title = await self._detect_active_dialog()
            if not dialog_title:
                return check

            # Get full dialog info using the detected title
            dialog_result = await self._executor.execute(
                "get_dialog_info", {"window_title": dialog_title},
            )
            if dialog_result.success and isinstance(dialog_result.data, dict):
                data = dialog_result.data
                check["error_dialog"] = True
                check["dialog_title"] = data.get("window_title", dialog_title)
                # Combine all text lines into a single message
                texts = data.get("texts", [])
                check["dialog_message"] = " ".join(
                    t.get("text", "") if isinstance(t, dict) else str(t)
                    for t in texts
                ).strip()
                check["dialog_buttons"] = data.get("button_names", [])
                check["dialog_type"] = data.get("dialog_type", "")
                check["suggested_action"] = data.get("suggested_action", "")
        except Exception:
            pass

        return check

    async def _handle_unexpected_dialog(self, dialog_info: dict) -> dict:
        """Handle an unexpected dialog that appeared after an action.

        Returns ``{"retry": bool, "action_taken": str}``.
        """
        title = dialog_info.get("dialog_title", "").lower()
        message = dialog_info.get("dialog_message", "").lower()
        buttons = dialog_info.get("dialog_buttons", [])

        # Case 1: "File already exists, replace?" → Accept
        if any(
            w in message
            for w in ("already exists", "replace", "overwrite", "reemplazar")
        ):
            logger.info("Handling 'file exists' dialog — clicking Yes/Replace")
            try:
                await self._executor.execute(
                    "handle_dialog", {"action": "accept"},
                )
                return {"retry": False, "action_taken": "accepted_replace"}
            except Exception:
                await self._executor.execute(
                    "press_key", {"key": "enter"},
                )
                return {"retry": False, "action_taken": "pressed_enter"}

        # Case 2: "Path does not exist" → Dismiss and let replan handle it
        if any(
            w in message
            for w in ("path does not exist", "not found", "cannot find", "no existe")
        ):
            logger.warning("Path error dialog: %s", message)
            await self._executor.execute("press_key", {"key": "enter"})
            return {"retry": False, "action_taken": "dismissed_path_error"}

        # Case 3: Error / warning dialog → Dismiss
        if any(w in title for w in ("error", "warning", "alert")):
            logger.warning("Error dialog: %s — %s", title, message)
            await self._executor.execute("press_key", {"key": "enter"})
            return {"retry": False, "action_taken": "dismissed_error"}

        # Case 4: Confirmation dialog → Accept (safe default)
        if any(
            w in message
            for w in (
                "are you sure", "do you want", "would you like",
                "¿desea", "¿está seguro",
            )
        ):
            logger.info("Confirmation dialog: %s — accepting", message[:80])
            await self._executor.execute("press_key", {"key": "enter"})
            return {"retry": False, "action_taken": "accepted_confirmation"}

        # Case 5: Unknown dialog → Try to dismiss with Escape
        logger.warning(
            "Unknown dialog: %s — %s. Pressing Escape.", title, message[:80],
        )
        await self._executor.execute("press_key", {"key": "escape"})
        return {"retry": False, "action_taken": "escaped_unknown"}

    # ── Callbacks ──

    async def _confirm(self, plan: Plan) -> bool:
        """Auto-confirm handler."""
        logger.info(
            f"Auto-confirming plan with {len(plan.steps)} steps "
            f"(requires_confirmation={plan.requires_confirmation})",
        )
        return True

    async def _on_progress(
        self, step_num: int, total: int, description: str,
    ) -> None:
        """Log progress."""
        logger.info(f"  [{step_num}/{total}] {description}")
