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
from marlow.core import app_detector

# Phase 3 Tools
from marlow.tools import visual_diff, memory, clipboard_ext, scraper
from marlow.extensions import registry as ext_registry

# Phase 4 Tools
from marlow.tools import watcher, scheduler

# Phase 5: Voice Control + TTS
from marlow.core import voice_hotkey
from marlow.tools import tts

# Adaptive Behavior + Workflows
from marlow.core import adaptive
from marlow.core import workflows

# Self-Improve: Error Journal
from marlow.core import error_journal

# Smart Wait
from marlow.tools import wait

# Voice Overlay
from marlow.core import voice_overlay

# Setup Wizard
from marlow.core import setup_wizard

# Help / Capabilities
from marlow.tools import help as help_mod

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

# Wire kill switch into scheduler so scheduled tasks respect it
from marlow.tools.scheduler import set_kill_switch_check
set_kill_switch_check(lambda: safety.is_killed)


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
                "Extract text from a window or screen region using OCR. "
                "Primary: Windows OCR (~50-200ms, built-in). Fallback: Tesseract (~200-500ms). "
                "Cost: 0 tokens. Returns text + word bounding boxes for clicking."
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
                        "description": (
                            "Language for OCR. Windows OCR: BCP-47 tag (e.g., 'en-US', 'es-MX'). "
                            "Tesseract: ISO 639-3 (e.g., 'eng', 'spa'). Auto-detects if omitted."
                        ),
                    },
                    "engine": {
                        "type": "string",
                        "enum": ["windows", "tesseract"],
                        "description": "Force a specific OCR engine. Default: auto (Windows primary, Tesseract fallback).",
                    },
                },
            },
        ),
        Tool(
            name="list_ocr_languages",
            description=(
                "List available OCR languages for each engine (Windows OCR and Tesseract). "
                "Use to check which languages are installed before running OCR."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),

        # ── Phase 2: Smart Find (Escalation) ──
        Tool(
            name="smart_find",
            description=(
                "Find a UI element using escalating methods: "
                "1) UI Automation with fuzzy search (0 tokens, ~10-50ms) → "
                "2) OCR (0 tokens, ~50-200ms) → "
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
        Tool(
            name="find_elements",
            description=(
                "Multi-property fuzzy search for UI elements. "
                "Searches name, automation_id, help_text, class_name with Levenshtein distance. "
                "Returns top 5 candidates ranked by similarity score. "
                "Use when you need to explore what elements exist or find approximate matches."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Text to search for (e.g., 'Save', 'btnSubmit', 'Edit').",
                    },
                    "window_title": {
                        "type": "string",
                        "description": "Window to search in. If omitted, uses active window.",
                    },
                    "control_type": {
                        "type": "string",
                        "description": "Filter by control type (e.g., 'Button', 'Edit', 'MenuItem').",
                    },
                },
                "required": ["query"],
            },
        ),

        # ── Phase 2: App Framework Detection ──
        Tool(
            name="detect_app_framework",
            description=(
                "Detect the UI framework of a window (Electron, CEF, Chromium, WPF, WinForms, "
                "WinUI 3, UWP, Win32) by analyzing loaded DLLs. If no window specified, scans "
                "all visible windows. Useful to choose the best automation strategy."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "window_title": {
                        "type": "string",
                        "description": "Window to analyze. If omitted, scans all visible windows.",
                    },
                },
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

        # ── Phase 3: Visual Diff ──
        Tool(
            name="visual_diff",
            description=(
                "Capture a 'before' screenshot for later comparison. "
                "Call this BEFORE performing an action, then call "
                "visual_diff_compare with the returned diff_id."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "window_title": {
                        "type": "string",
                        "description": "Window to capture. If omitted, full screen.",
                    },
                    "description": {
                        "type": "string",
                        "description": "What you're about to do (for reference).",
                        "default": "",
                    },
                },
            },
        ),
        Tool(
            name="visual_diff_compare",
            description=(
                "Compare current state with a previous 'before' capture. "
                "Returns change percentage and changed region."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "diff_id": {
                        "type": "string",
                        "description": "The diff_id returned by visual_diff.",
                    },
                },
                "required": ["diff_id"],
            },
        ),

        # ── Phase 3: Memory ──
        Tool(
            name="memory_save",
            description=(
                "Save a value persistently across sessions. "
                "Categories: general, preferences, projects, tasks."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Unique key for this memory."},
                    "value": {"type": "string", "description": "The text/data to store."},
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
            description=(
                "Recall stored memories. Pass key+category for specific lookup, "
                "category only for listing, key only to search all, "
                "or nothing to list all categories."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Key to look up."},
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
                    "key": {"type": "string", "description": "Key to delete."},
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

        # ── Phase 3: Clipboard History ──
        Tool(
            name="clipboard_history",
            description=(
                "Manage clipboard history. Actions: "
                "'start' to begin monitoring, 'stop' to end, "
                "'list' to see entries, 'search' to find text, 'clear' to wipe."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["start", "stop", "list", "search", "clear"],
                        "default": "list",
                    },
                    "search": {
                        "type": "string",
                        "description": "Text to search for (with action='search').",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max entries to return (default: 20).",
                        "default": 20,
                    },
                },
            },
        ),

        # ── Phase 3: Web Scraper ──
        Tool(
            name="scrape_url",
            description=(
                "Extract content from a URL. "
                "Formats: 'text' (clean text), 'links' (all links), "
                "'tables' (HTML tables), 'html' (raw HTML)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to scrape."},
                    "selector": {
                        "type": "string",
                        "description": "CSS selector to filter content.",
                    },
                    "format": {
                        "type": "string",
                        "enum": ["text", "links", "tables", "html"],
                        "default": "text",
                    },
                },
                "required": ["url"],
            },
        ),

        # ── Phase 3: Extensions ──
        Tool(
            name="extensions_list",
            description="List all installed Marlow extensions with their permissions.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="extensions_install",
            description="Install a Marlow extension from pip.",
            inputSchema={
                "type": "object",
                "properties": {
                    "package": {
                        "type": "string",
                        "description": "pip package name or GitHub URL.",
                    },
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
                    "name": {
                        "type": "string",
                        "description": "Extension name to uninstall.",
                    },
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="extensions_audit",
            description="Audit an installed extension's security and permissions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Extension name to audit.",
                    },
                },
                "required": ["name"],
            },
        ),

        # ── Phase 4: Folder Watcher ──
        Tool(
            name="watch_folder",
            description=(
                "Start monitoring a folder for file changes. "
                "Returns a watch_id to track events. "
                "Events: created, modified, deleted, moved."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Folder path to monitor.",
                    },
                    "events": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["created", "modified", "deleted", "moved"]},
                        "description": "Event types to watch (default: all).",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "Watch subdirectories too (default: false).",
                        "default": False,
                    },
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="unwatch_folder",
            description="Stop monitoring a folder by watch_id.",
            inputSchema={
                "type": "object",
                "properties": {
                    "watch_id": {
                        "type": "string",
                        "description": "The watch_id returned by watch_folder.",
                    },
                },
                "required": ["watch_id"],
            },
        ),
        Tool(
            name="get_watch_events",
            description=(
                "Get detected filesystem events. "
                "Optionally filter by watch_id or timestamp."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "watch_id": {
                        "type": "string",
                        "description": "Filter to a specific watcher.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max events to return (default: 50).",
                        "default": 50,
                    },
                    "since": {
                        "type": "string",
                        "description": "ISO timestamp — only events after this time.",
                    },
                },
            },
        ),
        Tool(
            name="list_watchers",
            description="List all active folder watchers.",
            inputSchema={"type": "object", "properties": {}},
        ),

        # ── Phase 4: Task Scheduler ──
        Tool(
            name="schedule_task",
            description=(
                "Schedule a recurring command. Runs in a background thread "
                "at the specified interval. Use max_runs to limit executions. "
                "Commands go through the same safety checks as run_command."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Unique name for this task.",
                    },
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute.",
                    },
                    "interval_seconds": {
                        "type": "integer",
                        "description": "Run every N seconds (default: 300, min: 10).",
                        "default": 300,
                    },
                    "shell": {
                        "type": "string",
                        "enum": ["powershell", "cmd"],
                        "default": "powershell",
                    },
                    "max_runs": {
                        "type": "integer",
                        "description": "Stop after N runs (omit for unlimited).",
                    },
                },
                "required": ["name", "command"],
            },
        ),
        Tool(
            name="list_scheduled_tasks",
            description="List all scheduled tasks with their status and run counts.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="remove_task",
            description="Remove a scheduled task by name.",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_name": {
                        "type": "string",
                        "description": "Name of the task to remove.",
                    },
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
                    "task_name": {
                        "type": "string",
                        "description": "Filter to a specific task.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max entries to return (default: 20).",
                        "default": 20,
                    },
                },
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

        # ── Phase 5: Voice Control + TTS ──
        Tool(
            name="speak",
            description=(
                "Speak text aloud using Windows SAPI5 text-to-speech. "
                "Auto-detects Spanish/English. Uses system speakers."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text to speak aloud.",
                    },
                    "language": {
                        "type": "string",
                        "enum": ["auto", "es", "en"],
                        "description": "Language: 'auto' (detect), 'es', or 'en'.",
                        "default": "auto",
                    },
                    "voice": {
                        "type": "string",
                        "description": "Specific voice name (e.g., 'David', 'Sabina').",
                    },
                    "rate": {
                        "type": "integer",
                        "description": "Speech rate in words/min (default: 175).",
                        "default": 175,
                    },
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="speak_and_listen",
            description=(
                "Speak text aloud, then listen for a voice response. "
                "Combines TTS + mic recording + transcription. "
                "Ideal for conversational flows."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text to speak first.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "How long to listen after speaking (default: 10, max: 60).",
                        "default": 10,
                    },
                    "language": {
                        "type": "string",
                        "enum": ["auto", "es", "en"],
                        "description": "Language for TTS and transcription.",
                        "default": "auto",
                    },
                    "voice": {
                        "type": "string",
                        "description": "Specific voice name for TTS.",
                    },
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="get_voice_hotkey_status",
            description=(
                "Check the status of the voice hotkey (Ctrl+Shift+M). "
                "Shows if active, currently recording, last transcribed text, and errors."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),

        # ── Adaptive Behavior ──
        Tool(
            name="get_suggestions",
            description=(
                "Analyze recent tool actions and detect repeating patterns. "
                "Returns suggestions for sequences you perform frequently. "
                "Dismissed patterns are filtered out."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="accept_suggestion",
            description="Mark a pattern suggestion as accepted (acknowledged as useful).",
            inputSchema={
                "type": "object",
                "properties": {
                    "pattern_id": {
                        "type": "string",
                        "description": "The pattern ID to accept.",
                    },
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
                    "pattern_id": {
                        "type": "string",
                        "description": "The pattern ID to dismiss.",
                    },
                },
                "required": ["pattern_id"],
            },
        ),

        # ── Workflows ──
        Tool(
            name="workflow_record",
            description=(
                "Start recording a new workflow. All subsequent tool calls "
                "(except meta-tools) will be captured as steps. "
                "Call workflow_stop when done."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Unique name for this workflow.",
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
            description=(
                "Replay a saved workflow. Executes each recorded step "
                "with safety checks (kill switch + approval) before each. "
                "Stops on first failure."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name of the workflow to run.",
                    },
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="workflow_list",
            description="List all saved workflows with step counts and creation dates.",
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
                        "description": "Name of the workflow to delete.",
                    },
                },
                "required": ["name"],
            },
        ),

        # ── Self-Improve: Error Journal ──
        Tool(
            name="get_error_journal",
            description=(
                "Show the error journal — records which methods fail/work "
                "on specific apps. Marlow uses this to skip methods that "
                "are known to fail. Optionally filter by app/window."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "window": {
                        "type": "string",
                        "description": "Filter by window/app name.",
                    },
                },
            },
        ),
        Tool(
            name="clear_error_journal",
            description=(
                "Clear error journal entries for a specific app, "
                "or all entries if no window specified."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "window": {
                        "type": "string",
                        "description": "Clear entries for this app only. Omit to clear all.",
                    },
                },
            },
        ),

        # ── Smart Wait ──
        Tool(
            name="wait_for_element",
            description=(
                "Wait for a UI element to appear in the Accessibility Tree. "
                "Polls every interval seconds until found or timeout. "
                "Returns element info with position when found."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name/text of element to wait for (e.g., 'Save', 'OK').",
                    },
                    "window_title": {
                        "type": "string",
                        "description": "Window to search in.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Max seconds to wait (default: 30, max: 120).",
                        "default": 30,
                    },
                    "interval": {
                        "type": "number",
                        "description": "Seconds between checks (default: 1).",
                        "default": 1,
                    },
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="wait_for_text",
            description=(
                "Wait for specific text to appear on screen via OCR. "
                "Polls every interval seconds, case insensitive. "
                "Returns text position and surrounding context when found."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text to wait for.",
                    },
                    "window_title": {
                        "type": "string",
                        "description": "Window to OCR. If omitted, full screen.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Max seconds to wait (default: 30, max: 120).",
                        "default": 30,
                    },
                    "interval": {
                        "type": "number",
                        "description": "Seconds between checks (default: 2).",
                        "default": 2,
                    },
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="wait_for_window",
            description=(
                "Wait for a window with the given title to appear. "
                "Useful after open_application to wait for the app to load. "
                "Returns window info with position when found."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Window title (or partial) to wait for.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Max seconds to wait (default: 30, max: 120).",
                        "default": 30,
                    },
                    "interval": {
                        "type": "number",
                        "description": "Seconds between checks (default: 1).",
                        "default": 1,
                    },
                },
                "required": ["title"],
            },
        ),
        Tool(
            name="wait_for_idle",
            description=(
                "Wait until the screen or window stops changing (idle state). "
                "Compares screenshots every second. When no change for "
                "stable_seconds, considers idle. Useful for waiting for loading."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "window_title": {
                        "type": "string",
                        "description": "Window to monitor. If omitted, full screen.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Max seconds to wait (default: 30, max: 120).",
                        "default": 30,
                    },
                    "stable_seconds": {
                        "type": "number",
                        "description": "Seconds of no change = idle (default: 2, max: 10).",
                        "default": 2,
                    },
                },
            },
        ),

        # ── Agent Screen Only ──
        Tool(
            name="set_agent_screen_only",
            description=(
                "Enable or disable agent_screen_only mode. When enabled, "
                "open_application and manage_window auto-redirect windows "
                "to the agent monitor (second screen). Disabled = windows "
                "stay where opened/moved."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "enabled": {
                        "type": "boolean",
                        "description": "True to auto-redirect to agent screen, False to disable.",
                    },
                },
                "required": ["enabled"],
            },
        ),

        # ── Voice Overlay ──
        Tool(
            name="toggle_voice_overlay",
            description=(
                "Show or hide the floating voice overlay window. "
                "The overlay displays voice control status (idle/listening/processing), "
                "transcribed text, and a mini-log of recent interactions."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "visible": {
                        "type": "boolean",
                        "description": "True to show, False to hide.",
                    },
                },
                "required": ["visible"],
            },
        ),

        # ── Help / Capabilities ──
        Tool(
            name="get_capabilities",
            description=(
                "List all Marlow MCP tools organized by category. "
                "Returns tool names, descriptions (EN/ES), and parameters. "
                "Optionally filter by category name."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": (
                            "Filter to a specific category. Options: Core, System, "
                            "Background, Audio, Intelligence, Memory, Clipboard, "
                            "Web, Extensions, Automation, Adaptive, Workflow, "
                            "Self-Improve, Wait, UX, Security, Help."
                        ),
                    },
                },
            },
        ),
        Tool(
            name="get_version",
            description=(
                "Get Marlow version, total tool count, and current system state "
                "(kill switch, confirmation mode, background mode, voice hotkey)."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),

        # ── Diagnostics ──
        Tool(
            name="run_diagnostics",
            description=(
                "Run system diagnostics: check Python, monitors, microphone, "
                "Tesseract OCR, TTS engines, Whisper, system info, and safety config. "
                "Returns structured results for troubleshooting."
            ),
            inputSchema={"type": "object", "properties": {}},
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

    # ── Agent screen only: auto-move after open_application ──
    if (
        name == "open_application"
        and isinstance(result, dict)
        and result.get("success")
        and config.automation.agent_screen_only
        and background.is_background_mode_active()
    ):
        try:
            app_name = arguments.get("app_name") or arguments.get("app_path", "")
            await _auto_move_to_agent(app_name)
        except Exception:
            pass  # Best effort — don't break open_application

    # ── Adaptive behavior: record action ──
    try:
        adaptive._detector.record_action(name, arguments)
        if workflows._manager.is_recording:
            success = isinstance(result, dict) and "error" not in result
            workflows._manager.record_step(name, arguments, success)
    except Exception:
        pass  # Never break tool execution for adaptive tracking

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
        "manage_window": lambda args: _manage_window_with_redirect(args),
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
            language=args.get("language"),
            engine=args.get("engine"),
        ),
        "list_ocr_languages": lambda args: ocr.list_ocr_languages(),
        # Phase 2: Smart Find
        "smart_find": lambda args: escalation.smart_find(
            target=args["target"],
            window_title=args.get("window_title"),
            click_if_found=args.get("click_if_found", False),
        ),
        "find_elements": lambda args: escalation.find_elements(
            query=args["query"],
            window_title=args.get("window_title"),
            control_type=args.get("control_type"),
        ),
        "detect_app_framework": lambda args: app_detector.detect_app_framework(
            window_title=args.get("window_title"),
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
        # Phase 3: Visual Diff
        "visual_diff": lambda args: visual_diff.visual_diff(
            window_title=args.get("window_title"),
            description=args.get("description", ""),
        ),
        "visual_diff_compare": lambda args: visual_diff.visual_diff_compare(
            diff_id=args["diff_id"],
        ),
        # Phase 3: Memory
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
        # Phase 3: Clipboard History
        "clipboard_history": lambda args: clipboard_ext.clipboard_history(
            action=args.get("action", "list"),
            search=args.get("search"),
            limit=args.get("limit", 20),
        ),
        # Phase 3: Web Scraper
        "scrape_url": lambda args: scraper.scrape_url(
            url=args["url"],
            selector=args.get("selector"),
            format=args.get("format", "text"),
        ),
        # Phase 3: Extensions
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
        # Phase 4: Folder Watcher
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
        # Phase 4: Task Scheduler
        "schedule_task": lambda args: scheduler.schedule_task(
            name=args["name"],
            command=args["command"],
            interval_seconds=args.get("interval_seconds", 300),
            shell=args.get("shell", "powershell"),
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
        # Adaptive Behavior
        "get_suggestions": lambda args: adaptive.get_suggestions(),
        "accept_suggestion": lambda args: adaptive.accept_suggestion(
            pattern_id=args["pattern_id"],
        ),
        "dismiss_suggestion": lambda args: adaptive.dismiss_suggestion(
            pattern_id=args["pattern_id"],
        ),
        # Workflows
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
        # Self-Improve: Error Journal
        "get_error_journal": lambda args: error_journal.get_error_journal(
            window=args.get("window"),
        ),
        "clear_error_journal": lambda args: error_journal.clear_error_journal(
            window=args.get("window"),
        ),
        # Smart Wait
        "wait_for_element": lambda args: wait.wait_for_element(
            name=args["name"],
            window_title=args.get("window_title"),
            timeout=args.get("timeout", 30),
            interval=args.get("interval", 1),
        ),
        "wait_for_text": lambda args: wait.wait_for_text(
            text=args["text"],
            window_title=args.get("window_title"),
            timeout=args.get("timeout", 30),
            interval=args.get("interval", 2),
        ),
        "wait_for_window": lambda args: wait.wait_for_window(
            title=args["title"],
            timeout=args.get("timeout", 30),
            interval=args.get("interval", 1),
        ),
        "wait_for_idle": lambda args: wait.wait_for_idle(
            window_title=args.get("window_title"),
            timeout=args.get("timeout", 30),
            stable_seconds=args.get("stable_seconds", 2),
        ),
        # Safety
        "restore_user_focus": lambda args: focus.restore_user_focus_tool(),
        # Phase 5: Voice Control + TTS
        "speak": lambda args: tts.speak(
            text=args["text"],
            language=args.get("language", "auto"),
            voice=args.get("voice"),
            rate=args.get("rate", 175),
        ),
        "speak_and_listen": lambda args: tts.speak_and_listen(
            text=args["text"],
            timeout=args.get("timeout", 10),
            language=args.get("language", "auto"),
            voice=args.get("voice"),
        ),
        "get_voice_hotkey_status": lambda args: voice_hotkey.get_voice_hotkey_status(),
        # Agent Screen Only
        "set_agent_screen_only": lambda args: background.set_agent_screen_only(
            enabled=args["enabled"],
        ),
        # Voice Overlay
        "toggle_voice_overlay": lambda args: voice_overlay.toggle_voice_overlay(
            visible=args["visible"],
        ),
        # Help / Capabilities
        "get_capabilities": lambda args: help_mod.get_capabilities(
            category=args.get("category"),
        ),
        "get_version": lambda args: help_mod.get_version(
            safety_status=safety.get_status(),
            background_mode=background._manager.mode,
            voice_hotkey_active=voice_hotkey._hotkey_active,
        ),
        # Diagnostics
        "run_diagnostics": lambda args: setup_wizard.run_diagnostics(),
    }

    handler = tool_map.get(name)
    if handler:
        return await handler(arguments)
    else:
        return {"error": f"Unknown tool: {name}"}


async def _auto_move_to_agent(app_name: str) -> None:
    """
    After open_application, wait for window to appear and move to agent screen.
    Best effort — failures are silently ignored.

    / Después de abrir app, esperar ventana y mover al monitor del agente.
    """
    import asyncio as _aio
    import re

    # Wait for the window to appear (up to 3 seconds)
    search = app_name.split("\\")[-1].split(".")[0]  # "notepad.exe" → "notepad"
    if not search:
        return

    for _ in range(6):
        await _aio.sleep(0.5)
        try:
            from pywinauto import Desktop
            desktop = Desktop(backend="uia")
            wins = desktop.windows(title_re=f".*{re.escape(search)}.*")
            if wins:
                title = wins[0].window_text()
                await background.move_to_agent_screen(title)
                logger.debug(f"Auto-moved '{title}' to agent screen")
                return
        except Exception:
            continue


async def _manage_window_with_redirect(args: dict) -> dict:
    """
    Wrapper for manage_window that redirects moves to agent screen
    when agent_screen_only is active.

    / Wrapper que redirige movimientos al monitor del agente cuando
    / agent_screen_only esta activo.
    """
    action = args.get("action", "")

    # Only intercept "move" actions
    if (
        action == "move"
        and config.automation.agent_screen_only
        and background.is_background_mode_active()
    ):
        x = args.get("x")
        y = args.get("y")
        if x is not None and y is not None and background.is_on_user_screen(x, y):
            # Redirect to agent monitor
            coords = background.get_agent_move_coords()
            if coords:
                args = dict(args)
                args["x"] = coords[0]
                args["y"] = coords[1]

    return await windows.manage_window(
        window_title=args["window_title"],
        action=args["action"],
        x=args.get("x"),
        y=args.get("y"),
        width=args.get("width"),
        height=args.get("height"),
    )


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

    # First-use setup wizard (runs once, then never again)
    if setup_wizard.is_first_run():
        logger.info("First-use setup wizard starting...")
        setup_wizard.run_setup_wizard()

    logger.info(f"Marlow v{__version__} starting...")
    logger.info(f"Security: confirmation_mode={config.security.confirmation_mode}")
    logger.info(f"Kill switch: {'enabled' if config.security.kill_switch_enabled else 'DISABLED'}")
    logger.info(f"Telemetry: NEVER (zero data leaves your machine)")

    # Start kill switch listener
    safety.start_kill_switch()

    # Start voice hotkey (Ctrl+Shift+M + Ctrl+Shift+N)
    try:
        voice_hotkey.start_voice_hotkey(
            hotkey="ctrl+shift+m",
            kill_check=lambda: safety.is_killed,
        )
    except Exception as e:
        logger.warning(f"Voice hotkey failed to start: {e}")

    # Auto-setup background mode if 2+ monitors detected
    try:
        monitors = background._manager._enumerate_monitors()
        if len(monitors) >= 2:
            import asyncio as _aio
            loop = _aio.new_event_loop()
            try:
                result = loop.run_until_complete(background.setup_background_mode())
                if result.get("success"):
                    logger.info(
                        f"🖥️ Background mode auto-configured: {result.get('mode')} "
                        f"({len(monitors)} monitors)"
                    )
            finally:
                loop.close()
    except Exception as e:
        logger.warning(f"Auto background mode failed: {e}")

    # Run MCP server via stdio
    asyncio.run(_run_server())


if __name__ == "__main__":
    main()
