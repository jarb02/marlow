"""
Marlow Linux — MCP Server for desktop automation on Sway/Wayland.

Parallel server to server.py (Windows). Uses the platform abstraction
layer for all desktop operations (AT-SPI2, Sway IPC, wtype, grim, PipeWire).

Usage:
    python -m marlow          # auto-detects platform
    python -m marlow.server_linux  # force Linux server

/ Servidor MCP Linux — automatizacion de escritorio en Sway/Wayland.
"""

import asyncio
import base64
import json
import logging
import sys

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

# Agnostic tools — import directly
from marlow.tools import memory, scraper, watcher, scheduler
from marlow.extensions import registry as ext_registry
from marlow.core import error_journal
from marlow.tools import help as help_mod

# Platform layer — replaces all Windows-specific modules
from marlow.platform import get_platform

# ─────────────────────────────────────────────────────────────
# Setup
# ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("marlow")

config = MarlowConfig.load()
safety = SafetyEngine(config)
sanitizer = DataSanitizer(config)

app = Server("marlow")

# Wire kill switch into scheduler
from marlow.tools.scheduler import set_kill_switch_check
set_kill_switch_check(lambda: safety.is_killed)

# Lazy platform singleton — initialized on first tool call
_platform = None


def _get_platform():
    """Get or create the platform singleton."""
    global _platform
    if _platform is None:
        _platform = get_platform()
        logger.info(f"Platform initialized: {_platform.name}")
    return _platform


# ─────────────────────────────────────────────────────────────
# Tool Definitions (51 tools: 29 platform + 22 agnostic)
# ─────────────────────────────────────────────────────────────

@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        # ── Platform: UI Tree ──
        Tool(
            name="get_ui_tree",
            description=(
                "Read the accessibility tree of a window or the entire desktop. "
                "Returns hierarchical structure with roles, names, states, and bounds. "
                "Zero tokens — reads structure directly via AT-SPI2."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "window_title": {
                        "type": "string",
                        "description": "Window title (substring match). If omitted, returns desktop tree.",
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "Max tree depth (default: 8).",
                    },
                },
            },
        ),
        Tool(
            name="find_elements",
            description=(
                "Search the accessibility tree for elements matching criteria. "
                "Supports fuzzy name matching (Levenshtein), role filter, and state filter."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Element name to search for (fuzzy match).",
                    },
                    "role": {
                        "type": "string",
                        "description": "Role filter (e.g., 'button', 'text', 'menu item').",
                    },
                    "states": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Required states (e.g., ['focused'], ['enabled', 'visible']).",
                    },
                    "window_title": {
                        "type": "string",
                        "description": "Limit search to this window.",
                    },
                },
            },
        ),
        Tool(
            name="get_element_properties",
            description=(
                "Get detailed properties for a specific element by tree path. "
                "Returns role, name, states, bounds, interfaces, actions, text, value."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Dot-separated tree path (e.g., '0.2.1').",
                    },
                    "window_title": {
                        "type": "string",
                        "description": "Window context for the path.",
                    },
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="do_action",
            description=(
                "Execute an action on an element via the AT-SPI2 Action interface. "
                "Common actions: 'click', 'activate', 'press', 'toggle'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Dot-separated tree path.",
                    },
                    "action_name": {
                        "type": "string",
                        "description": "Action to perform (e.g., 'click', 'activate').",
                    },
                    "window_title": {
                        "type": "string",
                        "description": "Window context.",
                    },
                },
                "required": ["path", "action_name"],
            },
        ),
        Tool(
            name="get_text",
            description="Get text content from an element via the AT-SPI2 Text interface.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Dot-separated tree path.",
                    },
                    "window_title": {
                        "type": "string",
                        "description": "Window context.",
                    },
                },
                "required": ["path"],
            },
        ),

        # ── Platform: Screenshot ──
        Tool(
            name="take_screenshot",
            description=(
                "Capture a screenshot of the full screen, a specific window, "
                "or a region. Returns PNG image. Uses grim on Wayland."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "window_title": {
                        "type": "string",
                        "description": "Capture only this window. If omitted, full screen.",
                    },
                    "region": {
                        "type": "object",
                        "description": "Region to capture: {x, y, width, height}.",
                        "properties": {
                            "x": {"type": "integer"},
                            "y": {"type": "integer"},
                            "width": {"type": "integer"},
                            "height": {"type": "integer"},
                        },
                    },
                },
            },
        ),

        # ── Platform: Input ──
        Tool(
            name="click",
            description="Click at absolute screen coordinates using ydotool.",
            inputSchema={
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "X coordinate."},
                    "y": {"type": "integer", "description": "Y coordinate."},
                    "button": {
                        "type": "string",
                        "enum": ["left", "right", "middle"],
                        "default": "left",
                    },
                },
                "required": ["x", "y"],
            },
        ),
        Tool(
            name="type_text",
            description="Type text into the focused window using wtype.",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text to type.",
                    },
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="press_key",
            description=(
                "Press and release a single key. Uses XKB key names "
                "(Return, Tab, Escape, BackSpace, Up, Down, F1-F12, etc.)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Key name (e.g., 'Return', 'Tab', 'Escape').",
                    },
                },
                "required": ["key"],
            },
        ),
        Tool(
            name="hotkey",
            description=(
                "Press a key combination (modifier + key). "
                "Example: hotkey('ctrl', 'shift', 't')."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "keys": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Keys to press together (e.g., ['ctrl', 'c']).",
                    },
                },
                "required": ["keys"],
            },
        ),
        Tool(
            name="move_mouse",
            description="Move the mouse to absolute screen coordinates.",
            inputSchema={
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "X coordinate."},
                    "y": {"type": "integer", "description": "Y coordinate."},
                },
                "required": ["x", "y"],
            },
        ),

        # ── Platform: Windows ──
        Tool(
            name="list_windows",
            description="List all open windows with titles, positions, and app info.",
            inputSchema={
                "type": "object",
                "properties": {
                    "include_minimized": {
                        "type": "boolean",
                        "description": "Include minimized windows (default: true).",
                        "default": True,
                    },
                },
            },
        ),
        Tool(
            name="focus_window",
            description="Focus a window by title substring or container ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "window_title": {
                        "type": "string",
                        "description": "Window title (substring) or Sway con_id.",
                    },
                },
                "required": ["window_title"],
            },
        ),
        Tool(
            name="manage_window",
            description=(
                "Perform a management action on a window: "
                "minimize, maximize, restore, close, move, resize, fullscreen."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "window_title": {
                        "type": "string",
                        "description": "Window title or ID.",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["minimize", "maximize", "restore", "close",
                                 "move", "resize", "fullscreen"],
                    },
                    "x": {"type": "integer", "description": "X for move."},
                    "y": {"type": "integer", "description": "Y for move."},
                    "width": {"type": "integer", "description": "Width for resize."},
                    "height": {"type": "integer", "description": "Height for resize."},
                },
                "required": ["window_title", "action"],
            },
        ),

        # ── Platform: System ──
        Tool(
            name="run_command",
            description="Execute a shell command via bash. Returns stdout, stderr, exit code.",
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Max seconds (default: 30).",
                        "default": 30,
                    },
                },
                "required": ["command"],
            },
        ),
        Tool(
            name="open_application",
            description="Launch an application by name or path. Uses which + xdg-open fallback.",
            inputSchema={
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "description": "Application name or path (e.g., 'firefox', 'nautilus').",
                    },
                },
                "required": ["app_name"],
            },
        ),
        Tool(
            name="system_info",
            description="Get system information: OS, CPU, memory, display.",
            inputSchema={"type": "object", "properties": {}},
        ),

        # ── Platform: Audio ──
        Tool(
            name="capture_system_audio",
            description=(
                "Record system audio via PipeWire monitor loopback. "
                "Captures what's playing through speakers. Max 300 seconds."
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
            description="Record microphone audio via PipeWire. Max 300 seconds.",
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

        # ── Platform: Focus ──
        Tool(
            name="restore_user_focus",
            description=(
                "Restore focus to the user's previously active window. "
                "Marlow auto-preserves focus, but call this for manual correction."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),

        # ── Platform: Accessibility ──
        Tool(
            name="start_ui_monitor",
            description=(
                "Start real-time UI event monitoring via AT-SPI2. "
                "Detects window opens/closes and focus changes."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="stop_ui_monitor",
            description="Stop the AT-SPI2 event monitor.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="detect_dialogs",
            description=(
                "Scan the accessibility tree for active dialog windows. "
                "Returns title, message, buttons, type, and app for each dialog."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),

        # ── Platform: OCR ──
        Tool(
            name="ocr_region",
            description=(
                "Extract text from a screen region using Tesseract OCR. "
                "Returns full text and word-level bounding boxes with confidence. "
                "Can target a specific window or region."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "window_title": {
                        "type": "string",
                        "description": "Window to OCR. If omitted, full screen.",
                    },
                    "region": {
                        "type": "object",
                        "description": "Region: {x, y, width, height}.",
                        "properties": {
                            "x": {"type": "integer"},
                            "y": {"type": "integer"},
                            "width": {"type": "integer"},
                            "height": {"type": "integer"},
                        },
                    },
                    "language": {
                        "type": "string",
                        "description": "Tesseract language code (default: 'eng').",
                        "default": "eng",
                    },
                },
            },
        ),
        Tool(
            name="list_ocr_languages",
            description="List available Tesseract OCR language packs.",
            inputSchema={"type": "object", "properties": {}},
        ),

        # ── Platform: Smart Find / Escalation ──
        Tool(
            name="smart_find",
            description=(
                "Find a UI element using escalating strategies: "
                "1) AT-SPI2 accessibility tree (fast), "
                "2) OCR text search (finds visible text), "
                "3) Screenshot fallback (returns image for visual analysis). "
                "Returns the found element with method used and confidence score."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Element name or visible text to find.",
                    },
                    "role": {
                        "type": "string",
                        "description": "Role filter (e.g., 'button', 'text').",
                    },
                    "window_title": {
                        "type": "string",
                        "description": "Limit search to this window.",
                    },
                },
            },
        ),
        Tool(
            name="cascade_find",
            description=(
                "Find an element trying multiple strategies in order: "
                "exact match, partial name, all windows, full-screen OCR. "
                "Returns the first successful result with strategy metadata."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Element name or text to find.",
                    },
                    "role": {
                        "type": "string",
                        "description": "Role filter.",
                    },
                    "window_title": {
                        "type": "string",
                        "description": "Limit initial search to this window.",
                    },
                },
            },
        ),

        # ── Platform: Set-of-Mark (SoM) ──
        Tool(
            name="get_annotated_screenshot",
            description=(
                "Take a screenshot with numbered [1], [2], [3]... labels drawn "
                "on each interactive UI element. Returns annotated PNG + element map. "
                "Use som_click(index) to click an element by its number."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "window_title": {
                        "type": "string",
                        "description": "Window to annotate. If omitted, uses focused window.",
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "Max tree depth for element discovery (default: 10).",
                    },
                },
            },
        ),
        Tool(
            name="som_click",
            description=(
                "Click an element by its numbered label from get_annotated_screenshot. "
                "Tries AT-SPI2 action first, falls back to coordinate click."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "index": {
                        "type": "integer",
                        "description": "Element number from the annotated screenshot.",
                    },
                    "action": {
                        "type": "string",
                        "description": "AT-SPI2 action name (default: 'click').",
                        "default": "click",
                    },
                },
                "required": ["index"],
            },
        ),

        # ── Agnostic: Memory ──
        Tool(
            name="memory_save",
            description="Save a value persistently across sessions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Unique key."},
                    "value": {"type": "string", "description": "Text/data to store."},
                    "category": {
                        "type": "string",
                        "enum": ["general", "preferences", "projects", "tasks"],
                        "default": "general",
                    },
                },
                "required": ["key", "value"],
            },
        ),
        Tool(
            name="memory_recall",
            description="Recall stored memories by key and/or category.",
            inputSchema={
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "category": {
                        "type": "string",
                        "enum": ["general", "preferences", "projects", "tasks"],
                    },
                },
            },
        ),
        Tool(
            name="memory_delete",
            description="Delete a specific memory by key and category.",
            inputSchema={
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "category": {
                        "type": "string",
                        "enum": ["general", "preferences", "projects", "tasks"],
                        "default": "general",
                    },
                },
                "required": ["key"],
            },
        ),
        Tool(
            name="memory_list",
            description="List all stored memories organized by category.",
            inputSchema={"type": "object", "properties": {}},
        ),

        # ── Agnostic: Web Scraper ──
        Tool(
            name="scrape_url",
            description="Extract content from a URL. Formats: text, links, tables, html.",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to scrape."},
                    "selector": {"type": "string", "description": "CSS selector filter."},
                    "format": {
                        "type": "string",
                        "enum": ["text", "links", "tables", "html"],
                        "default": "text",
                    },
                },
                "required": ["url"],
            },
        ),

        # ── Agnostic: Extensions ──
        Tool(
            name="extensions_list",
            description="List all installed Marlow extensions.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="extensions_install",
            description="Install a Marlow extension from pip.",
            inputSchema={
                "type": "object",
                "properties": {
                    "package": {"type": "string", "description": "pip package name."},
                },
                "required": ["package"],
            },
        ),
        Tool(
            name="extensions_uninstall",
            description="Uninstall a Marlow extension.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Extension name."},
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="extensions_audit",
            description="Audit an extension's security and permissions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Extension name."},
                },
                "required": ["name"],
            },
        ),

        # ── Agnostic: Folder Watcher ──
        Tool(
            name="watch_folder",
            description="Start monitoring a folder for file changes.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Folder path."},
                    "events": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["created", "modified", "deleted", "moved"]},
                    },
                    "recursive": {"type": "boolean", "default": False},
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="unwatch_folder",
            description="Stop monitoring a folder.",
            inputSchema={
                "type": "object",
                "properties": {
                    "watch_id": {"type": "string"},
                },
                "required": ["watch_id"],
            },
        ),
        Tool(
            name="get_watch_events",
            description="Get detected filesystem events.",
            inputSchema={
                "type": "object",
                "properties": {
                    "watch_id": {"type": "string"},
                    "limit": {"type": "integer", "default": 50},
                    "since": {"type": "string", "description": "ISO timestamp."},
                },
            },
        ),
        Tool(
            name="list_watchers",
            description="List all active folder watchers.",
            inputSchema={"type": "object", "properties": {}},
        ),

        # ── Agnostic: Task Scheduler ──
        Tool(
            name="schedule_task",
            description="Schedule a recurring shell command.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Unique task name."},
                    "command": {"type": "string", "description": "Shell command."},
                    "interval_seconds": {
                        "type": "integer",
                        "description": "Run every N seconds (default: 300, min: 10).",
                        "default": 300,
                    },
                    "shell": {
                        "type": "string",
                        "enum": ["bash", "sh"],
                        "default": "bash",
                    },
                    "max_runs": {"type": "integer"},
                },
                "required": ["name", "command"],
            },
        ),
        Tool(
            name="list_scheduled_tasks",
            description="List all scheduled tasks with status.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="remove_task",
            description="Remove a scheduled task by name.",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_name": {"type": "string"},
                },
                "required": ["task_name"],
            },
        ),
        Tool(
            name="get_task_history",
            description="Get execution history for scheduled tasks.",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_name": {"type": "string"},
                    "limit": {"type": "integer", "default": 20},
                },
            },
        ),

        # ── Agnostic: Error Journal ──
        Tool(
            name="get_error_journal",
            description="Show method failure/success records per tool+app.",
            inputSchema={
                "type": "object",
                "properties": {
                    "window": {"type": "string", "description": "Filter by app."},
                },
            },
        ),
        Tool(
            name="clear_error_journal",
            description="Clear error journal entries.",
            inputSchema={
                "type": "object",
                "properties": {
                    "window": {"type": "string", "description": "Clear for this app only."},
                },
            },
        ),

        # ── Safety ──
        Tool(
            name="kill_switch",
            description=(
                "Emergency stop: halt ALL Marlow automation. "
                "activate=stop, reset=resume, status=check."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["activate", "reset", "status"],
                    },
                },
                "required": ["action"],
            },
        ),

        # ── Help ──
        Tool(
            name="get_capabilities",
            description="List all Marlow tools organized by category.",
            inputSchema={
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                },
            },
        ),
        Tool(
            name="get_version",
            description="Get Marlow version and system state.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


# ─────────────────────────────────────────────────────────────
# Tool Execution (with safety + focus guard)
# ─────────────────────────────────────────────────────────────

# Saved focus snapshot for the focus guard
_saved_focus = None


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent | ImageContent]:
    """Execute a tool with safety checks and focus guard."""
    global _saved_focus

    p = _get_platform()

    # Save focus before action (skip for focus-changing tools)
    _skip_focus = name in ("focus_window", "restore_user_focus")
    if not _skip_focus:
        try:
            _saved_focus = p.focus.save_user_focus()
        except Exception:
            _saved_focus = None

    try:
        return await _call_tool_inner(name, arguments)
    finally:
        if not _skip_focus and _saved_focus is not None:
            try:
                p.focus.restore_user_focus(_saved_focus)
            except Exception:
                pass


async def _call_tool_inner(name: str, arguments: dict) -> list[TextContent | ImageContent]:
    """Safety check + dispatch + sanitize."""

    # Kill switch always allowed
    if name == "kill_switch":
        return _handle_kill_switch(arguments)

    # Safety check
    approved, reason = await safety.approve_action(name, name, arguments)
    if not approved:
        return [TextContent(type="text", text=reason)]

    # Dispatch
    try:
        result = await _dispatch_tool(name, arguments)
    except Exception as e:
        logger.error("Tool execution error: %s: %s", name, e)
        result = {"error": str(e)}

    # Sanitize output
    if isinstance(result, dict):
        result = sanitizer.sanitize_ui_tree(result)

    # Handle screenshot results
    if name == "take_screenshot" and isinstance(result, bytes):
        image_b64 = base64.b64encode(result).decode()
        return [
            ImageContent(type="image", data=image_b64, mimeType="image/png"),
            TextContent(type="text", text=f"Screenshot captured ({len(result):,} bytes)"),
        ]

    # Handle smart_find/cascade_find with screenshot fallback
    if (name in ("smart_find", "cascade_find")
            and isinstance(result, dict)
            and result.get("requires_vision")
            and "image_base64" in result):
        image_data = result.pop("image_base64")
        return [
            ImageContent(type="image", data=image_data, mimeType="image/png"),
            TextContent(
                type="text",
                text=json.dumps(result, indent=2, ensure_ascii=False, default=str),
            ),
        ]

    # Handle get_annotated_screenshot — return image + element map
    if (name == "get_annotated_screenshot"
            and isinstance(result, dict)
            and result.get("success")
            and "image" in result):
        image_data = result.pop("image")
        return [
            ImageContent(type="image", data=image_data, mimeType="image/png"),
            TextContent(
                type="text",
                text=json.dumps(result, indent=2, ensure_ascii=False, default=str),
            ),
        ]

    # Return as JSON text
    return [TextContent(
        type="text",
        text=json.dumps(result, indent=2, ensure_ascii=False, default=str),
    )]


async def _dispatch_tool(name: str, arguments: dict) -> dict:
    """Route tool call to the correct handler."""
    p = _get_platform()

    tool_map = {
        # ── Platform: UI Tree ──
        "get_ui_tree": lambda args: p.ui_tree.get_tree(
            window_title=args.get("window_title"),
            max_depth=args.get("max_depth"),
        ),
        "find_elements": lambda args: p.ui_tree.find_elements(
            name=args.get("name"),
            role=args.get("role"),
            states=args.get("states"),
            window_title=args.get("window_title"),
        ),
        "get_element_properties": lambda args: p.ui_tree.get_element_properties(
            path=args["path"],
            window_title=args.get("window_title"),
        ),
        "do_action": lambda args: p.ui_tree.do_action(
            path=args["path"],
            action_name=args["action_name"],
            window_title=args.get("window_title"),
        ),
        "get_text": lambda args: p.ui_tree.get_text(
            path=args["path"],
            window_title=args.get("window_title"),
        ),

        # ── Platform: Screenshot ──
        "take_screenshot": lambda args: _take_screenshot(p, args),

        # ── Platform: Input ──
        "click": lambda args: p.input.click(
            x=args["x"],
            y=args["y"],
            button=args.get("button", "left"),
        ),
        "type_text": lambda args: p.input.type_text(
            text=args["text"],
        ),
        "press_key": lambda args: p.input.press_key(
            key=args["key"],
        ),
        "hotkey": lambda args: p.input.hotkey(*args["keys"]),
        "move_mouse": lambda args: p.input.move_mouse(
            x=args["x"],
            y=args["y"],
        ),

        # ── Platform: Windows ──
        "list_windows": lambda args: _list_windows(p, args),
        "focus_window": lambda args: p.windows.focus_window(
            identifier=args["window_title"],
        ),
        "manage_window": lambda args: p.windows.manage_window(
            identifier=args["window_title"],
            action=args["action"],
            x=args.get("x"),
            y=args.get("y"),
            width=args.get("width"),
            height=args.get("height"),
        ),

        # ── Platform: System ──
        "run_command": lambda args: p.system.run_command(
            command=args["command"],
            timeout=args.get("timeout", 30),
        ),
        "open_application": lambda args: p.system.open_application(
            name_or_path=args["app_name"],
        ),
        "system_info": lambda args: p.system.get_system_info(),

        # ── Platform: Audio ──
        "capture_system_audio": lambda args: p.audio.capture_system_audio(
            duration_seconds=args.get("duration_seconds", 10),
        ),
        "capture_mic_audio": lambda args: p.audio.capture_mic_audio(
            duration_seconds=args.get("duration_seconds", 10),
        ),

        # ── Platform: Focus ──
        "restore_user_focus": lambda args: _restore_focus(p),

        # ── Platform: Accessibility ──
        "start_ui_monitor": lambda args: _start_monitor(p),
        "stop_ui_monitor": lambda args: _stop_monitor(p),
        "detect_dialogs": lambda args: {"dialogs": p.accessibility.detect_dialogs()},

        # ── Platform: OCR ──
        "ocr_region": lambda args: _ocr_region(p, args),
        "list_ocr_languages": lambda args: {"languages": p.ocr.list_languages()},

        # ── Platform: Smart Find / Escalation ──
        "smart_find": lambda args: p.escalation.smart_find(
            name=args.get("name"),
            role=args.get("role"),
            window_title=args.get("window_title"),
        ),
        "cascade_find": lambda args: p.cascade_recovery.cascade_find(
            name=args.get("name"),
            role=args.get("role"),
            window_title=args.get("window_title"),
        ),

        # ── Platform: Set-of-Mark (SoM) ──
        "get_annotated_screenshot": lambda args: p.som.get_annotated_screenshot(
            window_title=args.get("window_title"),
            max_depth=args.get("max_depth"),
        ),
        "som_click": lambda args: p.som.som_click(
            index=args["index"],
            action=args.get("action", "click"),
        ),

        # ── Agnostic: Memory ──
        "memory_save": lambda args: memory.memory_save(
            key=args["key"],
            value=args["value"],
            category=args.get("category", "general"),
        ),
        "memory_recall": lambda args: memory.memory_recall(
            key=args.get("key"),
            category=args.get("category"),
        ),
        "memory_delete": lambda args: memory.memory_delete(
            key=args["key"],
            category=args.get("category", "general"),
        ),
        "memory_list": lambda args: memory.memory_list(),

        # ── Agnostic: Web Scraper ──
        "scrape_url": lambda args: scraper.scrape_url(
            url=args["url"],
            selector=args.get("selector"),
            format=args.get("format", "text"),
        ),

        # ── Agnostic: Extensions ──
        "extensions_list": lambda args: ext_registry.extensions_list(),
        "extensions_install": lambda args: ext_registry.extensions_install(
            package=args["package"],
        ),
        "extensions_uninstall": lambda args: ext_registry.extensions_uninstall(
            name=args["name"],
        ),
        "extensions_audit": lambda args: ext_registry.extensions_audit(
            name=args["name"],
        ),

        # ── Agnostic: Folder Watcher ──
        "watch_folder": lambda args: watcher.watch_folder(
            path=args["path"],
            events=args.get("events"),
            recursive=args.get("recursive", False),
        ),
        "unwatch_folder": lambda args: watcher.unwatch_folder(
            watch_id=args["watch_id"],
        ),
        "get_watch_events": lambda args: watcher.get_watch_events(
            watch_id=args.get("watch_id"),
            limit=args.get("limit", 50),
            since=args.get("since"),
        ),
        "list_watchers": lambda args: watcher.list_watchers(),

        # ── Agnostic: Task Scheduler ──
        "schedule_task": lambda args: scheduler.schedule_task(
            name=args["name"],
            command=args["command"],
            interval_seconds=args.get("interval_seconds", 300),
            shell=args.get("shell", "bash"),
            max_runs=args.get("max_runs"),
        ),
        "list_scheduled_tasks": lambda args: scheduler.list_scheduled_tasks(),
        "remove_task": lambda args: scheduler.remove_task(
            task_name=args["task_name"],
        ),
        "get_task_history": lambda args: scheduler.get_task_history(
            task_name=args.get("task_name"),
            limit=args.get("limit", 20),
        ),

        # ── Agnostic: Error Journal ──
        "get_error_journal": lambda args: error_journal.get_error_journal(
            window=args.get("window"),
        ),
        "clear_error_journal": lambda args: error_journal.clear_error_journal(
            window=args.get("window"),
        ),

        # ── Help ──
        "get_capabilities": lambda args: help_mod.get_capabilities(
            category=args.get("category"),
        ),
        "get_version": lambda args: _get_version(),
    }

    handler = tool_map.get(name)
    if handler:
        result = handler(arguments)
        # Await if coroutine
        if asyncio.iscoroutine(result):
            result = await result
        return result
    else:
        return {"error": f"Unknown tool: {name}"}


# ─────────────────────────────────────────────────────────────
# Platform helper wrappers
# ─────────────────────────────────────────────────────────────

def _parse_region(args: dict):
    """Convert region dict to tuple if present."""
    region = args.get("region")
    if region and isinstance(region, dict):
        return (
            region.get("x", 0),
            region.get("y", 0),
            region.get("width", 0),
            region.get("height", 0),
        )
    return None


def _take_screenshot(p, args: dict):
    """Capture screenshot, handle region tuple conversion."""
    return p.screen.screenshot(
        window_title=args.get("window_title"),
        region=_parse_region(args),
    )


def _ocr_region(p, args: dict) -> dict:
    """OCR a screen region with optional region crop."""
    region_tuple = _parse_region(args)
    return p.ocr.ocr_region(
        window_title=args.get("window_title"),
        region=region_tuple,
        language=args.get("language", "eng"),
    )


def _list_windows(p, args: dict) -> dict:
    """List windows, convert WindowInfo dataclasses to dicts."""
    windows = p.windows.list_windows(
        include_minimized=args.get("include_minimized", True),
    )
    return {
        "success": True,
        "windows": [
            {
                "id": w.identifier,
                "title": w.title,
                "app_name": w.app_name,
                "pid": w.pid,
                "is_focused": w.is_focused,
                "is_visible": w.is_visible,
                "x": w.x, "y": w.y,
                "width": w.width, "height": w.height,
                "extra": w.extra,
            }
            for w in windows
        ],
        "count": len(windows),
    }


def _restore_focus(p) -> dict:
    """Restore focus to the last saved snapshot."""
    global _saved_focus
    if _saved_focus:
        ok = p.focus.restore_user_focus(_saved_focus)
        return {"success": ok}
    return {"success": False, "error": "No saved focus snapshot"}


def _start_monitor(p) -> dict:
    """Start AT-SPI2 event monitoring."""
    ok = p.accessibility.start_listening()
    return {"success": ok}


def _stop_monitor(p) -> dict:
    """Stop AT-SPI2 event monitoring."""
    ok = p.accessibility.stop_listening()
    return {"success": ok}


def _get_version() -> dict:
    """Version info for Linux server."""
    return {
        "version": __version__,
        "platform": "linux",
        "tools_registered": 49,
        "kill_switch": safety.get_status(),
    }


def _handle_kill_switch(arguments: dict) -> list[TextContent]:
    """Handle kill switch commands."""
    action = arguments.get("action", "status")
    if action == "activate":
        safety._trigger_kill()
        return [TextContent(
            type="text",
            text="KILL SWITCH ACTIVATED -- All Marlow automation stopped.\n"
                 "Use kill_switch(action='reset') to resume.",
        )]
    elif action == "reset":
        safety.reset_kill_switch()
        return [TextContent(
            type="text",
            text="Kill switch reset -- Marlow automation can resume.",
        )]
    elif action == "status":
        status = safety.get_status()
        return [TextContent(
            type="text",
            text=json.dumps(status, indent=2),
        )]
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
    """Start the Marlow Linux MCP server."""
    import os
    # Prevent AT-SPI2 dbind from aborting the process on connection failure
    os.environ.setdefault("NO_AT_BRIDGE", "0")
    os.environ.setdefault("GSETTINGS_BACKEND", "memory")

    ensure_dirs()

    logger.info("Marlow v%s (Linux) starting...", __version__)
    logger.info("Security: confirmation_mode=%s", config.security.confirmation_mode)
    logger.info("Kill switch: %s",
                "enabled" if config.security.kill_switch_enabled else "DISABLED")
    logger.info("Telemetry: NEVER (zero data leaves your machine)")

    # Start kill switch (graceful — no keyboard module needed)
    safety.start_kill_switch()

    # Log registered tools
    logger.info("Tools: 51 registered (29 platform + 22 agnostic)")
    logger.info("Platform providers: Sway IPC, wtype, ydotool, grim, AT-SPI2, PipeWire")
    logger.info("Skipped (not yet ported): CDP, LfD, SoM, voice_hotkey, "
                "background_mode, app_script, visual_diff, "
                "workflows, adaptive, clipboard_history, TTS, wait_for_*")

    # Run MCP server via stdio
    asyncio.run(_run_server())


if __name__ == "__main__":
    main()
