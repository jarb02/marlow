"""Shared Gemini tool declarations and system prompt for Marlow OS.

Used by both GeminiLiveVoiceBridge (audio) and GeminiTextBridge (text).
Single source of truth for Marlow capabilities exposed to Gemini.

/ Schema de tools compartido para Gemini — voz y texto.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("marlow.bridges.tools_schema")

# Tool name aliases — maps Gemini-friendly names to real tool names
TOOL_ALIASES = {
    "close_window": "manage_window",
    "minimize_window": "manage_window",
    "maximize_window": "manage_window",
}


def resolve_tool_call(name: str, args: dict) -> tuple[str, dict]:
    """Resolve aliased tool names and transform args.

    Returns (real_tool_name, transformed_args).
    """
    if name == "close_window":
        window_id = args.get("window_id")
        return "manage_window", {
            "window_title": str(window_id) if window_id else "",
            "action": "close",
        }
    if name == "minimize_window":
        window_id = args.get("window_id")
        return "manage_window", {
            "window_title": str(window_id) if window_id else "",
            "action": "minimize",
        }
    if name == "maximize_window":
        window_id = args.get("window_id")
        return "manage_window", {
            "window_title": str(window_id) if window_id else "",
            "action": "maximize",
        }
    return name, args


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
        f"Guidelines:\n"
        f"- Be concise: 1—3 sentences max.\n"
        f"- Greetings: respond warmly but briefly.\n"
        f"- If a message combines a greeting with an action, ALWAYS call the tool AND "
        f"respond. Never just promise to do something without executing it.\n"
        f"- After an action, summarize the result naturally.\n"
        f"- On failure, explain simply and offer alternatives.\n"
        f"- Maintain multi-turn context within this session.\n"
        f"- Never expose technical details (window IDs, JSON, APIs).\n\n"
        f"Shadow workflow for searches:\n"
        f"1. launch_in_shadow to open the browser invisibly.\n"
        f"2. Use the window_id from the response for subsequent operations.\n"
        f"3. take_screenshot then ocr_region to read the page content.\n"
        f"4. Respond with the extracted information.\n"
        f"5. Only move_to_user if the user explicitly asks to see the window.\n"
        f"Never fabricate a window_id — use the one from launch_in_shadow.\n\n"
        f"For complex tasks (4+ steps, multi-page, document creation), "
        f"call execute_complex_goal instead of handling step by step.\n"
    )


# ─────────────────────────────────────────────────────────────
# Tool declarations for Gemini
# ─────────────────────────────────────────────────────────────

def build_tool_declarations():
    """Build Gemini function declarations for Marlow desktop tools.

    Returns a list of types.Tool for both Gemini Live and Gemini text APIs.
    """
    from google.genai import types

    return [
        types.Tool(function_declarations=[
            types.FunctionDeclaration(
                name="launch_in_shadow",
                description=(
                    "Launch an application in shadow mode (invisible to user). "
                    "Use for web searches, opening apps in background. "
                    "Example: firefox https://google.com/search?q=weather+miami"
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "command": types.Schema(
                            type=types.Type.STRING,
                            description="Command to launch, e.g. firefox https://google.com/search?q=weather",
                        ),
                    },
                    required=["command"],
                ),
            ),
            types.FunctionDeclaration(
                name="move_to_user",
                description="Move a shadow window to the user visible screen.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "window_id": types.Schema(
                            type=types.Type.INTEGER,
                            description="Window ID to promote to visible screen",
                        ),
                    },
                    required=["window_id"],
                ),
            ),
            types.FunctionDeclaration(
                name="open_application",
                description="Open an application on the user desktop.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "app_name": types.Schema(
                            type=types.Type.STRING,
                            description="Application name or command: firefox, foot, nautilus, etc.",
                        ),
                    },
                    required=["app_name"],
                ),
            ),
            types.FunctionDeclaration(
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
            ),
            types.FunctionDeclaration(
                name="list_windows",
                description="List all open windows on the desktop with their IDs, titles, and app names.",
                parameters=types.Schema(type=types.Type.OBJECT, properties={}),
            ),
            types.FunctionDeclaration(
                name="focus_window",
                description="Focus/activate a window by its title or part of title.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "window_title": types.Schema(
                            type=types.Type.STRING,
                            description="Window title or substring to match",
                        ),
                    },
                    required=["window_title"],
                ),
            ),
            types.FunctionDeclaration(
                name="take_screenshot",
                description="Take a screenshot of a window or the full screen.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "window_title": types.Schema(
                            type=types.Type.STRING,
                            description="Window title to capture. Omit for full screen.",
                        ),
                    },
                ),
            ),
            types.FunctionDeclaration(
                name="run_command",
                description="Run a shell command on the system and return its output.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "command": types.Schema(
                            type=types.Type.STRING,
                            description="Shell command to execute",
                        ),
                    },
                    required=["command"],
                ),
            ),
            types.FunctionDeclaration(
                name="type_text",
                description="Type text into the currently focused window using virtual keyboard.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "text": types.Schema(
                            type=types.Type.STRING,
                            description="Text to type",
                        ),
                    },
                    required=["text"],
                ),
            ),
            types.FunctionDeclaration(
                name="press_key",
                description="Press a single key (Return, Escape, Tab, BackSpace, etc.).",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "key": types.Schema(
                            type=types.Type.STRING,
                            description="Key name: Return, Escape, Tab, BackSpace, Up, Down, Left, Right, etc.",
                        ),
                    },
                    required=["key"],
                ),
            ),
            types.FunctionDeclaration(
                name="get_shadow_windows",
                description=(
                    "List all windows in shadow (invisible) space with their "
                    "window_id, title, and app_id."
                ),
                parameters=types.Schema(type=types.Type.OBJECT, properties={}),
            ),
            types.FunctionDeclaration(
                name="ocr_region",
                description=(
                    "Read text from the screen or a specific window using OCR. "
                    "Use after take_screenshot to extract visible text content."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "window_title": types.Schema(
                            type=types.Type.STRING,
                            description="Window title to OCR. Omit for full screen.",
                        ),
                    },
                ),
            ),
            types.FunctionDeclaration(
                name="execute_complex_goal",
                description=(
                    "Delegate a complex multi-step task to the advanced AI planner. "
                    "Use for tasks requiring 4+ steps, multi-page interaction, "
                    "document creation, or workflows beyond simple open/search/close."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "goal": types.Schema(
                            type=types.Type.STRING,
                            description="Full description of the task to accomplish.",
                        ),
                    },
                    required=["goal"],
                ),
            ),
            types.FunctionDeclaration(
                name="hotkey",
                description="Press a keyboard shortcut (e.g. ctrl+c, alt+F4, super+e).",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "keys": types.Schema(
                            type=types.Type.STRING,
                            description="Key combination, e.g. ctrl+c, alt+F4, super+e",
                        ),
                    },
                    required=["keys"],
                ),
            ),
        ])
    ]
