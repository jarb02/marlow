"""ExecutionPipeline — unified tool execution with security, observability, learning.

Every tool execution (Gemini direct, Claude fallback, GoalEngine, future
ProactiveEngine) passes through this single pipeline. Modeled on the 14
subsystems from integration_linux._execute_tool(), now available to all paths.

Usage::

    pipeline = ExecutionPipeline(
        tool_map=tool_map,
        security_gate=gate,
        event_bus=bus,
        ...
    )
    result = await pipeline.execute("open_application", {"app_name": "firefox"}, origin="gemini")

/ Pipeline unificado de ejecucion de herramientas.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger("marlow.kernel.execution_pipeline")

# Tools that need the target window focused before execution
_INPUT_TOOLS = frozenset({
    "type_text", "press_key", "hotkey", "click", "som_click",
})

# Read-only tools skip window snapshots
_READ_ONLY_TOOLS = frozenset({
    "list_windows", "take_screenshot", "system_info", "get_ui_tree",
    "find_elements", "ocr_region", "list_ocr_languages",
    "get_annotated_screenshot", "smart_find", "cascade_find",
    "detect_app_framework", "get_dialog_info", "get_ui_events",
    "get_error_journal", "clear_error_journal", "get_suggestions",
    "accept_suggestion", "dismiss_suggestion", "get_agent_screen_state",
    "visual_diff", "visual_diff_compare", "wait_for_element",
    "wait_for_text", "wait_for_window", "wait_for_idle",
    "memory_recall", "memory_list", "list_watchers",
    "list_scheduled_tasks", "get_task_history", "workflow_list",
    "run_diagnostics", "clipboard_history", "extensions_list",
    "extensions_audit", "get_voice_hotkey_status", "get_shadow_windows",
    "cdp_discover", "cdp_list_connections", "cdp_screenshot",
    "cdp_get_dom", "cdp_get_knowledge_base", "demo_status", "demo_list",
})


@dataclass
class PipelineResult:
    """Result of a pipeline execution."""
    success: bool
    data: Any = None
    error: str = ""
    duration_ms: float = 0.0
    origin: str = ""
    tool_name: str = ""
    trust_level: int = 0
    interrupted: bool = False
    interrupt_priority: int = -1

    def to_dict(self) -> dict:
        """Convert to dict for daemon responses."""
        d = {
            "success": self.success,
            "duration_ms": round(self.duration_ms, 1),
            "origin": self.origin,
            "tool_name": self.tool_name,
            "trust_level": self.trust_level,
        }
        if self.data is not None:
            if isinstance(self.data, dict):
                d.update(self.data)
            else:
                d["result"] = str(self.data)
        if self.error:
            d["error"] = self.error
        if self.interrupted:
            d["interrupted"] = True
            d["interrupt_priority"] = self.interrupt_priority
        return d


class ExecutionPipeline:
    """Unified tool execution pipeline with security, observability, learning.

    All subsystems are Optional. If None, that step is skipped.
    Only security_gate and tool_map are blocking — everything else is
    fault-tolerant (errors are logged as warnings, pipeline continues).

    Parameters
    ----------
    tool_map : dict[str, Callable]
        Tool name -> callable mapping.
    security_gate : Optional
        SecurityGate instance for pre-execution checks.
    event_bus : Optional
        EventBus for publishing action events.
    pre_scorer : Optional
        PreActionScorer for pre-action scoring.
    desktop_weather : Optional
        DesktopWeather for environment assessment.
    blackboard : Optional
        Blackboard for shared key-value state.
    window_tracker : Optional
        WindowTracker for window change detection.
    interrupt_manager : Optional
        InterruptManager for interrupt classification.
    memory : Optional
        MemorySystem for short-term action recording.
    knowledge : Optional
        AppKnowledgeManager for app reliability tracking.
    adaptive_waits : Optional
        AdaptiveWaits for post-launch delays.
    focus_handler : Optional[Callable]
        Async callable for focusing windows before input tools.
        Signature: async focus_handler(tool_name, params) -> None
    snapshot_handler : Optional[Callable]
        Async callable for taking window snapshots.
        Signature: async snapshot_handler() -> None
    """

    # Reliability threshold: below this, proactive actions are blocked
    RELIABILITY_BLOCK_THRESHOLD = 0.3

    def __init__(
        self,
        tool_map: dict[str, Callable],
        security_gate=None,
        event_bus=None,
        pre_scorer=None,
        desktop_weather=None,
        blackboard=None,
        window_tracker=None,
        interrupt_manager=None,
        memory=None,
        knowledge=None,
        adaptive_waits=None,
        error_journal=None,
        focus_handler: Optional[Callable] = None,
        snapshot_handler: Optional[Callable] = None,
    ):
        self._tool_map = tool_map
        self._security_gate = security_gate
        self._event_bus = event_bus
        self._pre_scorer = pre_scorer
        self._weather = desktop_weather
        self._blackboard = blackboard
        self._window_tracker = window_tracker
        self._interrupt_mgr = interrupt_manager
        self._memory = memory
        self._knowledge = knowledge
        self._adaptive_waits = adaptive_waits
        self._error_journal = error_journal
        self._focus_handler = focus_handler
        self._snapshot_handler = snapshot_handler

    async def execute(
        self,
        tool_name: str,
        params: dict,
        origin: str = "user",
        goal_id: str = "",
    ) -> PipelineResult:
        """Execute a tool through the full pipeline.

        PRE:  SecurityGate, Blackboard, DesktopWeather, PreActionScorer, EventBus
        EXEC: focus + tool_map[name](**params)
        POST: EventBus, Weather, Memory, Knowledge, AdaptiveWaits, WindowTracker, InterruptManager
        """
        start = time.time()

        # ── Resolve tool ──
        if tool_name not in self._tool_map:
            # launch_in_shadow fallback to open_application
            if tool_name == "launch_in_shadow" and "open_application" in self._tool_map:
                logger.info("launch_in_shadow not available, trying open_application")
                fallback_params = {"app_name": params.get("command", "")}
                result = await self.execute(
                    "open_application", fallback_params, origin=origin, goal_id=goal_id,
                )
                if result.success and isinstance(result.data, dict):
                    result.data["note"] = "Opened visibly (shadow mode unavailable)"
                return result
            return PipelineResult(
                success=False,
                error=f"Unknown tool: {tool_name}",
                tool_name=tool_name,
                origin=origin,
            )

        # ══════════════════════════════════════════════════
        # PRE-EXECUTION
        # ══════════════════════════════════════════════════

        # 1. SecurityGate (blocking)
        if self._security_gate:
            try:
                sec = await self._security_gate.check(
                    tool_name, params, origin=origin, goal_id=goal_id,
                )
                if not sec.allowed:
                    logger.warning(
                        "SecurityGate blocked %s (origin=%s): %s",
                        tool_name, origin, sec.reason,
                    )
                    return PipelineResult(
                        success=False,
                        error=sec.reason,
                        tool_name=tool_name,
                        origin=origin,
                        trust_level=sec.trust_level,
                    )
                params = sec.sanitized_params or params
            except Exception as e:
                logger.error("SecurityGate crash (blocking): %s", e)
                return PipelineResult(
                    success=False,
                    error=f"Security check failed: {e}",
                    tool_name=tool_name,
                    origin=origin,
                )

        # 2. Blackboard state
        app_name = params.get(
            "expected_app",
            params.get("name", params.get("app_name", params.get("app", ""))),
        )
        if self._blackboard:
            try:
                self._blackboard.set(
                    "world.active_tool", tool_name, source="pipeline",
                )
                self._blackboard.set(
                    "world.active_app", app_name, source="pipeline",
                )
            except Exception as e:
                logger.debug("Blackboard error: %s", e)

        # 3. DesktopWeather check
        if self._weather:
            try:
                report = self._weather.get_report()
                if report.should_pause:
                    logger.warning(
                        "Desktop in TORMENTA — pausing before %s", tool_name,
                    )
                    await asyncio.sleep(min(report.recommended_delay, 10.0))
            except Exception as e:
                logger.debug("DesktopWeather error: %s", e)

        # 4. Window snapshot (before)
        if tool_name not in _READ_ONLY_TOOLS and self._snapshot_handler:
            try:
                await self._snapshot_handler()
            except Exception as e:
                logger.debug("Pre-snapshot error: %s", e)

        # 5. PreActionScorer + reliability gate
        pre_score_value = 0.0
        if self._pre_scorer:
            try:
                pre_context = {
                    "target_app": params.get(
                        "window_title", params.get("app_name", ""),
                    ),
                    "element_type": params.get("control_type", ""),
                    "estimated_tokens": 0,
                    "estimated_ms": 0,
                }
                pre_score = self._pre_scorer.score(
                    tool_name=tool_name,
                    context=pre_context,
                    app_name=params.get(
                        "window_title", params.get("app_name", ""),
                    ),
                )
                pre_score_value = pre_score.composite
                rel_score = pre_score.reliability

                if rel_score < self.RELIABILITY_BLOCK_THRESHOLD:
                    if origin == "proactive":
                        logger.warning(
                            "Proactive blocked: %s has low reliability (%.2f)",
                            tool_name, rel_score,
                        )
                        return PipelineResult(
                            success=False,
                            error=(
                                f"Tool {tool_name} has low reliability "
                                f"({rel_score:.2f}), skipping proactive execution"
                            ),
                            tool_name=tool_name,
                            origin=origin,
                        )
                    else:
                        logger.warning(
                            "Low reliability tool: %s score=%.2f",
                            tool_name, rel_score,
                        )

                if pre_score_value < 0.3:
                    logger.warning(
                        "Low pre-score for %s: %.2f", tool_name, pre_score_value,
                    )
                else:
                    logger.debug(
                        "PreActionScore: %s -> %.2f", tool_name, pre_score_value,
                    )
            except Exception as e:
                logger.debug("PreActionScorer error: %s", e)

        # 5b. ErrorJournal: check for known solutions before executing
        if self._error_journal:
            try:
                best_method = self._error_journal.get_best_method(
                    tool_name, params.get("window_title", params.get("app_name")),
                )
                if best_method:
                    logger.info(
                        "Known error pattern for %s: preferred method=%s",
                        tool_name, best_method,
                    )
            except Exception as e:
                logger.debug("ErrorJournal pre-check error: %s", e)

        # 6. EventBus: ActionStarting
        if self._event_bus:
            try:
                from .events import ActionStarting
                await self._event_bus.publish(ActionStarting(
                    tool_name=tool_name,
                    source=origin,
                    pre_score=pre_score_value,
                ))
            except Exception as e:
                logger.debug("EventBus ActionStarting error: %s", e)

        # ══════════════════════════════════════════════════
        # EXECUTION
        # ══════════════════════════════════════════════════

        # 7. Focus target window for input tools
        if tool_name in _INPUT_TOOLS and self._focus_handler:
            try:
                await self._focus_handler(tool_name, params)
            except Exception as e:
                logger.debug("Focus handler error: %s", e)

        # 8. Execute the tool
        exec_start = time.time()
        try:
            func = self._tool_map[tool_name]
            loop = asyncio.get_event_loop()
            raw_result = await loop.run_in_executor(None, lambda: func(**params))
        except Exception as e:
            duration_ms = (time.time() - start) * 1000
            logger.error("Tool execution error (%s): %s", tool_name, e)

            # Post-error: publish failure
            await self._post_failure(tool_name, origin, str(e), duration_ms, app_name, params)

            return PipelineResult(
                success=False,
                error=str(e),
                duration_ms=duration_ms,
                origin=origin,
                tool_name=tool_name,
            )

        duration_ms = (time.time() - start) * 1000
        exec_duration_ms = (time.time() - exec_start) * 1000

        # Normalize result
        if isinstance(raw_result, dict):
            success = raw_result.get("success", True)
            error = raw_result.get("error", "")
            data = raw_result
        else:
            success = True
            error = ""
            data = {"success": True, "result": str(raw_result)}

        # launch_in_shadow fallback
        if (
            tool_name == "launch_in_shadow"
            and not success
            and "open_application" in self._tool_map
        ):
            command = params.get("command", "")
            logger.info(
                "launch_in_shadow failed, falling back to open_application: %s",
                command,
            )
            fallback_result = await self.execute(
                "open_application",
                {"app_name": command},
                origin=origin,
                goal_id=goal_id,
            )
            if fallback_result.success and isinstance(fallback_result.data, dict):
                fallback_result.data["note"] = "Opened visibly (shadow mode unavailable)"
            return fallback_result

        # ══════════════════════════════════════════════════
        # POST-EXECUTION
        # ══════════════════════════════════════════════════

        if success:
            await self._post_success(
                tool_name, origin, duration_ms, exec_duration_ms, app_name, params,
            )
        else:
            await self._post_failure(
                tool_name, origin, error, duration_ms, app_name, params,
            )

        # 13-14. Window changes + interrupt detection
        interrupt_info = await self._post_window_check(tool_name, origin)

        result = PipelineResult(
            success=success,
            data=data,
            error=error,
            duration_ms=duration_ms,
            origin=origin,
            tool_name=tool_name,
        )

        if interrupt_info:
            result.interrupted = True
            result.interrupt_priority = interrupt_info

        return result

    # ── Post-execution helpers (all fault-tolerant) ──

    async def _post_success(
        self,
        tool_name: str,
        origin: str,
        duration_ms: float,
        exec_duration_ms: float,
        app_name: str,
        params: dict,
    ):
        """Post-execution steps for successful tool runs."""
        # 9. EventBus: ActionCompleted
        if self._event_bus:
            try:
                from .events import ActionCompleted
                await self._event_bus.publish(ActionCompleted(
                    tool_name=tool_name,
                    source=origin,
                    success=True,
                    duration_ms=round(duration_ms, 1),
                ))
            except Exception as e:
                logger.debug("EventBus ActionCompleted error: %s", e)

        # 11. Memory: remember short-term
        if self._memory:
            try:
                self._memory.remember_short(
                    {
                        "tool": tool_name,
                        "success": True,
                        "duration_ms": round(duration_ms),
                    },
                    category="action",
                    tool_name=tool_name,
                )
            except Exception as e:
                logger.debug("Memory error: %s", e)

        # 12. Knowledge: record action
        if self._knowledge and app_name:
            try:
                await self._knowledge.record_action(
                    app_name=app_name, success=True,
                )
            except Exception as e:
                logger.debug("Knowledge record error: %s", e)

        # 13. AdaptiveWaits after open_application
        if tool_name == "open_application" and self._adaptive_waits:
            try:
                name = (
                    params.get("app_name")
                    or params.get("name")
                    or ""
                )
                wait_time = self._adaptive_waits.get_wait(name)
                logger.info("Waiting %.1fs for %s (adaptive)", wait_time, name)
                await asyncio.sleep(wait_time)
            except Exception as e:
                logger.debug("AdaptiveWaits error: %s", e)

    async def _post_failure(
        self,
        tool_name: str,
        origin: str,
        error: str,
        duration_ms: float,
        app_name: str,
        params: dict = None,
    ):
        """Post-execution steps for failed tool runs."""
        # 9. EventBus: ActionFailed
        if self._event_bus:
            try:
                from .events import ActionFailed
                await self._event_bus.publish(ActionFailed(
                    tool_name=tool_name,
                    source=origin,
                    error=error,
                ))
            except Exception as e:
                logger.debug("EventBus ActionFailed error: %s", e)

        # 10. DesktopWeather: record error
        if self._weather:
            try:
                self._weather.record_error()
            except Exception as e:
                logger.debug("DesktopWeather record_error: %s", e)

        # 11. Memory
        if self._memory:
            try:
                self._memory.remember_short(
                    {
                        "tool": tool_name,
                        "success": False,
                        "error": error[:200],
                        "duration_ms": round(duration_ms),
                    },
                    category="action",
                    tool_name=tool_name,
                )
            except Exception as e:
                logger.debug("Memory error: %s", e)

        # 12. Knowledge: record failure
        if self._knowledge and app_name:
            try:
                await self._knowledge.record_action(
                    app_name=app_name, success=False,
                )
                await self._knowledge.record_error(
                    app_name=app_name,
                    tool_name=tool_name,
                    error_type="execution_error",
                    error_message=error[:500],
                )
            except Exception as e:
                logger.debug("Knowledge record error: %s", e)

        # 12b. ErrorJournal: record failure for learning
        if self._error_journal:
            try:
                self._error_journal.record_failure(
                    tool=tool_name,
                    window=app_name or None,
                    method=tool_name,
                    error=error[:500],
                    params={
                        k: v for k, v in (params or {}).items()
                        if k in ("element_name", "window_title", "app_name", "target")
                    } if params else None,
                )
            except Exception as e:
                logger.debug("ErrorJournal record_failure error: %s", e)

    async def _post_window_check(
        self,
        tool_name: str,
        origin: str,
    ) -> Optional[int]:
        """Post-execution: window snapshots, change detection, interrupts.

        Returns interrupt priority (int) if an interrupt was detected, else None.
        """
        if tool_name in _READ_ONLY_TOOLS:
            return None

        # Take post-action snapshot
        if self._snapshot_handler:
            try:
                await self._snapshot_handler()
            except Exception as e:
                logger.debug("Post-snapshot error: %s", e)

        # Detect window changes
        if not self._window_tracker:
            return None

        try:
            changes = self._window_tracker.detect_changes()
        except Exception as e:
            logger.debug("WindowTracker detect_changes error: %s", e)
            return None

        if not changes:
            return None

        # Publish changes + check for interrupts
        highest_interrupt = None
        for change in changes:
            # Record in weather
            if self._weather:
                try:
                    self._weather.record_window_change()
                except Exception:
                    pass

            # Publish WindowChanged event
            if self._event_bus:
                try:
                    from .events import WindowChanged
                    await self._event_bus.publish(WindowChanged(
                        change_type=change.change_type,
                        window_title=change.window_title,
                        source=origin,
                    ))
                except Exception:
                    pass

            # Classify as interrupt
            if self._interrupt_mgr:
                try:
                    interrupt = self._interrupt_mgr.classify_event(
                        event_type=change.change_type,
                        title=change.window_title,
                    )
                    if self._interrupt_mgr.should_interrupt(interrupt):
                        logger.info(
                            "Interrupt detected: P%d %s (%s)",
                            interrupt.priority, interrupt.source,
                            change.window_title,
                        )
                        if highest_interrupt is None or interrupt.priority < highest_interrupt:
                            highest_interrupt = int(interrupt.priority)
                except Exception as e:
                    logger.debug("InterruptManager error: %s", e)

        return highest_interrupt
