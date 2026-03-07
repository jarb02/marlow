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
from marlow.core import workflows
from marlow.tools import audio as audio_tools
# CDP — lazy import to avoid crash if websocket-client not installed
cdp_manager = None

def _get_cdp_manager():
    global cdp_manager
    if cdp_manager is None:
        from marlow.core import cdp_manager as _cdp
        cdp_manager = _cdp
    return cdp_manager
from marlow.core import adaptive
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
# Agent mode: higher rate limit (120/min vs default 30/min)
# Autonomous agents make 40-60 calls/min during real tasks.
config.security.max_actions_per_minute = 120
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
# Tool Definitions (101 tools: 71 platform + 30 agnostic)
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

        # ── Platform: Wait ──
        Tool(
            name="wait_for_element",
            description=(
                "Poll until a UI element appears (AT-SPI2 with OCR fallback). "
                "Returns when found or after timeout."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Element name to wait for."},
                    "role": {"type": "string", "description": "Role filter (e.g., 'button')."},
                    "window_title": {"type": "string", "description": "Limit to this window."},
                    "timeout": {"type": "number", "description": "Max seconds to wait (default: 10)."},
                    "interval": {"type": "number", "description": "Poll interval seconds (default: 0.5)."},
                },
            },
        ),
        Tool(
            name="wait_for_text",
            description=(
                "Poll OCR until target text appears on screen. "
                "Returns when found or after timeout."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to wait for."},
                    "window_title": {"type": "string", "description": "Limit OCR to this window."},
                    "timeout": {"type": "number", "description": "Max seconds (default: 10)."},
                    "interval": {"type": "number", "description": "Poll interval (default: 0.5)."},
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="wait_for_window",
            description=(
                "Poll until a window with matching title appears. "
                "Uses fuzzy substring match on window titles."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Window title to wait for."},
                    "timeout": {"type": "number", "description": "Max seconds (default: 10)."},
                    "interval": {"type": "number", "description": "Poll interval (default: 0.5)."},
                },
                "required": ["title"],
            },
        ),
        Tool(
            name="wait_for_idle",
            description=(
                "Poll until screen content stabilizes (stops changing). "
                "Compares consecutive screenshots to detect stability."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "window_title": {"type": "string", "description": "Limit to this window."},
                    "timeout": {"type": "number", "description": "Max seconds (default: 10)."},
                    "threshold": {
                        "type": "number",
                        "description": "Similarity threshold 0-1 (default: 0.95).",
                    },
                },
            },
        ),

        # ── CDP (Chrome DevTools Protocol) ──
        Tool(
            name="cdp_discover",
            description=(
                "Scan localhost ports for apps with CDP (Chrome DevTools Protocol) enabled. "
                "Finds Electron apps, Chrome with --remote-debugging-port, etc. "
                "Returns list of targets with port, title, URL, and WebSocket URL."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "port_start": {
                        "type": "integer",
                        "description": "Start of port range to scan (default: 9222).",
                        "default": 9222,
                    },
                    "port_end": {
                        "type": "integer",
                        "description": "End of port range to scan (default: 9250).",
                        "default": 9250,
                    },
                },
            },
        ),
        Tool(
            name="cdp_connect",
            description=(
                "Connect to a CDP endpoint on a given port. Establishes WebSocket "
                "connection to the first debuggable page target."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "port": {
                        "type": "integer",
                        "description": "Port number of the CDP endpoint.",
                    },
                },
                "required": ["port"],
            },
        ),
        Tool(
            name="cdp_disconnect",
            description="Disconnect from a CDP endpoint.",
            inputSchema={
                "type": "object",
                "properties": {
                    "port": {
                        "type": "integer",
                        "description": "Port number to disconnect from.",
                    },
                },
                "required": ["port"],
            },
        ),
        Tool(
            name="cdp_list_connections",
            description="List all active CDP connections.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="cdp_send",
            description=(
                "Send a raw CDP command. For advanced use when specific CDP methods "
                "are needed beyond the convenience tools."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "port": {
                        "type": "integer",
                        "description": "CDP port.",
                    },
                    "method": {
                        "type": "string",
                        "description": "CDP method (e.g., 'Network.enable', 'CSS.getComputedStyleForNode').",
                    },
                    "params": {
                        "type": "object",
                        "description": "Optional parameters for the CDP method.",
                    },
                },
                "required": ["port", "method"],
            },
        ),
        Tool(
            name="cdp_click",
            description=(
                "Click at page coordinates via CDP. 100% invisible — no focus steal, "
                "no mouse movement. Coordinates are relative to page viewport."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "port": {
                        "type": "integer",
                        "description": "CDP port.",
                    },
                    "x": {
                        "type": "integer",
                        "description": "X coordinate in page viewport.",
                    },
                    "y": {
                        "type": "integer",
                        "description": "Y coordinate in page viewport.",
                    },
                },
                "required": ["port", "x", "y"],
            },
        ),
        Tool(
            name="cdp_type_text",
            description=(
                "Type text via CDP. 100% invisible — no focus steal, no keyboard events. "
                "Focus the target input first with cdp_click or cdp_click_selector."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "port": {
                        "type": "integer",
                        "description": "CDP port.",
                    },
                    "text": {
                        "type": "string",
                        "description": "Text to type.",
                    },
                },
                "required": ["port", "text"],
            },
        ),
        Tool(
            name="cdp_key_combo",
            description=(
                "Press a key combination via CDP (e.g., Ctrl+A, Enter, Escape). "
                "100% invisible."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "port": {
                        "type": "integer",
                        "description": "CDP port.",
                    },
                    "key": {
                        "type": "string",
                        "description": "Key name (e.g., 'a', 'Enter', 'Tab', 'Escape').",
                    },
                    "modifiers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Modifier keys: 'ctrl', 'alt', 'shift', 'meta'.",
                    },
                },
                "required": ["port", "key"],
            },
        ),
        Tool(
            name="cdp_screenshot",
            description=(
                "Take screenshot via CDP. Works even if window is behind others or minimized. "
                "Returns base64 image."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "port": {
                        "type": "integer",
                        "description": "CDP port.",
                    },
                    "format": {
                        "type": "string",
                        "enum": ["png", "jpeg"],
                        "description": "Image format (default: png).",
                        "default": "png",
                    },
                },
                "required": ["port"],
            },
        ),
        Tool(
            name="cdp_evaluate",
            description=(
                "Evaluate JavaScript expression in the page context via CDP. "
                "Returns the result value and type."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "port": {
                        "type": "integer",
                        "description": "CDP port.",
                    },
                    "expression": {
                        "type": "string",
                        "description": "JavaScript expression to evaluate.",
                    },
                },
                "required": ["port", "expression"],
            },
        ),
        Tool(
            name="cdp_get_dom",
            description=(
                "Get the DOM tree of the page via CDP. Returns structured node tree "
                "with tag names, attributes, and children."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "port": {
                        "type": "integer",
                        "description": "CDP port.",
                    },
                    "depth": {
                        "type": "integer",
                        "description": "Tree depth (-1 = full tree, default).",
                        "default": -1,
                    },
                },
                "required": ["port"],
            },
        ),
        Tool(
            name="cdp_click_selector",
            description=(
                "Click an element by CSS selector via CDP. Executes "
                "document.querySelector(selector).click() in the page."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "port": {
                        "type": "integer",
                        "description": "CDP port.",
                    },
                    "css_selector": {
                        "type": "string",
                        "description": "CSS selector (e.g., '#submit-btn', '.nav-link').",
                    },
                },
                "required": ["port", "css_selector"],
            },
        ),
        Tool(
            name="cdp_ensure",
            description=(
                "Ensure CDP is available for an Electron app. Checks existing "
                "connections, scans ports, and if needed proposes a restart plan "
                "for user confirmation. NEVER restarts automatically — returns "
                "action_required='restart' so you can ask the user first."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "description": "App name (e.g., 'code', 'slack', 'notion', 'chrome').",
                    },
                    "preferred_port": {
                        "type": "integer",
                        "description": "Preferred CDP port. If omitted, uses known defaults.",
                    },
                },
                "required": ["app_name"],
            },
        ),
        Tool(
            name="cdp_restart_confirmed",
            description=(
                "Execute CDP restart AFTER user confirmation. Closes the app, "
                "relaunches with --remote-debugging-port, waits for CDP, and "
                "auto-connects. Only call after the user explicitly agreed."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "description": "App name to restart.",
                    },
                    "port": {
                        "type": "integer",
                        "description": "CDP port to use. If omitted, uses default for app.",
                    },
                },
                "required": ["app_name"],
            },
        ),
        Tool(
            name="cdp_get_knowledge_base",
            description=(
                "Get the CDP knowledge base: which apps needed restart, what ports "
                "worked, and default port assignments for known Electron apps."
            ),
            inputSchema={"type": "object", "properties": {}},
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

        # ── Platform: Clipboard ──
        Tool(
            name="clipboard",
            description=(
                "Read or write the system clipboard. "
                "action='get' reads, action='set' writes text."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["get", "set"],
                        "description": "Action: 'get' to read, 'set' to write.",
                    },
                    "text": {
                        "type": "string",
                        "description": "Text to write (only for action='set').",
                    },
                },
                "required": ["action"],
            },
        ),
        Tool(
            name="clipboard_history",
            description="Get clipboard history (in-memory, since session start).",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Max entries to return (default: 20).",
                    },
                },
            },
        ),

        # ── Platform: Visual Diff ──
        Tool(
            name="visual_diff",
            description=(
                "Capture 'before' screenshot for later comparison. "
                "Call BEFORE an action, then call visual_diff_compare AFTER."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "window_title": {
                        "type": "string",
                        "description": "Window to capture. If omitted, full screen.",
                    },
                    "label": {
                        "type": "string",
                        "description": "Optional label for this capture.",
                    },
                },
            },
        ),
        Tool(
            name="visual_diff_compare",
            description=(
                "Compare current screen with 'before' capture. "
                "Returns change percentage, bounding box, and diff image."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "diff_id": {
                        "type": "string",
                        "description": "ID from visual_diff().",
                    },
                    "window_title": {
                        "type": "string",
                        "description": "Window to capture for 'after'. If omitted, uses same as before.",
                    },
                },
                "required": ["diff_id"],
            },
        ),

        # ── Agnostic: Workflows ──
        Tool(
            name="workflow_record",
            description="Start recording a new workflow (sequence of tool calls).",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name for the workflow.",
                    },
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="workflow_stop",
            description="Stop recording and save the current workflow.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="workflow_run",
            description="Replay a saved workflow. Checks safety before each step.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Workflow name to replay.",
                    },
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="workflow_list",
            description="List all saved workflows with metadata.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="workflow_delete",
            description="Delete a saved workflow by name.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Workflow name to delete.",
                    },
                },
                "required": ["name"],
            },
        ),

        # ── Platform: Diagnostics ──
        Tool(
            name="run_diagnostics",
            description=(
                "Run system diagnostics: check Python, Sway, AT-SPI2, CLI tools, "
                "Tesseract, PipeWire, disk, RAM, GPU, and pip dependencies."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),

        # ── Adaptive (pattern detection) ──
        Tool(
            name="get_suggestions",
            description="Analyze recorded tool call patterns and suggest automatable sequences.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="accept_suggestion",
            description="Mark a pattern suggestion as accepted.",
            inputSchema={
                "type": "object",
                "properties": {
                    "pattern_id": {"type": "string", "description": "Pattern ID to accept."},
                },
                "required": ["pattern_id"],
            },
        ),
        Tool(
            name="dismiss_suggestion",
            description="Dismiss a pattern suggestion so it won't appear again.",
            inputSchema={
                "type": "object",
                "properties": {
                    "pattern_id": {"type": "string", "description": "Pattern ID to dismiss."},
                },
                "required": ["pattern_id"],
            },
        ),

        # ── Learning from Demonstration (LfD) ──
        Tool(
            name="demo_start",
            description=(
                "Start recording a user demonstration. Captures tool calls and "
                "AT-SPI2 events. On Linux, keyboard hooks are not available — "
                "demonstrations are based on tool call and accessibility events."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name for this demonstration."},
                    "description": {"type": "string", "description": "Optional description."},
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="demo_stop",
            description="Stop recording and extract a replayable plan from the demonstration.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="demo_status",
            description="Get current demonstration recording status.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="demo_list",
            description="List all saved demonstrations.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="demo_replay",
            description="Load a saved demonstration and return its extracted plan steps for replay.",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "Demo filename to load."},
                },
                "required": ["filename"],
            },
        ),

        # ── app_script (Windows-only stub) ──
        Tool(
            name="run_app_script",
            description=(
                "Run a COM automation script (Windows-only). On Linux, returns "
                "a message suggesting alternatives like CDP or D-Bus."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "script": {"type": "string", "description": "Script to execute."},
                    "app_name": {"type": "string", "description": "Target application."},
                },
                "required": ["script"],
            },
        ),

        # ── Voice / TTS ──
        Tool(
            name="speak",
            description=(
                "Speak text aloud via TTS. Primary: edge-tts (neural, online). "
                "Fallback: Piper TTS (neural, offline). Auto-detects language."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to speak."},
                    "language": {
                        "type": "string",
                        "description": "Language: 'auto', 'es', or 'en'. Default: auto.",
                    },
                    "voice": {
                        "type": "string",
                        "description": "Voice name or alias (e.g., 'jorge', 'jenny').",
                    },
                    "rate": {
                        "type": "integer",
                        "description": "Speech rate in WPM (default: 175).",
                    },
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="speak_and_listen",
            description="Speak text, then listen for a voice response via microphone.",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to speak first."},
                    "timeout": {
                        "type": "integer",
                        "description": "Seconds to listen after speaking (default: 10, max: 60).",
                    },
                    "language": {"type": "string"},
                    "voice": {"type": "string"},
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="listen_for_command",
            description=(
                "Listen for a voice command via microphone. Records immediately, "
                "transcribes with Whisper, returns text."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "duration_seconds": {
                        "type": "integer",
                        "description": "How long to listen (default: 10, max: 60).",
                    },
                    "language": {
                        "type": "string",
                        "description": "Language code or 'auto'.",
                    },
                    "model_size": {
                        "type": "string",
                        "description": "Whisper model: 'tiny', 'base', 'small', 'medium'.",
                    },
                },
            },
        ),
        Tool(
            name="transcribe_audio",
            description="Transcribe an audio file using faster-whisper.",
            inputSchema={
                "type": "object",
                "properties": {
                    "audio_path": {
                        "type": "string",
                        "description": "Path to WAV audio file.",
                    },
                    "language": {"type": "string", "description": "Language or 'auto'."},
                    "model_size": {"type": "string", "description": "Whisper model size."},
                },
                "required": ["audio_path"],
            },
        ),
        Tool(
            name="download_whisper_model",
            description="Pre-download a Whisper model so transcription is instant.",
            inputSchema={
                "type": "object",
                "properties": {
                    "model_size": {
                        "type": "string",
                        "enum": ["tiny", "base", "small", "medium"],
                        "description": "Model to download (default: base).",
                    },
                },
            },
        ),
        Tool(
            name="get_voice_hotkey_status",
            description="Get voice hotkey status (on Linux, reports stub info).",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="toggle_voice_overlay",
            description="Toggle voice overlay (not yet available on Linux/Sway).",
            inputSchema={"type": "object", "properties": {}},
        ),

        # ── Background Mode (Shadow Mode) ──
        Tool(
            name="setup_background_mode",
            description=(
                "Set up background mode using a dedicated Sway workspace. "
                "Marlow can work invisibly in its own workspace."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "preferred_mode": {"type": "string"},
                },
            },
        ),
        Tool(
            name="move_to_agent_screen",
            description="Move a window to the agent workspace (Shadow Mode).",
            inputSchema={
                "type": "object",
                "properties": {
                    "window_title": {
                        "type": "string",
                        "description": "Window to move. If omitted, moves focused window.",
                    },
                },
            },
        ),
        Tool(
            name="move_to_user_screen",
            description="Move a window back from agent workspace to user workspace.",
            inputSchema={
                "type": "object",
                "properties": {
                    "window_title": {
                        "type": "string",
                        "description": "Window to move back.",
                    },
                },
            },
        ),
        Tool(
            name="get_agent_screen_state",
            description="Get the state of the agent workspace (windows, active status).",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="set_agent_screen_only",
            description="Enable/disable agent-only mode (Marlow stays in its workspace).",
            inputSchema={
                "type": "object",
                "properties": {
                    "enabled": {
                        "type": "boolean",
                        "description": "True to restrict Marlow to agent workspace.",
                    },
                },
                "required": ["enabled"],
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

    # Normalize non-dict/non-bytes results into dicts
    if not isinstance(result, (dict, bytes, list)):
        result = {"success": bool(result), "tool": name}
    elif isinstance(result, list) and name != "take_screenshot":
        result = {"success": True, "data": result, "count": len(result)}

    # Workflow recording hook — record successful tool calls
    if workflows._manager.is_recording:
        if isinstance(result, bytes):
            step_ok = True  # screenshots are always success
        elif isinstance(result, dict):
            step_ok = "error" not in result and result.get("success", True) is not False
        else:
            step_ok = bool(result)
        workflows._manager.record_step(name, arguments, step_ok)

    # Handle screenshot results (before sanitizer — raw bytes)
    if name == "take_screenshot" and isinstance(result, bytes):
        image_b64 = base64.b64encode(result).decode()
        return [
            ImageContent(type="image", data=image_b64, mimeType="image/png"),
            TextContent(type="text", text=f"Screenshot captured ({len(result):,} bytes)"),
        ]

    # Extract image data BEFORE sanitizer (base64 can match sanitizer patterns)
    _image_data = None
    if isinstance(result, dict):
        if result.get("requires_vision") and "image_base64" in result:
            _image_data = result.pop("image_base64")
        elif name == "get_annotated_screenshot" and "image" in result:
            _image_data = result.pop("image")
        elif name == "cdp_screenshot" and "image_base64" in result:
            _image_data = result.pop("image_base64")
        elif name == "visual_diff_compare" and "diff_image" in result:
            _image_data = result.pop("diff_image")

    # Sanitize output
    if isinstance(result, dict):
        result = sanitizer.sanitize_ui_tree(result)

    # Handle smart_find/cascade_find with screenshot fallback
    if (name in ("smart_find", "cascade_find")
            and isinstance(result, dict)
            and result.get("requires_vision")
            and _image_data):
        return [
            ImageContent(type="image", data=_image_data, mimeType="image/png"),
            TextContent(
                type="text",
                text=json.dumps(result, indent=2, ensure_ascii=False, default=str),
            ),
        ]

    # Handle get_annotated_screenshot — return image + element map
    if (name == "get_annotated_screenshot"
            and isinstance(result, dict)
            and result.get("success")
            and _image_data):
        return [
            ImageContent(type="image", data=_image_data, mimeType="image/png"),
            TextContent(
                type="text",
                text=json.dumps(result, indent=2, ensure_ascii=False, default=str),
            ),
        ]

    # Handle cdp_screenshot — return image + metadata
    if (name == "cdp_screenshot"
            and isinstance(result, dict)
            and result.get("success")
            and _image_data):
        fmt = result.get("format", "png")
        mime = f"image/{fmt}"
        return [
            ImageContent(type="image", data=_image_data, mimeType=mime),
            TextContent(
                type="text",
                text=json.dumps(result, indent=2, ensure_ascii=False, default=str),
            ),
        ]

    # Handle visual_diff_compare — return diff image + stats
    if (name == "visual_diff_compare"
            and isinstance(result, dict)
            and result.get("success")
            and _image_data):
        return [
            ImageContent(type="image", data=_image_data, mimeType="image/png"),
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
        "find_elements": lambda args: _wrap_find_elements(p, args),
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
        "click": lambda args: _wrap_bool(p.input.click(
            x=args["x"], y=args["y"], button=args.get("button", "left"),
        ), "click"),
        "type_text": lambda args: _wrap_bool(p.input.type_text(text=args["text"]), "type_text"),
        "press_key": lambda args: _wrap_bool(p.input.press_key(key=args["key"]), "press_key"),
        "hotkey": lambda args: _wrap_bool(p.input.hotkey(*args["keys"]), "hotkey"),
        "move_mouse": lambda args: _wrap_bool(p.input.move_mouse(
            x=args["x"], y=args["y"],
        ), "move_mouse"),

        # ── Platform: Windows ──
        "list_windows": lambda args: _list_windows(p, args),
        "focus_window": lambda args: _wrap_focus_window(p, args),
        "manage_window": lambda args: _wrap_bool(p.windows.manage_window(
            identifier=args["window_title"],
            action=args["action"],
            x=args.get("x"),
            y=args.get("y"),
            width=args.get("width"),
            height=args.get("height"),
        ), "manage_window"),

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

        # ── Platform: Wait ──
        "wait_for_element": lambda args: p.waits.wait_for_element(
            name=args.get("name"),
            role=args.get("role"),
            window_title=args.get("window_title"),
            timeout=args.get("timeout", 10),
            interval=args.get("interval", 0.5),
        ),
        "wait_for_text": lambda args: p.waits.wait_for_text(
            text=args["text"],
            window_title=args.get("window_title"),
            timeout=args.get("timeout", 10),
            interval=args.get("interval", 0.5),
        ),
        "wait_for_window": lambda args: p.waits.wait_for_window(
            title=args["title"],
            timeout=args.get("timeout", 10),
            interval=args.get("interval", 0.5),
        ),
        "wait_for_idle": lambda args: p.waits.wait_for_idle(
            window_title=args.get("window_title"),
            timeout=args.get("timeout", 10),
            threshold=args.get("threshold", 0.95),
        ),

        # ── CDP (Chrome DevTools Protocol) ──
        "cdp_discover": lambda args: _get_cdp_manager().cdp_discover(
            port_start=args.get("port_start", 9222),
            port_end=args.get("port_end", 9250),
        ),
        "cdp_connect": lambda args: _get_cdp_manager().cdp_connect(
            port=args["port"],
        ),
        "cdp_disconnect": lambda args: _get_cdp_manager().cdp_disconnect(
            port=args["port"],
        ),
        "cdp_list_connections": lambda args: _get_cdp_manager().cdp_list_connections(),
        "cdp_send": lambda args: _get_cdp_manager().cdp_send(
            port=args["port"],
            method=args["method"],
            params=args.get("params"),
        ),
        "cdp_click": lambda args: _get_cdp_manager().cdp_click(
            port=args["port"],
            x=args["x"],
            y=args["y"],
        ),
        "cdp_type_text": lambda args: _get_cdp_manager().cdp_type_text(
            port=args["port"],
            text=args["text"],
        ),
        "cdp_key_combo": lambda args: _get_cdp_manager().cdp_key_combo(
            port=args["port"],
            key=args["key"],
            modifiers=args.get("modifiers"),
        ),
        "cdp_screenshot": lambda args: _get_cdp_manager().cdp_screenshot(
            port=args["port"],
            format=args.get("format", "png"),
        ),
        "cdp_evaluate": lambda args: _get_cdp_manager().cdp_evaluate(
            port=args["port"],
            expression=args["expression"],
        ),
        "cdp_get_dom": lambda args: _get_cdp_manager().cdp_get_dom(
            port=args["port"],
            depth=args.get("depth", -1),
        ),
        "cdp_click_selector": lambda args: _get_cdp_manager().cdp_click_selector(
            port=args["port"],
            css_selector=args["css_selector"],
        ),
        "cdp_ensure": lambda args: _get_cdp_manager().cdp_ensure(
            app_name=args["app_name"],
            preferred_port=args.get("preferred_port"),
        ),
        "cdp_restart_confirmed": lambda args: _get_cdp_manager().cdp_restart_confirmed(
            app_name=args["app_name"],
            port=args.get("port"),
        ),
        "cdp_get_knowledge_base": lambda args: _get_cdp_manager().cdp_get_knowledge_base(),

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

        # ── Platform: Clipboard ──
        "clipboard": lambda args: _clipboard(p, args),
        "clipboard_history": lambda args: {
            "success": True,
            "entries": p.clipboard.get_clipboard_history()[-args.get("limit", 20):],
            "total": len(p.clipboard.get_clipboard_history()),
        },

        # ── Platform: Visual Diff ──
        "visual_diff": lambda args: p.visual_diff.capture_before(
            window_title=args.get("window_title"),
            label=args.get("label"),
        ),
        "visual_diff_compare": lambda args: p.visual_diff.compare(
            diff_id=args["diff_id"],
            window_title=args.get("window_title"),
        ),

        # ── Agnostic: Workflows ──
        "workflow_record": lambda args: workflows.workflow_record(
            name=args["name"],
        ),
        "workflow_stop": lambda args: workflows.workflow_stop(),
        "workflow_run": lambda args: workflows.workflow_run(
            name=args["name"],
            safety_engine=safety,
            dispatch_fn=_dispatch_tool,
        ),
        "workflow_list": lambda args: workflows.workflow_list(),
        "workflow_delete": lambda args: workflows.workflow_delete(
            name=args["name"],
        ),

        # ── Platform: Diagnostics ──
        "run_diagnostics": lambda args: _run_diagnostics(),

        # ── Voice / TTS ──
        "speak": lambda args: _speak(args),
        "speak_and_listen": lambda args: _speak_and_listen(args),
        "listen_for_command": lambda args: _listen_for_command(args),
        "transcribe_audio": lambda args: audio_tools.transcribe_audio(
            audio_path=args["audio_path"],
            language=args.get("language", "auto"),
            model_size=args.get("model_size", "base"),
        ),
        "download_whisper_model": lambda args: audio_tools.download_whisper_model(
            model_size=args.get("model_size", "base"),
        ),
        "get_voice_hotkey_status": lambda args: _get_voice_hotkey_status(),
        "toggle_voice_overlay": lambda args: _toggle_voice_overlay(),

        # ── Background Mode ──
        "setup_background_mode": lambda args: p.background.setup_background_mode(
            preferred_mode=args.get("preferred_mode"),
        ),
        "move_to_agent_screen": lambda args: p.background.move_to_agent_screen(
            window_title=args.get("window_title"),
        ),
        "move_to_user_screen": lambda args: p.background.move_to_user_screen(
            window_title=args.get("window_title"),
        ),
        "get_agent_screen_state": lambda args: p.background.get_agent_screen_state(),
        "set_agent_screen_only": lambda args: p.background.set_agent_screen_only(
            enabled=args["enabled"],
        ),

        # ── Adaptive ──
        "get_suggestions": lambda args: adaptive.get_suggestions(),
        "accept_suggestion": lambda args: adaptive.accept_suggestion(
            pattern_id=args["pattern_id"],
        ),
        "dismiss_suggestion": lambda args: adaptive.dismiss_suggestion(
            pattern_id=args["pattern_id"],
        ),

        # ── LfD ──
        "demo_start": lambda args: _demo_start(args),
        "demo_stop": lambda args: _demo_stop(args),
        "demo_status": lambda args: _demo_status(args),
        "demo_list": lambda args: _demo_list(args),
        "demo_replay": lambda args: _demo_replay(args),

        # ── app_script (stub) ──
        "run_app_script": lambda args: {
            "success": False,
            "error": (
                "run_app_script uses COM automation (win32com) which is Windows-only. "
                "On Linux, use CDP tools for Electron/Chrome apps, "
                "D-Bus for GTK/Qt apps, or AT-SPI2 (get_ui_tree + do_action) "
                "for general desktop automation."
            ),
            "platform": "linux",
            "alternatives": ["cdp_evaluate", "cdp_click", "do_action", "get_ui_tree"],
        },

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

def _wrap_bool(result, tool_name: str = "") -> dict:
    """Wrap a bool return into a consistent dict response."""
    if isinstance(result, dict):
        return result  # Already a dict
    return {"success": bool(result), "tool": tool_name}


def _wrap_find_elements(p, args: dict) -> dict:
    """Wrap find_elements list return into a dict with success/elements/count."""
    result = p.ui_tree.find_elements(
        name=args.get("name"),
        role=args.get("role"),
        states=args.get("states"),
        window_title=args.get("window_title"),
    )
    if isinstance(result, list):
        return {"success": True, "elements": result, "count": len(result)}
    return result  # Already a dict (error case)


def _wrap_focus_window(p, args: dict) -> dict:
    """Wrap focus_window bool into a dict with window title."""
    title = args.get("window_title", args.get("identifier", ""))
    result = p.windows.focus_window(identifier=title)
    if isinstance(result, bool):
        if result:
            return {"success": True, "window": title}
        return {"success": False, "error": f"Window not found: {title}"}
    return result  # Already a dict


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


def _clipboard(p, args: dict) -> dict:
    """Handle clipboard get/set."""
    action = args.get("action", "get")
    if action == "set":
        text = args.get("text", "")
        if not text:
            return {"error": "Must provide 'text' for set action"}
        ok = p.clipboard.set_clipboard(text)
        return {"success": ok, "action": "set", "length": len(text)}
    else:
        text = p.clipboard.get_clipboard()
        return {"success": True, "action": "get", "text": text, "length": len(text)}


async def _speak(args: dict) -> dict:
    """Speak via Linux TTS (edge-tts + Piper)."""
    from marlow.platform.linux.tts import speak
    return await speak(
        text=args["text"],
        language=args.get("language", "auto"),
        voice=args.get("voice"),
        rate=args.get("rate", 175),
    )


async def _speak_and_listen(args: dict) -> dict:
    """Speak then listen via Linux TTS + mic."""
    from marlow.platform.linux.tts import speak_and_listen
    return await speak_and_listen(
        text=args["text"],
        timeout=args.get("timeout", 10),
        language=args.get("language", "auto"),
        voice=args.get("voice"),
    )


async def _listen_for_command(args: dict) -> dict:
    """Listen for voice command via mic + whisper."""
    from marlow.tools.voice import listen_for_command
    return await listen_for_command(
        duration_seconds=args.get("duration_seconds", 10),
        language=args.get("language", "auto"),
        model_size=args.get("model_size", "base"),
    )


async def _get_voice_hotkey_status() -> dict:
    """Get voice hotkey status (Linux stub)."""
    from marlow.platform.linux.voice_hotkey import get_voice_hotkey_status
    return await get_voice_hotkey_status()


async def _toggle_voice_overlay() -> dict:
    """Toggle voice overlay (Linux stub)."""
    from marlow.platform.linux.voice_hotkey import toggle_voice_overlay
    return await toggle_voice_overlay()


def _run_diagnostics() -> dict:
    """Run Linux system diagnostics."""
    from marlow.platform.linux.diagnostics import run_diagnostics
    return run_diagnostics()


# ─────────────────────────────────────────────────────────────
# LfD (Learning from Demonstration) helpers
# ─────────────────────────────────────────────────────────────

_demo_recorder = None


def _get_demo_recorder():
    global _demo_recorder
    if _demo_recorder is None:
        from marlow.kernel.demonstration import DemonstrationRecorder
        _demo_recorder = DemonstrationRecorder()
    return _demo_recorder


async def _demo_start(args: dict) -> dict:
    recorder = _get_demo_recorder()
    if recorder.is_recording:
        return {"success": False, "error": "Already recording a demonstration"}

    demo = recorder.start(
        name=args["name"],
        description=args.get("description", ""),
    )
    return {
        "success": True,
        "message": f"Recording started: {demo.name}",
        "recording": True,
        "keyboard_hook": "not_available",
        "platform_note": (
            "On Linux, keyboard hooks are not available. "
            "Demonstrations capture tool calls and AT-SPI2 events."
        ),
    }


async def _demo_stop(args: dict) -> dict:
    from marlow.kernel.demonstration import PlanExtractor

    recorder = _get_demo_recorder()
    if not recorder.is_recording:
        return {"success": False, "error": "Not recording"}

    demo = recorder.stop()
    if demo:
        extractor = PlanExtractor()
        steps = extractor.extract(demo)
        filepath = recorder.save(demo)
        return {
            "success": True,
            "name": demo.name,
            "events": demo.event_count,
            "steps": len(steps),
            "duration": round(demo.duration_seconds, 1),
            "plan": extractor.format_for_review(steps),
            "saved_to": filepath,
        }
    return {"success": False, "error": "Recording stop failed"}


async def _demo_status(args: dict) -> dict:
    recorder = _get_demo_recorder()
    if recorder.is_recording and recorder.current_demo:
        demo = recorder.current_demo
        return {
            "recording": True,
            "name": demo.name,
            "events": demo.event_count,
            "duration": round(demo.duration_seconds, 1),
        }
    return {"recording": False}


async def _demo_list(args: dict) -> dict:
    recorder = _get_demo_recorder()
    demos = recorder.list_demos()
    return {"demos": demos, "count": len(demos)}


async def _demo_replay(args: dict) -> dict:
    recorder = _get_demo_recorder()
    data = recorder.load_demo(args["filename"])
    if data:
        return {
            "success": True,
            "name": data.get("name", ""),
            "steps": data.get("extracted_steps", []),
            "step_count": data.get("step_count", 0),
        }
    return {"success": False, "error": "Demo not found"}


def _get_version() -> dict:
    """Version info for Linux server."""
    return {
        "version": __version__,
        "platform": "linux",
        "tools_registered": 101,
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

    # Monkey-patch CDPManager with Linux-compatible methods
    try:
        from marlow.platform.linux.cdp import patch_cdp_manager
        patch_cdp_manager()
    except ImportError:
        logger.warning("CDP dependencies not available — CDP tools will fail at runtime")

    logger.info("Marlow v%s (Linux) starting...", __version__)
    logger.info("Security: confirmation_mode=%s", config.security.confirmation_mode)
    logger.info("Kill switch: %s",
                "enabled" if config.security.kill_switch_enabled else "DISABLED")
    logger.info("Telemetry: NEVER (zero data leaves your machine)")

    # Start kill switch (graceful — no keyboard module needed)
    safety.start_kill_switch()

    # Log registered tools
    logger.info("Tools: 101 registered (71 platform + 30 agnostic)")
    logger.info("Platform providers: Sway IPC, wtype, ydotool, grim, AT-SPI2, PipeWire, CDP")
    logger.info("All 96 Windows tools ported (101 on Linux: 96 + 5 Linux-specific)")

    # Run MCP server via stdio
    asyncio.run(_run_server())


if __name__ == "__main__":
    main()
