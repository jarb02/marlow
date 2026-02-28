# Changelog

All notable changes to Marlow are documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.10.0] - 2026-02-28

First-use experience. 69 total MCP tools. Setup wizard auto-runs on first launch,
detects hardware and software, pre-downloads Whisper model. Simple installer script
for non-technical users. Diagnostics MCP tool for troubleshooting.

### Added

#### New Core Module (1)
- **`marlow/core/setup_wizard.py`** — First-use setup wizard with 8 steps: detect Python version, detect monitors (auto-setup background mode if 2+), detect microphone (sounddevice), detect Tesseract OCR, detect TTS engines (edge-tts + pyttsx3), pre-download Whisper base model (120s timeout), create default config, save setup marker. `is_first_run()` checks for `~/.marlow/setup_complete.json`. `run_diagnostics()` async MCP tool returns structured component status for troubleshooting.

#### New Root Script (1)
- **`install.py`** — Standalone installer for non-technical users. Bilingual EN/ES output. 4 steps: check Python >= 3.10, pip install in editable mode, run setup wizard via subprocess, detect and configure MCP clients (Claude Desktop, Cursor) by adding Marlow to their config files (never overwrites existing entries).

#### New Tools (1 total)
| Tool | Description |
|------|-------------|
| `run_diagnostics` | Run system diagnostics (Python, monitors, mic, OCR, TTS, Whisper, system info, safety) |

### Changed

- **`marlow/server.py`** — Import setup_wizard, call `run_setup_wizard()` on first launch in `main()`. Added 1 new Tool definition and dispatch entry for `run_diagnostics`. Total: 69 tools registered.
- **`marlow/tools/help.py`** — Added `run_diagnostics` to "Help" category in `_TOOLS_CATALOG`.
- **`marlow/__init__.py`** — Version bumped from `0.9.0` to `0.10.0`.

---

## [0.9.0] - 2026-02-28

UX improvements. 68 total MCP tools. Agent screen auto-redirect keeps
windows on the second monitor. Voice overlay floating window for real-time
voice control feedback with status indicator and mini-log.

### Added
- **Agent Screen Only mode** — `agent_screen_only` config setting (default: True)
  - `open_application()` auto-moves new windows to agent monitor
  - `manage_window(action="move")` redirects to agent screen when target is on user's monitor
  - `setup_background_mode()` auto-executes at server startup if 2+ monitors detected
  - New tool: `set_agent_screen_only(enabled)` — toggle the mode
- **Voice Overlay** — floating tkinter window (`marlow/core/voice_overlay.py`)
  - Status indicator: Idle (grey), Listening (red pulse), Processing (yellow), Ready (green)
  - Shows transcribed user text and Marlow responses
  - Last 5 lines mini-log, 300x200px, topmost, semi-transparent, bottom-right corner
  - Opens automatically on voice hotkey press
  - New tool: `toggle_voice_overlay(visible)` — show/hide manually
- **Ctrl+Shift+N** — manual stop recording hotkey (skip silence detection)
- Added `is_on_user_screen()`, `get_agent_move_coords()`, `is_background_mode_active()` helpers to background.py

### Changed
- Voice hotkey now registers both Ctrl+Shift+M (record) and Ctrl+Shift+N (stop)
- Voice pipeline updates overlay status at each stage (listening → processing → ready)
- `manage_window` dispatch wraps through `_manage_window_with_redirect` for agent screen checks

---

## [0.8.0] - 2026-02-28

Smart Wait tools. 66 total MCP tools. Intelligent polling-based wait functions
for UI elements, text on screen, window appearance, and screen idle detection.

### Added

#### New Tool Module (1)
- **`marlow/tools/wait.py`** — 4 tools for intelligent waiting with configurable timeout and interval. `wait_for_element` polls UI Automation tree via `find_element_by_name()` for a named element. `wait_for_text` polls OCR via `ocr_region()` for text on screen (case insensitive, returns position and context). `wait_for_window` polls for a window title to appear via pywinauto Desktop. `wait_for_idle` compares consecutive screenshots (downscaled 4x via mss+PIL) and declares idle when no change for `stable_seconds`. All tools clamp timeout to 1-120s.

#### New Tools (4 total)
| Tool | Description |
|------|-------------|
| `wait_for_element` | Wait for a UI element to appear (polls UIA tree) |
| `wait_for_text` | Wait for text on screen via OCR |
| `wait_for_window` | Wait for a window to appear by title |
| `wait_for_idle` | Wait for screen/window to stop changing (idle) |

### Changed

- **`marlow/server.py`** — Added 4 new Tool definitions and dispatch entries. Import for wait module. Total: 66 tools registered.
- **`marlow/tools/help.py`** — Added "Wait" category to `_TOOLS_CATALOG`.
- **`marlow/__init__.py`** — Version bumped from `0.7.0` to `0.8.0`.

---

## [0.7.0] - 2026-02-28

Self-Improve Level 1: Error Journal. 62 total MCP tools. Persistent diary of
method failures and successes per tool+app combination. When invoke(), SetValue(),
or UIA fails on a specific app, Marlow remembers and skips straight to the method
that works next time.

### Added

#### New Core Module (1)
- **`marlow/core/error_journal.py`** — `ErrorJournal` singleton maintains persistent diary in `~/.marlow/memory/error_journal.json`. Each entry: tool, app, method_failed, method_worked, error_message, params, timestamp, success_count, failure_count. Internal functions `record_failure()`, `record_success()`, `get_best_method()`, `get_known_issues()` used by tools. Max 500 entries with smart eviction (keeps high success_count entries). 2 MCP tools: `get_error_journal`, `clear_error_journal`.

#### New Tools (2 total)
| Tool | Description |
|------|-------------|
| `get_error_journal` | Show error journal (which methods fail/work per app) |
| `clear_error_journal` | Clear error journal entries for an app or all |

### Changed

- **`marlow/tools/mouse.py`** — `_click_by_name()` consults `get_best_method("click", window)` before invoke(). If journal says invoke fails, skips to click_input(). Records failure on invoke exception, records success on fallback.
- **`marlow/tools/keyboard.py`** — `_type_into_window()` and `_type_by_name()` consult `get_best_method("type_text", window)` before silent methods. If journal says SetValue fails, skips to type_keys(). Records failure/success.
- **`marlow/core/escalation.py`** — `smart_find()` consults `get_best_method("smart_find", window)` before UIA step. If journal says UIA fails on an app, starts directly at OCR. Records failure on UIA miss, records success on OCR hit.
- **`marlow/server.py`** — Added 2 new Tool definitions and dispatch entries. Import for error_journal. Total: 62 tools registered.
- **`marlow/tools/help.py`** — Added "Self-Improve" category to `_TOOLS_CATALOG`.
- **`marlow/__init__.py`** — Version bumped from `0.6.0` to `0.7.0`.

---

## [0.6.0] - 2026-02-28

Adaptive Behavior system. 60 total MCP tools. Pattern detection suggests
repeating action sequences. Workflow recording allows saving and replaying
named tool call sequences with safety checks at each step.

### Added

#### New Core Modules (2)
- **`marlow/core/adaptive.py`** — `PatternDetector` singleton analyzes tool call history with sliding window (length 2-10) to detect repeating subsequences (3+ occurrences). Persistent storage in `~/.marlow/memory/patterns.json`. Key params extracted: window_title, app_name, element_name, text, command. 3 MCP tools: `get_suggestions`, `accept_suggestion`, `dismiss_suggestion`.
- **`marlow/core/workflows.py`** — `WorkflowManager` singleton records, saves, and replays named tool call sequences. Storage in `~/.marlow/workflows/workflows.json`. Meta-tools (kill_switch, workflow_*, adaptive tools, help) excluded from recording. Replay checks kill switch + safety approval before each step, delays between steps (100ms-5s), stops on first failure. 5 MCP tools: `workflow_record`, `workflow_stop`, `workflow_run`, `workflow_list`, `workflow_delete`.

#### New Tools (8 total)
| Tool | Description |
|------|-------------|
| `get_suggestions` | Detect repeating action patterns and suggest them |
| `accept_suggestion` | Accept a pattern suggestion |
| `dismiss_suggestion` | Dismiss a pattern suggestion |
| `workflow_record` | Start recording a workflow |
| `workflow_stop` | Stop recording and save workflow |
| `workflow_run` | Replay a saved workflow with safety checks |
| `workflow_list` | List all saved workflows |
| `workflow_delete` | Delete a saved workflow |

### Changed

- **`marlow/server.py`** — Added 8 new Tool definitions and dispatch entries. Recording hook in `_call_tool_inner` feeds adaptive detector and workflow recorder. Total: 60 tools registered.
- **`marlow/core/safety.py`** — Added `workflow_run` to `sensitive_tools` set (requires confirmation).
- **`marlow/tools/help.py`** — Added "Adaptive" and "Workflow" categories to `_TOOLS_CATALOG`.

---

## [0.5.0] - 2026-02-28

Phase 5 complete. 52 total MCP tools. Voice control with TTS (edge-tts neural voices
with pyttsx3 offline fallback), voice hotkey for hands-free dictation,
speak-then-listen for conversational flows, and help/capabilities discovery.

### Added

#### New Tool Modules (2)
- **`marlow/tools/help.py`** — 2 tools: `get_capabilities` returns all 52 tools organized by 12 categories with bilingual descriptions (EN/ES) and parameter lists, optional category filter; `get_version` returns version, tool count, and live system state (kill switch, confirmation mode, background mode, voice hotkey). Pure data module — no external imports beyond `__version__`.

#### New Tool Modules (1) — TTS
- **`marlow/tools/tts.py`** — 2 tools: `speak` uses edge-tts as primary engine (Microsoft Edge neural voices: es-MX-DaliaNeural, es-MX-JorgeNeural, en-US-JennyNeural, en-US-GuyNeural, etc.) with pyttsx3 SAPI5 as offline fallback. Auto-detects Spanish/English via character analysis + common word matching. Audio playback via Windows MCI API (`ctypes.windll.winmm.mciSendStringW`) — plays MP3 natively with zero external deps. `speak_and_listen` combines TTS + `listen_for_command()` for conversational flows. Fresh pyttsx3 engine per call to avoid COM threading deadlocks.

#### New Core Module (1)
- **`marlow/core/voice_hotkey.py`** — Background voice hotkey (Ctrl+Shift+M). Saves foreground window HWND on hotkey press, records speech with chunk-based VAD (0.5s chunks, RMS threshold, stops after 2s silence post-speech, max 30s), transcribes via faster-whisper, then restores focus and types text into the saved window via UIA `SetValue()` with clipboard paste fallback. Kill switch checked before recording and between each chunk. Audio feedback via `winsound.Beep()`. 1 MCP tool: `get_voice_hotkey_status` returns hotkey state, recording status, last transcribed text.

#### New Tools (5 total)
| Tool | Description |
|------|-------------|
| `speak` | Text-to-speech with edge-tts neural voices (ES/EN auto-detect) |
| `speak_and_listen` | Speak text, then listen for voice response |
| `get_voice_hotkey_status` | Check voice hotkey status (active, recording, last text) |
| `get_capabilities` | List all tools by category with bilingual descriptions |
| `get_version` | Get version, tool count, and live system state |

#### Edge-TTS Voice Aliases
| Alias | Voice ID | Language |
|-------|----------|----------|
| dalia | es-MX-DaliaNeural | Spanish (Mexico) |
| jorge | es-MX-JorgeNeural | Spanish (Mexico) |
| elvira | es-ES-ElviraNeural | Spanish (Spain) |
| alvaro | es-ES-AlvaroNeural | Spanish (Spain) |
| jenny | en-US-JennyNeural | English (US) |
| guy | en-US-GuyNeural | English (US) |
| sonia | en-GB-SoniaNeural | English (UK) |
| ryan | en-GB-RyanNeural | English (UK) |

#### Integration Tests (`tests/test_integration.py`)
17 tests across 7 scenarios testing complete tool chains:
1. **Background Mode Flow** — setup → open Notepad → move to agent screen → type → move back → restore focus
2. **Audio Pipeline** — capture (system/mic) → verify WAV → TTS speak; transcription pipeline (skips if whisper model not cached)
3. **Kill Switch Stops Scheduler** — schedule_task → kill switch → verify 0 runs → verify skip entries
4. **Memory Persistence** — save → recall → verify → delete → verify gone; list keys
5. **Focus Under Stress** — 5 tool actions with focus save/restore after each
6. **Security Chain** — safe echo OK → format C: BLOCKED → scheduled del /f BLOCKED → app_script import/eval/dunder BLOCKED
7. **Watcher + Scheduler** — watch_folder → create file → verify event → schedule_task → verify history

All tests use try/finally cleanup and `asyncio.wait_for()` timeouts for audio operations.

### Changed

- **`pyproject.toml`** — Added `pyttsx3>=2.90` and `edge-tts>=6.1.0` to main dependencies.
- **`marlow/__init__.py`** — Version bumped from `0.4.1` to `0.5.0`.
- **`marlow/server.py`** — Added 5 new Tool definitions and dispatch entries. Voice hotkey startup in `main()` after kill switch. Total: 52 tools registered.
- **Total test count** — 142 tests (125 unit + 17 integration), all passing.

---

## [0.4.1] - 2026-02-27

Comprehensive code audit: 21 issues fixed across 6 critical, 7 important, and 8 minor
categories. Security hardening, code deduplication, and correctness improvements.
125 tests passing, 47 tools verified.

### Security Fixes (Critical)

- **`app_script.py`** — Replaced regex-based script validation with AST analysis (`_ScriptValidator`). Blocks `import`, `eval()`, `exec()`, `type().__subclasses__`, dunder attribute access, and forbidden module references. Sandbox uses `safe_builtins` whitelist instead of empty `__builtins__`.
- **`system.py` clipboard** — Fixed PowerShell injection: user text now piped via stdin (`$input | Set-Clipboard`) instead of f-string interpolation. Added early parameter validation.
- **`scheduler.py`** — Scheduled tasks now check kill switch before every execution. Added `set_kill_switch_check()` callback, wired from `server.py`.
- **`safety.py`** — Added `"block"` confirmation mode that rejects all actions unconditionally. Clarified `"all"` mode delegates confirmation to MCP client.
- **`watcher.py`** — watchdog imports changed from module-level to lazy loading via `_ensure_watchdog()`. Server no longer fails to start if watchdog is missing.
- **10+ files** — Added `re.escape(window_title)` before all `title_re=f".*{...}.*"` patterns to prevent regex injection.

### Important Fixes

- **`safety.py`** — Rate limiter is now thread-safe with `threading.Lock()` around timestamp reads/writes.
- **`system.py`** — `clipboard()` can no longer return `None`; all code paths return a dict.
- **`mouse.py`, `escalation.py`** — Changed element matching from substring to whole-word (prevents "File" matching "Profile").
- **`background.py`** — Fixed `ctypes.c_double` to `ctypes.wintypes.LPARAM` in `MONITORENUMPROC` callback.
- **`audio.py`, `app_script.py`** — Replaced deprecated `asyncio.get_event_loop()` with `get_running_loop()` (5 locations).
- **`mouse.py`, `keyboard.py`** — Removed redundant `preserve_focus()` calls from 7 functions (server.py already handles focus save/restore).
- **`registry.py`** — Added regex validation for pip package names before running `pip install`.

### Minor Improvements

- Removed unused imports from `voice.py`, `safety.py`, `sanitizer.py`, `server.py`, `ui_tree.py`.
- Added `object` type hints to 8+ functions across `ui_tree.py`, `screenshot.py`, `mouse.py`, `keyboard.py`.
- **`scraper.py`** — User-Agent now uses dynamic `__version__` instead of hardcoded `"0.3.0"`.
- **`visual_diff.py`** — Replaced `hashlib.md5()` ID generation with `uuid.uuid4().hex[:8]`.
- **`escalation.py`** — Removed stale `preserve_focus()` import from `_click_element`.

### Added

- **`marlow/core/uia_utils.py`** — New shared utility module with `find_window()` and `find_element_by_name()`. Replaces 10 duplicate window-finding patterns and 3 duplicate element search implementations across tool modules.

### Changed

- **`marlow/__init__.py`** — Version bumped from `0.4.0` to `0.4.1`.
- **10 tool modules** — Refactored to use `find_window()` from `uia_utils.py` instead of inline Desktop/windows pattern.
- **`mouse.py`** — `_find_element()` is now a thin alias for `uia_utils.find_element_by_name()` (backward compatible).

---

## [0.4.0] - 2026-02-27

Phase 4 complete. 47 total MCP tools. Folder monitoring with watchdog and
recurring task scheduler with safety-checked command execution.

### Added

#### New Tool Modules (2)
- **`marlow/tools/watcher.py`** — 4 tools: `watch_folder` starts monitoring a directory using watchdog Observer + `MarlowEventHandler`; `unwatch_folder` stops monitoring; `get_watch_events` retrieves detected events (filterable by watch_id, timestamp, limit); `list_watchers` shows all active watchers. Events: created, modified, deleted, moved. Max 500 events buffered.
- **`marlow/tools/scheduler.py`** — 4 tools: `schedule_task` creates a recurring command via `TaskRunner` daemon thread; `list_scheduled_tasks` shows all tasks with status/run counts; `remove_task` stops and removes a task; `get_task_history` shows execution history with stdout/stderr/exit_code. Min interval: 10s, execution timeout: 60s, max 200 history entries.

#### New Tools (8 total)
| Tool | Description |
|------|-------------|
| `watch_folder` | Start monitoring a folder for file changes |
| `unwatch_folder` | Stop monitoring a folder |
| `get_watch_events` | Get detected filesystem events |
| `list_watchers` | List all active folder watchers |
| `schedule_task` | Schedule a recurring command |
| `list_scheduled_tasks` | List scheduled tasks with status |
| `remove_task` | Remove a scheduled task |
| `get_task_history` | Get task execution history |

### Changed

- **`pyproject.toml`** — Added `watchdog>=4.0.0` to main dependencies.
- **`marlow/__init__.py`** — Version bumped from `0.3.0` to `0.4.0`.
- **`marlow/server.py`** — Added 8 new Tool definitions and dispatch entries. Total: 47 tools registered.
- **`marlow/core/safety.py`** — Added `schedule_task` and `watch_folder` to `sensitive_tools` set (require confirmation).

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

### Phase 6 (Planned)
- End-to-end testing with real workflows
- PyPI publish (`pip install marlow-mcp`)
- MCP Registry listing
- Extension packaging for MCP clients
