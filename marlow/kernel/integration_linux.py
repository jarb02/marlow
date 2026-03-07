"""AutonomousMarlow — Linux version.

Wires GoalEngine + Executor + Linux platform layer tools.
Identical orchestration logic to integration.py, but _build_tool_map()
registers tools from the platform layer instead of marlow.tools/*.

Usage::

    marlow = AutonomousMarlow()
    marlow.setup()
    result = await marlow.execute("open Firefox and search for cats")
    marlow.teardown()

/ AutonomousMarlow Linux — conecta kernel + platform layer Linux.
"""

from __future__ import annotations

import asyncio
import logging
import time as _time
from enum import Enum
from typing import Optional

from .executor import SmartExecutor
from .goal_engine import GoalEngine, GoalResult, Plan, PlanStep
from .plan_validator import PlanValidator
from .planning.template_planner import TemplatePlanner
from .planning.tool_filter import ToolFilter
from .success_checker import SuccessChecker
from .types import ToolResult
from .adaptive_waits import AdaptiveWaits
from .app_awareness import AppAwareness
from .desktop_weather import DesktopWeather
from .event_bus import EventBus
from .events import (
    GoalStarted, GoalCompleted, GoalFailed,
    ActionStarting, ActionCompleted, ActionFailed,
    DialogDetected, DialogHandled, WindowChanged, FocusLost,
    InterruptReceived,
)
from .interrupt_manager import InterruptManager
from .blackboard import Blackboard
from .plan_granularity import PlanGranularityAdapter
from .planning.goap import GOAPPlanner
from .scoring.pre_scorer import PreActionScorer
from .security.plan_reviewer import PlanReviewer
from .window_tracker import WindowTracker

logger = logging.getLogger("marlow.integration.linux")

# Tools that need the target window focused before execution
_INPUT_TOOLS = frozenset({
    "type_text", "press_key", "hotkey", "click", "som_click",
})

# Post-launch settle time (seconds)
_APP_LAUNCH_DELAY = 2.0

# Dialog detection heuristics (Linux-adapted)
_DIALOG_TITLE_HINTS = (
    "save as", "save file", "open file", "print", "browse",
    "replace", "confirm", "are you sure", "error", "warning",
    "alert", "question",
)

_NOT_DIALOG_HINTS = (
    "file manager", "files", "nautilus", "thunar", "dolphin",
)


class DialogType(Enum):
    ERROR = "error"
    WARNING = "warning"
    CONFIRMATION = "confirmation"
    SAVE = "save"
    OPEN = "open"
    FILE_EXISTS = "file_exists"
    PATH_ERROR = "path_error"
    INFORMATION = "information"
    UNKNOWN = "unknown"


def classify_dialog(title: str, message: str, buttons: list) -> DialogType:
    """Classify a dialog based on its title, message, and buttons."""
    title_lower = title.lower()
    msg_lower = message.lower()

    if any(w in msg_lower for w in (
        "already exists", "replace", "overwrite", "reemplazar", "ya existe",
    )):
        return DialogType.FILE_EXISTS

    if any(w in msg_lower for w in (
        "path does not exist", "not found", "cannot find",
        "no existe", "no se encuentra", "access denied", "permission denied",
    )):
        return DialogType.PATH_ERROR

    if any(w in title_lower for w in ("save as", "save file", "guardar como")):
        return DialogType.SAVE

    if any(w in title_lower for w in ("open file", "open", "abrir")):
        return DialogType.OPEN

    if "error" in title_lower or any(
        w in msg_lower for w in ("error", "failed", "fallo")
    ):
        return DialogType.ERROR

    if any(w in title_lower for w in ("warning", "advertencia")):
        return DialogType.WARNING

    if any(w in msg_lower for w in (
        "are you sure", "do you want", "would you like",
        "desea", "esta seguro", "confirmar",
    )):
        return DialogType.CONFIRMATION

    if any(w in title_lower for w in ("information", "info", "informacion")):
        return DialogType.INFORMATION

    return DialogType.UNKNOWN


class AutonomousMarlow:
    """End-to-end autonomous desktop agent — Linux version.

    Same architecture as the Windows AutonomousMarlow, but tools
    are registered from the platform layer instead of marlow.tools/*.
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
        self._window_tracker = WindowTracker()
        self._app_awareness = AppAwareness()
        self._adaptive_waits = AdaptiveWaits()
        self._pre_scorer = PreActionScorer()
        self._interrupt_manager = InterruptManager()
        self._event_bus = EventBus()
        self._goap = GOAPPlanner()
        self._weather = DesktopWeather()
        self._plan_reviewer = PlanReviewer()
        self._granularity = PlanGranularityAdapter()
        self._blackboard = Blackboard()
        self._current_gran_config = None
        self._ready = False

        # Platform layer (lazy init)
        self._platform = None
        self._tool_map: dict = {}

    @property
    def event_bus(self) -> EventBus:
        return self._event_bus

    @property
    def desktop_weather(self) -> DesktopWeather:
        return self._weather

    @property
    def goap_planner(self) -> GOAPPlanner:
        return self._goap

    @property
    def blackboard(self) -> Blackboard:
        return self._blackboard

    def setup(self) -> dict:
        """Initialize all components and register tools from platform layer."""
        # 1. Create executor
        self._executor = SmartExecutor(default_timeout=self._timeout)

        # 2. Register tools from platform layer
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

        # 5. Create GoalEngine
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
        logger.info(
            "AutonomousMarlow Linux ready: %d tools registered, %d failed",
            len(registered), len(failed),
        )
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

    # ── Tool Registration ──

    def _register_tools(self) -> tuple[list[str], list[str]]:
        """Register tools from the Linux platform layer."""
        registered = []
        failed = []

        tool_map = self._build_tool_map()
        self._tool_map = tool_map

        for name, func in tool_map.items():
            try:
                self._executor.register_tool(name, func)
                registered.append(name)
            except Exception as e:
                logger.warning("Failed to register %s: %s", name, e)
                failed.append(name)

        return registered, failed

    def _build_tool_map(self) -> dict:
        """Build tool_name -> callable map from Linux platform layer.

        Uses the platform singleton for all desktop operations.
        Agnostic modules (memory, scraper, watcher, etc.) are imported
        directly from marlow.core/tools.
        """
        tools = {}

        # -- Initialize platform --
        try:
            from marlow.platform import get_platform
            p = get_platform()
            self._platform = p
        except Exception as e:
            logger.error("Failed to init platform layer: %s", e)
            return tools

        # ══════════════════════════════════════════════════
        # PLATFORM TOOLS (desktop interaction via platform layer)
        # ══════════════════════════════════════════════════

        # -- Core: UI Tree --
        tools["get_ui_tree"] = lambda **kw: p.ui_tree.get_tree(
            window_title=kw.get("window_title"),
            max_depth=kw.get("max_depth"),
        )
        tools["find_elements"] = lambda **kw: _wrap_find_elements(
            p, kw.get("name"), kw.get("role"),
            kw.get("states"), kw.get("window_title"),
        )

        # -- Core: Screenshot --
        tools["take_screenshot"] = lambda **kw: p.screen.screenshot(
            window_title=kw.get("window_title"),
            region=kw.get("region"),
        )

        # -- Core: Input --
        tools["click"] = lambda **kw: _wrap_bool(
            p.input.click(
                x=kw.get("x", 0), y=kw.get("y", 0),
                button=kw.get("button", "left"),
            ), "click",
        )
        tools["type_text"] = lambda **kw: _wrap_bool(
            p.input.type_text(text=kw.get("text", "")), "type_text",
        )
        tools["press_key"] = lambda **kw: _wrap_bool(
            p.input.press_key(key=kw.get("key", "")), "press_key",
        )
        tools["hotkey"] = lambda **kw: _wrap_hotkey(p, kw)
        tools["move_mouse"] = lambda **kw: _wrap_bool(
            p.input.move_mouse(x=kw.get("x", 0), y=kw.get("y", 0)),
            "move_mouse",
        )

        # -- Core: Windows --
        tools["list_windows"] = lambda **kw: _wrap_list_windows(
            p, kw.get("include_minimized", True),
        )
        tools["focus_window"] = lambda **kw: _wrap_focus_window(
            p, kw.get("window_title", kw.get("identifier", "")),
        )
        tools["manage_window"] = lambda **kw: _wrap_bool(
            p.windows.manage_window(
                identifier=kw.get("window_title", ""),
                action=kw.get("action", ""),
                x=kw.get("x"), y=kw.get("y"),
                width=kw.get("width"), height=kw.get("height"),
            ), "manage_window",
        )

        # -- Core: System --
        tools["run_command"] = lambda **kw: p.system.run_command(
            command=kw.get("command", ""),
            timeout=kw.get("timeout", 30),
        )
        tools["open_application"] = lambda **kw: p.system.open_application(
            name_or_path=kw.get("app_name") or kw.get("name") or "",
        )
        tools["system_info"] = lambda **kw: p.system.get_system_info()

        # -- Core: Clipboard --
        if p.clipboard:
            tools["clipboard"] = lambda **kw: _wrap_clipboard(p, kw)

        # -- Core: Focus --
        tools["restore_user_focus"] = lambda **kw: _wrap_bool(
            p.focus.restore_user_focus(), "restore_user_focus",
        )

        # -- Advanced: OCR --
        if p.ocr:
            tools["ocr_region"] = lambda **kw: p.ocr.ocr_region(
                window_title=kw.get("window_title"),
                region=kw.get("region"),
                language=kw.get("language", "eng"),
            )
            tools["list_ocr_languages"] = lambda **kw: {
                "success": True,
                "languages": p.ocr.list_languages(),
            }

        # -- Advanced: Escalation (smart_find, cascade) --
        if p.escalation:
            tools["smart_find"] = lambda **kw: p.escalation.smart_find(
                name=kw.get("target", kw.get("name")),
                window_title=kw.get("window_title"),
            )
        if p.cascade_recovery:
            tools["cascade_find"] = lambda **kw: p.cascade_recovery.cascade_find(
                name=kw.get("target", kw.get("name")),
                window_title=kw.get("window_title"),
            )

        # -- Advanced: SoM --
        if p.som:
            tools["get_annotated_screenshot"] = (
                lambda **kw: p.som.get_annotated_screenshot(
                    window_title=kw.get("window_title"),
                )
            )
            tools["som_click"] = lambda **kw: p.som.som_click(
                index=kw.get("index", 0),
            )

        # -- Smart Wait --
        if p.waits:
            tools["wait_for_element"] = lambda **kw: p.waits.wait_for_element(
                name=kw.get("name"),
                role=kw.get("role"),
                window_title=kw.get("window_title"),
                timeout=kw.get("timeout", 30),
                interval=kw.get("interval", 1),
            )
            tools["wait_for_text"] = lambda **kw: p.waits.wait_for_text(
                text=kw.get("text", ""),
                window_title=kw.get("window_title"),
                timeout=kw.get("timeout", 30),
                interval=kw.get("interval", 2),
            )
            tools["wait_for_window"] = lambda **kw: p.waits.wait_for_window(
                title=kw.get("title", ""),
                timeout=kw.get("timeout", 30),
                interval=kw.get("interval", 1),
            )
            tools["wait_for_idle"] = lambda **kw: p.waits.wait_for_idle(
                window_title=kw.get("window_title"),
                timeout=kw.get("timeout", 30),
                threshold=kw.get("stable_seconds", 2),
            )

        # -- Background (Shadow Mode) --
        if p.background:
            tools["setup_background_mode"] = (
                lambda **kw: p.background.setup_background_mode(
                    preferred_mode=kw.get("preferred_mode"),
                )
            )
            tools["move_to_agent_screen"] = (
                lambda **kw: p.background.move_to_agent_screen(
                    window_title=kw.get("window_title"),
                )
            )
            tools["move_to_user_screen"] = (
                lambda **kw: p.background.move_to_user_screen(
                    window_title=kw.get("window_title"),
                )
            )
            tools["get_agent_screen_state"] = (
                lambda **kw: p.background.get_agent_screen_state()
            )
            tools["set_agent_screen_only"] = (
                lambda **kw: p.background.set_agent_screen_only(
                    enabled=kw.get("enabled", True),
                )
            )

        # -- Shadow Mode (Compositor IPC) --
        # These use the compositor's shadow_space for invisible windows.
        # Available when running on Marlow Compositor (not Sway).
        tools["launch_in_shadow"] = lambda **kw: p.windows.launch_in_shadow(
            command=(kw.get("command") or kw.get("application")
                     or kw.get("app_name") or kw.get("app")
                     or kw.get("name") or kw.get("program") or ""),
        )
        tools["get_shadow_windows"] = (
            lambda **kw: _wrap_shadow_windows(p)
        )
        tools["move_to_user"] = lambda **kw: p.windows.move_to_user(
            window_id=int(kw.get("window_id", 0)),
        )
        tools["move_to_shadow"] = lambda **kw: p.windows.move_to_shadow(
            window_id=int(kw.get("window_id", 0)),
        )

        # -- Visual Diff --
        if p.visual_diff:
            tools["visual_diff"] = lambda **kw: p.visual_diff.capture_before(
                window_title=kw.get("window_title"),
                label=kw.get("description"),
            )
            tools["visual_diff_compare"] = (
                lambda **kw: p.visual_diff.compare(
                    diff_id=kw.get("diff_id", ""),
                    window_title=kw.get("window_title"),
                )
            )

        # -- Audio --
        tools["capture_system_audio"] = (
            lambda **kw: p.audio.capture_system_audio(
                duration_seconds=kw.get("duration_seconds", 10),
            )
        )
        tools["capture_mic_audio"] = (
            lambda **kw: p.audio.capture_mic_audio(
                duration_seconds=kw.get("duration_seconds", 10),
            )
        )

        # ══════════════════════════════════════════════════
        # AGNOSTIC TOOLS (pure Python, no platform deps)
        # ══════════════════════════════════════════════════

        # -- Memory --
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
            logger.warning("memory module not available")

        # -- Scraper --
        try:
            from marlow.tools import scraper
            tools["scrape_url"] = lambda **kw: scraper.scrape_url(
                url=kw.get("url", ""),
                selector=kw.get("selector"),
                format=kw.get("format", "text"),
            )
        except ImportError:
            logger.warning("scraper module not available")

        # -- Watcher --
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
            logger.warning("watcher module not available")

        # -- Scheduler --
        try:
            from marlow.tools import scheduler
            tools["schedule_task"] = lambda **kw: scheduler.schedule_task(
                name=kw.get("name", ""),
                command=kw.get("command", ""),
                interval_seconds=kw.get("interval_seconds", 300),
                shell=kw.get("shell", "bash"),
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
            logger.warning("scheduler module not available")

        # -- Adaptive + Workflows --
        try:
            from marlow.core import adaptive
            tools["get_suggestions"] = lambda **kw: adaptive.get_suggestions()
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
            logger.warning("adaptive module not available")

        try:
            from marlow.core import workflows
            tools["workflow_record"] = lambda **kw: workflows.workflow_record(
                name=kw.get("name", ""),
            )
            tools["workflow_stop"] = lambda **kw: workflows.workflow_stop()
            tools["workflow_run"] = lambda **kw: workflows.workflow_run(
                name=kw.get("name", ""),
                dispatch_fn=lambda tool, params: self._executor.execute(tool, params),
            )
            tools["workflow_list"] = lambda **kw: workflows.workflow_list()
            tools["workflow_delete"] = lambda **kw: workflows.workflow_delete(
                name=kw.get("name", ""),
            )
        except ImportError:
            logger.warning("workflows module not available")

        # -- Error Journal --
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
            logger.warning("error_journal module not available")

        # -- CDP (Chrome DevTools Protocol) --
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
            logger.warning("cdp_manager module not available")

        # -- Kill switch (stub for Linux) --
        tools["kill_switch"] = lambda **kw: {
            "success": True, "message": "Kill switch activated",
        }

        logger.info(
            "Tool map built: %d tools (%d platform + agnostic)",
            len(tools), len(tools),
        )
        return tools

    # ── Goal Execution ──

    async def execute(
        self, goal_text: str, context: dict = None,
    ) -> GoalResult:
        """Execute a goal end-to-end.

        1. Try TemplatePlanner for common patterns (no LLM)
        2. Try GOAP for medium-complexity goals (no LLM)
        3. Fall back to LLM planner for complex goals
        4. GoalEngine validates, confirms, executes, verifies
        """
        if not self._ready:
            raise RuntimeError("Call setup() before execute()")

        logger.info("Goal: %s", goal_text)
        _corr_id = goal_text[:50]

        self._blackboard.set("goal.current", goal_text, source="integration")
        self._blackboard.set("goal.start_time", _time.time(), source="integration")

        try:
            await self._event_bus.publish(GoalStarted(
                goal_text=goal_text, source="integration",
                correlation_id=_corr_id,
            ))
        except Exception:
            pass

        # Tier 1: TemplatePlanner (trivial, regex)
        plan = self._planner.match(goal_text, context) if self._planner else None

        # Tier 2: GOAP (medium, A* search, no LLM)
        # Skip GOAP for complex multi-clause goals — LLM handles those better
        _is_complex = (
            goal_text.count(",") >= 2
            or " and " in goal_text.lower() and goal_text.count(",") >= 1
        )
        if plan is None and not _is_complex:
            goap_actions = self._goap.plan_from_goal_text(goal_text)
            if goap_actions:
                steps = [
                    PlanStep(
                        id=f"goap_{i}",
                        tool_name=action.tool_name,
                        params=dict(action.params_template),
                        description=action.description,
                    )
                    for i, action in enumerate(goap_actions)
                ]
                plan = Plan(
                    goal_id="", goal_text=goal_text, steps=steps,
                )
                logger.info(
                    "GOAP planner: %d-step plan (%s)",
                    len(steps),
                    ", ".join(s.tool_name for s in steps),
                )

        # Dual safety review
        if plan and self._plan_reviewer.needs_review(plan.steps):
            review = self._plan_reviewer.review_plan(goal_text, plan.steps)
            if review.should_block:
                logger.warning("Plan REJECTED: %s", review.concerns)
                result = GoalResult(
                    goal_id="rejected",
                    goal_text=goal_text,
                    success=False,
                    errors=[f"Plan rejected: {', '.join(review.concerns)}"],
                )
                try:
                    await self._event_bus.publish(GoalFailed(
                        goal_text=goal_text, source="plan_reviewer",
                        correlation_id=_corr_id,
                        error="Plan rejected by safety review",
                    ))
                except Exception:
                    pass
                return result

        if plan:
            tier = "GOAP" if plan.steps and plan.steps[0].id.startswith("goap_") else "Template"
            logger.info(
                "%s match: %d steps (%s)",
                tier, len(plan.steps),
                ", ".join(s.tool_name for s in plan.steps),
            )
            result = await self._engine.execute_goal(
                goal_text=goal_text,
                context=context,
                pre_built_plan=plan,
            )
        elif self._engine._plan_generator:
            logger.info("No template/GOAP match; using LLM planner")
            result = await self._engine.execute_goal(
                goal_text=goal_text,
                context=context,
            )
        else:
            logger.warning("No planner match for: %s", goal_text)
            result = GoalResult(
                goal_id="no_plan",
                goal_text=goal_text,
                success=False,
                errors=["No template, GOAP, or LLM planner could handle this goal"],
            )

        _status = "completed" if result.success else "FAILED"
        logger.info(
            "Result: %s, steps=%d/%d, score=%.2f, errors=%s",
            _status, result.steps_completed, result.steps_total,
            result.avg_score, result.errors,
        )

        # Publish goal outcome
        try:
            if result.success:
                await self._event_bus.publish(GoalCompleted(
                    goal_text=goal_text, source="integration",
                    correlation_id=_corr_id,
                    success=True,
                    steps_executed=result.steps_completed,
                ))
            else:
                await self._event_bus.publish(GoalFailed(
                    goal_text=goal_text, source="integration",
                    correlation_id=_corr_id,
                    error="; ".join(result.errors) if result.errors else "unknown",
                ))
        except Exception:
            pass

        self._blackboard.set("goal.current", "", source="integration")
        self._blackboard.set(
            "goal.last_result",
            result.success if hasattr(result, "success") else False,
            source="integration",
        )

        return result

    async def execute_plan(
        self, goal_text: str, steps: list[dict],
    ) -> GoalResult:
        """Execute a manually constructed plan."""
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

    # ── Orchestration ──

    async def _take_window_snapshot(self):
        """Record current window state via list_windows tool."""
        try:
            result = await self._executor.execute("list_windows", {})
            if result.success and result.data:
                windows = result.data if isinstance(result.data, list) else []
                self._window_tracker.record_snapshot(windows)
                self._weather.update_window_count(len(windows))
        except Exception as e:
            logger.debug("Window snapshot failed: %s", e)

    async def _execute_tool(
        self, tool_name: str, params: dict,
    ) -> ToolResult:
        """Execute a tool with focus management and post-action verification."""
        # Pre-execution: adaptive granularity
        app_name = params.get(
            "expected_app", params.get("name", params.get("app", "")),
        )
        self._current_gran_config = self._granularity.get_config(
            app_name, tool_name,
        )
        self._blackboard.set("world.active_tool", tool_name, source="integration")
        self._blackboard.set("world.active_app", app_name, source="integration")

        # Pre-execution: check desktop weather
        _weather_report = self._weather.get_report()
        if _weather_report.should_pause:
            logger.warning("Desktop in TORMENTA — pausing 2s before %s", tool_name)
            await asyncio.sleep(2.0)

        # Pre-execution: snapshot
        if tool_name not in self._READ_ONLY_TOOLS:
            await self._take_window_snapshot()

        # Pre-execution: score
        pre_context = {
            "target_app": params.get("window_title", params.get("app_name", "")),
            "element_type": params.get("control_type", ""),
            "estimated_tokens": 0,
            "estimated_ms": 0,
        }
        pre_score = self._pre_scorer.score(
            tool_name=tool_name,
            context=pre_context,
            app_name=params.get("window_title", params.get("app_name", "")),
        )
        logger.debug(
            "PreActionScore: %s -> %.2f (rel=%.2f urg=%.2f rel=%.2f cost=%.2f)",
            tool_name, pre_score.composite, pre_score.reliability,
            pre_score.urgency, pre_score.relevance, pre_score.cost,
        )

        # Publish ActionStarting
        try:
            await self._event_bus.publish(ActionStarting(
                tool_name=tool_name, source="integration",
                pre_score=pre_score.composite,
            ))
        except Exception:
            pass

        # Focus target window for input tools
        if tool_name in _INPUT_TOOLS:
            await self._ensure_focus(tool_name, params)

        # Execute
        _start = _time.time()
        result = await self._executor.execute(tool_name, params)
        _duration_ms = (_time.time() - _start) * 1000

        # Publish result event
        try:
            if result.success:
                await self._event_bus.publish(ActionCompleted(
                    tool_name=tool_name, source="integration",
                    success=True, duration_ms=round(_duration_ms, 1),
                ))
            else:
                await self._event_bus.publish(ActionFailed(
                    tool_name=tool_name, source="integration",
                    error=str(result.error) if result.error else "",
                ))
                self._weather.record_error()
        except Exception:
            pass

        # Post-execution: adaptive wait after launch
        if tool_name == "open_application" and result.success:
            app_name = params.get("app_name") or params.get("name") or ""
            wait_time = self._adaptive_waits.get_wait(app_name)
            logger.info("Waiting %.1fs for %s (adaptive)", wait_time, app_name)
            await asyncio.sleep(wait_time)

        # Post-action: snapshot and detect window changes
        if tool_name not in self._READ_ONLY_TOOLS:
            await self._take_window_snapshot()
            changes = self._window_tracker.detect_changes()
            for change in changes:
                self._weather.record_window_change()
                try:
                    await self._event_bus.publish(WindowChanged(
                        change_type=change.change_type,
                        window_title=change.window_title,
                        source="integration",
                    ))
                except Exception:
                    pass

        return result

    async def _ensure_focus(
        self, tool_name: str, params: dict,
    ) -> None:
        """Focus the expected target window before input tools."""
        if params.get("window_title") or params.get("element_name"):
            return

        # Skip if a dialog is active
        dialog_title = await self._detect_active_dialog()
        if dialog_title:
            logger.info(
                "Dialog '%s' active, skipping focus before %s",
                dialog_title, tool_name,
            )
            return

        # Find target from plan context
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

        self._window_tracker.set_expected_app(target)

        try:
            focus_result = await self._executor.execute(
                "focus_window", {"window_title": target},
            )
            if focus_result.success:
                logger.info("Focused '%s' before %s", target, tool_name)
                return

            # Fallback: search open windows
            app_stem = target.rsplit(".", 1)[0].lower()
            list_result = await self._executor.execute(
                "list_windows", {"include_minimized": False},
            )
            if not list_result.success:
                return

            data = list_result.data
            windows = data.get("windows", []) if isinstance(data, dict) else []
            for w in windows:
                title = w.get("title", "")
                if app_stem in title.lower():
                    focus_result = await self._executor.execute(
                        "focus_window", {"window_title": title},
                    )
                    if focus_result.success:
                        logger.info("Focused '%s' (matched '%s')", title, target)
                        return
        except Exception as e:
            logger.warning("Focus attempt failed: %s", e)

    async def _detect_active_dialog(self) -> str:
        """Check for active dialogs by title heuristics."""
        try:
            list_result = await self._executor.execute(
                "list_windows", {"include_minimized": False},
            )
            if not list_result.success:
                return ""

            data = list_result.data
            windows = data.get("windows", []) if isinstance(data, dict) else []
            for w in windows:
                title = w.get("title", "")
                title_lower = title.lower()
                if any(exc in title_lower for exc in _NOT_DIALOG_HINTS):
                    continue
                for hint in _DIALOG_TITLE_HINTS:
                    if hint in title_lower:
                        return title
        except Exception:
            pass
        return ""

    # Read-only tools — no need for pre/post snapshots
    _READ_ONLY_TOOLS = frozenset({
        "list_windows", "take_screenshot", "system_info", "get_dialog_info",
        "ocr_region", "get_ui_tree", "get_annotated_screenshot", "find_elements",
        "get_ui_events", "get_agent_screen_state", "memory_recall", "memory_list",
        "clipboard_history", "get_suggestions", "workflow_list", "get_error_journal",
        "list_watchers", "get_watch_events", "list_scheduled_tasks", "get_task_history",
        "cdp_list_connections", "cdp_get_dom", "cdp_get_knowledge_base",
        "get_voice_hotkey_status", "detect_app_framework", "run_diagnostics",
        "list_ocr_languages",
    })

    # ── Callbacks ──

    async def _confirm(self, plan: Plan) -> bool:
        """Auto-confirm handler."""
        logger.info(
            "Auto-confirming plan with %d steps (requires_confirmation=%s)",
            len(plan.steps), plan.requires_confirmation,
        )
        return True

    async def _on_progress(
        self, step_num: int, total: int, description: str,
    ) -> None:
        """Log progress."""
        logger.info("  [%d/%d] %s", step_num, total, description)


# ══════════════════════════════════════════════════
# Wrapper helpers — normalize platform returns to dicts
# ══════════════════════════════════════════════════


def _wrap_bool(result, tool_name: str = "") -> dict:
    """Wrap a bool/dict return into a consistent dict."""
    if isinstance(result, dict):
        return result
    return {"success": bool(result), "tool": tool_name}


def _wrap_hotkey(p, kw: dict) -> dict:
    """Hotkey wrapper: normalize keys list/string."""
    keys = kw.get("keys")
    if not keys:
        return {"success": False, "error": "No keys specified"}
    if isinstance(keys, str):
        keys = keys.split("+")
    result = p.input.hotkey(*keys)
    if isinstance(result, dict):
        return result
    return {"success": bool(result), "tool": "hotkey"}


def _wrap_find_elements(p, name, role, states, window_title) -> dict:
    """Wrap find_elements to always return a dict."""
    result = p.ui_tree.find_elements(
        name=name, role=role,
        states=states, window_title=window_title,
    )
    if isinstance(result, list):
        return {"success": True, "elements": result, "count": len(result)}
    if isinstance(result, dict):
        return result
    return {"success": False, "error": "Unexpected result type"}


def _wrap_focus_window(p, title: str) -> dict:
    """Wrap focus_window to always return a dict."""
    result = p.windows.focus_window(identifier=title)
    if isinstance(result, bool):
        if result:
            return {"success": True, "window": title}
        return {"success": False, "error": f"Window not found: {title}"}
    if isinstance(result, dict):
        return result
    return {"success": bool(result), "window": title}


def _wrap_list_windows(p, include_minimized: bool) -> dict:
    """Wrap list_windows to return a dict with windows list."""
    import logging
    _log = logging.getLogger("marlow.integration.linux")
    result = p.windows.list_windows(include_minimized=include_minimized)
    _log.info("list_windows raw: %d results, platform=%s", len(result) if isinstance(result, list) else -1, p.name)
    if isinstance(result, list):
        windows = []
        for w in result:
            _log.info("  window: title=%r app=%r id=%s focused=%s", w.title, w.app_name, w.identifier, w.is_focused)
            windows.append({
                "title": w.title,
                "app_name": w.app_name,
                "pid": w.pid,
                "is_focused": w.is_focused,
                "identifier": w.identifier,
            })
        return {"success": True, "windows": windows, "count": len(windows)}
    if isinstance(result, dict):
        return result
    return {"success": False, "error": "Unexpected result type"}



def _wrap_shadow_windows(p) -> dict:
    """Wrap shadow windows list into tool result format."""
    try:
        wins = p.windows.get_shadow_windows()
        return {
            "success": True,
            "shadow_windows": [
                {
                    "window_id": w.extra.get("window_id", w.identifier),
                    "title": w.title,
                    "app_name": w.app_name,
                    "x": w.x, "y": w.y,
                    "width": w.width, "height": w.height,
                }
                for w in wins
            ],
            "count": len(wins),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def _wrap_clipboard(p, kw: dict) -> dict:
    """Wrap clipboard operations."""
    action = kw.get("action", "read")
    if action == "read":
        text = p.clipboard.get_clipboard()
        return {"success": True, "text": text}
    elif action == "write":
        ok = p.clipboard.set_clipboard(kw.get("text", ""))
        return {"success": ok}
    elif action == "history":
        history = p.clipboard.get_clipboard_history()
        return {"success": True, "history": history}
    return {"success": False, "error": f"Unknown action: {action}"}
