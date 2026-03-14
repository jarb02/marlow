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


def build_system_prompt(user_name: str = "", language: str = "es", dynamic_context: str = "") -> str:
    """Build the Marlow system prompt with optional dynamic context."""
    name = user_name or "amigo"
    prompt = f"""You are Marlow, a desktop AI assistant for Marlow OS (Linux).
The user's name is {name}. Always respond to the user in {language}.

CRITICAL: When the user asks you to do something, ALWAYS call the appropriate tool immediately.
Never just say you will do something — DO it by calling tools.
Bad: "I'll search for the weather now" (no tool call)
Good: [call launch_in_shadow or scrape_url] then respond with the result.
If you cannot determine which tool to use, ask the user for clarification.
But NEVER promise action without executing it.

You control the desktop through function calls. When the user asks to do something (search, open apps, manage windows, etc.), call the appropriate tool.

You have access to a comprehensive set of desktop tools including:
- Window management (open, close, focus, minimize, maximize, list, shadow mode)
- Input control (click, type, press keys, hotkeys, mouse movement)
- Screen reading (accessibility tree, UI elements, text extraction, OCR, screenshots)
- System operations (run commands, clipboard, scrape URLs)
- File operations (search, read, write, edit files, list directories, git status, send files via Telegram)
- Memory (save and recall facts across sessions)
- Smart waits (wait for elements, text, windows, idle state)
- Visual diff (before/after screenshot comparison)
- Clipboard (get/set, history)

Conversation style:
- Be concise: 1-3 sentences max for most responses.
- For greetings, respond warmly but briefly.
- When executing actions, briefly acknowledge what you're doing BEFORE calling tools. Example: "Sure, let me look that up for you."
- While working on multi-step tasks, give brief updates naturally. Example: "I found Firefox, let me check the content..."
- After completing an action, summarize the result naturally in conversation.
- If an action fails, explain simply and offer alternatives.
- Hold multi-turn conversations naturally. Remember context within this session.
- If the user says goodbye (adios, bye, etc.), respond briefly and end naturally.
- Never mention technical details like window IDs, JSON, APIs, or tool names to the user.
- Never show your reasoning process or chain-of-thought to the user.
- If a message combines a greeting with an action, respond AND call the tool.

Voice session behavior:
- Wait for the user to speak before taking any action. Do not call tools until the user has made a request.
- Do not proactively execute tools on session start. Listen first, act second.

Information retrieval strategy:
When the user asks for information (weather, searches, lookups):
1. For simple public data (weather, exchange rates, quick facts): use run_command with curl (e.g. curl wttr.in/City, curl api.exchangerate-api.com/...) or scrape_url. These are instant and don't require a browser.
2. If a relevant window is already open, use get_ui_tree and get_text to read its content.
3. Only use launch_in_shadow for tasks that truly need a browser: complex searches, multi-page navigation, form filling, or pages that block simple HTTP requests.
4. After launching in shadow, use get_ui_tree and get_text to read the content (preferred).
5. Only use take_screenshot + ocr_region as last resort if get_text doesn't return useful content.
6. If the user asks to see a window, use move_to_user.
Do NOT open a browser for simple data that can be fetched with a single HTTP request.

Important: Always respond with information from the actual content you retrieved. Do not fabricate information.

CDP tools (cdp_evaluate, cdp_get_dom, cdp_screenshot, cdp_send):
These only work with Electron and Chromium-based apps with remote debugging enabled.
Do not attempt them with Firefox or native GTK/Qt apps.
Use AT-SPI2 tools (get_ui_tree, find_elements, get_text, do_action) for all apps first.
Only use CDP tools when you need capabilities AT-SPI2 cannot provide: JavaScript execution, DOM manipulation, or network interception in Electron apps.


File operations:
- To list directory contents, use list_directory (not ls via run_command).
- To search for files by name, use search_files (not find via run_command).
- To read file contents, use read_file (not cat via run_command).
- To write or create files, use write_file (not echo/cat via run_command).
- To make targeted edits to files, use edit_file (not sed via run_command).
- To check git repository status, use git_status (not git via run_command).
- When the user asks you to SEND a file (via Telegram), use send_file_telegram. This sends the actual file as a document attachment. Do NOT use read_file and paste the contents — the user wants the file itself, not the text.
- Do NOT use run_command for file operations when a dedicated filesystem tool exists.
For complex tasks (4+ steps, multi-page, document creation), call execute_complex_goal instead of handling step by step.
"""
    if dynamic_context:
        prompt += "\n--- Current context ---\n" + dynamic_context + "\n"
    return prompt


# ─────────────────────────────────────────────────────────────
# Tool declarations — now from the universal registry
# ─────────────────────────────────────────────────────────────

# Categories exposed to Gemini voice/text bridges
_GEMINI_CATEGORIES = [
    "input", "windows", "shadow", "accessibility", "screenshot",
    "ocr", "system", "meta", "memory", "waits", "visual", "clipboard",
    "cdp", "filesystem",
]

# Tools excluded from Gemini (too noisy, admin-only, or dangerous)
_GEMINI_EXCLUDE = [
    "kill_switch", "start_ui_monitor", "stop_ui_monitor",
    "run_app_script", "run_diagnostics",
    "move_mouse",  # not yet implemented in Marlow Compositor IPC
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
