"""Filters 96 tools down to relevant subset for LLM planning.

The LLM doesn't need to see all 96 tools. We filter based on:
1. Goal keywords -> relevant tool categories
2. Target app -> app-specific tools (CDP for Electron, etc.)
3. Always include core tools (click, type, screenshot)
"""

from __future__ import annotations

# Tool categories
CORE_TOOLS = [
    "click",
    "type_text",
    "press_key",
    "hotkey",
    "take_screenshot",
    "wait_for_idle",
    "wait_for_element",
]

WINDOW_TOOLS = [
    "list_windows",
    "focus_window",
    "manage_window",
    "open_application",
]

NAVIGATION_TOOLS = [
    "smart_find",
    "find_elements",
    "get_ui_tree",
    "get_annotated_screenshot",
    "som_click",
]

OCR_TOOLS = ["ocr_region", "wait_for_text"]

CDP_TOOLS = [
    "cdp_click",
    "cdp_type_text",
    "cdp_evaluate",
    "cdp_key_combo",
    "cdp_screenshot",
]

DIALOG_TOOLS = ["handle_dialog", "get_dialog_info"]

FILE_TOOLS = ["run_command", "clipboard"]

VOICE_TOOLS = ["speak", "speak_and_listen", "listen_for_command"]

# Keyword -> category mapping
KEYWORD_MAP: dict[str, list[str]] = {
    "type": CORE_TOOLS,
    "write": CORE_TOOLS,
    "click": CORE_TOOLS + NAVIGATION_TOOLS,
    "open": WINDOW_TOOLS + CORE_TOOLS,
    "close": WINDOW_TOOLS,
    "find": NAVIGATION_TOOLS,
    "search": NAVIGATION_TOOLS + OCR_TOOLS,
    "read": OCR_TOOLS + NAVIGATION_TOOLS,
    "web": CDP_TOOLS + NAVIGATION_TOOLS,
    "browser": CDP_TOOLS + NAVIGATION_TOOLS,
    "chrome": CDP_TOOLS,
    "electron": CDP_TOOLS,
    "dialog": DIALOG_TOOLS,
    "save": CORE_TOOLS + DIALOG_TOOLS,
    "file": FILE_TOOLS + DIALOG_TOOLS,
    "copy": [*CORE_TOOLS, "clipboard"],
    "paste": [*CORE_TOOLS, "clipboard"],
    "say": VOICE_TOOLS,
    "speak": VOICE_TOOLS,
    "voice": VOICE_TOOLS,
    "screenshot": ["take_screenshot", "get_annotated_screenshot"],
    "ocr": OCR_TOOLS,
}


class ToolFilter:
    """Filter tools for LLM planning context.

    Parameters
    ----------
    * **all_tools** (list of str or None):
        Full set of available tool names. If provided, results are
        intersected with this set.
    """

    def __init__(self, all_tools: list[str] = None):
        self._all_tools = set(all_tools or [])

    def filter_for_goal(
        self,
        goal_text: str,
        app_framework: str = None,
    ) -> list[str]:
        """Return relevant tools for a goal.

        Always includes ``CORE_TOOLS`` + ``WINDOW_TOOLS``.
        Adds extras based on keywords and app framework.
        """
        relevant: set[str] = set(CORE_TOOLS + WINDOW_TOOLS)

        # Add tools based on keywords in goal
        goal_lower = goal_text.lower()
        for keyword, tools in KEYWORD_MAP.items():
            if keyword in goal_lower:
                relevant.update(tools)

        # Add framework-specific tools
        if app_framework in ("electron", "cef"):
            relevant.update(CDP_TOOLS)

        # Always add dialog tools (dialogs can appear anytime)
        relevant.update(DIALOG_TOOLS)

        # Filter to only tools that actually exist
        if self._all_tools:
            relevant = relevant.intersection(self._all_tools)

        return sorted(relevant)

    def format_for_prompt(
        self,
        tools: list[str],
        tool_descriptions: dict[str, str] = None,
    ) -> str:
        """Format tool list for LLM prompt.

        Parameters
        ----------
        * **tools** (list of str): Tool names to include.
        * **tool_descriptions** (dict or None):
            Mapping of tool name -> short description.
        """
        if tool_descriptions:
            lines = []
            for t in tools:
                desc = tool_descriptions.get(t, "")
                lines.append(f"- {t}: {desc}" if desc else f"- {t}")
            return "\n".join(lines)
        return "\n".join(f"- {t}" for t in tools)
