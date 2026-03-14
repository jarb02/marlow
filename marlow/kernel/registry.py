"""Universal Tool Registry for Marlow.

Single source of truth for all tool declarations. Each tool is declared ONCE
with its parameters, category, and metadata. Adapters in adapters.py generate
format-specific declarations for each LLM provider (Gemini, Anthropic, etc.).

Usage::

    from marlow.kernel.registry import TOOL_REGISTRY, resolve_tool_call

    # Get all tool names
    names = list(TOOL_REGISTRY.keys())

    # Resolve an alias
    real_name, transformed_args = resolve_tool_call("close_window", {"window_title": "Firefox"})

/ Registro universal de herramientas para Marlow.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

# ─────────────────────────────────────────────────────────────
# Tool Registry
# ─────────────────────────────────────────────────────────────

TOOL_REGISTRY: dict[str, dict[str, Any]] = {

    # ══════════════════════════════════════════════════════════
    # INPUT (5 tools)
    # ══════════════════════════════════════════════════════════

    "click": {
        "description": "Click at absolute screen coordinates using ydotool.",
        "category": "input",
        "params": {
            "x": {
                "type": "integer",
                "description": "X coordinate.",
            },
            "y": {
                "type": "integer",
                "description": "Y coordinate.",
            },
            "button": {
                "type": "string",
                "description": "Mouse button: left, right, or middle.",
                "default": "left",
            },
        },
        "required": ["x", "y"],
    },

    "type_text": {
        "description": "Type text into the focused window using wtype.",
        "category": "input",
        "params": {
            "text": {
                "type": "string",
                "description": "Text to type.",
            },
        },
        "required": ["text"],
    },

    "press_key": {
        "description": "Press and release a single key using XKB key names (Return, Tab, Escape, BackSpace, Up, Down, F1-F12, etc.).",
        "category": "input",
        "params": {
            "key": {
                "type": "string",
                "description": "Key name (e.g., 'Return', 'Tab', 'Escape').",
            },
        },
        "required": ["key"],
    },

    "hotkey": {
        "description": "Press a key combination (modifier + key). Example: hotkey(['ctrl', 'shift', 't']).",
        "category": "input",
        "params": {
            "keys": {
                "type": "array",
                "description": "Keys to press together (e.g., ['ctrl', 'c']).",
            },
        },
        "required": ["keys"],
    },

    "move_mouse": {
        "description": "Move the mouse to absolute screen coordinates.",
        "category": "input",
        "params": {
            "x": {
                "type": "integer",
                "description": "X coordinate.",
            },
            "y": {
                "type": "integer",
                "description": "Y coordinate.",
            },
        },
        "required": ["x", "y"],
    },

    # ══════════════════════════════════════════════════════════
    # WINDOWS (3 tools)
    # ══════════════════════════════════════════════════════════

    "list_windows": {
        "description": "List all open windows with titles, positions, and app info.",
        "category": "windows",
        "params": {
            "include_minimized": {
                "type": "boolean",
                "description": "Include minimized windows (default: true).",
                "default": True,
            },
        },
    },

    "focus_window": {
        "description": "Focus a window by title substring or container ID.",
        "category": "windows",
        "params": {
            "window_title": {
                "type": "string",
                "description": "Window title (substring) or Sway con_id.",
            },
        },
        "required": ["window_title"],
    },

    "manage_window": {
        "description": "Perform a management action on a window: minimize, maximize, restore, close, move, resize, fullscreen.",
        "category": "windows",
        "params": {
            "window_title": {
                "type": "string",
                "description": "Window title or ID.",
            },
            "action": {
                "type": "string",
                "description": "Action to perform: minimize, maximize, restore, close, move, resize, fullscreen.",
            },
            "x": {
                "type": "integer",
                "description": "X position for move.",
                "optional": True,
            },
            "y": {
                "type": "integer",
                "description": "Y position for move.",
                "optional": True,
            },
            "width": {
                "type": "integer",
                "description": "Width for resize.",
                "optional": True,
            },
            "height": {
                "type": "integer",
                "description": "Height for resize.",
                "optional": True,
            },
        },
        "required": ["window_title", "action"],
    },

    # ══════════════════════════════════════════════════════════
    # SHADOW (9 tools)
    # ══════════════════════════════════════════════════════════

    "setup_background_mode": {
        "description": "Set up background mode using a dedicated Sway workspace. Marlow can work invisibly in its own workspace.",
        "category": "shadow",
        "params": {
            "preferred_mode": {
                "type": "string",
                "description": "Preferred background mode.",
                "optional": True,
            },
        },
    },

    "move_to_agent_screen": {
        "description": "Move a window to the agent workspace (Shadow Mode).",
        "category": "shadow",
        "params": {
            "window_title": {
                "type": "string",
                "description": "Window to move. If omitted, moves focused window.",
                "optional": True,
            },
        },
    },

    "move_to_user_screen": {
        "description": "Move a window back from agent workspace to user workspace.",
        "category": "shadow",
        "params": {
            "window_title": {
                "type": "string",
                "description": "Window to move back.",
                "optional": True,
            },
        },
    },

    "get_agent_screen_state": {
        "description": "Get the state of the agent workspace (windows, active status).",
        "category": "shadow",
        "params": {},
    },

    "set_agent_screen_only": {
        "description": "Enable/disable agent-only mode (Marlow stays in its workspace).",
        "category": "shadow",
        "params": {
            "enabled": {
                "type": "boolean",
                "description": "True to restrict Marlow to agent workspace.",
            },
        },
        "required": ["enabled"],
    },

    "launch_in_shadow": {
        "description": "Launch an application directly in the shadow workspace without affecting the user's screen.",
        "category": "shadow",
        "params": {
            "app_name": {
                "type": "string",
                "description": "Application name or path to launch.",
            },
        },
        "required": ["app_name"],
    },

    "get_shadow_windows": {
        "description": "List all windows currently in the shadow workspace.",
        "category": "shadow",
        "params": {},
    },

    "move_to_user": {
        "description": "Move a window from the shadow workspace to the user's visible workspace.",
        "category": "shadow",
        "params": {
            "window_title": {
                "type": "string",
                "description": "Window to move to user workspace.",
            },
        },
        "required": ["window_title"],
    },

    "move_to_shadow": {
        "description": "Move a window from the user's workspace into the shadow workspace.",
        "category": "shadow",
        "params": {
            "window_title": {
                "type": "string",
                "description": "Window to move to shadow workspace.",
            },
        },
        "required": ["window_title"],
    },

    # ══════════════════════════════════════════════════════════
    # ACCESSIBILITY (12 tools)
    # ══════════════════════════════════════════════════════════

    "get_ui_tree": {
        "description": "Read the accessibility tree of a window or the entire desktop. Returns hierarchical structure with roles, names, states, and bounds via AT-SPI2.",
        "category": "accessibility",
        "params": {
            "window_title": {
                "type": "string",
                "description": "Window title (substring match). If omitted, returns desktop tree.",
                "optional": True,
            },
            "max_depth": {
                "type": "integer",
                "description": "Max tree depth (default: 8).",
                "optional": True,
            },
        },
    },

    "find_elements": {
        "description": "Search the accessibility tree for elements matching criteria. Supports fuzzy name matching (Levenshtein), role filter, and state filter.",
        "category": "accessibility",
        "params": {
            "name": {
                "type": "string",
                "description": "Element name to search for (fuzzy match).",
                "optional": True,
            },
            "role": {
                "type": "string",
                "description": "Role filter (e.g., 'button', 'text', 'menu item').",
                "optional": True,
            },
            "states": {
                "type": "array",
                "description": "Required states (e.g., ['focused'], ['enabled', 'visible']).",
                "optional": True,
            },
            "window_title": {
                "type": "string",
                "description": "Limit search to this window.",
                "optional": True,
            },
        },
    },

    "get_element_properties": {
        "description": "Get detailed properties for a specific element by tree path. Returns role, name, states, bounds, interfaces, actions, text, value.",
        "category": "accessibility",
        "params": {
            "path": {
                "type": "string",
                "description": "Dot-separated tree path (e.g., '0.2.1').",
            },
            "window_title": {
                "type": "string",
                "description": "Window context for the path.",
                "optional": True,
            },
        },
        "required": ["path"],
    },

    "do_action": {
        "description": "Execute an action on an element via the AT-SPI2 Action interface. Common actions: 'click', 'activate', 'press', 'toggle'.",
        "category": "accessibility",
        "params": {
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
                "optional": True,
            },
        },
        "required": ["path", "action_name"],
    },

    "get_text": {
        "description": "Get text content from an element via the AT-SPI2 Text interface.",
        "category": "accessibility",
        "params": {
            "path": {
                "type": "string",
                "description": "Dot-separated tree path.",
            },
            "window_title": {
                "type": "string",
                "description": "Window context.",
                "optional": True,
            },
        },
        "required": ["path"],
    },

    "smart_find": {
        "description": "Find a UI element using escalating strategies: 1) AT-SPI2 accessibility tree, 2) OCR text search, 3) Screenshot fallback. Returns the found element with method used and confidence score.",
        "category": "accessibility",
        "params": {
            "name": {
                "type": "string",
                "description": "Element name or visible text to find.",
                "optional": True,
            },
            "role": {
                "type": "string",
                "description": "Role filter (e.g., 'button', 'text').",
                "optional": True,
            },
            "window_title": {
                "type": "string",
                "description": "Limit search to this window.",
                "optional": True,
            },
        },
    },

    "cascade_find": {
        "description": "Find an element trying multiple strategies in order: exact match, partial name, all windows, full-screen OCR. Returns the first successful result with strategy metadata.",
        "category": "accessibility",
        "params": {
            "name": {
                "type": "string",
                "description": "Element name or text to find.",
                "optional": True,
            },
            "role": {
                "type": "string",
                "description": "Role filter.",
                "optional": True,
            },
            "window_title": {
                "type": "string",
                "description": "Limit initial search to this window.",
                "optional": True,
            },
        },
    },

    "get_annotated_screenshot": {
        "description": "Take a screenshot with numbered [1], [2], [3]... labels drawn on each interactive UI element. Returns annotated PNG + element map. Use som_click(index) to click an element by its number.",
        "category": "accessibility",
        "params": {
            "window_title": {
                "type": "string",
                "description": "Window to annotate. If omitted, uses focused window.",
                "optional": True,
            },
            "max_depth": {
                "type": "integer",
                "description": "Max tree depth for element discovery (default: 10).",
                "optional": True,
            },
        },
    },

    "som_click": {
        "description": "Click an element by its numbered label from get_annotated_screenshot. Tries AT-SPI2 action first, falls back to coordinate click.",
        "category": "accessibility",
        "params": {
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

    "detect_dialogs": {
        "description": "Scan the accessibility tree for active dialog windows. Returns title, message, buttons, type, and app for each dialog.",
        "category": "accessibility",
        "kernel_registered": False,
        "params": {},
    },

    "start_ui_monitor": {
        "description": "Start real-time UI event monitoring via AT-SPI2. Detects window opens/closes and focus changes.",
        "category": "accessibility",
        "kernel_registered": False,
        "params": {},
    },

    "stop_ui_monitor": {
        "description": "Stop the AT-SPI2 event monitor.",
        "category": "accessibility",
        "kernel_registered": False,
        "params": {},
    },

    # ══════════════════════════════════════════════════════════
    # SCREENSHOT (1 tool)
    # ══════════════════════════════════════════════════════════

    "take_screenshot": {
        "description": "Capture a screenshot of the full screen, a specific window, or a region. Returns PNG image. Uses grim on Wayland.",
        "category": "screenshot",
        "params": {
            "window_title": {
                "type": "string",
                "description": "Capture only this window. If omitted, full screen.",
                "optional": True,
            },
            "region": {
                "type": "object",
                "description": "Region to capture: {x, y, width, height}.",
                "optional": True,
            },
        },
    },

    # ══════════════════════════════════════════════════════════
    # OCR (2 tools)
    # ══════════════════════════════════════════════════════════

    "ocr_region": {
        "description": "Extract text from a screen region using Tesseract OCR. Returns full text and word-level bounding boxes with confidence. Can target a specific window or region.",
        "category": "ocr",
        "params": {
            "window_title": {
                "type": "string",
                "description": "Window to OCR. If omitted, full screen.",
                "optional": True,
            },
            "region": {
                "type": "object",
                "description": "Region: {x, y, width, height}.",
                "optional": True,
            },
            "language": {
                "type": "string",
                "description": "Tesseract language code (default: 'eng').",
                "default": "eng",
            },
        },
    },

    "list_ocr_languages": {
        "description": "List available Tesseract OCR language packs.",
        "category": "ocr",
        "params": {},
    },

    # ══════════════════════════════════════════════════════════
    # SYSTEM (6 tools)
    # ══════════════════════════════════════════════════════════

    "run_command": {
        "description": "Execute a shell command via bash. Returns stdout, stderr, exit code.",
        "category": "system",
        "params": {
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

    "open_application": {
        "description": "Launch an application by name or path. Uses which + xdg-open fallback.",
        "category": "system",
        "params": {
            "app_name": {
                "type": "string",
                "description": "Application name or path (e.g., 'firefox', 'nautilus').",
            },
        },
        "required": ["app_name"],
    },

    "system_info": {
        "description": "Get system information: OS, CPU, memory, display.",
        "category": "system",
        "params": {},
    },

    "run_diagnostics": {
        "description": "Run system diagnostics: check Python, Sway, AT-SPI2, CLI tools, Tesseract, PipeWire, disk, RAM, GPU, and pip dependencies.",
        "category": "system",
        "kernel_registered": False,
        "params": {},
    },

    "run_app_script": {
        "description": "Run a COM automation script (Windows-only). On Linux, returns a message suggesting alternatives like CDP or D-Bus.",
        "category": "system",
        "kernel_registered": False,
        "params": {
            "script": {
                "type": "string",
                "description": "Script to execute.",
            },
            "app_name": {
                "type": "string",
                "description": "Target application.",
                "optional": True,
            },
        },
        "required": ["script"],
    },

    "kill_switch": {
        "description": "Emergency stop: halt ALL Marlow automation. action: activate=stop, reset=resume, status=check.",
        "category": "system",
        "params": {
            "action": {
                "type": "string",
                "description": "Action: 'activate', 'reset', or 'status'.",
            },
        },
        "required": ["action"],
    },

    # ══════════════════════════════════════════════════════════
    # AUDIO (4 tools)
    # ══════════════════════════════════════════════════════════

    "capture_system_audio": {
        "description": "Record system audio via PipeWire monitor loopback. Captures what's playing through speakers. Max 300 seconds.",
        "category": "audio",
        "params": {
            "duration_seconds": {
                "type": "integer",
                "description": "Recording duration in seconds (default: 10, max: 300).",
                "default": 10,
            },
        },
    },

    "capture_mic_audio": {
        "description": "Record microphone audio via PipeWire. Max 300 seconds.",
        "category": "audio",
        "params": {
            "duration_seconds": {
                "type": "integer",
                "description": "Recording duration in seconds (default: 10, max: 300).",
                "default": 10,
            },
        },
    },

    "transcribe_audio": {
        "description": "Transcribe an audio file using faster-whisper.",
        "category": "audio",
        "params": {
            "audio_path": {
                "type": "string",
                "description": "Path to WAV audio file.",
            },
            "language": {
                "type": "string",
                "description": "Language or 'auto'.",
                "optional": True,
            },
            "model_size": {
                "type": "string",
                "description": "Whisper model size.",
                "optional": True,
            },
        },
        "required": ["audio_path"],
    },

    "download_whisper_model": {
        "description": "Pre-download a Whisper model so transcription is instant.",
        "category": "audio",
        "params": {
            "model_size": {
                "type": "string",
                "description": "Model to download: tiny, base, small, medium (default: base).",
                "optional": True,
            },
        },
    },

    # ══════════════════════════════════════════════════════════
    # VOICE (5 tools)
    # ══════════════════════════════════════════════════════════

    "speak": {
        "description": "Speak text aloud via TTS. Primary: edge-tts (neural, online). Fallback: Piper TTS (neural, offline). Auto-detects language.",
        "category": "voice",
        "params": {
            "text": {
                "type": "string",
                "description": "Text to speak.",
            },
            "language": {
                "type": "string",
                "description": "Language: 'auto', 'es', or 'en'. Default: auto.",
                "optional": True,
            },
            "voice": {
                "type": "string",
                "description": "Voice name or alias (e.g., 'jorge', 'jenny').",
                "optional": True,
            },
            "rate": {
                "type": "integer",
                "description": "Speech rate in WPM (default: 175).",
                "optional": True,
            },
        },
        "required": ["text"],
    },

    "speak_and_listen": {
        "description": "Speak text, then listen for a voice response via microphone.",
        "category": "voice",
        "params": {
            "text": {
                "type": "string",
                "description": "Text to speak first.",
            },
            "timeout": {
                "type": "integer",
                "description": "Seconds to listen after speaking (default: 10, max: 60).",
                "optional": True,
            },
            "language": {
                "type": "string",
                "description": "Language code.",
                "optional": True,
            },
            "voice": {
                "type": "string",
                "description": "Voice name or alias.",
                "optional": True,
            },
        },
        "required": ["text"],
    },

    "listen_for_command": {
        "description": "Listen for a voice command via microphone. Records immediately, transcribes with Whisper, returns text.",
        "category": "voice",
        "params": {
            "duration_seconds": {
                "type": "integer",
                "description": "How long to listen (default: 10, max: 60).",
                "optional": True,
            },
            "language": {
                "type": "string",
                "description": "Language code or 'auto'.",
                "optional": True,
            },
            "model_size": {
                "type": "string",
                "description": "Whisper model: 'tiny', 'base', 'small', 'medium'.",
                "optional": True,
            },
        },
    },

    "get_voice_hotkey_status": {
        "description": "Get voice hotkey status and configuration.",
        "category": "voice",
        "params": {},
    },

    "toggle_voice_overlay": {
        "description": "Toggle the voice overlay display.",
        "category": "voice",
        "params": {},
    },

    # ══════════════════════════════════════════════════════════
    # MEMORY (4 tools)
    # ══════════════════════════════════════════════════════════

    "memory_save": {
        "description": "Save a value persistently across sessions.",
        "category": "memory",
        "params": {
            "key": {
                "type": "string",
                "description": "Unique key.",
            },
            "value": {
                "type": "string",
                "description": "Text/data to store.",
            },
            "category": {
                "type": "string",
                "description": "Category: general, preferences, projects, or tasks.",
                "default": "general",
            },
        },
        "required": ["key", "value"],
    },

    "memory_recall": {
        "description": "Recall stored memories by key and/or category.",
        "category": "memory",
        "params": {
            "key": {
                "type": "string",
                "description": "Key to recall.",
                "optional": True,
            },
            "category": {
                "type": "string",
                "description": "Category: general, preferences, projects, or tasks.",
                "optional": True,
            },
        },
    },

    "memory_delete": {
        "description": "Delete a specific memory by key and category.",
        "category": "memory",
        "params": {
            "key": {
                "type": "string",
                "description": "Key to delete.",
            },
            "category": {
                "type": "string",
                "description": "Category: general, preferences, projects, or tasks.",
                "default": "general",
            },
        },
        "required": ["key"],
    },

    "memory_list": {
        "description": "List all stored memories organized by category.",
        "category": "memory",
        "params": {},
    },

    # ══════════════════════════════════════════════════════════
    # CLIPBOARD (3 tools)
    # ══════════════════════════════════════════════════════════

    "clipboard": {
        "description": "Read or write the system clipboard. action='get' reads, action='set' writes text.",
        "category": "clipboard",
        "params": {
            "action": {
                "type": "string",
                "description": "Action: 'get' to read, 'set' to write.",
            },
            "text": {
                "type": "string",
                "description": "Text to write (only for action='set').",
                "optional": True,
            },
        },
        "required": ["action"],
    },

    "clipboard_history": {
        "description": "Get clipboard history (in-memory, since session start).",
        "category": "clipboard",
        "kernel_registered": False,
        "params": {
            "limit": {
                "type": "integer",
                "description": "Max entries to return (default: 20).",
                "optional": True,
            },
        },
    },

    "restore_user_focus": {
        "description": "Restore focus to the user's previously active window. Marlow auto-preserves focus, but call this for manual correction.",
        "category": "clipboard",
        "params": {},
    },

    # ══════════════════════════════════════════════════════════
    # CDP — Chrome DevTools Protocol (4 tools, Electron/Chromium only)
    # ══════════════════════════════════════════════════════════

    "cdp_send": {
        "description": "Send a raw CDP command to an Electron/Chromium app. Supports any CDP method: Page.navigate, Network.enable, CSS queries, etc. Only works with apps that have remote debugging enabled.",
        "category": "cdp",
        "params": {
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
                "optional": True,
            },
        },
        "required": ["port", "method"],
    },

    "cdp_screenshot": {
        "description": "Take screenshot of an Electron/Chromium app via CDP. Works even if window is behind others or minimized. Only works with apps that have remote debugging enabled.",
        "category": "cdp",
        "params": {
            "port": {
                "type": "integer",
                "description": "CDP port.",
            },
            "format": {
                "type": "string",
                "description": "Image format: png or jpeg (default: png).",
                "default": "png",
            },
        },
        "required": ["port"],
    },

    "cdp_evaluate": {
        "description": "Evaluate JavaScript in an Electron/Chromium app page context via CDP. Only works with apps that have remote debugging enabled.",
        "category": "cdp",
        "params": {
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

    "cdp_get_dom": {
        "description": "Get the DOM tree of an Electron/Chromium app page via CDP. Returns structured node tree with tag names, attributes, and children. Only works with apps that have remote debugging enabled.",
        "category": "cdp",
        "params": {
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

    # ══════════════════════════════════════════════════════════
    # AUTOMATION (18 tools)
    # ══════════════════════════════════════════════════════════

    "watch_folder": {
        "description": "Start monitoring a folder for file changes.",
        "category": "automation",
        "params": {
            "path": {
                "type": "string",
                "description": "Folder path.",
            },
            "events": {
                "type": "array",
                "description": "Event types to watch: created, modified, deleted, moved.",
                "optional": True,
            },
            "recursive": {
                "type": "boolean",
                "description": "Watch subdirectories recursively.",
                "default": False,
            },
        },
        "required": ["path"],
    },

    "unwatch_folder": {
        "description": "Stop monitoring a folder.",
        "category": "automation",
        "params": {
            "watch_id": {
                "type": "string",
                "description": "Watcher ID to stop.",
            },
        },
        "required": ["watch_id"],
    },

    "get_watch_events": {
        "description": "Get detected filesystem events.",
        "category": "automation",
        "params": {
            "watch_id": {
                "type": "string",
                "description": "Watcher ID.",
                "optional": True,
            },
            "limit": {
                "type": "integer",
                "description": "Max events to return.",
                "default": 50,
            },
            "since": {
                "type": "string",
                "description": "ISO timestamp to filter events after.",
                "optional": True,
            },
        },
    },

    "list_watchers": {
        "description": "List all active folder watchers.",
        "category": "automation",
        "params": {},
    },

    "schedule_task": {
        "description": "Schedule a recurring shell command.",
        "category": "automation",
        "params": {
            "name": {
                "type": "string",
                "description": "Unique task name.",
            },
            "command": {
                "type": "string",
                "description": "Shell command.",
            },
            "interval_seconds": {
                "type": "integer",
                "description": "Run every N seconds (default: 300, min: 10).",
                "default": 300,
            },
            "shell": {
                "type": "string",
                "description": "Shell to use: bash or sh.",
                "default": "bash",
            },
            "max_runs": {
                "type": "integer",
                "description": "Maximum number of runs before auto-stop.",
                "optional": True,
            },
        },
        "required": ["name", "command"],
    },

    "list_scheduled_tasks": {
        "description": "List all scheduled tasks with status.",
        "category": "automation",
        "params": {},
    },

    "remove_task": {
        "description": "Remove a scheduled task by name.",
        "category": "automation",
        "params": {
            "task_name": {
                "type": "string",
                "description": "Task name to remove.",
            },
        },
        "required": ["task_name"],
    },

    "get_task_history": {
        "description": "Get execution history for scheduled tasks.",
        "category": "automation",
        "params": {
            "task_name": {
                "type": "string",
                "description": "Task name. If omitted, all tasks.",
                "optional": True,
            },
            "limit": {
                "type": "integer",
                "description": "Max entries to return.",
                "default": 20,
            },
        },
    },

    "workflow_record": {
        "description": "Start recording a new workflow (sequence of tool calls).",
        "category": "automation",
        "params": {
            "name": {
                "type": "string",
                "description": "Name for the workflow.",
            },
        },
        "required": ["name"],
    },

    "workflow_stop": {
        "description": "Stop recording and save the current workflow.",
        "category": "automation",
        "params": {},
    },

    "workflow_run": {
        "description": "Replay a saved workflow. Checks safety before each step.",
        "category": "automation",
        "params": {
            "name": {
                "type": "string",
                "description": "Workflow name to replay.",
            },
        },
        "required": ["name"],
    },

    "workflow_list": {
        "description": "List all saved workflows with metadata.",
        "category": "automation",
        "params": {},
    },

    "workflow_delete": {
        "description": "Delete a saved workflow by name.",
        "category": "automation",
        "params": {
            "name": {
                "type": "string",
                "description": "Workflow name to delete.",
            },
        },
        "required": ["name"],
    },

    "get_suggestions": {
        "description": "Analyze recorded tool call patterns and suggest automatable sequences.",
        "category": "automation",
        "params": {},
    },

    "accept_suggestion": {
        "description": "Mark a pattern suggestion as accepted.",
        "category": "automation",
        "params": {
            "pattern_id": {
                "type": "string",
                "description": "Pattern ID to accept.",
            },
        },
        "required": ["pattern_id"],
    },

    "dismiss_suggestion": {
        "description": "Dismiss a pattern suggestion so it won't appear again.",
        "category": "automation",
        "params": {
            "pattern_id": {
                "type": "string",
                "description": "Pattern ID to dismiss.",
            },
        },
        "required": ["pattern_id"],
    },

    "get_error_journal": {
        "description": "Show method failure/success records per tool+app.",
        "category": "automation",
        "params": {
            "window": {
                "type": "string",
                "description": "Filter by app.",
                "optional": True,
            },
        },
    },

    "clear_error_journal": {
        "description": "Clear error journal entries.",
        "category": "automation",
        "params": {
            "window": {
                "type": "string",
                "description": "Clear for this app only.",
                "optional": True,
            },
        },
    },

    # ══════════════════════════════════════════════════════════
    # WAITS (4 tools)
    # ══════════════════════════════════════════════════════════

    "wait_for_element": {
        "description": "Poll until a UI element appears (AT-SPI2 with OCR fallback). Returns when found or after timeout.",
        "category": "waits",
        "params": {
            "name": {
                "type": "string",
                "description": "Element name to wait for.",
                "optional": True,
            },
            "role": {
                "type": "string",
                "description": "Role filter (e.g., 'button').",
                "optional": True,
            },
            "window_title": {
                "type": "string",
                "description": "Limit to this window.",
                "optional": True,
            },
            "timeout": {
                "type": "number",
                "description": "Max seconds to wait (default: 10).",
                "default": 10,
            },
            "interval": {
                "type": "number",
                "description": "Poll interval seconds (default: 0.5).",
                "default": 0.5,
            },
        },
    },

    "wait_for_text": {
        "description": "Poll OCR until target text appears on screen. Returns when found or after timeout.",
        "category": "waits",
        "params": {
            "text": {
                "type": "string",
                "description": "Text to wait for.",
            },
            "window_title": {
                "type": "string",
                "description": "Limit OCR to this window.",
                "optional": True,
            },
            "timeout": {
                "type": "number",
                "description": "Max seconds (default: 10).",
                "default": 10,
            },
            "interval": {
                "type": "number",
                "description": "Poll interval (default: 0.5).",
                "default": 0.5,
            },
        },
        "required": ["text"],
    },

    "wait_for_window": {
        "description": "Poll until a window with matching title appears. Uses fuzzy substring match on window titles.",
        "category": "waits",
        "params": {
            "title": {
                "type": "string",
                "description": "Window title to wait for.",
            },
            "timeout": {
                "type": "number",
                "description": "Max seconds (default: 10).",
                "default": 10,
            },
            "interval": {
                "type": "number",
                "description": "Poll interval (default: 0.5).",
                "default": 0.5,
            },
        },
        "required": ["title"],
    },

    "wait_for_idle": {
        "description": "Poll until screen content stabilizes (stops changing). Compares consecutive screenshots to detect stability.",
        "category": "waits",
        "params": {
            "window_title": {
                "type": "string",
                "description": "Limit to this window.",
                "optional": True,
            },
            "timeout": {
                "type": "number",
                "description": "Max seconds (default: 10).",
                "default": 10,
            },
            "threshold": {
                "type": "number",
                "description": "Similarity threshold 0-1 (default: 0.95).",
                "default": 0.95,
            },
        },
    },

    # ══════════════════════════════════════════════════════════
    # VISUAL (2 tools)
    # ══════════════════════════════════════════════════════════

    "visual_diff": {
        "description": "Capture 'before' screenshot for later comparison. Call BEFORE an action, then call visual_diff_compare AFTER.",
        "category": "visual",
        "params": {
            "window_title": {
                "type": "string",
                "description": "Window to capture. If omitted, full screen.",
                "optional": True,
            },
            "label": {
                "type": "string",
                "description": "Optional label for this capture.",
                "optional": True,
            },
        },
    },

    "visual_diff_compare": {
        "description": "Compare current screen with 'before' capture. Returns change percentage, bounding box, and diff image.",
        "category": "visual",
        "params": {
            "diff_id": {
                "type": "string",
                "description": "ID from visual_diff().",
            },
            "window_title": {
                "type": "string",
                "description": "Window to capture for 'after'. If omitted, uses same as before.",
                "optional": True,
            },
        },
        "required": ["diff_id"],
    },

    # ══════════════════════════════════════════════════════════
    # META (1 tool)
    # ══════════════════════════════════════════════════════════

    "execute_complex_goal": {
        "description": "Delegate a complex multi-step task to the autonomous kernel planner. The kernel will plan, validate, and execute the goal step-by-step with replanning on failure.",
        "category": "meta",
        "params": {
            "goal": {
                "type": "string",
                "description": "Natural language description of the goal to achieve.",
            },
            "context": {
                "type": "object",
                "description": "Optional context dict with hints for the planner.",
                "optional": True,
            },
        },
        "required": ["goal"],
    },

    # ══════════════════════════════════════════════════════════
    # DEMO — Learning from Demonstration (5 tools)
    # ══════════════════════════════════════════════════════════

    "demo_start": {
        "description": "Start recording a user demonstration. Captures tool calls and AT-SPI2 events for later replay.",
        "category": "demo",
        "kernel_registered": False,
        "params": {
            "name": {
                "type": "string",
                "description": "Name for this demonstration.",
            },
            "description": {
                "type": "string",
                "description": "Optional description.",
                "optional": True,
            },
        },
        "required": ["name"],
    },

    "demo_stop": {
        "description": "Stop recording and extract a replayable plan from the demonstration.",
        "category": "demo",
        "kernel_registered": False,
        "params": {},
    },

    "demo_status": {
        "description": "Get current demonstration recording status.",
        "category": "demo",
        "kernel_registered": False,
        "params": {},
    },

    "demo_list": {
        "description": "List all saved demonstrations.",
        "category": "demo",
        "kernel_registered": False,
        "params": {},
    },

    "demo_replay": {
        "description": "Load a saved demonstration and return its extracted plan steps for replay.",
        "category": "demo",
        "kernel_registered": False,
        "params": {
            "filename": {
                "type": "string",
                "description": "Demo filename to load.",
            },
        },
        "required": ["filename"],
    },

    # ══════════════════════════════════════════════════════════
    # SCRAPER (1 tool — in automation conceptually, separate here)
    # ══════════════════════════════════════════════════════════

    "scrape_url": {
        "description": "Extract content from a URL. Formats: text, links, tables, html.",
        "category": "system",
        "params": {
            "url": {
                "type": "string",
                "description": "URL to scrape.",
            },
            "selector": {
                "type": "string",
                "description": "CSS selector filter.",
                "optional": True,
            },
            "format": {
                "type": "string",
                "description": "Output format: text, links, tables, html.",
                "default": "text",
            },
        },
        "required": ["url"],
    },
    # ══════════════════════════════════════════════════════════
    # FILESYSTEM (2 tools)
    # ══════════════════════════════════════════════════════════

    "search_files": {
        "description": "Search for files by name with fuzzy matching. Supports partial names, multiple keywords, extension filtering, and date filtering. Use scope='system' to search entire computer, default is home directory only.",
        "category": "filesystem",
        "params": {
            "query": {
                "type": "string",
                "description": "Search terms - partial filename, keywords, or pattern. Multiple words match files containing ALL words in any order.",
            },
            "path": {
                "type": "string",
                "description": "Specific directory to search in. Overrides scope if provided.",
                "optional": True,
            },
            "scope": {
                "type": "string",
                "description": "Search scope: 'home' (default, ~/), or 'system' (entire computer, excludes system dirs).",
                "default": "home",
            },
            "extension": {
                "type": "string",
                "description": "Filter by file extension, e.g. '.pdf', '.txt'.",
                "optional": True,
            },
            "modified_after": {
                "type": "string",
                "description": "Only files modified after this date (ISO format: '2026-03-13').",
                "optional": True,
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum results to return (default 20, max 100).",
                "default": 20,
            },
        },
        "required": ["query"],
    },

    "list_directory": {
        "description": "List contents of a directory (files and subdirectories). Shows name, type, size, and modification date. Non-recursive (one level only).",
        "category": "filesystem",
        "params": {
            "path": {
                "type": "string",
                "description": "Directory to list (default: home directory ~).",
                "default": "~",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum entries to return (default 50, max 200).",
                "default": 50,
            },
            "show_hidden": {
                "type": "boolean",
                "description": "Include hidden files/dirs starting with '.' (default false).",
                "default": False,
            },
        },
    },

    "read_file": {
        "description": "Read the contents of a text file. Supports partial reading by line range. Blocks binary files and sensitive paths (SSH keys, secrets). Max default size 1MB.",
        "category": "filesystem",
        "params": {
            "path": {
                "type": "string",
                "description": "Path to the file to read. Supports ~ for home directory.",
            },
            "max_size_kb": {
                "type": "integer",
                "description": "Maximum file size in KB to read (default 1024 = 1MB).",
                "default": 1024,
            },
            "encoding": {
                "type": "string",
                "description": "File encoding (default 'utf-8'). Try 'latin-1' if utf-8 fails.",
                "default": "utf-8",
            },
            "line_start": {
                "type": "integer",
                "description": "First line to read (1-indexed). Omit to read from beginning.",
                "optional": True,
            },
            "line_end": {
                "type": "integer",
                "description": "Last line to read (1-indexed). Omit to read to end.",
                "optional": True,
            },
        },
        "required": ["path"],
    },

    "write_file": {
        "description": "Create a new text file or append to an existing one. By default refuses to overwrite existing files. Only writes within home directory. Use for notes, scripts, configs, or any text content.",
        "category": "filesystem",
        "params": {
            "path": {
                "type": "string",
                "description": "Path for the file. Supports ~ for home directory.",
            },
            "content": {
                "type": "string",
                "description": "Text content to write to the file.",
            },
            "overwrite": {
                "type": "boolean",
                "description": "Allow overwriting existing files (default false).",
                "default": False,
            },
            "create_dirs": {
                "type": "boolean",
                "description": "Create parent directories if they don't exist (default false).",
                "default": False,
            },
            "append": {
                "type": "boolean",
                "description": "Append to end of file instead of replacing (default false).",
                "default": False,
            },
        },
        "required": ["path", "content"],
    },

}


# ─────────────────────────────────────────────────────────────
# Tool Aliases
# ─────────────────────────────────────────────────────────────

def _close_window_transform(args: dict) -> dict:
    """Transform close_window args into manage_window args."""
    return {
        "window_title": args.get("window_title", ""),
        "action": "close",
    }


TOOL_ALIASES: dict[str, tuple[str, Optional[Callable[[dict], dict]]]] = {
    "close_window": ("manage_window", _close_window_transform),
}


# ─────────────────────────────────────────────────────────────
# Alias Resolution
# ─────────────────────────────────────────────────────────────

def resolve_tool_call(
    name: str, args: dict
) -> tuple[str, dict]:
    """Resolve a tool call, handling aliases.

    If ``name`` is an alias, returns the real tool name and transformed args.
    Otherwise returns (name, args) unchanged.

    Args:
        name: Tool name (may be an alias).
        args: Tool arguments dict.

    Returns:
        Tuple of (resolved_tool_name, resolved_args).
    """
    if name in TOOL_ALIASES:
        real_name, transform_fn = TOOL_ALIASES[name]
        if transform_fn is not None:
            args = transform_fn(args)
        return real_name, args
    return name, args


# ─────────────────────────────────────────────────────────────
# Utility Functions
# ─────────────────────────────────────────────────────────────

def get_categories() -> list[str]:
    """Return sorted list of all tool categories."""
    cats = set()
    for tool in TOOL_REGISTRY.values():
        cats.add(tool["category"])
    return sorted(cats)


def get_tools_by_category(category: str) -> list[str]:
    """Return tool names belonging to a category."""
    return [
        name for name, tool in TOOL_REGISTRY.items()
        if tool["category"] == category
    ]


def get_kernel_tools() -> list[str]:
    """Return tool names registered in the kernel (kernel_registered != False)."""
    return [
        name for name, tool in TOOL_REGISTRY.items()
        if tool.get("kernel_registered", True)
    ]


def get_mcp_only_tools() -> list[str]:
    """Return tool names in MCP server but NOT registered in kernel."""
    return [
        name for name, tool in TOOL_REGISTRY.items()
        if not tool.get("kernel_registered", True)
    ]
