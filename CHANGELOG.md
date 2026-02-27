# Changelog

All notable changes to Marlow are documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.3.0] - 2026-02-27

Phase 3 complete. 39 total MCP tools. Visual diff, persistent memory,
clipboard history, web scraping, and a community extension system with
sandboxed permissions.

### Added

#### New Tool Modules (4)
- **`marlow/tools/visual_diff.py`** — 2 tools: `visual_diff` captures a "before" screenshot and returns a `diff_id`; `visual_diff_compare` captures "after" and computes pixel-level difference using PIL `ImageChops.difference()`. Returns change percentage, changed pixel count, and bounding box of changed region. States auto-expire after 5 minutes.
- **`marlow/tools/memory.py`** — 4 tools: `memory_save`, `memory_recall`, `memory_delete`, `memory_list`. Persistent key-value storage in `~/.marlow/memory/` as JSON files organized by category (general, preferences, projects, tasks). Survives across sessions.
- **`marlow/tools/clipboard_ext.py`** — 1 tool: `clipboard_history` with actions start/stop/list/search/clear. Background daemon thread monitors clipboard via Win32 API every 1 second. Stores up to 100 entries (500 chars each).
- **`marlow/tools/scraper.py`** — 1 tool: `scrape_url` with httpx async client + BeautifulSoup. Formats: text (5KB limit), links (100 max), tables, html (10KB limit). CSS selector support. Security: localhost/private IPs blocked, 30s timeout, max 5 redirects, honest User-Agent.

#### Extension System (3 modules)
- **`marlow/extensions/__init__.py`** — Manifest loader and validator for `marlow_extension.json` files.
- **`marlow/extensions/registry.py`** — 4 tools: `extensions_list`, `extensions_install` (via pip), `extensions_uninstall`, `extensions_audit` (security audit of declared permissions). Registry stored in `~/.marlow/extensions/installed.json`.
- **`marlow/extensions/sandbox.py`** — `ExtensionSandbox` class enforces declared permissions (com_automation, file_system, network, shell_commands) at runtime.

#### New Tools (12 total)
| Tool | Description |
|------|-------------|
| `visual_diff` | Capture "before" state for comparison |
| `visual_diff_compare` | Compare before/after, return change % |
| `memory_save` | Save persistent key-value data |
| `memory_recall` | Recall memories by key/category |
| `memory_delete` | Delete a specific memory |
| `memory_list` | List all memories by category |
| `clipboard_history` | Monitor and search clipboard history |
| `scrape_url` | Extract content from URLs |
| `extensions_list` | List installed extensions |
| `extensions_install` | Install extension from pip |
| `extensions_uninstall` | Uninstall extension |
| `extensions_audit` | Audit extension security |

### Changed

- **`pyproject.toml`** — Added `httpx>=0.27.0` and `beautifulsoup4>=4.12.0` to main dependencies.
- **`marlow/__init__.py`** — Version bumped from `0.2.0` to `0.3.0`.
- **`marlow/server.py`** — Added 12 new Tool definitions and dispatch entries. Total: 39 tools registered.

---

## [0.2.0] - 2026-02-27

Phase 2 complete. 27 total MCP tools. OCR, smart escalation, background mode,
audio capture/transcription, voice commands, COM automation, and focus protection.

### Added

#### New Modules (6)
- **`marlow/tools/ocr.py`** — `ocr_region` tool: extract text from windows/screen via Tesseract OCR with image preprocessing (grayscale, 2x upscale, threshold). Returns word-level bounding boxes with confidence scores. Graceful error if Tesseract binary not installed.
- **`marlow/core/escalation.py`** — `smart_find` tool: find UI elements using tiered escalation. Step 1: UI Automation tree (0 tokens, ~10-50ms). Step 2: OCR (0 tokens, ~200-500ms). Step 3: Screenshot for LLM Vision (~1,500 tokens). Tracks `methods_tried` per step. Optional `click_if_found` auto-clicks via invoke() or OCR coordinates.
- **`marlow/tools/background.py`** — 4 tools: `setup_background_mode`, `move_to_agent_screen`, `move_to_user_screen`, `get_agent_screen_state`. `BackgroundManager` singleton auto-detects monitors via `ctypes.windll.user32.EnumDisplayMonitors`. Dual monitor mode (2+ screens) or offscreen mode (1 screen).
- **`marlow/tools/audio.py`** — 4 tools: `capture_system_audio` (WASAPI loopback via PyAudioWPatch, stereo WAV), `capture_mic_audio` (sounddevice, 16kHz mono WAV), `transcribe_audio` (faster-whisper CPU int8, 300s timeout, module-level model cache), `download_whisper_model` (pre-download with 600s timeout, checks huggingface cache). Audio stored in `~/.marlow/audio/`, auto-cleanup after 1 hour. Max recording: 300 seconds.
- **`marlow/tools/voice.py`** — `listen_for_command` tool: records mic, transcribes via faster-whisper, includes RMS-based silence detection. Max 60 seconds.
- **`marlow/tools/app_script.py`** — `run_app_script` tool: COM automation for Office/Adobe apps (Word, Excel, PowerPoint, Outlook, Photoshop, Access). Sandboxed execution with empty `__builtins__`, only `app` and `result` exposed. 16 forbidden patterns block imports, eval, exec, os, sys, subprocess. Thread-safe with `pythoncom.CoInitialize()`/`CoUninitialize()`.

#### New Core Module
- **`marlow/core/focus.py`** — Focus preservation system. `save_user_focus()` stores foreground HWND via `GetForegroundWindow()`. `restore_user_focus()` restores via `SetForegroundWindow()` with `AttachThreadInput` trick. `preserve_focus()` context manager wraps individual focus-stealing calls. New MCP tool: `restore_user_focus` for manual correction.

#### New Tools (13 total)
| Tool | Description |
|------|-------------|
| `ocr_region` | Extract text via Tesseract OCR |
| `smart_find` | Find UI element with UIA/OCR/screenshot escalation |
| `setup_background_mode` | Configure dual monitor or offscreen mode |
| `move_to_agent_screen` | Move window to agent workspace |
| `move_to_user_screen` | Return window to user's primary monitor |
| `get_agent_screen_state` | List windows on agent screen |
| `capture_system_audio` | Record system audio (WASAPI loopback) |
| `capture_mic_audio` | Record microphone audio |
| `transcribe_audio` | Transcribe audio file (faster-whisper CPU) |
| `download_whisper_model` | Pre-download Whisper model |
| `listen_for_command` | Voice command via mic + transcription |
| `run_app_script` | COM automation (Office/Adobe, sandboxed) |
| `restore_user_focus` | Manually restore user's active window |

#### Notepad Win11 Protection (`keyboard.py`)
- `_find_editable_element()` — finds Document/Edit controls by `control_type` instead of name (Win11 Notepad editor has empty name and empty automation_id)
- `_set_text_silent()` — writes via `iface_value.SetValue()` (UIA ValuePattern) when `set_edit_text()` is unavailable
- `_is_win11_notepad()` — detects new tabbed Notepad (class_name `Notepad` with `RichEditD2DPT` child)
- `_get_editor_content()` — reads current editor content via ValuePattern
- `_ensure_safe_notepad_tab()` — opens a new tab (invokes `AddButton`) before writing if current tab has existing content, preventing data loss

#### Focus Guard Architecture (`server.py` + `core/focus.py`)
- `server.py:call_tool()` wraps ALL tool calls in try/finally to save/restore user's foreground window
- `focus_window` and `restore_user_focus` are excluded from auto-restore
- All 12 focus-stealing fallback calls (click_input, type_keys, pyautogui) wrapped with `preserve_focus()` context manager in mouse.py, keyboard.py, windows.py, escalation.py
- Uses Win32 `AttachThreadInput` trick for reliable `SetForegroundWindow` on Windows 11

#### server.py: smart_find Image Handling
- When `smart_find` falls back to screenshot (`requires_vision=True`), server returns `ImageContent` + `TextContent` so the LLM can use vision to locate the element

### Changed

- **`pyproject.toml`** — Audio dependencies (`PyAudioWPatch`, `sounddevice`, `soundfile`, `faster-whisper`) moved from optional to main dependencies. OCR (`pytesseract`) remains optional. Version field stays in `__init__.py`.
- **`marlow/__init__.py`** — Version bumped from `0.1.0` to `0.2.0`.
- **`marlow/server.py`** — Restructured `call_tool()` into outer focus guard + inner `_call_tool_inner()`. Added 13 new Tool definitions and dispatch entries. Total: 27 tools registered.
- **`marlow/core/safety.py`** — Added `run_app_script` to `sensitive_tools` set.
- **`marlow/tools/windows.py`** — `manage_window` move/resize now uses `ctypes.windll.user32.MoveWindow()` instead of `target.move_window()` (which doesn't exist on UIAWrapper).

### Fixed

- **`wrapper_object()` error** — `UIAWrapper` objects from `Desktop.windows()` don't have `.wrapper_object()`. Removed calls in `ui_tree.py`, `mouse.py`, `keyboard.py`.
- **MCP server startup** — `stdio_server()` in mcp v1.26.0 is an async context manager yielding `(read_stream, write_stream)`. Updated `server.py` to use `async with stdio_server() as (read_stream, write_stream)`.
- **`UIAWrapper` has no `move_window()`** — Only exists on win32 backend's `HwndWrapper`. Fixed in `windows.py` and `background.py` with `ctypes.windll.user32.MoveWindow(hwnd, x, y, w, h, True)`.
- **`type_text` fails on Win11 Notepad** — `RichEditD2DPT` Document control has empty name, empty automation_id, and no `set_edit_text()`. Fixed with `_find_editable_element()` (searches by control_type) and `iface_value.SetValue()` (UIA ValuePattern).
- **`type_text` overwrites existing Notepad content** — Added `_ensure_safe_notepad_tab()` that opens a new tab before writing if the current tab has user content.
- **Tools steal user focus** — Even "silent" methods like `SetValue` on `RichEditD2DPT` steal focus. Fixed with server-level try/finally focus guard and `preserve_focus()` context manager on every fallback path.
- **Audio dependencies not auto-installed** — Were in `[project.optional-dependencies]`, not installed by `pip install marlow-mcp`. Moved to main dependencies.
- **Transcription timeout on first use** — faster-whisper downloads ~150MB model on first call, exceeding 30s timeout. Increased to 300s and added `download_whisper_model` tool (600s timeout) for pre-downloading.

### Known Issues

- `list_windows` minimized detection uses `rect.left == -32000`, but some minimized windows report `width=0, height=0` instead
- Password field sanitizer regex is too aggressive — replaces the word "password" itself, not just the value
- `open_application` via `Start-Process 'Notepad'` may fail; direct `subprocess.Popen(['notepad.exe'])` works
- `desktop.windows(title_re=...)` fails if `window_title` contains `*` (regex special character from Notepad's modified indicator)

---

## [0.1.0] - 2026-02-27

Phase 1 complete. 14 MCP tools for Windows desktop automation with security-first design.
All tools tested on Windows 11 with MCP client integration verified.

### Added

#### Core Infrastructure
- **`marlow/server.py`** — MCP server using `mcp` SDK v1.26.0 with stdio transport. Registers tools, enforces safety pipeline on every call, sanitizes output.
- **`marlow/core/config.py`** — `MarlowConfig` with secure defaults. Config stored in `~/.marlow/config.json`.
- **`marlow/core/safety.py`** — `SafetyEngine`: kill switch (Ctrl+Shift+Escape), confirmation mode, blocked apps (banking, PayPal, password managers, authenticators), blocked destructive commands (format, del /f, rm -rf, shutdown, reg delete), rate limiter (30 actions/minute).
- **`marlow/core/sanitizer.py`** — `DataSanitizer`: redacts credit card numbers, SSN, emails, phone numbers, passwords before returning to caller. AES-256 encrypted audit logs.

#### Tools (14)
| Tool | Module | Description |
|------|--------|-------------|
| `get_ui_tree` | `tools/ui_tree.py` | Read Windows UI Automation Accessibility Tree (0 tokens, ~10-50ms) |
| `take_screenshot` | `tools/screenshot.py` | Screenshot full screen, window, or region (~1,500 tokens) |
| `click` | `tools/mouse.py` | Click by element name (silent `invoke()`) or coordinates (`pyautogui`) |
| `type_text` | `tools/keyboard.py` | Type text by element name (silent) or at cursor |
| `press_key` | `tools/keyboard.py` | Press individual keyboard key |
| `hotkey` | `tools/keyboard.py` | Execute keyboard shortcuts (Ctrl+C, Alt+F4, etc.) |
| `list_windows` | `tools/windows.py` | List open windows with titles, positions, sizes |
| `focus_window` | `tools/windows.py` | Bring window to foreground |
| `manage_window` | `tools/windows.py` | Minimize, maximize, restore, close, move, resize |
| `run_command` | `tools/system.py` | Execute PowerShell/CMD (destructive commands blocked) |
| `open_application` | `tools/system.py` | Open app by name or path |
| `clipboard` | `tools/system.py` | Read/write system clipboard |
| `system_info` | `tools/system.py` | OS, CPU, RAM, disk usage, top processes |
| `kill_switch` | `server.py` | Emergency stop: halt all automation |

#### Silent Methods (Background Mode)
- `invoke()` for clicking without moving mouse
- `set_edit_text()` for typing without keyboard simulation
- `select()` for list selection without mouse
- `toggle()` for checkboxes without click
- Automatic fallback to `click_input()` / `type_keys()` when silent methods fail

#### Tests
- `tests/test_config.py` — Secure defaults verification
- `tests/test_safety.py` — Blocked apps, blocked commands, rate limiter
- `tests/test_sanitizer.py` — Sensitive data redaction (credit cards, SSN, email, phone)
- 125 tests passing

#### MCP Client Integration
- MCP server starts via `python -m marlow.server`
- Compatible with any MCP client (desktop apps, IDEs, etc.)
- 14 tools visible and functional

### Fixed

- `disk_usage("/")` resolves to `C:\` on Windows (psutil compatibility)

---

## Roadmap

### Phase 3 (Planned)
- `visual_diff` — Compare screenshots to detect changes
- `memory` — Persistent memory across sessions
- `clipboard_history` — Track clipboard changes over time
- `scraper` — Web scraping with browser automation
- Extension system for community plugins

### Phase 4 (Planned)
- `watch_folder` — Monitor folder for file changes
- `schedule_task` — Schedule automation tasks

### Phase 5 (Planned)
- End-to-end testing with real workflows
- PyPI publish (`pip install marlow-mcp`)
- MCP Registry listing
- Extension packaging for MCP clients
