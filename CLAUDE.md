# CLAUDE.md — Instrucciones para Claude Code
# Proyecto: Marlow — Windows Desktop Automation MCP Server

## IDENTIDAD

**Marlow** v0.20.0 — "AI that works beside you, not instead of you"
Python 3.10+ (dev 3.14) | MIT | PyPI: marlow-mcp | MCP SDK v1.26.0

MCP Server para Windows: automatizacion de escritorio con seguridad desde commit #1, metodos silenciosos (no roba mouse/teclado), proteccion de foco, persistencia entre sesiones, extensiones sandboxed, cero telemetria.

Primer tarea autonoma de escritorio completada. Kernel con 4 LLM providers.

## VISION

**Principio rector:** Primero ver bien, despues entender, despues ser invisible.
**Capas:** UIA tree (estructura) → OCR con bboxes (texto) → CDP (Electron) → Computer Vision (ultimo recurso).
**Shadow Mode (futuro):** Virtual Desktops invisibles + SendMessage + PrintWindow + COM invisible.

## ESTADO (96 tools, 540 tests)

| Capa | Tools/Modulos | Estado |
|------|---------------|--------|
| Core (Phase 1) | 14 tools | COMPLETA |
| Advanced (Phase 2: audio/OCR/background/COM/voice) | 19 tools | COMPLETA |
| Intelligence + Extensions (Phase 3) | 12 tools | COMPLETA |
| Automation (Phase 4: watcher/scheduler) | 8 tools | COMPLETA |
| Voice + TTS (Phase 5) | 3 tools | COMPLETA |
| Adaptive + Workflows | 8 tools | COMPLETA |
| Self-Improve + Smart Wait | 6 tools | COMPLETA |
| UX + Diagnostics | 3 tools | COMPLETA |
| CDP (Chrome DevTools Protocol) | 15 tools | COMPLETA |
| UIA Events + Dialog Handler | 5 tools | COMPLETA |
| Cascade Recovery | 1 tool | COMPLETA |
| Set-of-Mark (SoM) Prompting | 2 tools | COMPLETA |
| Kernel Tiers 0-3, 6 | types, executor, kernel, goal_engine, scoring, security, planning, cognition | COMPLETA |
| Phase 1: Ver Mejor (Perception) | WindowTracker, DialogType, AppAwareness | COMPLETA |
| Phase 2: Escuchar y Hablar (Audio) | AdaptiveVAD, PiperTTS, GPUDetect | COMPLETA |

Plataforma: Windows 11 Home 10.0.26200, dual monitor
Laptop Training Node: Lenovo IdeaPad i5/16GB, Ollama qwen2.5:7b judge

## ESTRUCTURA

```
marlow/
├── server.py                  # MCP server (96 tools, focus guard, safety pipeline)
├── __init__.py                # Version
├── core/
│   ├── config.py              # Config con defaults seguros
│   ├── safety.py              # Kill switch, confirmacion, blocked apps/cmds, rate limiter
│   ├── sanitizer.py           # Redacta datos sensibles
│   ├── escalation.py          # Smart find: UIA→OCR→cascade→screenshot
│   ├── cascade_recovery.py    # 5-step fallback pipeline
│   ├── focus.py               # Save/restore foco (Win32 API)
│   ├── uia_utils.py           # find_window, find_element_enhanced (fuzzy Levenshtein)
│   ├── uia_events.py          # COM event handlers (window/focus/structure changes)
│   ├── dialog_handler.py      # Dialog detection/classification/auto-handling
│   ├── app_detector.py        # Framework detection via DLL analysis
│   ├── som.py                 # Set-of-Mark: numbered labels on screenshots
│   ├── cdp_manager.py         # CDP WebSocket connections (Electron/CEF)
│   ├── adaptive.py            # Pattern detection
│   ├── workflows.py           # Record/replay workflows
│   ├── error_journal.py       # Error/solution diary per tool+app
│   ├── vad.py                 # AdaptiveVAD: Silero VAD (primary) -> RMS (fallback)
│   ├── piper_tts.py           # Piper TTS offline — es_MX + en_US voices
│   ├── gpu_detect.py          # GPU detection, CUDA auto-config for whisper
│   ├── voice_hotkey.py        # Ctrl+Shift+M (record) + Ctrl+Shift+N (stop), uses AdaptiveVAD
│   ├── voice_overlay.py       # Floating tkinter overlay
│   └── setup_wizard.py        # First-run wizard + diagnostics
├── tools/
│   ├── ui_tree.py             # get_ui_tree (auto depth per framework)
│   ├── screenshot.py          # take_screenshot
│   ├── mouse.py               # click (silent invoke or coords)
│   ├── keyboard.py            # type_text, press_key, hotkey (Notepad Win11 safe)
│   ├── windows.py             # list_windows, focus_window, manage_window
│   ├── system.py              # run_command, open_application, clipboard, system_info
│   ├── ocr.py                 # Windows OCR (primary) + Tesseract (fallback)
│   ├── background.py          # Dual monitor / offscreen + agent_screen_only
│   ├── audio.py               # WASAPI loopback, mic, whisper transcription (GPU auto-detect)
│   ├── voice.py               # listen_for_command
│   ├── app_script.py          # COM automation sandboxed (invisible default)
│   ├── visual_diff.py         # Before/after pixel comparison
│   ├── memory.py              # Persistent key-value storage
│   ├── clipboard_ext.py       # Clipboard history daemon
│   ├── scraper.py             # httpx + BeautifulSoup
│   ├── watcher.py             # watchdog folder monitoring
│   ├── scheduler.py           # Recurring task scheduler
│   ├── tts.py                 # edge-tts (Jorge) -> Piper offline -> pyttsx3
│   └── wait.py                # wait_for_element/text/window/idle
├── kernel/
│   ├── types.py               # ToolResult, enums, base types
│   ├── config.py              # Kernel configuration
│   ├── constants.py           # Shared constants
│   ├── tool_wrapper.py        # Tool function wrapping
│   ├── kernel.py              # 8-state decision loop HSM
│   ├── executor.py            # SmartExecutor (async/sync + coroutine detection)
│   ├── integration.py         # AutonomousMarlow — orchestration, focus mgmt, dialog handling, window tracking, post-action verification
│   ├── window_tracker.py      # WindowSnapshot, WindowChange, WindowTracker — state between steps
│   ├── app_awareness.py       # AppAwareness — Electron vs native, CDP/UIA recommendation
│   ├── world_state.py         # World state representation
│   ├── loop_guard.py          # Infinite loop detection
│   ├── memory.py              # Kernel-level memory
│   ├── knowledge.py           # Knowledge base
│   ├── goal_engine.py         # 13-state goal lifecycle
│   ├── plan_validator.py      # Plan validation
│   ├── success_checker.py     # Success verification
│   ├── replan.py              # Re-planning on failure
│   ├── db/                    # SQLite: state.db (10 tables), logs.db (3 tables)
│   ├── scoring/               # ActionScorer — 4-dimension: execution, outcome, safety, efficiency
│   ├── security/              # 6 security layers
│   ├── planning/              # prompts.py, parser.py, template_planner.py, tool_filter.py
│   └── cognition/             # LLM providers (Anthropic, OpenAI, Gemini, Ollama) + LLMPlanner
├── extensions/                # Plugin system (manifest + sandbox)
└── tests/                     # 540 tests (unit + integration)
```

## HERRAMIENTAS MCP (96 tools)

**Core (14):** get_ui_tree, take_screenshot, click, type_text, press_key, hotkey, list_windows, focus_window, manage_window, run_command, open_application, clipboard, system_info, kill_switch

**Advanced (19):** ocr_region, list_ocr_languages, smart_find, find_elements, cascade_find, get_annotated_screenshot, som_click, detect_app_framework, setup_background_mode, move_to_agent_screen, move_to_user_screen, get_agent_screen_state, capture_system_audio, capture_mic_audio, transcribe_audio, download_whisper_model, listen_for_command, run_app_script, restore_user_focus

**Intelligence + Extensions (12):** visual_diff, visual_diff_compare, memory_save, memory_recall, memory_delete, memory_list, clipboard_history, scrape_url, extensions_list, extensions_install, extensions_uninstall, extensions_audit

**Automation (8):** watch_folder, unwatch_folder, get_watch_events, list_watchers, schedule_task, list_scheduled_tasks, remove_task, get_task_history

**Voice + TTS (3):** speak, speak_and_listen, get_voice_hotkey_status

**Adaptive + Workflows (8):** get_suggestions, accept_suggestion, dismiss_suggestion, workflow_record, workflow_stop, workflow_run, workflow_list, workflow_delete

**Self-Improve (2):** get_error_journal, clear_error_journal

**Smart Wait (4):** wait_for_element, wait_for_text, wait_for_window, wait_for_idle

**UX (2):** set_agent_screen_only, toggle_voice_overlay

**Monitor (5):** start_ui_monitor, stop_ui_monitor, get_ui_events, handle_dialog, get_dialog_info

**Diagnostics (1):** run_diagnostics

**CDP (15):** cdp_discover, cdp_connect, cdp_disconnect, cdp_list_connections, cdp_send, cdp_click, cdp_type_text, cdp_key_combo, cdp_screenshot, cdp_evaluate, cdp_get_dom, cdp_click_selector, cdp_ensure, cdp_restart_confirmed, cdp_get_knowledge_base

## ARQUITECTURA DEL KERNEL

### AutonomousMarlow (integration.py)
Orchestration layer que conecta GoalEngine + SmartExecutor + real Marlow tools.
- **Pre-action:** window snapshots, focus management (with dialog skip)
- **Execution:** SmartExecutor async dispatch with timeout
- **Post-action:** dialog detection (#32770 class), window change tracking, active window verification
- **Dialog classification:** DialogType enum (9 types: ERROR, WARNING, CONFIRMATION, SAVE, OPEN, FILE_EXISTS, PATH_ERROR, INFORMATION, UNKNOWN)
- **App awareness:** AppAwareness detects Electron vs native, recommends CDP/UIA
- **Window tracking:** WindowTracker records snapshots, detects appeared/disappeared/focus changes

### Kernel HSM (kernel.py)
8-state decision loop: IDLE → PLANNING → VALIDATING → CONFIRMING → EXECUTING → VERIFYING → REPLANNING → COMPLETE

### GoalEngine (goal_engine.py)
13-state goal lifecycle. Receives plans from TemplatePlanner (no LLM) or LLMPlanner (4 providers). Validates, confirms, executes step-by-step, verifies success, replans on failure.

### LLM Providers (cognition/)
4 providers: Anthropic, OpenAI, Gemini, Ollama. LLMPlanner generates plans from natural language goals. ToolFilter provides relevant tool subsets to reduce prompt size.

### ActionScorer (scoring/)
4-dimension scoring: execution (did it run?), outcome (did it work?), safety (any violations?), efficiency (time/resources). Weighted composite score.

### Security (security/)
6 layers: input validation, tool authorization, rate limiting, output sanitization, audit logging, kill switch.

## ARQUITECTURA CLAVE (MCP Server)

### Focus Guard (focus.py)
`server.py:call_tool()` wraps ALL tools in try/finally save/restore foco via `GetForegroundWindow()` + `SetForegroundWindow()` + `AttachThreadInput`. `focus_window` y `restore_user_focus` excluidos del auto-restore.

### Smart Escalation (escalation.py)
1. **UIA fuzzy search** — 0 tokens, ~10-50ms, Levenshtein on name/automation_id/help_text/class_name
2. **OCR** — 0 tokens, ~50-500ms, Windows OCR primary + Tesseract fallback
3. **Cascade recovery** — 5 steps: wait+retry, dialog check, wide fuzzy (0.4), OCR, screenshot
4. **Screenshot + LLM** — ~1,500 tokens (last resort)

Score thresholds: >0.8 use directly, 0.6-0.8 partial_matches for LLM, <0.6 escalate.

### Fuzzy Element Search (uia_utils.py)
`find_element_enhanced()` searches 4 properties with Levenshtein distance. Thresholds: name=0.7, automation_id/help_text/class_name=0.6. Early exit on exact match. Returns top N ranked candidates with score/bbox.

### Notepad Win11 (keyboard.py)
RichEditD2DPT class: `_find_editable_element()` by control_type, `SetValue()` via UIA ValuePattern, `_ensure_safe_notepad_tab()` opens new tab if content exists.

### CDP Manager (cdp_manager.py)
Singleton manages WebSocket connections to Electron/CEF CDP endpoints. Invisible input: dispatchMouseEvent, insertText, dispatchKeyEvent. Auto-restart requires user confirmation. Knowledge base at `~/.marlow/cdp_knowledge.json`. Default ports for 10 known apps.

### UIA Events (uia_events.py)
STA daemon thread with `CoInitialize()` + Win32 message pump. 3 COM handlers via comtypes: WindowOpened/Closed, FocusChanged, StructureChanged. Thread-safe event buffer (max 500), empty events filtered.

### Dialog Handler (dialog_handler.py)
Scans UIA tree for buttons/text, classifies: not_responding > error > save > update > confirmation > info. Actions: report/dismiss/auto. Filters by `#32770` class to avoid false positives.

### Set-of-Mark (som.py)
Walks UIA tree (depth 8), collects interactive elements with valid bboxes, draws numbered [1],[2],[3]... labels on screenshot using PIL alpha compositing. Returns annotated PNG + element map. `som_click(index)` clicks element center by number. Max 100 elements. Orange labels with semi-transparent background.

### Other Subsystems
- **Cascade Recovery:** 5-step pipeline, timeout 5-30s, `config.automation.cascade_recovery=True`
- **Error Journal:** failures/successes per tool+app, `get_best_method()`, max 500 entries
- **Agent Screen Only:** dual monitor redirect, block moves to user screen
- **App Framework Detector:** DLL analysis via psutil, 8 frameworks, cached per PID
- **Workflows:** record/replay tool sequences, safety check per step
- **Visual Diff:** PIL ImageChops pixel comparison, threshold 30, 5min expiry
- **Memory:** JSON in `~/.marlow/memory/`, categories: general/preferences/projects/tasks
- **Clipboard History:** daemon polls 1s, max 100 entries
- **Scraper:** httpx + BS4, localhost/private IPs blocked
- **Extensions:** pip packages with manifest, sandboxed permissions
- **Watcher:** watchdog observer, max 500 events, filterable
- **Scheduler:** daemon threads, interval min 10s, max 200 history, safety-checked
- **TTS:** edge-tts (Jorge) → Piper offline → pyttsx3 (SAPI5), MCI API playback, auto language detect
- **Voice Hotkey:** Ctrl+Shift+M record, AdaptiveVAD (Silero → RMS), max 30s
- **Smart Wait:** 4 polling tools, timeout 1-120s, interval 0.5-10s

## SEGURIDAD (NO NEGOCIABLE)

1. TODA accion pasa por SafetyEngine
2. Kill switch: Ctrl+Shift+Escape
3. Confirmacion "all" default; modo "block" bloquea todo
4. Apps bloqueadas: banking, PayPal, password managers, authenticators
5. Comandos bloqueados: format, del /f, rm -rf, shutdown, reg delete, etc.
6. Sanitizacion: credit cards, SSN, emails, passwords redactados
7. CERO telemetria — nada sale de la maquina
8. AES-256 logs, rate limiter 30/min, focus guard
9. Extensions sandboxed, scheduler safety-checked, app_script AST validation
10. clipboard stdin (no injection), registry regex, re.escape() en window_title

## ARQUITECTURA DE INPUT

| Tier | Metodo | Roba foco | Requiere visibilidad |
|------|--------|-----------|---------------------|
| 0 | CDP / COM invisible | No | No |
| 1 | UIA Patterns (invoke, SetValue) | No* | No |
| 2 | SendMessage/PostMessage (futuro) | No | No |
| 3 | click_input + focus restore | Si | Si |
| 4 | pyautogui | Si | Si |

## ARQUITECTURA DE VISION

| Tier | Metodo | Tokens | Velocidad |
|------|--------|--------|-----------|
| 0 | CDP DOM/screenshot | 0 | ~5ms |
| 1 | UIA tree + Windows OCR | 0 | ~10-200ms |
| 2 | mss/PrintWindow | 0 | ~50ms |
| 3 | Set-of-Mark + LLM | ~1,500 | ~1-3s |

## ARQUITECTURA DE AUDIO

| Componente | Implementado | Futuro |
|------------|-------------|--------|
| Activacion | Ctrl+Shift+M hotkey | Wake word "Hey Marlow" (OpenWakeWord) |
| ASR | faster-whisper (GPU auto-detect) | Moonshine v2 streaming |
| TTS | edge-tts (Jorge) → Piper offline → pyttsx3 | — |
| VAD | AdaptiveVAD: Silero neuronal → RMS fallback | TEN VAD |
| GPU | Auto-detect via gpu_detect.py | CUDA torch for RTX 4080 SUPER |

## DEVELOPMENT ROADMAP (Master Plan v7)

| Fase | Nombre | Estado |
|------|--------|--------|
| Phase 1 | Ver Mejor (Perception): WindowTracker, DialogType, AppAwareness | COMPLETA |
| Phase 2 | Escuchar y Hablar: Silero VAD, Piper TTS, GPU detect, voz Jorge | COMPLETA |
| Phase 3 | Reaccionar (Game AI-A: PreActionScorer, InterruptManager, AdaptiveWaits) | NEXT |
| Phase 4 | EventBus (janus, typed events, 6 priority levels) | PENDIENTE |
| Phase 5 | Planificar Mejor (GOAP, DesktopWeather, Selector nodes) | PENDIENTE |
| Phase 6 | Seguridad (OWASP defenses, sandboxing, dual LLM review) | PENDIENTE |
| Phase 7 | Aprendizaje (Blackboard, Shadow Mode, Training Node integration) | PENDIENTE |
| Phase 8 | AI Vision (OmniParser, VLM, CDP for Electron, Sensor Fusion) | PENDIENTE |
| Phase D | Training Node (running in parallel on Lenovo IdeaPad) | EN PROGRESO |

### Kernel Tiers Completados
- **Tier 0:** Types, config, constants, tool_wrapper
- **Tier 1:** SmartExecutor (async/sync dispatch, timeout, ToolResult)
- **Tier 2:** Kernel HSM (8-state decision loop)
- **Tier 3:** GoalEngine (13-state lifecycle), PlanValidator, SuccessChecker, Replan
- **Tier 6:** LLM Interface (4 providers: Anthropic, OpenAI, Gemini, Ollama)

## KEY DECISIONS

- **Discord:** CDP disabled (violates ToS)
- **Shadow Mode:** OFF by default (explicit activation)
- **Spotify:** Web API, not CDP (official public API)
- **GPU:** Auto-detect, transparent, graceful CPU fallback, optional deps only
- **TTS voice:** es-MX-JorgeNeural (Jorge, male Mexican Spanish)
- **Post-task:** Marlow does NOT close apps after completing tasks (Jose reviews first)

## CONVENTIONS

- Python 3.10+, type hints, docstrings EN con comentario ES
- async/await para tools MCP, blocking ops en `run_in_executor`
- Error handling retorna dicts, nunca crashes. Cada tool: `"success"` o `"error"` key
- `ctypes.windll.user32` para Win32 API directa
- UIAWrapper (backend "uia"): NO tiene `wrapper_object()`, `move_window()`, ni `set_edit_text()`
- Tests: `python -m pytest tests/ -x -q` (540 passing)
- Git: always remove Co-authored-by and "Claude" references
- Git user: jarb02@users.noreply.github.com
- Project path: `E:\Project Marlow\marlow-final`
- PowerShell for live tests

## BUGS RESUELTOS

1. `wrapper_object()` no existe en UIAWrapper — eliminado
2. MCP startup: `async with stdio_server()` (v1.26.0)
3. `move_window()` → `ctypes.windll.user32.MoveWindow()`
4. Notepad Win11: `_find_editable_element()` + `SetValue()`
5. Tab overwrite: `_ensure_safe_notepad_tab()`
6. Focus steal: try/finally guard + `preserve_focus()`
7. Audio deps: moved to main dependencies
8. Transcription timeout: 300s + `download_whisper_model`

## AUDITORIA v0.4.1

**21 fixes.** Criticos: AST validation app_script, clipboard stdin, scheduler kill switch, modo "block", lazy watchdog, re.escape() window_title. Importantes: thread-safe rate limiter, clipboard null safety, whole-word match, LPARAM type, get_running_loop(), shared uia_utils. Menores: unused imports, type hints, dynamic version, uuid4.

## ISSUES CONOCIDOS

- `list_windows`: minimized detection inconsistente
- Sanitizer password regex agresivo
- `open_application` via Start-Process puede fallar; Popen funciona
