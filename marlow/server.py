"""
Marlow — AI that works beside you, not instead of you.

MCP Server for Windows desktop automation with:
- Security from commit #1 (kill switch, confirmation, data sanitization)
- Background mode compatibility (silent methods)
- Zero telemetry — your data never leaves your machine

Usage:
    # Run directly
    python -m marlow.server

    # Or via the installed command
    marlow
    
    # Or with uvx (for MCP clients)
    uvx marlow-mcp
"""

import asyncio
import logging
import sys
from typing import Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    Tool,
    TextContent,
    ImageContent,
)

from marlow import __version__
from marlow.core.config import MarlowConfig, ensure_dirs
from marlow.core.safety import SafetyEngine
from marlow.core.sanitizer import DataSanitizer

# Phase 1 Tools
from marlow.tools import ui_tree, screenshot, mouse, keyboard, windows, system

# Phase 2 Tools
from marlow.tools import ocr, background, audio, voice, app_script
from marlow.core import escalation
from marlow.core import focus

# ─────────────────────────────────────────────────────────────
# Setup
# ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("marlow")

# Load config and initialize safety systems
config = MarlowConfig.load()
safety = SafetyEngine(config)
sanitizer = DataSanitizer(config)

# Create MCP server
app = Server("marlow")


# ─────────────────────────────────────────────────────────────
# Tool Definitions
# ─────────────────────────────────────────────────────────────

@app.list_tools()
async def list_tools() -> list[Tool]:
    """Register all Marlow tools with the MCP server."""
    return [
        # ── UI Tree (Primary vision — 0 tokens) ──
        Tool(
            name="get_ui_tree",
            description=(
                "Read the Windows UI Automation Accessibility Tree for a window. "
                "This is Marlow's primary 'vision' — understands what's on screen "
                "without screenshots. Cost: 0 tokens. Speed: ~10-50ms. "
                "ALWAYS try this before take_screenshot."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "window_title": {
                        "type": "string",
                        "description": "Window title to inspect. If omitted, uses the active window.",
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "Tree depth (default: 3). Higher = more detail.",
                        "default": 3,
                    },
                    "include_invisible": {
                        "type": "boolean",
                        "description": "Include non-visible elements.",
                        "default": False,
                    },
                },
            },
        ),

        # ── Screenshot (Last resort — ~1,500 tokens) ──
        Tool(
            name="take_screenshot",
            description=(
                "Take a screenshot of the screen or a specific window. "
                "⚠️ Costs ~1,500 tokens. Use get_ui_tree first (0 tokens). "
                "Use this only when UI tree is insufficient."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "window_title": {
                        "type": "string",
                        "description": "Capture specific window only.",
                    },
                    "region": {
                        "type": "object",
                        "description": "Capture region: {x, y, width, height}.",
                        "properties": {
                            "x": {"type": "integer"},
                            "y": {"type": "integer"},
                            "width": {"type": "integer"},
                            "height": {"type": "integer"},
                        },
                    },
                    "quality": {
                        "type": "integer",
                        "description": "JPEG quality 1-100 (default: 85).",
                        "default": 85,
                    },
                },
            },
        ),

        # ── Mouse ──
        Tool(
            name="click",
            description=(
                "Click a UI element by name (preferred) or at coordinates. "
                "By name: finds element in Accessibility Tree and clicks silently "
                "(works in background mode). By coordinates: real mouse click."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "element_name": {
                        "type": "string",
                        "description": "Name/text of element to click (e.g., 'Save', 'OK').",
                    },
                    "window_title": {
                        "type": "string",
                        "description": "Window to search in.",
                    },
                    "x": {"type": "integer", "description": "X coordinate."},
                    "y": {"type": "integer", "description": "Y coordinate."},
                    "button": {
                        "type": "string",
                        "enum": ["left", "right", "middle"],
                        "default": "left",
                    },
                    "double_click": {"type": "boolean", "default": False},
                },
            },
        ),

        # ── Keyboard ──
        Tool(
            name="type_text",
            description=(
                "Type text into an element by name (preferred) or at cursor position. "
                "By name: finds text field and types silently (background compatible). "
                "Direct: simulates keyboard at current cursor."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text to type.",
                    },
                    "element_name": {
                        "type": "string",
                        "description": "Name of text field (e.g., 'Search', 'Email').",
                    },
                    "window_title": {"type": "string"},
                    "clear_first": {"type": "boolean", "default": False},
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="press_key",
            description="Press a keyboard key. Examples: 'enter', 'tab', 'escape', 'f5', 'delete'.",
            inputSchema={
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Key to press.",
                    },
                    "times": {
                        "type": "integer",
                        "description": "Times to press (default: 1).",
                        "default": 1,
                    },
                },
                "required": ["key"],
            },
        ),
        Tool(
            name="hotkey",
            description=(
                "Execute keyboard shortcut. Examples: "
                "['ctrl','c'] for copy, ['ctrl','shift','s'] for save as, "
                "['alt','f4'] to close."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "keys": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Keys to press simultaneously.",
                    },
                },
                "required": ["keys"],
            },
        ),

        # ── Windows ──
        Tool(
            name="list_windows",
            description="List all open windows with titles, positions, and sizes.",
            inputSchema={
                "type": "object",
                "properties": {
                    "include_minimized": {"type": "boolean", "default": True},
                },
            },
        ),
        Tool(
            name="focus_window",
            description="Bring a window to the foreground.",
            inputSchema={
                "type": "object",
                "properties": {
                    "window_title": {
                        "type": "string",
                        "description": "Title or partial title of window.",
                    },
                },
                "required": ["window_title"],
            },
        ),
        Tool(
            name="manage_window",
            description=(
                "Manage a window: minimize, maximize, restore, close, move, or resize."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "window_title": {"type": "string"},
                    "action": {
                        "type": "string",
                        "enum": ["minimize", "maximize", "restore", "close",
                                 "move", "resize"],
                    },
                    "x": {"type": "integer", "description": "For move action."},
                    "y": {"type": "integer", "description": "For move action."},
                    "width": {"type": "integer", "description": "For resize action."},
                    "height": {"type": "integer", "description": "For resize action."},
                },
                "required": ["window_title", "action"],
            },
        ),

        # ── System ──
        Tool(
            name="run_command",
            description=(
                "Execute a PowerShell or CMD command. "
                "⚠️ Destructive commands (format, del /f, rm -rf, etc.) are BLOCKED."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Command to execute."},
                    "shell": {
                        "type": "string",
                        "enum": ["powershell", "cmd"],
                        "default": "powershell",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default: 30).",
                        "default": 30,
                    },
                },
                "required": ["command"],
            },
        ),
        Tool(
            name="open_application",
            description="Open an application by name (Start Menu) or file path.",
            inputSchema={
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "description": "App name (e.g., 'Notepad', 'Chrome').",
                    },
                    "app_path": {
                        "type": "string",
                        "description": "Full path to executable.",
                    },
                },
            },
        ),
        Tool(
            name="clipboard",
            description="Read from or write to the system clipboard.",
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["read", "write"],
                        "default": "read",
                    },
                    "text": {
                        "type": "string",
                        "description": "Text to write (for action='write').",
                    },
                },
            },
        ),
        Tool(
            name="system_info",
            description="Get system info: OS, CPU, RAM, disk usage, top processes.",
            inputSchema={"type": "object", "properties": {}},
        ),

        # ── Phase 2: OCR ──
        Tool(
            name="ocr_region",
            description=(
                "Extract text from a window or screen region using OCR (Tesseract). "
                "Cost: 0 tokens (returns text). Speed: ~200-500ms. "
                "Useful when UI Automation can't read text (images, custom controls)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "window_title": {
                        "type": "string",
                        "description": "Window to OCR. If omitted, captures full screen.",
                    },
                    "region": {
                        "type": "object",
                        "description": "Specific region: {x, y, width, height}.",
                        "properties": {
                            "x": {"type": "integer"},
                            "y": {"type": "integer"},
                            "width": {"type": "integer"},
                            "height": {"type": "integer"},
                        },
                    },
                    "language": {
                        "type": "string",
                        "description": "Tesseract language code (default: eng).",
                        "default": "eng",
                    },
                    "preprocess": {
                        "type": "boolean",
                        "description": "Apply image preprocessing for better accuracy.",
                        "default": True,
                    },
                },
            },
        ),

        # ── Phase 2: Smart Find (Escalation) ──
        Tool(
            name="smart_find",
            description=(
                "Find a UI element using escalating methods: "
                "1) UI Automation (0 tokens, ~10-50ms) → "
                "2) OCR (0 tokens, ~200-500ms) → "
                "3) Screenshot for LLM Vision (~1,500 tokens). "
                "BEST tool for finding elements — automatically picks the cheapest method."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Text/name of element to find (e.g., 'File', 'Save').",
                    },
                    "window_title": {
                        "type": "string",
                        "description": "Window to search in.",
                    },
                    "click_if_found": {
                        "type": "boolean",
                        "description": "Automatically click the element if found.",
                        "default": False,
                    },
                },
                "required": ["target"],
            },
        ),

        # ── Phase 2: Background Mode ──
        Tool(
            name="setup_background_mode",
            description=(
                "Configure background mode so Marlow works on a separate screen. "
                "Auto-detects: 2+ monitors → dual_monitor, 1 monitor → offscreen."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "preferred_mode": {
                        "type": "string",
                        "enum": ["dual_monitor", "offscreen"],
                        "description": "Force a specific mode. Auto-detects if omitted.",
                    },
                },
            },
        ),
        Tool(
            name="move_to_agent_screen",
            description="Move a window to the agent workspace (second monitor or offscreen area).",
            inputSchema={
                "type": "object",
                "properties": {
                    "window_title": {
                        "type": "string",
                        "description": "Window to move to agent screen.",
                    },
                },
                "required": ["window_title"],
            },
        ),
        Tool(
            name="move_to_user_screen",
            description="Move a window back to the user's primary monitor.",
            inputSchema={
                "type": "object",
                "properties": {
                    "window_title": {
                        "type": "string",
                        "description": "Window to move back to user screen.",
                    },
                },
                "required": ["window_title"],
            },
        ),
        Tool(
            name="get_agent_screen_state",
            description="List all windows currently on the agent screen/workspace.",
            inputSchema={"type": "object", "properties": {}},
        ),

        # ── Phase 2: Audio ──
        Tool(
            name="capture_system_audio",
            description=(
                "Record system audio (what you hear) via WASAPI loopback. "
                "Captures audio from speakers/headphones. Max 300 seconds."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "duration_seconds": {
                        "type": "integer",
                        "description": "Recording duration in seconds (default: 10, max: 300).",
                        "default": 10,
                    },
                },
            },
        ),
        Tool(
            name="capture_mic_audio",
            description="Record microphone audio. Max 300 seconds.",
            inputSchema={
                "type": "object",
                "properties": {
                    "duration_seconds": {
                        "type": "integer",
                        "description": "Recording duration in seconds (default: 10, max: 300).",
                        "default": 10,
                    },
                },
            },
        ),
        Tool(
            name="transcribe_audio",
            description=(
                "Transcribe an audio file using faster-whisper (CPU, int8). "
                "Supports auto language detection. First call downloads the model (~150MB). "
                "Use download_whisper_model first to avoid timeout on first transcription."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "audio_path": {
                        "type": "string",
                        "description": "Path to WAV audio file to transcribe.",
                    },
                    "language": {
                        "type": "string",
                        "description": "Language code (e.g., 'en', 'es') or 'auto'.",
                        "default": "auto",
                    },
                    "model_size": {
                        "type": "string",
                        "enum": ["tiny", "base", "small", "medium"],
                        "description": "Whisper model size (default: base).",
                        "default": "base",
                    },
                },
                "required": ["audio_path"],
            },
        ),
        Tool(
            name="download_whisper_model",
            description=(
                "Pre-download a Whisper model so transcribe_audio doesn't timeout. "
                "Downloads the model to local cache (~75MB tiny, ~150MB base, ~500MB small). "
                "Run this BEFORE first transcription to avoid delays."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "model_size": {
                        "type": "string",
                        "enum": ["tiny", "base", "small", "medium"],
                        "description": "Model to download (default: base).",
                        "default": "base",
                    },
                },
            },
        ),

        # ── Phase 2: Voice ──
        Tool(
            name="listen_for_command",
            description=(
                "Listen for a voice command via microphone. "
                "Starts recording immediately, transcribes, returns text. "
                "Includes silence detection."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "duration_seconds": {
                        "type": "integer",
                        "description": "How long to listen (default: 10, max: 60).",
                        "default": 10,
                    },
                    "language": {
                        "type": "string",
                        "description": "Language code or 'auto'.",
                        "default": "auto",
                    },
                    "model_size": {
                        "type": "string",
                        "enum": ["tiny", "base", "small", "medium"],
                        "default": "base",
                    },
                },
            },
        ),

        # ── Phase 2: COM Automation ──
        Tool(
            name="run_app_script",
            description=(
                "Run a Python script that controls an Office/Adobe app via COM. "
                "The script has access to 'app' (COM object). Store output in 'result'. "
                "Supported: Word, Excel, PowerPoint, Outlook, Photoshop, Access. "
                "⚠️ Sandboxed: no imports, no file access, no eval/exec."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "enum": ["word", "excel", "powerpoint", "outlook",
                                 "photoshop", "access"],
                        "description": "Application to control.",
                    },
                    "script": {
                        "type": "string",
                        "description": "Python script. Use 'app' for COM object, store output in 'result'.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Max execution time in seconds (default: 30).",
                        "default": 30,
                    },
                },
                "required": ["app_name", "script"],
            },
        ),

        # ── Safety ──
        Tool(
            name="restore_user_focus",
            description=(
                "Restore focus to the user's previously active window. "
                "Marlow automatically preserves focus, but call this if "
                "the user's window lost focus and needs manual correction."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="kill_switch",
            description=(
                "🛑 Emergency stop: immediately halt ALL Marlow automation. "
                "Use 'activate' to stop everything, 'reset' to resume, "
                "'status' to check current state."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["activate", "reset", "status"],
                        "description": "activate=stop all, reset=resume, status=check state.",
                    },
                },
                "required": ["action"],
            },
        ),
    ]


# ─────────────────────────────────────────────────────────────
# Tool Execution (with safety checks)
# ─────────────────────────────────────────────────────────────

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent | ImageContent]:
    """
    Execute a tool with safety checks.

    EVERY tool call passes through the safety engine:
    1. Save user focus
    2. Kill switch check
    3. Blocked app/command check
    4. Rate limit check
    5. Execute
    6. Sanitize output
    7. Restore user focus
    """

    # ── Save user's active window before any action ──
    # Skip for focus_window (intentionally changes focus) and
    # restore_user_focus (would overwrite the saved hwnd)
    _skip_focus = name in ("focus_window", "restore_user_focus")
    if not _skip_focus:
        focus.save_user_focus()

    try:
        return await _call_tool_inner(name, arguments)
    finally:
        # ── Restore user's focus after every tool call ──
        if not _skip_focus:
            focus.restore_user_focus()


async def _call_tool_inner(name: str, arguments: dict) -> list[TextContent | ImageContent]:
    """Inner tool execution — focus save/restore is handled by call_tool."""

    # ── Kill switch tool (always allowed) ──
    if name == "kill_switch":
        return await _handle_kill_switch(arguments)

    # ── Safety check for all other tools ──
    approved, reason = await safety.approve_action(name, name, arguments)
    if not approved:
        return [TextContent(type="text", text=reason)]

    # ── Execute the tool ──
    try:
        result = await _dispatch_tool(name, arguments)
    except Exception as e:
        logger.error(f"Tool execution error: {name}: {e}")
        result = {"error": str(e)}

    # ── Sanitize output (redact sensitive data) ──
    if isinstance(result, dict):
        result = sanitizer.sanitize_ui_tree(result)

    # ── Handle screenshot results (return as image) ──
    if name == "take_screenshot" and "image_base64" in result:
        import base64
        return [
            ImageContent(
                type="image",
                data=result["image_base64"],
                mimeType="image/jpeg",
            ),
            TextContent(
                type="text",
                text=f"Screenshot: {result.get('width')}x{result.get('height')} "
                     f"({result.get('size_kb')}KB) — Source: {result.get('source')}",
            ),
        ]

    # ── Handle smart_find with screenshot fallback (return image + context) ──
    if name == "smart_find" and result.get("requires_vision") and "image_base64" in result:
        import json
        image_data = result.pop("image_base64")
        return [
            ImageContent(
                type="image",
                data=image_data,
                mimeType="image/jpeg",
            ),
            TextContent(
                type="text",
                text=json.dumps(result, indent=2, ensure_ascii=False, default=str),
            ),
        ]

    # ── Return as text ──
    import json
    return [TextContent(
        type="text",
        text=json.dumps(result, indent=2, ensure_ascii=False, default=str),
    )]


async def _dispatch_tool(name: str, arguments: dict) -> dict:
    """Route tool call to the correct function."""

    tool_map = {
        # UI Tree
        "get_ui_tree": lambda args: ui_tree.get_ui_tree(
            window_title=args.get("window_title"),
            max_depth=args.get("max_depth", 3),
            include_invisible=args.get("include_invisible", False),
        ),
        # Screenshot
        "take_screenshot": lambda args: screenshot.take_screenshot(
            window_title=args.get("window_title"),
            region=args.get("region"),
            quality=args.get("quality", 85),
        ),
        # Mouse
        "click": lambda args: mouse.click(
            element_name=args.get("element_name"),
            window_title=args.get("window_title"),
            x=args.get("x"),
            y=args.get("y"),
            button=args.get("button", "left"),
            double_click=args.get("double_click", False),
        ),
        # Keyboard
        "type_text": lambda args: keyboard.type_text(
            text=args["text"],
            element_name=args.get("element_name"),
            window_title=args.get("window_title"),
            clear_first=args.get("clear_first", False),
        ),
        "press_key": lambda args: keyboard.press_key(
            key=args["key"],
            times=args.get("times", 1),
        ),
        "hotkey": lambda args: keyboard.hotkey(*args["keys"]),
        # Windows
        "list_windows": lambda args: windows.list_windows(
            include_minimized=args.get("include_minimized", True),
        ),
        "focus_window": lambda args: windows.focus_window(
            window_title=args["window_title"],
        ),
        "manage_window": lambda args: windows.manage_window(
            window_title=args["window_title"],
            action=args["action"],
            x=args.get("x"),
            y=args.get("y"),
            width=args.get("width"),
            height=args.get("height"),
        ),
        # System
        "run_command": lambda args: system.run_command(
            command=args["command"],
            shell=args.get("shell", "powershell"),
            timeout=args.get("timeout", 30),
        ),
        "open_application": lambda args: system.open_application(
            app_name=args.get("app_name"),
            app_path=args.get("app_path"),
        ),
        "clipboard": lambda args: system.clipboard(
            action=args.get("action", "read"),
            text=args.get("text"),
        ),
        "system_info": lambda args: system.system_info(),
        # Phase 2: OCR
        "ocr_region": lambda args: ocr.ocr_region(
            window_title=args.get("window_title"),
            region=args.get("region"),
            language=args.get("language", "eng"),
            preprocess=args.get("preprocess", True),
        ),
        # Phase 2: Smart Find
        "smart_find": lambda args: escalation.smart_find(
            target=args["target"],
            window_title=args.get("window_title"),
            click_if_found=args.get("click_if_found", False),
        ),
        # Phase 2: Background Mode
        "setup_background_mode": lambda args: background.setup_background_mode(
            preferred_mode=args.get("preferred_mode"),
        ),
        "move_to_agent_screen": lambda args: background.move_to_agent_screen(
            window_title=args["window_title"],
        ),
        "move_to_user_screen": lambda args: background.move_to_user_screen(
            window_title=args["window_title"],
        ),
        "get_agent_screen_state": lambda args: background.get_agent_screen_state(),
        # Phase 2: Audio
        "capture_system_audio": lambda args: audio.capture_system_audio(
            duration_seconds=args.get("duration_seconds", 10),
        ),
        "capture_mic_audio": lambda args: audio.capture_mic_audio(
            duration_seconds=args.get("duration_seconds", 10),
        ),
        "transcribe_audio": lambda args: audio.transcribe_audio(
            audio_path=args["audio_path"],
            language=args.get("language", "auto"),
            model_size=args.get("model_size", "base"),
        ),
        "download_whisper_model": lambda args: audio.download_whisper_model(
            model_size=args.get("model_size", "base"),
        ),
        # Phase 2: Voice
        "listen_for_command": lambda args: voice.listen_for_command(
            duration_seconds=args.get("duration_seconds", 10),
            language=args.get("language", "auto"),
            model_size=args.get("model_size", "base"),
        ),
        # Phase 2: COM Automation
        "run_app_script": lambda args: app_script.run_app_script(
            app_name=args["app_name"],
            script=args["script"],
            timeout=args.get("timeout", 30),
        ),
        # Safety
        "restore_user_focus": lambda args: focus.restore_user_focus_tool(),
    }

    handler = tool_map.get(name)
    if handler:
        return await handler(arguments)
    else:
        return {"error": f"Unknown tool: {name}"}


async def _handle_kill_switch(arguments: dict) -> list[TextContent]:
    """Handle kill switch commands."""
    action = arguments.get("action", "status")

    if action == "activate":
        safety._trigger_kill()
        return [TextContent(
            type="text",
            text="🛑 KILL SWITCH ACTIVATED — All Marlow automation has been stopped.\n"
                 "Use kill_switch(action='reset') to resume.",
        )]
    elif action == "reset":
        safety.reset_kill_switch()
        return [TextContent(
            type="text",
            text="✅ Kill switch reset — Marlow automation can resume.",
        )]
    elif action == "status":
        import json
        status = safety.get_status()
        return [TextContent(
            type="text",
            text=json.dumps(status, indent=2),
        )]
    else:
        return [TextContent(type="text", text=f"Unknown action: {action}")]


# ─────────────────────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────────────────────

async def _run_server():
    """Run the MCP server using stdio transport."""
    async with stdio_server() as (read_stream, write_stream):
        init_options = app.create_initialization_options()
        await app.run(read_stream, write_stream, init_options)


def main():
    """Start the Marlow MCP server."""
    ensure_dirs()

    logger.info(f"👻 Marlow v{__version__} starting...")
    logger.info(f"🔒 Security: confirmation_mode={config.security.confirmation_mode}")
    logger.info(f"🛑 Kill switch: {'enabled' if config.security.kill_switch_enabled else 'DISABLED'}")
    logger.info(f"📊 Telemetry: NEVER (zero data leaves your machine)")

    # Start kill switch listener
    safety.start_kill_switch()

    # Run MCP server via stdio
    asyncio.run(_run_server())


if __name__ == "__main__":
    main()
