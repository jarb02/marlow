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

_LANG_INSTRUCTIONS = {
    "es": "Responde siempre en espanol. Usa un tono natural, amigable y conciso.",
    "en": "Always respond in English. Use a natural, friendly, and concise tone.",
    "pt": "Responda sempre em portugues. Use um tom natural, amigavel e conciso.",
    "fr": "Reponds toujours en francais. Utilise un ton naturel, amical et concis.",
}


def build_system_prompt(user_name: str = "", language: str = "es") -> str:
    """Build the Marlow system prompt for Gemini.

    Identical personality for voice and text channels.
    """
    lang_instruction = _LANG_INSTRUCTIONS.get(
        language,
        f"Respond in {language}. Be natural, friendly, concise.",
    )

    return (
        f"You are Marlow, an AI desktop assistant integrated into Marlow OS "
        f"(a custom Linux desktop environment).\n"
        f"The user's name is {user_name or 'amigo'}. {lang_instruction}\n\n"
        f"You control the user's desktop through function calls. When the user "
        f"asks you to do something on their computer (search, open apps, close "
        f"windows, take screenshots, etc.), use the available tools.\n\n"
        f"Conversation guidelines:\n"
        f"- Be concise. 1-3 sentences max for most responses.\n"
        f"- For greetings, respond warmly but briefly.\n"
        f"- If a message combines a greeting or courtesy with an action "
        f"(search, open, close, etc.), ALWAYS call the relevant tool AND "
        f"respond briefly. Never reply only with text promising to do "
        f"something — execute the function call in the same turn.\n"
        f"- When executing actions, briefly acknowledge what you're doing.\n"
        f"- After completing an action, summarize the result naturally.\n"
        f"- If an action fails, explain simply and offer alternatives.\n"
        f"- Hold multi-turn conversations naturally. Remember context within this session.\n"
        f"- If the user asks to see something you found in shadow mode, use move_to_user.\n"
        f"- Never mention technical details like window IDs, JSON, or APIs.\n"
        f"- When the user says goodbye (adios, bye, etc.), respond briefly and end naturally.\n"
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
