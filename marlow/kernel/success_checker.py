"""Verifies step outcomes using programmatic checks.

Supports 10 check types (from Research #3):
- window_exists: check if window with title is open
- element_exists: check if UI element is present (stub -- needs UIA)
- ocr_text_visible: check if text appears on screen (stub -- needs OCR)
- dialog_appeared: check for dialog
- clipboard_contains: check clipboard content
- window_title_changed: active window title changed
- window_count_changed: number of windows changed
- file_exists: check if file exists at path
- all_of: all sub-checks pass
- any_of: any sub-check passes
- none: always passes (no verification)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class CheckResult:
    """Result of a single success check."""

    passed: bool
    check_type: str
    detail: str = ""


class SuccessChecker:
    """Verifies success conditions for plan steps.

    For now, uses simple programmatic checks.
    Tier 7 adds visual verification (phash -> SSIM -> LLM).

    Parameters
    ----------
    * **tool_executor** (Callable or None):
        ``async (tool_name, params) -> ToolResult``.
    * **world_state_getter** (Callable or None):
        ``() -> WorldStateSnapshot``.
    """

    def __init__(
        self,
        tool_executor: Callable = None,
        world_state_getter: Callable = None,
    ):
        self._executor = tool_executor
        self._get_world = world_state_getter

    async def check(self, check_config: dict) -> bool:
        """Run a success check.

        Parameters
        ----------
        * **check_config** (dict):
            ``{"type": "window_exists", "params": {"title_contains": "Notepad"}}``

        Returns
        -------
        bool
            True if check passes, False otherwise.
        """
        check_type = check_config.get("type", "none")
        params = check_config.get("params", {})

        try:
            if check_type == "none":
                return True

            elif check_type == "window_exists":
                return await self._check_window_exists(params)

            elif check_type == "window_title_changed":
                return await self._check_window_title_changed(params)

            elif check_type == "window_count_changed":
                return await self._check_window_count_changed(params)

            elif check_type == "file_exists":
                return self._check_file_exists(params)

            elif check_type == "clipboard_contains":
                return await self._check_clipboard(params)

            elif check_type == "all_of":
                sub_checks = params.get("checks", [])
                results = [await self.check(c) for c in sub_checks]
                return all(results)

            elif check_type == "any_of":
                sub_checks = params.get("checks", [])
                results = [await self.check(c) for c in sub_checks]
                return any(results)

            elif check_type in (
                "element_exists",
                "ocr_text_visible",
                "dialog_appeared",
                "llm_verify",
            ):
                # These need tool execution -- stub for now
                if self._executor:
                    return await self._check_via_tool(check_type, params)
                return True  # Assume pass if no executor

            else:
                return True  # Unknown type -- pass

        except Exception:
            return False  # Error = fail

    async def _check_window_exists(self, params: dict) -> bool:
        title = params.get("title_contains", "")
        if not title:
            return True
        world = self._get_world() if self._get_world else None
        if world:
            return world.has_window(title)
        return True  # Can't check -- assume pass

    async def _check_window_title_changed(self, params: dict) -> bool:
        expected = params.get("expected_title", "")
        world = self._get_world() if self._get_world else None
        if world and expected:
            return expected.lower() in world.active_window_title.lower()
        return True

    async def _check_window_count_changed(self, params: dict) -> bool:
        direction = params.get("direction", "increased")
        previous = params.get("previous_count", 0)
        world = self._get_world() if self._get_world else None
        if world:
            if direction == "increased":
                return world.window_count > previous
            elif direction == "decreased":
                return world.window_count < previous
        return True

    def _check_file_exists(self, params: dict) -> bool:
        path = params.get("path", "")
        return os.path.exists(path) if path else True

    async def _check_clipboard(self, params: dict) -> bool:
        text = params.get("text", "")
        if not text or not self._executor:
            return True
        result = await self._executor("clipboard", {"action": "read"})
        if result.success and result.data:
            return text.lower() in str(result.data).lower()
        return False

    async def _check_via_tool(self, check_type: str, params: dict) -> bool:
        """Route check to appropriate tool."""
        if check_type == "element_exists":
            result = await self._executor(
                "find_elements",
                {
                    "name": params.get("name", ""),
                    "control_type": params.get("control_type"),
                },
            )
            return result.success and bool(result.data)

        elif check_type == "ocr_text_visible":
            result = await self._executor(
                "ocr_region",
                {"region": params.get("region", "full_screen")},
            )
            if result.success and result.data:
                return (
                    params.get("text", "").lower() in str(result.data).lower()
                )
            return False

        elif check_type == "dialog_appeared":
            result = await self._executor("get_dialog_info", {})
            if result.success and result.data:
                title = params.get("title_contains", "")
                if title:
                    return title.lower() in str(result.data).lower()
                return True
            return False

        return True
