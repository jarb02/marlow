"""ObservationRouter — decides how to verify action results.

Three observation paths:
1. Data tools: result IS the observation (confidence=1.0)
2. Window tools: verify via DesktopObserver state (confidence=0.9)
3. Input tools: verify via AT-SPI2 when possible (confidence=0.95)

Produces Observation objects with success, confidence, and summaries
that feed into the ReactiveGoalLoop scratchpad.

/ Router de observacion — decide como verificar resultados de acciones.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger("marlow.kernel.observation_router")


@dataclass
class Observation:
    """Result of observing an action's outcome."""

    type: str  # "data", "ui_state"
    success: bool
    content: dict
    confidence: float = 1.0
    summary: str = ""
    needs_llm_verify: bool = False


class ObservationRouter:
    """Decides how to observe action results and verify success."""

    def __init__(self, execution_pipeline=None, desktop_observer=None):
        self.pipeline = execution_pipeline
        self.observer = desktop_observer

    # ── Tool classifications ──

    DATA_TOOLS = frozenset({
        "search_files", "list_directory", "read_file", "write_file",
        "edit_file", "git_status", "send_file_telegram",
        "memory_save", "memory_recall", "memory_list", "memory_delete",
        "run_command", "scrape_url", "system_info", "clipboard",
    })

    WINDOW_TOOLS = frozenset({
        "open_application", "focus_window", "manage_window",
        "launch_in_shadow",
    })

    INPUT_TOOLS = frozenset({
        "click", "type_text", "press_key", "hotkey", "do_action",
    })

    # ── Main entry point ──

    async def observe(
        self, tool_name: str, action_params: dict, result: dict,
    ) -> Observation:
        """Observe the result of a tool execution."""
        if tool_name in self.DATA_TOOLS:
            return self._observe_data(tool_name, result)

        if tool_name in self.WINDOW_TOOLS:
            return await self._observe_window(tool_name, action_params, result)

        if tool_name in self.INPUT_TOOLS:
            return await self._observe_input(tool_name, action_params, result)

        # Unknown tool — treat as data
        return self._observe_data(tool_name, result)

    # ── Data tools ──

    def _observe_data(self, tool_name: str, result: dict) -> Observation:
        """Data tools: the result itself is sufficient verification."""
        has_error = isinstance(result, dict) and "error" in result
        if has_error and result.get("success") is not True:
            return Observation(
                type="data",
                success=False,
                content=result,
                confidence=1.0,
                summary=f"{tool_name} failed: {result.get('error', 'unknown')}",
            )

        summary = self._summarize_data_result(tool_name, result)
        return Observation(
            type="data",
            success=True,
            content=result,
            confidence=1.0,
            summary=summary,
        )

    def _summarize_data_result(self, tool_name: str, result: dict) -> str:
        """Create a meaningful 1-line summary based on tool type."""
        if not isinstance(result, dict):
            return f"{tool_name}: completed"

        if tool_name == "search_files":
            count = len(result.get("results", []))
            total = result.get("total_found", count)
            return f"Found {count} files (total: {total})"

        if tool_name == "list_directory":
            return f"Listed {result.get('total_entries', 0)} entries in {result.get('path', '?')}"

        if tool_name == "read_file":
            lines = result.get("lines", 0)
            size = result.get("size_kb", 0)
            return f"Read file: {lines} lines, {size}KB"

        if tool_name == "write_file":
            action = result.get("action", "written")
            return f"File {action}: {result.get('path', '?')}"

        if tool_name == "edit_file":
            applied = result.get("edits_applied", 0)
            failed = result.get("edits_failed", 0)
            return f"Edited: {applied} applied, {failed} failed"

        if tool_name == "git_status":
            branch = result.get("branch", "?")
            clean = result.get("clean", None)
            return f"Git: branch={branch}, clean={clean}"

        if tool_name == "send_file_telegram":
            if result.get("success"):
                return "File sent via Telegram"
            return f"Telegram: {result.get('error', 'unknown')}"

        if tool_name == "run_command":
            code = result.get("exit_code", result.get("returncode", "?"))
            return f"Command exit code: {code}"

        if tool_name in ("memory_save", "memory_recall", "memory_list", "memory_delete"):
            action = result.get("action", result.get("status", "done"))
            return f"{tool_name}: {action}"

        return f"{tool_name}: completed successfully"

    # ── Window tools ──

    async def _observe_window(
        self, tool_name: str, params: dict, result: dict,
    ) -> Observation:
        """Window tools: check desktop state for verification."""
        has_error = isinstance(result, dict) and "error" in result
        if has_error and result.get("success") is not True:
            return Observation(
                type="ui_state",
                success=False,
                content=result,
                confidence=1.0,
                summary=f"{tool_name} failed: {result.get('error', 'unknown')}",
            )

        # Verify via DesktopObserver if available
        if self.observer:
            try:
                state = self.observer.get_state()

                if tool_name == "open_application":
                    app_name = (
                        params.get("name") or params.get("application")
                        or params.get("app_name") or ""
                    ).lower()
                    windows = state.windows if hasattr(state, "windows") else {}
                    found = any(
                        app_name in (getattr(w, "app_id", "") or "").lower()
                        or app_name in (getattr(w, "title", "") or "").lower()
                        for w in (
                            windows.values()
                            if isinstance(windows, dict)
                            else windows
                        )
                    )
                    if found:
                        return Observation(
                            type="ui_state",
                            success=True,
                            content={"window_found": True, "app": app_name},
                            confidence=0.9,
                            summary=f"Application '{app_name}' window detected",
                        )
                    return Observation(
                        type="ui_state",
                        success=True,
                        content={"window_found": False, "app": app_name},
                        confidence=0.5,
                        summary="Application launched but window not yet detected",
                        needs_llm_verify=True,
                    )

                if tool_name == "focus_window":
                    focused = state.focused_window
                    if focused:
                        return Observation(
                            type="ui_state",
                            success=True,
                            content={"focused": getattr(focused, "title", "?")},
                            confidence=0.9,
                            summary=f"Window focused: {getattr(focused, 'title', '?')}",
                        )

            except Exception as e:
                logger.warning("Desktop observation failed: %s", e)

        # Fallback: trust tool result with lower confidence
        return Observation(
            type="ui_state",
            success=True,
            content=result,
            confidence=0.6,
            summary=f"{tool_name}: tool reported success (unverified)",
            needs_llm_verify=True,
        )

    # ── Input tools ──

    async def _observe_input(
        self, tool_name: str, params: dict, result: dict,
    ) -> Observation:
        """Input tools: try AT-SPI2 verification if pipeline available."""
        has_error = isinstance(result, dict) and "error" in result
        if has_error and result.get("success") is not True:
            return Observation(
                type="ui_state",
                success=False,
                content=result,
                confidence=1.0,
                summary=f"{tool_name} failed: {result.get('error', 'unknown')}",
            )

        # AT-SPI2 verification for type_text
        if self.pipeline and tool_name == "type_text":
            try:
                text_result = await self.pipeline.execute("get_text", {})
                typed_text = params.get("text", "")
                actual_text = ""
                if hasattr(text_result, "data") and isinstance(text_result.data, dict):
                    actual_text = text_result.data.get("text", "")
                elif isinstance(text_result, dict):
                    actual_text = text_result.get("text", "")

                if typed_text and typed_text in actual_text:
                    return Observation(
                        type="ui_state",
                        success=True,
                        content={"text_verified": True, "typed": typed_text},
                        confidence=0.95,
                        summary=f"Text '{typed_text[:30]}' verified in field",
                    )
                return Observation(
                    type="ui_state",
                    success=True,
                    content={"text_verified": False, "typed": typed_text},
                    confidence=0.4,
                    summary="Text typed but verification inconclusive",
                    needs_llm_verify=True,
                )
            except Exception as e:
                logger.debug("AT-SPI2 text verification failed: %s", e)

        # Default: trust tool result
        return Observation(
            type="ui_state",
            success=True,
            content=result,
            confidence=0.6,
            summary=f"{tool_name}: tool reported success",
            needs_llm_verify=True,
        )
