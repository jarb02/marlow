# CLAUDE.md — Instrucciones para Claude Code
# Proyecto: Marlow — Windows Desktop Automation MCP Server

## IDENTIDAD DEL PROYECTO

**Nombre:** Marlow
**Tagline:** "AI that works beside you, not instead of you"
**Version:** 0.14.0
**Licencia:** MIT
**Lenguaje:** Python 3.10+ (desarrollado en 3.14)
**PyPI package:** marlow-mcp
**MCP SDK:** v1.26.0

Marlow es un MCP Server para Windows que automatiza el escritorio con:
- Seguridad desde el commit #1 (kill switch, confirmacion, data sanitization)
- Metodos silenciosos para modo background (no roba mouse/teclado)
- Proteccion de foco — nunca roba la ventana activa del usuario
- Persistencia entre sesiones (memory system)
- Sistema de extensiones con permisos sandboxed
- Cero telemetria — los datos nunca salen de la maquina

## VISION DEL PROYECTO

Marlow va hacia un sistema de **vision por capas** y **Shadow Mode** (operar invisible).

**Principio rector:** Primero ver bien, despues entender, despues ser invisible.

**Capas de vision:** UIA tree (estructura) + OCR con bboxes (texto visual) + CDP (apps Electron) + Computer Vision (ultimo recurso). Cada capa agrega informacion que la anterior no puede capturar.

**Shadow Mode:** Operar en Virtual Desktops invisibles al usuario, usando metodos que no requieren foco ni visibilidad (SendMessage, PrintWindow, COM invisible). El usuario nunca ve que Marlow esta trabajando.

## ESTADO DEL PROYECTO

- **Phase 1: COMPLETA** — 14 tools core, 125 unit tests passing
- **Phase 2: COMPLETA** — 13 tools (audio/OCR/background/COM/voice/escalation)
- **Phase 3: COMPLETA** — 12 tools (visual_diff/memory/clipboard/scraper/extensions)
- **Phase 4: COMPLETA** — 8 tools (watcher/scheduler)
- **Phase 5: COMPLETA** — 3 tools (speak/speak_and_listen/voice hotkey) + edge-tts + voice hotkey background
- **Adaptive Behavior: COMPLETA** — 8 tools (pattern detection + workflow record/replay)
- **Self-Improve: COMPLETA** — 2 tools (error journal) + integracion en mouse/keyboard/escalation
- **Smart Wait: COMPLETA** — 4 tools (wait_for_element/text/window/idle)
- **UX: COMPLETA** — 2 tools (agent_screen_only + voice overlay) + auto-setup background + Ctrl+Shift+N
- **First-Use Experience: COMPLETA** — 1 tool (run_diagnostics) + setup wizard + install.py
- **Integration Tests:** 17 tests (7 scenarios) — tool chains completos
- **Total: 72 herramientas MCP registradas, 142 tests (125 unit + 17 integration)**
- **Plataforma probada:** Windows 11 Home 10.0.26200, dual monitor

## ESTRUCTURA DEL PROYECTO

```
marlow/
├── .github/
│   └── SECURITY.md               # Politica de reportes de vulnerabilidades
├── assets/
│   ├── logo.png                   # Mascota: fantasma amigable con monitores
│   └── banner.png                 # Banner para README con texto "MARLOW"
├── marlow/
│   ├── __init__.py                # Version 0.13.0
│   ├── server.py                  # Servidor MCP (72 tools, focus guard, safety pipeline)
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py              # Configuracion con defaults seguros
│   │   ├── safety.py              # Kill switch, confirmacion, blocked apps/cmds, rate limiter
│   │   ├── sanitizer.py           # Redacta tarjetas, SSN, passwords antes de enviar al AI
│   │   ├── escalation.py          # Smart find: UIA -> OCR -> screenshot (escalamiento)
│   │   ├── focus.py               # Guardar/restaurar foco del usuario (Win32 API)
│   │   ├── uia_utils.py           # Utilidades compartidas: find_window, find_element_by_name
│   │   ├── voice_hotkey.py        # Hotkey Ctrl+Shift+M (grabar) + Ctrl+Shift+N (parar) + overlay
│   │   ├── voice_overlay.py       # Ventana flotante tkinter para feedback de voice control
│   │   ├── adaptive.py            # PatternDetector: deteccion de patrones repetitivos + 3 tools
│   │   ├── workflows.py           # WorkflowManager: grabar, guardar, reproducir secuencias + 5 tools
│   │   ├── error_journal.py       # ErrorJournal: diario de errores/soluciones por tool+app + 2 tools
│   │   ├── setup_wizard.py        # Setup wizard (8 steps) + run_diagnostics MCP tool
│   │   └── app_detector.py       # Framework detection via DLL analysis + detect_app_framework tool
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── ui_tree.py             # get_ui_tree — Accessibility Tree (0 tokens)
│   │   ├── screenshot.py          # take_screenshot (~1,500 tokens, ultimo recurso)
│   │   ├── mouse.py               # click — por nombre (silent) o coordenadas
│   │   ├── keyboard.py            # type_text, press_key, hotkey — con proteccion Notepad
│   │   ├── windows.py             # list_windows, focus_window, manage_window
│   │   ├── system.py              # run_command, open_application, clipboard, system_info
│   │   ├── ocr.py                 # ocr_region — Windows OCR (primary) + Tesseract (fallback)
│   │   ├── background.py          # BackgroundManager — dual monitor / offscreen
│   │   ├── audio.py               # WASAPI loopback, mic capture, whisper transcription
│   │   ├── voice.py               # listen_for_command — mic + transcribe + silence detect
│   │   ├── app_script.py          # run_app_script — COM automation sandboxed
│   │   ├── visual_diff.py         # visual_diff — comparacion antes/despues
│   │   ├── memory.py              # memory_save/recall — persistencia entre sesiones
│   │   ├── clipboard_ext.py       # clipboard_history — historial de clipboard
│   │   ├── scraper.py             # scrape_url — web scraping (httpx + BeautifulSoup)
│   │   ├── watcher.py             # watch_folder — monitoreo de carpetas (watchdog)
│   │   ├── scheduler.py           # schedule_task — tareas programadas recurrentes
│   │   ├── tts.py                 # speak, speak_and_listen — TTS edge-tts + pyttsx3 fallback
│   │   └── wait.py                # wait_for_element/text/window/idle — esperas inteligentes con polling
│   └── extensions/
│       ├── __init__.py             # Extension loader y manifest validation
│       ├── registry.py             # Descubrimiento, instalacion, auditoria
│       └── sandbox.py              # Enforcement de permisos declarados
├── tests/
│   ├── test_config.py             # Verifica defaults seguros
│   ├── test_safety.py             # Blocked apps, commands, rate limiter
│   ├── test_sanitizer.py          # Datos sensibles redactados correctamente
│   └── test_integration.py        # 17 integration tests (7 scenarios, tool chains completos)
├── install.py                     # Instalador simple para usuarios no tecnicos
├── README.md                      # Bilingue EN/ES con tabla comparativa
├── CHANGELOG.md                   # Historial de cambios
├── LICENSE                        # MIT
└── pyproject.toml                 # Config para pip install / PyPI
```

## DEPENDENCIAS

```toml
# Principales (pip install marlow-mcp)
mcp[cli]>=1.0.0          # Framework MCP
pywinauto>=0.6.8         # UI Automation + Win32 API
pyautogui>=0.9.54        # Mouse/keyboard fallback
mss>=9.0.0               # Screenshots rapidos
Pillow>=10.0.0           # Procesamiento de imagenes
psutil>=5.9.0            # Info del sistema
keyboard>=0.13.5         # Kill switch global hotkey
pywin32>=306             # Windows API avanzada
cryptography>=41.0.0     # Encriptacion AES-256
PyAudioWPatch>=0.2.12.6  # WASAPI loopback (system audio)
sounddevice>=0.4.6       # Mic recording
soundfile>=0.12.1        # WAV file I/O
faster-whisper>=1.0.0    # Transcripcion CPU int8
httpx>=0.27.0            # HTTP client async (scraper)
beautifulsoup4>=4.12.0   # HTML parsing (scraper)
watchdog>=4.0.0          # File system event monitoring
pyttsx3>=2.90            # TTS offline fallback (SAPI5)
edge-tts>=6.1.0          # TTS primario (voces neurales Microsoft Edge)
winrt-runtime>=3.0.0     # Windows OCR runtime
winrt-Windows.Media.Ocr>=3.0.0  # Windows OCR API (primary OCR engine)
winrt-Windows.Graphics.Imaging>=3.0.0  # Image processing para OCR
winrt-Windows.Storage.Streams>=3.0.0   # Streams para bitmap loading
winrt-Windows.Globalization>=3.0.0     # Soporte de idiomas para OCR
winrt-Windows.Foundation>=3.0.0        # winrt base types
winrt-Windows.Foundation.Collections>=3.0.0  # winrt collections

# Opcionales
[project.optional-dependencies]
ocr = ["pytesseract>=0.3.10"]  # Tesseract fallback (requiere binary instalado)
```

## HERRAMIENTAS MCP (72 tools)

### Phase 1: Core (14 tools)
| Tool | Funcion | Modulo | Estado |
|------|---------|--------|--------|
| get_ui_tree | Lee Accessibility Tree de ventana (0 tokens, auto depth per framework) | tools/ui_tree.py | OK |
| take_screenshot | Screenshot pantalla/ventana/region | tools/screenshot.py | OK |
| click | Click por nombre (silent invoke) O coordenadas | tools/mouse.py | OK |
| type_text | Escribir texto con proteccion Notepad Win11 | tools/keyboard.py | OK |
| press_key | Presionar tecla individual | tools/keyboard.py | OK |
| hotkey | Atajos de teclado (Ctrl+C, etc.) | tools/keyboard.py | OK |
| list_windows | Lista ventanas abiertas | tools/windows.py | OK |
| focus_window | Traer ventana al frente | tools/windows.py | OK |
| manage_window | Mover, resize, min, max, close (ctypes MoveWindow) | tools/windows.py | OK |
| run_command | PowerShell/CMD (destructivos bloqueados) | tools/system.py | OK |
| open_application | Abrir app por nombre o ruta | tools/system.py | OK |
| clipboard | Leer/escribir portapapeles | tools/system.py | OK |
| system_info | CPU, RAM, disco, procesos | tools/system.py | OK |
| kill_switch | Detener TODA automatizacion | server.py | OK |

### Phase 2: Advanced (13 tools)
| Tool | Funcion | Modulo | Estado |
|------|---------|--------|--------|
| ocr_region | Extraer texto via Windows OCR (primary) + Tesseract (fallback) | tools/ocr.py | OK |
| list_ocr_languages | Listar idiomas OCR disponibles por motor | tools/ocr.py | OK |
| smart_find | Buscar UI: UIA fuzzy->OCR->screenshot (escalamiento) | core/escalation.py | OK |
| find_elements | Busqueda fuzzy multi-propiedad (top 5 candidatos rankeados) | core/escalation.py | OK |
| detect_app_framework | Detectar framework UI (Electron, WPF, WinUI, etc.) via DLLs | core/app_detector.py | OK |
| setup_background_mode | Configurar dual monitor / offscreen | tools/background.py | OK |
| move_to_agent_screen | Mover ventana al monitor del agente | tools/background.py | OK |
| move_to_user_screen | Devolver ventana al monitor del usuario | tools/background.py | OK |
| get_agent_screen_state | Listar ventanas en pantalla del agente | tools/background.py | OK |
| capture_system_audio | Grabar audio del sistema (WASAPI loopback) | tools/audio.py | OK |
| capture_mic_audio | Grabar audio del microfono | tools/audio.py | OK |
| transcribe_audio | Transcribir audio (faster-whisper CPU int8) | tools/audio.py | OK |
| download_whisper_model | Pre-descargar modelo whisper (evita timeout) | tools/audio.py | OK |
| listen_for_command | Escuchar comando de voz (mic+transcribe) | tools/voice.py | OK |
| run_app_script | COM automation sandboxed, invisible by default (Office/Adobe) | tools/app_script.py | OK |
| restore_user_focus | Restaurar foco manualmente si se pierde | core/focus.py | OK |

### Phase 3: Intelligence + Extensions (12 tools)
| Tool | Funcion | Modulo | Estado |
|------|---------|--------|--------|
| visual_diff | Capturar estado 'antes' para comparacion | tools/visual_diff.py | OK |
| visual_diff_compare | Comparar antes/despues, % de cambio | tools/visual_diff.py | OK |
| memory_save | Guardar valor persistente entre sesiones | tools/memory.py | OK |
| memory_recall | Recuperar memorias por key/categoria | tools/memory.py | OK |
| memory_delete | Eliminar una memoria especifica | tools/memory.py | OK |
| memory_list | Listar todas las memorias por categoria | tools/memory.py | OK |
| clipboard_history | Historial de clipboard (start/stop/list/search/clear) | tools/clipboard_ext.py | OK |
| scrape_url | Extraer contenido de URL (text/links/tables/html) | tools/scraper.py | OK |
| extensions_list | Listar extensiones instaladas | extensions/registry.py | OK |
| extensions_install | Instalar extension desde pip | extensions/registry.py | OK |
| extensions_uninstall | Desinstalar extension | extensions/registry.py | OK |
| extensions_audit | Auditar seguridad de una extension | extensions/registry.py | OK |

### Phase 4: Automation (8 tools)
| Tool | Funcion | Modulo | Estado |
|------|---------|--------|--------|
| watch_folder | Monitorear carpeta por cambios (watchdog) | tools/watcher.py | OK |
| unwatch_folder | Detener monitoreo de carpeta | tools/watcher.py | OK |
| get_watch_events | Obtener eventos del filesystem detectados | tools/watcher.py | OK |
| list_watchers | Listar watchers activos | tools/watcher.py | OK |
| schedule_task | Programar tarea recurrente (intervalo) | tools/scheduler.py | OK |
| list_scheduled_tasks | Listar tareas programadas | tools/scheduler.py | OK |
| remove_task | Eliminar tarea programada | tools/scheduler.py | OK |
| get_task_history | Historial de ejecuciones de tareas | tools/scheduler.py | OK |

### Phase 5: Voice Control + TTS (3 tools + background hotkey)
| Tool | Funcion | Modulo | Estado |
|------|---------|--------|--------|
| speak | TTS con edge-tts (neural) + pyttsx3 fallback (offline) | tools/tts.py | OK |
| speak_and_listen | Hablar + escuchar respuesta de voz | tools/tts.py | OK |
| get_voice_hotkey_status | Estado del hotkey Ctrl+Shift+M | core/voice_hotkey.py | OK |
| *(background)* voice_hotkey | Ctrl+Shift+M: graba, transcribe, escribe en MCP client | core/voice_hotkey.py | OK |

### Adaptive Behavior (3 tools)
| Tool | Funcion | Modulo | Estado |
|------|---------|--------|--------|
| get_suggestions | Detectar patrones repetitivos y sugerirlos | core/adaptive.py | OK |
| accept_suggestion | Aceptar sugerencia de patron | core/adaptive.py | OK |
| dismiss_suggestion | Descartar sugerencia de patron | core/adaptive.py | OK |

### Workflows (5 tools)
| Tool | Funcion | Modulo | Estado |
|------|---------|--------|--------|
| workflow_record | Comenzar a grabar un workflow | core/workflows.py | OK |
| workflow_stop | Detener grabacion y guardar workflow | core/workflows.py | OK |
| workflow_run | Reproducir workflow con safety checks | core/workflows.py | OK |
| workflow_list | Listar todos los workflows guardados | core/workflows.py | OK |
| workflow_delete | Eliminar un workflow guardado | core/workflows.py | OK |

### Self-Improve (2 tools)
| Tool | Funcion | Modulo | Estado |
|------|---------|--------|--------|
| get_error_journal | Mostrar diario de errores por app | core/error_journal.py | OK |
| clear_error_journal | Limpiar entradas del diario de errores | core/error_journal.py | OK |

### Smart Wait (4 tools)
| Tool | Funcion | Modulo | Estado |
|------|---------|--------|--------|
| wait_for_element | Esperar a que un elemento UI aparezca (polling UIA) | tools/wait.py | OK |
| wait_for_text | Esperar a que texto aparezca en pantalla (polling OCR) | tools/wait.py | OK |
| wait_for_window | Esperar a que una ventana aparezca | tools/wait.py | OK |
| wait_for_idle | Esperar a que pantalla/ventana deje de cambiar | tools/wait.py | OK |

### UX (2 tools)
| Tool | Funcion | Modulo | Estado |
|------|---------|--------|--------|
| set_agent_screen_only | Activar/desactivar auto-redirect al monitor del agente | tools/background.py | OK |
| toggle_voice_overlay | Mostrar/ocultar ventana flotante de voice control | core/voice_overlay.py | OK |

### Diagnostics (1 tool)
| Tool | Funcion | Modulo | Estado |
|------|---------|--------|--------|
| run_diagnostics | Diagnosticos del sistema para troubleshooting | core/setup_wizard.py | OK |

## ARQUITECTURA CLAVE

### Focus Guard (core/focus.py)
Marlow NUNCA roba el foco de la ventana activa del usuario:
- `server.py:call_tool()` usa try/finally para save/restore foco en CADA tool call
- `save_user_focus()` — guarda HWND via `GetForegroundWindow()`
- `restore_user_focus()` — restaura via `SetForegroundWindow()` + `AttachThreadInput` trick
- `preserve_focus()` — context manager para calls individuales que roban foco
- `focus_window` y `restore_user_focus` estan excluidos del auto-restore

### Notepad Win11 Protection (keyboard.py)
El nuevo Notepad de Windows 11 (tabulado, clase `RichEditD2DPT`) necesita manejo especial:
- `_find_editable_element()` busca por control_type (Document, Edit) en vez de nombre
- `_set_text_silent()` usa `iface_value.SetValue()` (UIA ValuePattern)
- `_ensure_safe_notepad_tab()` abre tab nuevo si el actual tiene contenido
- `_is_win11_notepad()` verifica class_name=="Notepad" + control RichEditD2DPT

### Smart Escalation (core/escalation.py)
1. **UI Automation (fuzzy search)** — 0 tokens, ~10-50ms, Levenshtein multi-property matching
2. **OCR (Windows OCR / Tesseract)** — 0 tokens, ~50-500ms
3. **Screenshot + LLM Vision** — ~1,500 tokens (ultimo recurso)

### Fuzzy Element Search (core/uia_utils.py)
- `find_element_enhanced(parent, query, control_type, max_depth, max_results)` — busqueda principal
- Busca en 4 propiedades: name → automation_id → help_text → class_name
- Levenshtein distance normalizada (~20 lineas, zero deps): `_levenshtein()`, `_similarity()`
- Thresholds por propiedad: name=0.7, automation_id=0.6, help_text=0.6, class_name=0.6
- Scoring: exact=1.0, whole-word=0.95, starts-with=0.9, fuzzy=variable
- Early exit en match perfecto (score 1.0) — no sigue buscando
- Retorna top N candidatos rankeados: `[{element, property_matched, score, name, automation_id, control_type, bbox}]`
- `find_element_by_name()` es wrapper de compatibilidad (retorna primer resultado)
- `find_elements()` MCP tool expone la busqueda al LLM con top 5 candidatos
- smart_find score >0.8 usa directamente, 0.6-0.8 incluye partial_matches para LLM

### Visual Diff (tools/visual_diff.py)
- `visual_diff()` captura estado 'antes', retorna diff_id
- `visual_diff_compare(diff_id)` captura 'despues', compara pixel a pixel
- Usa PIL ImageChops.difference(), threshold de 30 para ruido
- Estados expiran despues de 5 minutos (auto-cleanup)
- Retorna change_percent, changed_region (bounding box), changed flag

### Memory System (tools/memory.py)
- Almacenamiento JSON en `~/.marlow/memory/`
- Categorias: general, preferences, projects, tasks
- Cada categoria es un archivo separado (general.json, etc.)
- Operaciones: save, recall (por key, categoria, o ambos), delete, list

### Clipboard History (tools/clipboard_ext.py)
- Thread daemon monitorea clipboard cada 1 segundo via Win32 API
- Historial max 100 entradas, truncadas a 500 chars cada una
- Operaciones: start, stop, list, search, clear

### Web Scraper (tools/scraper.py)
- httpx async client + BeautifulSoup parser
- Formatos: text (5KB max), links (100 max), tables, html (10KB max)
- Seguridad: localhost/private IPs bloqueados, timeout 30s, max 5 redirects
- User-Agent honesto: "Marlow/{version} (Desktop Automation Tool)" — usa __version__ dinamico

### Extension System (extensions/)
- Extensiones son paquetes pip con `marlow_extension.json` manifest
- Manifest declara permisos: com_automation, file_system, network, shell_commands
- `ExtensionSandbox` verifica acciones contra permisos declarados
- Registry descubre, instala, desinstala, audita extensiones
- Datos en `~/.marlow/extensions/installed.json`

### Folder Watcher (tools/watcher.py)
- watchdog Observer (lazy import) + factory handler en threads daemon
- Eventos: created, modified, deleted, moved (configurable)
- Buffer de hasta 500 eventos, filtrable por watch_id y timestamp
- Watchers se pueden detener individualmente via unwatch_folder

### Task Scheduler (tools/scheduler.py)
- TaskRunner threads daemon ejecutan comandos a intervalos regulares
- PowerShell o CMD, timeout 60s por ejecucion, max_runs configurable
- Intervalo minimo: 10 segundos (anti-abuse)
- Historial de hasta 200 ejecuciones con stdout/stderr/exit_code
- Comandos pasan por SafetyEngine (destructivos bloqueados)

### TTS — Text-to-Speech (tools/tts.py)
- **Motor primario: edge-tts** — voces neurales de Microsoft Edge, alta calidad, async
  - Voces ES: `es-MX-DaliaNeural` (default), `es-MX-JorgeNeural`, `es-ES-ElviraNeural`, `es-ES-AlvaroNeural`
  - Voces EN: `en-US-JennyNeural` (default), `en-US-GuyNeural`, `en-GB-SoniaNeural`, `en-GB-RyanNeural`
  - Aliases amigables: "dalia", "jorge", "elvira", "jenny", "guy", "sonia"
- **Fallback: pyttsx3** — SAPI5 offline cuando edge-tts falla (sin internet)
- **Playback: Windows MCI API** — `ctypes.windll.winmm.mciSendStringW()` reproduce MP3 nativo, cero deps externas
- **Deteccion de idioma:** caracteres especiales (ñ, ¿, ¡, acentos) + palabras comunes espanol
- `speak()` — habla texto, auto-detecta idioma, edge-tts -> pyttsx3 fallback
- `speak_and_listen()` — habla + luego escucha via `listen_for_command()`
- Engine pyttsx3 se crea fresco por llamada (evita deadlocks COM en executor threads)

### Agent Screen Only (config.py + server.py + background.py)
- `agent_screen_only: bool = True` en `AutomationConfig` — default activado
- `setup_background_mode()` se ejecuta automaticamente en `main()` si hay 2+ monitores
- `open_application()` post-hook: espera ~3s, busca ventana por nombre, llama `move_to_agent_screen()`
- `manage_window(action="move")` pasa por `_manage_window_with_redirect()` que verifica `is_on_user_screen(x,y)`
- Si destino esta en monitor del usuario y `agent_screen_only=True`, redirige a `get_agent_move_coords()`
- `set_agent_screen_only(enabled)` actualiza config y persiste a disco
- Helpers en background.py: `is_on_user_screen()`, `get_agent_move_coords()`, `is_background_mode_active()`

### Voice Hotkey (core/voice_hotkey.py)
- **Ctrl+Shift+M** inicia grabacion, **Ctrl+Shift+N** para manualmente (flag `_manual_stop`)
- Al presionar M: guarda HWND, abre overlay, inicia grabacion en daemon thread
- **Grabacion chunk-based VAD:** chunks de 0.5s, RMS threshold 500, para despues de 2s silencio post-voz o manual stop
- **Pipeline:** beep -> overlay(listening) -> grabar -> overlay(processing) -> transcribir -> overlay(user text) -> escribir -> overlay(ready)
- Restaura foco via `AttachThreadInput` + `SetForegroundWindow` (misma tecnica que focus.py)
- Escribe en ventana guardada via UIA `SetValue()`, fallback clipboard paste via PowerShell stdin
- Kill switch y manual stop se verifican entre cada chunk de grabacion
- Max 30 segundos de grabacion

### Voice Overlay (core/voice_overlay.py)
- Ventana flotante tkinter corriendo en daemon thread separado (`VoiceOverlay._run_tk()`)
- Comunicacion thread-safe via `queue.Queue` + `root.after(100ms)` polling
- **Topmost + semi-transparente** (alpha 0.85) + sin barra de titulo (`overrideredirect`)
- **300x200px**, esquina inferior derecha del monitor del usuario
- Estado visual: circulo de color (idle=gris, listening=rojo pulsante, processing=amarillo, ready=verde)
- Mini-log scrolleable con ultimas 5 lineas de conversacion
- **Escape** cierra overlay (keybinding en la ventana)
- Se abre automaticamente cuando se presiona Ctrl+Shift+M
- API publica: `show_overlay()`, `hide_overlay()`, `update_status()`, `update_text()`

### Adaptive Behavior (core/adaptive.py)
- `PatternDetector` singleton (`_detector`) analiza historial de tool calls
- Buffer rolling de hasta 500 acciones con timestamp
- Extrae solo params clave: `window_title`, `app_name`, `element_name`, `text`, `command`
- Sliding window de longitud 2-10 busca subsecuencias repetidas 3+ veces
- Patrones persistidos en `~/.marlow/memory/patterns.json`
- Cada patron: `{id, sequence, frequency, first_seen, last_seen, dismissed, accepted}`
- `get_suggestions()` analiza y retorna solo patrones no descartados
- `accept_suggestion(id)` / `dismiss_suggestion(id)` actualizan estado

### Workflow System (core/workflows.py)
- `WorkflowManager` singleton (`_manager`) graba y reproduce secuencias de tool calls
- Storage en `~/.marlow/workflows/workflows.json`
- Meta-tools excluidos de grabacion: kill_switch, workflow_*, adaptive tools, help tools
- `workflow_record(name)` inicia grabacion; `workflow_stop()` guarda
- `record_step()` captura tool, params, delay_ms entre pasos (solo exitosos)
- `workflow_run(name)` reproduce: verifica kill switch + `safety.approve_action()` por paso
- Delay entre pasos clamped: min 100ms, max 5s
- Se detiene en primer fallo, retorna resultados parciales

### Error Journal — Self-Improve (core/error_journal.py)
- `ErrorJournal` singleton (`_journal`) mantiene diario de fallos/exitos por tool+app
- Storage en `~/.marlow/memory/error_journal.json`
- Cada entrada: `{tool, app, window, method_failed, method_worked, error_message, params, timestamp, success_count, failure_count}`
- `record_failure(tool, window, method, error)` — registra fallo, deduplica por tool+app+method
- `record_success(tool, window, method)` — vincula metodo exitoso a la falla mas reciente
- `get_best_method(tool, window)` — retorna el metodo con mayor success_count para tool+app
- `get_known_issues(window)` — lista problemas conocidos, filtrable por app
- Normaliza window title a app name: `"Document - Notepad"` → `"notepad"`
- Max 500 entradas, eviccion inteligente: mantiene alto success_count, elimina viejas de bajo valor
- **Integracion:** mouse.py (`click`), keyboard.py (`type_text`), escalation.py (`smart_find`)

### Smart Wait (tools/wait.py)
- 4 herramientas de espera inteligente con polling configurable
- `wait_for_element(name, window_title, timeout, interval)` — polls `find_element_by_name()` del UIA tree
- `wait_for_text(text, window_title, timeout, interval)` — polls `ocr_region()`, case insensitive, busca en words + full text
- `wait_for_window(title, timeout, interval)` — polls `Desktop.windows(title_re=...)` con `re.escape()`
- `wait_for_idle(window_title, timeout, stable_seconds)` — compara screenshots consecutivos (mss + PIL downscale 4x NEAREST)
- `_capture_frame()` helper asincrono con `run_in_executor` para captura blocking
- Timeout clamped: 1-120s; interval clamped: 0.5-10s; stable_seconds clamped: 1-10s
- Retorna info detallada: elapsed_seconds, checks count, element/window info con posicion

### App Framework Detector (core/app_detector.py)
- `detect_framework(pid)` — analiza DLLs cargadas via `psutil.Process.memory_maps()`
- Detecta 8 frameworks: electron, cef, chromium, edge_webview2, winui3, uwp, wpf, winforms, win32
- Rules-based matching: markers DLL → framework, con fallback a exe path y cmdline
- Cache por PID (`_cache: dict[int, dict]`) para no re-escanear procesos conocidos
- `is_electron(pid)` — shortcut retorna True si Electron o CEF
- `detect_all_windows()` — escanea todas las ventanas, retorna framework de cada una
- `detect_app_framework(window_title)` — MCP tool, una ventana o todas
- `get_framework_hint(pid)` — retorna hint para smart_find si app es Electron/CEF
- Integrado en escalation.py: `_get_framework_hint()` agrega nota cuando UIA+OCR fallan en Electron

### Adaptive UIA Tree Depth (tools/ui_tree.py)
- `get_ui_tree()` default `max_depth="auto"` — resuelve profundidad por framework via `app_detector`
- `_DEPTH_MAP`: winui3/uwp/win32/wpf=15, winforms=12, chromium/edge_webview2=8, electron/cef=5
- `_DEPTH_DEFAULT = 10` para frameworks desconocidos
- `_resolve_depth(pid)` → `(depth, reason, framework)` — detecta framework y retorna profundidad optima
- User override: `max_depth=N` fuerza profundidad, aun detecta framework para metadata
- Resultado incluye: `depth_used`, `depth_reason`, `window.framework`

### COM Invisible Mode (tools/app_script.py)
- `run_app_script()` parametro `visible: bool = False` — instancias nuevas corren invisible
- `GetActiveObject` (instancias existentes): respeta visibilidad actual, no modifica
- `Dispatch` (instancias nuevas): `app.Visible = visible` — False por default
- `visible=True` para mostrar la ventana (override)
- No afecta Outlook/Photoshop que tipicamente requieren UI visible

### Setup Wizard (core/setup_wizard.py)
- `SETUP_FILE = ~/.marlow/setup_complete.json` — marker file
- `is_first_run()` — `not SETUP_FILE.exists()`
- `run_setup_wizard()` — synchronous, called from `main()` before event loop
- 8 steps: Python, monitors, microphone, OCR engines, TTS, Whisper download, config, summary
- Each step: `{"status": "ok"|"warning"|"skip", "detail": "..."}`
- Never raises — catches all errors per step
- Saves results + timestamp to `setup_complete.json`
- `run_diagnostics()` — async MCP tool, same checks + system info + safety status

### Installer Script (install.py)
- Standalone root-level script for non-technical users
- Bilingual EN/ES output via `p(en, es)` and `header(en, es)` helpers
- 4 steps: check Python >= 3.10, pip install -e ., run setup wizard, detect MCP clients
- MCP client detection: searches `%APPDATA%` for known config files (Claude Desktop, Cursor)
- Adds `{"marlow": {"command": "marlow"}}` to `mcpServers` — never overwrites existing entries

### Integration Tests (tests/test_integration.py)
17 tests across 7 scenarios testing complete tool chains (not isolated functions):
1. **Background Mode Flow** — setup → open Notepad → move to agent screen → type → verify on agent screen → restore focus → cleanup
2. **Audio Pipeline** — capture audio (system/mic) → verify WAV → speak TTS; separate transcription test (whisper, skips if model not cached)
3. **Kill Switch Stops Scheduler** — schedule_task → activate kill switch → verify 0 successful runs → verify skip entries
4. **Memory Persistence** — save → recall → verify value → delete → verify gone; also list keys
5. **Focus Under Stress** — 5 consecutive tool actions (get_ui_tree, screenshot, click, type_text, clipboard) with focus save/restore after each
6. **Security Chain** — safe echo OK → format C: BLOCKED → scheduled del /f BLOCKED → app_script import/eval/dunder BLOCKED → full chain test
7. **Watcher + Scheduler** — watch_folder → create file → verify event → schedule_task → verify history → combined chain

All tests have try/finally cleanup. Audio tests use `asyncio.wait_for()` timeouts (15s capture, 30s TTS, 90s transcription) to prevent hangs.

## PRINCIPIOS DE SEGURIDAD (NO NEGOCIABLES)

1. **TODA accion pasa por SafetyEngine** antes de ejecutarse
2. **Kill switch** (Ctrl+Shift+Escape) desde Phase 1
3. **Modo confirmacion = "all"** por default; modo "block" bloquea todo sin excepcion
4. **Apps bloqueadas:** banking, PayPal, password managers, authenticators
5. **Comandos bloqueados:** format, del /f, rm -rf, shutdown, reg delete, etc.
6. **Sanitizacion automatica:** credit cards, SSN, emails, passwords redactados
7. **CERO telemetria:** Nunca. Jamas. Nada sale de la maquina.
8. **Logs encriptados:** AES-256 para audit trail
9. **Rate limiter:** Maximo 30 acciones/minuto, thread-safe con Lock
10. **Focus guard:** NUNCA robar foco de la ventana activa del usuario
11. **Scraper:** URLs internas/privadas bloqueadas, response size limitado
12. **Extensions:** Permisos declarados en manifest, sandboxed por ExtensionSandbox
13. **Scheduler:** schedule_task y watch_folder son sensitive tools (requieren confirmacion)
14. **Scheduler:** Comandos programados pasan por blocked commands check + kill switch check
15. **app_script:** Validacion AST (no regex) bloquea imports, eval, exec, dunder access
16. **clipboard:** Input via stdin (no f-string interpolation en PowerShell)
17. **registry:** Validacion regex de nombres de paquetes antes de pip install
18. **window_title:** re.escape() en todas las busquedas por titulo (10+ ubicaciones)

## ESTILO DE CODIGO

- Python 3.10+, type hints, docstrings en ingles con comentario en espanol (`/ Comentario`)
- async/await para todas las herramientas MCP
- Blocking ops en `loop.run_in_executor(None, func)` para no bloquear event loop
- Error handling que devuelve dicts descriptivos, nunca crashes
- Cada tool retorna dict con "success" o "error" key
- `ctypes.windll.user32` para Win32 API directa (MoveWindow, SetForegroundWindow, etc.)
- UIAWrapper del backend "uia" — NO tiene `move_window()` ni `set_edit_text()` nativos

## BUGS CRITICOS RESUELTOS

1. `wrapper_object()` no existe en UIAWrapper — eliminado en ui_tree/mouse/keyboard
2. MCP Server startup (mcp v1.26.0) — async with stdio_server()
3. UIAWrapper no tiene `move_window()` — ctypes.windll.user32.MoveWindow
4. type_text falla en Notepad Win11 — _find_editable_element + SetValue
5. type_text sobrescribe contenido — _ensure_safe_notepad_tab
6. Herramientas roban foco — focus guard try/finally + preserve_focus()
7. Audio deps no se instalaban — movidas a main deps
8. Transcripcion timeout — 300s timeout + download_whisper_model tool

## AUDITORIA v0.4.1 — 21 problemas resueltos

### Criticos (6)
1. Sandbox bypass en app_script.py — AST-based validation reemplaza regex
2. PowerShell injection en clipboard — input via stdin, no f-string
3. Scheduled tasks ignoran kill switch — callback check antes de cada ejecucion
4. Modo "block" agregado a safety.py — rechaza todas las acciones
5. watchdog import lazy — _ensure_watchdog() con try/except
6. Regex injection via window_title — re.escape() en 10+ ubicaciones

### Importantes (7)
7. Rate limiter thread-safe — threading.Lock()
8. clipboard() podia retornar None — todos los paths retornan dict
9. Substring matching causa falsos positivos — whole-word match
10. ctypes.c_double incorrecto para LPARAM — ctypes.wintypes.LPARAM
11. asyncio.get_event_loop() deprecated — get_running_loop()
12. preserve_focus() redundante — removido de tools (server.py ya lo maneja)
13. registry.py acepta strings arbitrarios — regex validation de package names

### Menores (8)
14. Imports no usados removidos (voice.py, safety.py, sanitizer.py, server.py)
15. Type hints agregados a funciones con params object (8+ funciones)
16. Version hardcodeada en scraper.py — usa __version__ dinamico
17. Window-finding extraido a core/uia_utils.py (10 call sites actualizados)
18. Element search compartido via find_element_by_name() en uia_utils.py
19. MD5 reemplazado con uuid4 en visual_diff.py
20. preserve_focus() removido de escalation.py _click_element
21. (15 verificado: Optional ya importado en keyboard.py — no era issue)

## ISSUES MENORES CONOCIDOS

- `list_windows`: minimized detection inconsistente (rect.left==-32000 vs width=0)
- Sanitizer regex para passwords es agresivo
- `open_application` via Start-Process puede fallar; subprocess.Popen funciona

## ARQUITECTURA DE INPUT (tiers de interaccion)

Orden de preferencia para interactuar con aplicaciones. Siempre intentar el tier mas bajo primero.

| Tier | Metodo | Roba foco | Requiere visibilidad | Notas |
|------|--------|-----------|---------------------|-------|
| 0 | CDP (Electron) / COM invisible (Office) | No | No | Ideal: API directa a la app |
| 1 | UIA Patterns (invoke, SetValue) | No* | No | *SetValue roba foco en algunos controles |
| 2 | SendMessage/PostMessage | No | No | Futuro: mensajes Win32 directos al HWND |
| 3 | click_input + focus restore | Si | Si | Actual fallback con focus guard |
| 4 | pyautogui | Si | Si | Ultimo recurso: simula hardware |

## ARQUITECTURA DE VISION (tiers de percepcion)

Orden de preferencia para "ver" lo que hay en pantalla.

| Tier | Metodo | Tokens | Velocidad | Notas |
|------|--------|--------|-----------|-------|
| 0 | CDP DOM/screenshot (Electron) | 0 | ~5ms | Futuro: acceso directo al DOM |
| 1 | UIA tree + Windows OCR con bboxes | 0 | ~10-200ms | Estructura + texto visual con posiciones |
| 2 | mss/PrintWindow screenshots | 0 | ~50ms | Captura sin foco (PrintWindow futuro) |
| 3 | Set-of-Mark + LLM | ~1,500 | ~1-3s | Futuro: elementos numerados + vision LLM |

## KEY DECISIONS

- **Discord:** CDP deshabilitado por default (viola ToS de Discord)
- **Shadow Mode:** OFF por default (requiere activacion explicita del usuario)
- **pyvda:** Para Virtual Desktops, con fallback silencioso si no esta instalado
- **Spotify:** Usar Web API, no CDP (Spotify tiene API oficial publica)

## PRINCIPIO DE GPU

**Auto-detect, transparente, graceful fallback.**

- Sin GPU todo funciona bien. Con GPU todo funciona mejor.
- Dependencias GPU son siempre opcionales (`[project.optional-dependencies]`).
- Auto-deteccion al inicio: `torch.cuda.is_available()` / `onnxruntime.get_available_providers()`.
- Cada componente que puede usar GPU tiene fallback CPU transparente.
- El usuario nunca necesita configurar nada — Marlow detecta y usa lo mejor disponible.
- Aplica a: ASR (faster-whisper/Moonshine), VAD (Silero), Vision (OmniParser futuro).

## ARQUITECTURA DE AUDIO

### Activacion de voz
| Modo | Metodo | Estado |
|------|--------|--------|
| Hotkey | Ctrl+Shift+M graba, Ctrl+Shift+N para | Implementado (voice_hotkey.py) |
| Wake Word | "Hey Marlow" via OpenWakeWord — siempre escuchando | Fase 6 |
| Multi-turn | Conversacion continua sin re-activar | Fase 6 |

### ASR — Automatic Speech Recognition
| Motor | Caso de uso | Estado |
|-------|-------------|--------|
| Moonshine v2 streaming | Comandos cortos (<10s), baja latencia | Fase 6 |
| faster-whisper (CPU int8) | Audio largo, transcripcion, dictado | Implementado |
| faster-whisper (GPU auto) | Mismo pero acelerado si hay GPU | Fase 5 |

### TTS — Text-to-Speech (cadena de fallback)
| Prioridad | Motor | Requiere | Estado |
|-----------|-------|----------|--------|
| 1 | edge-tts (voces neurales Microsoft) | Internet | Implementado |
| 2 | Piper TTS (ONNX offline, alta calidad) | Modelo descargado | Fase 5 |
| 3 | pyttsx3 (SAPI5 nativo Windows) | Nada | Implementado |

### VAD — Voice Activity Detection
| Motor | Metodo | Estado |
|-------|--------|--------|
| RMS threshold | Energia de audio > 500 | Implementado (voice_hotkey.py) |
| Silero VAD | Red neuronal ONNX, preciso | Fase 5 (reemplaza RMS) |

## ROADMAP v4 (7 fases)

### Fase 1: Vision Enhancement
- 1.1 **Windows OCR** reemplaza Tesseract como motor OCR default ✓ COMPLETADO v0.11.0
- 1.2 **Multi-property fuzzy search** en UIA tree (name + automation_id + help_text + class_name, Levenshtein) ✓ COMPLETADO v0.12.0
- 1.3 **Deteccion de apps Electron** (DLL analysis + exe path + cmdline, 8 frameworks) ✓ COMPLETADO v0.13.0
- 1.4 **Profundidad adaptativa** de UIA tree (auto depth per framework: WinUI=15, Chromium=8, Electron=5) ✓ COMPLETADO v0.14.0
- 1.5 **COM invisible por default** en app_script.py (visible=False, no mostrar ventana de Office) ✓ COMPLETADO v0.14.0

### Fase 2: App Intelligence
- 2.1 **CDP para apps Electron** (VS Code, Slack, Notion) — debug protocol via puerto remoto
- 2.2 **UIA event handlers** via comtypes (reaccionar a cambios en tiempo real, no polling)
- 2.3 **Auto-handling de dialogos** (detectar y responder a popups conocidos automaticamente)

### Fase 3: Understanding
- 3.1 **Set-of-Mark prompting** — numerar elementos en screenshot para que LLM los identifique
- 3.2 **Context awareness** — entender que app esta activa y adaptar estrategia
- 3.3 **App Knowledge Base** — base de datos de como interactuar con cada app conocida
- 3.4 **Teach Marlow mode** — el usuario muestra una accion, Marlow la aprende como workflow

### Fase 4: Shadow Mode
- 4.1 **Virtual Desktops** con pyvda — crear desktop invisible para operar sin que el usuario vea
- 4.2 **SendMessage tier** — interactuar sin foco ni visibilidad via mensajes Win32
- 4.3 **PrintWindow** — capturar screenshots de ventanas sin que esten visibles
- 4.4 **Toast notifications** — notificar al usuario de resultados sin interrumpir
- 4.5 **System tray** — icono persistente con estado de Marlow

### Fase 5: Voice Core
- 5.1 **Silero VAD** reemplaza RMS threshold (red neuronal ONNX, deteccion precisa de voz)
- 5.2 **Piper TTS** offline como segundo fallback (ONNX, alta calidad, sin internet)
- 5.3 **GPU auto-detect** para ASR (faster-whisper usa GPU si disponible, CPU si no)
- 5.4 **Audio calibracion** — detectar ruido ambiente y ajustar threshold automaticamente

### Fase 6: Natural Conversation
- 6.1 **Moonshine v2 streaming** — ASR streaming para comandos cortos con baja latencia
- 6.2 **OpenWakeWord "Hey Marlow"** — wake word siempre escuchando, activa sin hotkey
- 6.3 **Barge-in** — interrumpir a Marlow mientras habla con nuevo comando de voz
- 6.4 **Multi-turn voice** — conversacion continua sin re-activar por cada frase
- 6.5 **Soundboard** — sonidos de feedback (beeps, confirmacion, error) configurables

### Fase 7: Advanced
- 7.1 **Sensor fusion** — combinar UIA + OCR + vision + audio para entendimiento completo
- 7.2 **OmniParser** — modelo de vision para entender UIs complejas (GPU opcional)
- 7.3 **YAMNet** — clasificacion de sonidos del sistema (alertas, notificaciones, errores)
- 7.4 **NVDA integration** — usar motor de accesibilidad NVDA como fuente adicional de info
- 7.5 **Meeting transcription** — transcribir reuniones en tiempo real (system audio + mic)
- 7.6 **Dictado** — modo dictado continuo que escribe todo lo que el usuario dice
