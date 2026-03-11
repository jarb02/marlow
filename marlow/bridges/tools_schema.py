"""Shared tool declarations and system prompt for Marlow OS.

Uses the universal Tool Registry for all LLM-facing tool declarations.
Single source of truth — no more manual tool list duplication.

/ Schema de tools compartido — usa el Tool Registry universal.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("marlow.bridges.tools_schema")

# Re-export aliases and resolver from the registry
from marlow.kernel.registry import TOOL_ALIASES, resolve_tool_call  # noqa: F401


# ─────────────────────────────────────────────────────────────
# System prompt
# ─────────────────────────────────────────────────────────────


def build_system_prompt(user_name: str = "", language: str = "es") -> str:
    """Build the Marlow system prompt for Gemini."""
    return (
        f"You are Marlow, a desktop AI assistant for Marlow OS (Linux).\n"
        f"The user's name is {user_name or 'amigo'}. "
        f"Always respond to the user in {language}.\n\n"
        f"You control the desktop through function calls. When the user asks to "
        f"do something (search, open apps, manage windows, etc.), call the tool.\n\n"
        f"You have access to a comprehensive set of desktop tools including:\n"
        f"- Window management (open, close, focus, minimize, maximize, list, shadow mode)\n"
        f"- Input control (click, type, press keys, hotkeys, mouse movement)\n"
        f"- Screen reading (screenshots, OCR, accessibility tree, UI element inspection)\n"
        f"- System operations (run commands, clipboard, file operations)\n"
        f"- Automation (wait for elements, scheduled tasks, workflows)\n\n"
        f"Use the most appropriate tool for each task. For accessibility tree operations, "
        f"prefer find_elements and get_ui_tree over OCR when interacting with app content.\n\n"
        f"Guidelines:\n"
        f"- Be concise: 1-3 sentences max.\n"
        f"- Greetings: respond warmly but briefly.\n"
        f"- If a message combines a greeting with an action, ALWAYS call the tool AND "
        f"respond. Never just promise to do something without executing it.\n"
        f"- After an action, summarize the result naturally.\n"
        f"- On failure, explain simply and offer alternatives.\n"
        f"- Maintain multi-turn context within this session.\n"
        f"- Never expose technical details (window IDs, JSON, APIs).\n"
        f"- If a tool fails, explain naturally without mentioning error codes, "
        f"exceptions, or internal details.\n\n"
        f"MANDATORY shadow workflow for any search or web lookup:\n"
        f"You MUST complete ALL steps before responding to the user.\n"
        f"Step 1: launch_in_shadow(command='firefox <url>') - save the window_id from the response.\n"
        f"Step 2: Wait for page load, then call take_screenshot(window_id=<id>).\n"
        f"Step 3: Call ocr_region(window_id=<id>) to extract the text.\n"
        f"Step 4: Read the OCR result and compose your answer from it.\n"
        f"Step 5: Only call move_to_user if the user explicitly asks to see the window.\n"
        f"DO NOT respond after step 1 alone. You MUST continue to steps 2-4.\n"
        f"DO NOT fabricate information - only report what OCR returns.\n"
        f"The window_id from launch_in_shadow works directly with take_screenshot "
        f"and ocr_region - pass it as the window_id parameter.\n\n"
        f"For complex tasks (4+ steps, multi-page, document creation), "
        f"call execute_complex_goal instead of handling step by step.\n"
    )


# ─────────────────────────────────────────────────────────────
# Tool declarations — now from the universal registry
# ─────────────────────────────────────────────────────────────

# Categories exposed to Gemini voice/text bridges
_GEMINI_CATEGORIES = [
    "input", "windows", "shadow", "accessibility", "screenshot",
    "ocr", "system", "meta",
]

# Tools excluded from Gemini (too noisy, admin-only, or dangerous)
_GEMINI_EXCLUDE = [
    "kill_switch", "start_ui_monitor", "stop_ui_monitor",
    "run_app_script", "run_diagnostics",
]


def build_tool_declarations(categories=None, exclude=None):
    """Build Gemini function declarations from the universal Tool Registry.

    Args:
        categories: List of categories to include. Defaults to _GEMINI_CATEGORIES.
        exclude: Tool names to exclude. Defaults to _GEMINI_EXCLUDE.

    Returns:
        list of google.genai.types.Tool wrapping FunctionDeclarations.
    """
    from google.genai import types
    from marlow.kernel.adapters import to_gemini

    cats = categories or _GEMINI_CATEGORIES
    excl = exclude or _GEMINI_EXCLUDE

    declarations = to_gemini(categories=cats, exclude=excl)

    # Also add the close_window alias (Gemini-friendly name)
    declarations.append(types.FunctionDeclaration(
        name="close_window",
        description="Close a window on the desktop.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "window_id": types.Schema(
                    type=types.Type.INTEGER,
                    description="Window ID to close (get from list_windows)",
                ),
            },
            required=["window_id"],
        ),
    ))

    return [types.Tool(function_declarations=declarations)]


def build_anthropic_tools(categories=None, exclude=None) -> list[dict]:
    """Build Anthropic tool declarations from the universal Tool Registry.

    Args:
        categories: List of categories to include. Defaults to _GEMINI_CATEGORIES.
        exclude: Tool names to exclude. Defaults to _GEMINI_EXCLUDE.

    Returns:
        List of dicts in Anthropic tool format.
    """
    from marlow.kernel.adapters import to_anthropic

    cats = categories or _GEMINI_CATEGORIES
    excl = exclude or _GEMINI_EXCLUDE

    tools = to_anthropic(categories=cats, exclude=excl)

    # Add close_window alias
    tools.append({
        "name": "close_window",
        "description": "Close a window on the desktop.",
        "input_schema": {
            "type": "object",
            "properties": {
                "window_id": {
                    "type": "integer",
                    "description": "Window ID to close (get from list_windows)",
                },
            },
            "required": ["window_id"],
        },
    })

    return tools
