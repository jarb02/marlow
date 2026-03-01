# ROADMAP.md — Marlow Development Roadmap v4

Detalle completo de las 7 fases del roadmap. Para resumen, ver CLAUDE.md.

## Dependencias

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
websocket-client>=1.7.0  # CDP WebSocket connections (Chrome DevTools Protocol)

# Opcionales
[project.optional-dependencies]
ocr = ["pytesseract>=0.3.10"]  # Tesseract fallback (requiere binary instalado)
```

## Principio de GPU

**Auto-detect, transparente, graceful fallback.**

- Sin GPU todo funciona bien. Con GPU todo funciona mejor.
- Dependencias GPU siempre opcionales (`[project.optional-dependencies]`).
- Auto-deteccion: `torch.cuda.is_available()` / `onnxruntime.get_available_providers()`.
- Cada componente con GPU tiene fallback CPU transparente.
- El usuario nunca configura nada — Marlow detecta y usa lo mejor disponible.
- Aplica a: ASR (faster-whisper/Moonshine), VAD (Silero), Vision (OmniParser futuro).

---

## Fase 1: Vision Enhancement — COMPLETA

- **1.1 Windows OCR** — Reemplaza Tesseract como motor OCR default. Windows OCR API via winrt bindings, soporte multi-idioma, bboxes per-word. Tesseract queda como fallback opcional. ✓ COMPLETADO v0.11.0
- **1.2 Multi-property fuzzy search** — Busqueda en UIA tree por name + automation_id + help_text + class_name con Levenshtein distance normalizada. Thresholds por propiedad, early exit en match perfecto, top N candidatos rankeados. ✓ COMPLETADO v0.12.0
- **1.3 Deteccion de apps Electron** — Analisis de DLLs cargadas via psutil.Process.memory_maps(). Detecta 8 frameworks: electron, cef, chromium, edge_webview2, winui3, uwp, wpf, winforms, win32. Cache por PID. ✓ COMPLETADO v0.13.0
- **1.4 Profundidad adaptativa UIA tree** — Auto depth per framework: WinUI/UWP/Win32/WPF=15, WinForms=12, Chromium/WebView2=8, Electron/CEF=5. Default=10. ✓ COMPLETADO v0.14.0
- **1.5 COM invisible por default** — app_script.py `visible=False` para instancias nuevas. GetActiveObject respeta visibilidad actual. ✓ COMPLETADO v0.14.0

## Fase 2: App Intelligence — COMPLETA

- **2.1 CDP para apps Electron** — 12 tools: discover, connect, disconnect, list_connections, send, click, type_text, key_combo, screenshot, evaluate, get_dom, click_selector. WebSocket via websocket-client, invisible input, async wrappers. Default ports para 10 apps conocidas. ✓ COMPLETADO v0.15.0
- **2.2 UIA event handlers** — COM event handlers via comtypes para reaccionar a cambios UI en tiempo real. STA daemon thread con message pump. 3 handlers: WindowOpened/Closed, FocusChanged, StructureChanged. Thread-safe event buffer (max 500). 3 tools: start_ui_monitor, stop_ui_monitor, get_ui_events. ✓ COMPLETADO v0.18.0
- **2.3 Auto-handling de dialogos** — Deteccion y clasificacion de dialogos: not_responding > error > save > update > confirmation > info. Acciones: report/dismiss/auto. Filtro por clase #32770 (Win32 dialogs). 2 tools: handle_dialog, get_dialog_info. ✓ COMPLETADO v0.18.0
- **2.4 CDP auto-restart** — ensure_cdp() propone restart (nunca auto-reinicia), restart_confirmed() cierra y relanza con --remote-debugging-port. Knowledge base persistida. 3 tools: cdp_ensure, cdp_restart_confirmed, cdp_get_knowledge_base. ✓ COMPLETADO v0.16.0
- **2.5 Cascade Recovery** — 5-step pipeline cuando smart_find falla: (1) wait 1.5s + retry UIA, (2) check blocking dialogs, (3) wide fuzzy search threshold 0.4, (4) OCR fallback, (5) screenshot for LLM. Timeout 5-30s. Config: cascade_recovery=True. 1 tool: cascade_find. ✓ COMPLETADO v0.19.0

## Fase 3: Understanding — PENDIENTE

- **3.1 Set-of-Mark prompting** — Numerar elementos visibles en screenshot con overlays numerados para que el LLM identifique elementos por numero en vez de coordenadas. Requiere: screenshot + UIA tree overlay + OCR bbox overlay. Output: imagen anotada + mapping numero→elemento.
- **3.2 Context awareness** — Entender que app esta activa, que tipo de contenido muestra, y adaptar la estrategia de interaccion automaticamente. Combinar framework detection + UIA tree analysis + window title parsing.
- **3.3 App Knowledge Base** — Base de datos local de como interactuar con cada app conocida. Almacena: mejores metodos por app (UIA vs OCR vs CDP), elementos importantes, atajos, quirks. Se alimenta del Error Journal. Storage en `~/.marlow/app_knowledge/`.
- **3.4 Teach Marlow mode** — El usuario muestra una accion paso a paso, Marlow la observa (screenshot diff + UIA events) y la graba como workflow reproducible. Extension del sistema de workflows con captura visual.

## Fase 4: Shadow Mode — PENDIENTE

- **4.1 Virtual Desktops** — Usar pyvda para crear/gestionar desktops virtuales de Windows. Marlow opera en un desktop invisible al usuario. Requiere: pyvda (optional dep), fallback silencioso si no instalado.
- **4.2 SendMessage tier** — Interactuar con controles Win32 via SendMessage/PostMessage sin necesidad de foco ni visibilidad. Mensajes: WM_SETTEXT, BM_CLICK, CB_SELECTSTRING, etc. Tier 2 en la arquitectura de input.
- **4.3 PrintWindow** — Capturar screenshots de ventanas aunque no esten visibles o esten detras de otras ventanas. `PrintWindow()` API + fallback a mss. Tier 2 en arquitectura de vision.
- **4.4 Toast notifications** — Notificar al usuario de resultados de tareas via Windows Toast notifications. Non-intrusive, no roba foco. Libreria: win10toast o windows-toasts.
- **4.5 System tray** — Icono persistente en system tray con estado de Marlow (idle, working, error). Menu contextual: status, kill switch, settings. Libreria: pystray.

## Fase 5: Voice Core — PENDIENTE

- **5.1 Silero VAD** — Reemplazar RMS threshold con Silero VAD (red neuronal ONNX). Deteccion precisa de voz vs ruido. ONNX Runtime para inference, ~5ms por frame. GPU opcional.
- **5.2 Piper TTS** — Motor TTS offline de alta calidad como segundo fallback (despues de edge-tts, antes de pyttsx3). Modelos ONNX descargables, voces en multiple idiomas. ~200ms latencia.
- **5.3 GPU auto-detect** — faster-whisper usa GPU automaticamente si CUDA disponible (torch.cuda.is_available()). Fallback transparente a CPU int8. Mismo API, mejor performance.
- **5.4 Audio calibracion** — Detectar nivel de ruido ambiente al inicio y ajustar VAD threshold automaticamente. Grabacion de 3s de silencio, calcular baseline RMS/energia.

## Fase 6: Natural Conversation — PENDIENTE

- **6.1 Moonshine v2 streaming** — ASR streaming para comandos cortos (<10s) con baja latencia (~200ms). Complementa faster-whisper (que es mejor para audio largo). Streaming chunks via WebSocket o callbacks.
- **6.2 OpenWakeWord "Hey Marlow"** — Wake word detection siempre escuchando en background. Activa grabacion sin necesidad de hotkey. Modelo ligero ONNX, ~1% CPU. Configurable: activar/desactivar, palabra custom.
- **6.3 Barge-in** — Interrumpir a Marlow mientras habla (TTS) con nuevo comando de voz. Deteccion de voz del usuario durante playback, cancel TTS y procesar nuevo input.
- **6.4 Multi-turn voice** — Conversacion continua sin re-activar por cada frase. Despues de responder, Marlow sigue escuchando por N segundos para follow-up. Timeout configurable.
- **6.5 Soundboard** — Sonidos de feedback configurables: beep inicio grabacion, confirmacion de accion, error, completado. WAV files en `~/.marlow/sounds/`. Personalizables por el usuario.

## Fase 7: Advanced — PENDIENTE

- **7.1 Sensor fusion** — Combinar UIA + OCR + vision + audio para entendimiento completo de la pantalla. Cada sensor aporta una capa de informacion que las otras no capturan. Fusion engine produce una representacion unificada.
- **7.2 OmniParser** — Modelo de computer vision para entender UIs complejas que UIA/OCR no pueden parsear. GPU opcional (ONNX). Detecta elementos, iconos, layouts. Ultimo recurso antes de pedir al LLM.
- **7.3 YAMNet** — Clasificacion de sonidos del sistema (alertas, notificaciones, errores de Windows, mensajes de apps). Trigger acciones basadas en sonidos: "escuche un error beep, reviso que paso".
- **7.4 NVDA integration** — Usar el motor de accesibilidad NVDA como fuente adicional de informacion sobre la UI. NVDA expone info que UIA a veces no captura (dynamic content, live regions).
- **7.5 Meeting transcription** — Transcribir reuniones en tiempo real combinando system audio (WASAPI loopback) + mic. Speaker diarization para identificar quien habla. Output: transcript con timestamps.
- **7.6 Dictado** — Modo dictado continuo que escribe todo lo que el usuario dice en la aplicacion activa. Streaming ASR + auto-puntuacion + formateo.

---

## Herramientas MCP por Modulo (referencia detallada)

### Phase 1: Core (14 tools)
| Tool | Funcion | Modulo |
|------|---------|--------|
| get_ui_tree | Accessibility Tree (0 tokens, auto depth per framework) | tools/ui_tree.py |
| take_screenshot | Screenshot pantalla/ventana/region | tools/screenshot.py |
| click | Click por nombre (silent invoke) o coordenadas | tools/mouse.py |
| type_text | Escribir texto con proteccion Notepad Win11 | tools/keyboard.py |
| press_key | Presionar tecla individual | tools/keyboard.py |
| hotkey | Atajos de teclado (Ctrl+C, etc.) | tools/keyboard.py |
| list_windows | Lista ventanas abiertas | tools/windows.py |
| focus_window | Traer ventana al frente | tools/windows.py |
| manage_window | Mover, resize, min, max, close | tools/windows.py |
| run_command | PowerShell/CMD (destructivos bloqueados) | tools/system.py |
| open_application | Abrir app por nombre o ruta | tools/system.py |
| clipboard | Leer/escribir portapapeles | tools/system.py |
| system_info | CPU, RAM, disco, procesos | tools/system.py |
| kill_switch | Detener TODA automatizacion | server.py |

### Phase 2: Advanced (17 tools)
| Tool | Funcion | Modulo |
|------|---------|--------|
| ocr_region | Windows OCR (primary) + Tesseract (fallback) | tools/ocr.py |
| list_ocr_languages | Idiomas OCR disponibles por motor | tools/ocr.py |
| smart_find | UIA fuzzy→OCR→cascade→screenshot | core/escalation.py |
| find_elements | Busqueda fuzzy multi-propiedad top 5 | core/escalation.py |
| cascade_find | Recovery: wait, dialogs, fuzzy, OCR, screenshot | core/cascade_recovery.py |
| detect_app_framework | Framework UI via DLLs (8 frameworks) | core/app_detector.py |
| setup_background_mode | Configurar dual monitor / offscreen | tools/background.py |
| move_to_agent_screen | Mover ventana al monitor del agente | tools/background.py |
| move_to_user_screen | Devolver ventana al usuario | tools/background.py |
| get_agent_screen_state | Ventanas en pantalla del agente | tools/background.py |
| capture_system_audio | WASAPI loopback recording | tools/audio.py |
| capture_mic_audio | Mic recording | tools/audio.py |
| transcribe_audio | faster-whisper CPU int8 | tools/audio.py |
| download_whisper_model | Pre-download modelo | tools/audio.py |
| listen_for_command | Mic + transcribe + silence detect | tools/voice.py |
| run_app_script | COM automation sandboxed invisible | tools/app_script.py |
| restore_user_focus | Restaurar foco manual | core/focus.py |

### Phase 3: Intelligence + Extensions (12 tools)
| Tool | Funcion | Modulo |
|------|---------|--------|
| visual_diff | Capturar estado 'antes' | tools/visual_diff.py |
| visual_diff_compare | Comparar antes/despues | tools/visual_diff.py |
| memory_save | Guardar valor persistente | tools/memory.py |
| memory_recall | Recuperar memorias | tools/memory.py |
| memory_delete | Eliminar memoria | tools/memory.py |
| memory_list | Listar memorias | tools/memory.py |
| clipboard_history | Historial de clipboard | tools/clipboard_ext.py |
| scrape_url | Web scraping | tools/scraper.py |
| extensions_list | Listar extensiones | extensions/registry.py |
| extensions_install | Instalar extension | extensions/registry.py |
| extensions_uninstall | Desinstalar extension | extensions/registry.py |
| extensions_audit | Auditar seguridad | extensions/registry.py |

### Phase 4: Automation (8 tools)
| Tool | Funcion | Modulo |
|------|---------|--------|
| watch_folder | Monitorear carpeta | tools/watcher.py |
| unwatch_folder | Detener monitoreo | tools/watcher.py |
| get_watch_events | Eventos detectados | tools/watcher.py |
| list_watchers | Watchers activos | tools/watcher.py |
| schedule_task | Tarea recurrente | tools/scheduler.py |
| list_scheduled_tasks | Listar programadas | tools/scheduler.py |
| remove_task | Eliminar tarea | tools/scheduler.py |
| get_task_history | Historial ejecuciones | tools/scheduler.py |

### Phase 5: Voice + TTS (3 tools)
| Tool | Funcion | Modulo |
|------|---------|--------|
| speak | TTS edge-tts + pyttsx3 fallback | tools/tts.py |
| speak_and_listen | Hablar + escuchar respuesta | tools/tts.py |
| get_voice_hotkey_status | Estado Ctrl+Shift+M | core/voice_hotkey.py |

### Adaptive + Workflows (8 tools)
| Tool | Funcion | Modulo |
|------|---------|--------|
| get_suggestions | Detectar patrones repetitivos | core/adaptive.py |
| accept_suggestion | Aceptar sugerencia | core/adaptive.py |
| dismiss_suggestion | Descartar sugerencia | core/adaptive.py |
| workflow_record | Comenzar grabacion | core/workflows.py |
| workflow_stop | Detener y guardar | core/workflows.py |
| workflow_run | Reproducir workflow | core/workflows.py |
| workflow_list | Listar workflows | core/workflows.py |
| workflow_delete | Eliminar workflow | core/workflows.py |

### Self-Improve + Smart Wait + UX (8 tools)
| Tool | Funcion | Modulo |
|------|---------|--------|
| get_error_journal | Diario de errores | core/error_journal.py |
| clear_error_journal | Limpiar diario | core/error_journal.py |
| wait_for_element | Esperar elemento UI | tools/wait.py |
| wait_for_text | Esperar texto (OCR) | tools/wait.py |
| wait_for_window | Esperar ventana | tools/wait.py |
| wait_for_idle | Esperar estabilidad | tools/wait.py |
| set_agent_screen_only | Auto-redirect agente | tools/background.py |
| toggle_voice_overlay | Overlay flotante | core/voice_overlay.py |

### Monitor (5 tools)
| Tool | Funcion | Modulo |
|------|---------|--------|
| start_ui_monitor | Iniciar eventos UI COM | core/uia_events.py |
| stop_ui_monitor | Detener monitoreo | core/uia_events.py |
| get_ui_events | Eventos recientes | core/uia_events.py |
| handle_dialog | Detectar/manejar dialogos | core/dialog_handler.py |
| get_dialog_info | Info completa dialogo | core/dialog_handler.py |

### Diagnostics (1 tool)
| Tool | Funcion | Modulo |
|------|---------|--------|
| run_diagnostics | Diagnosticos del sistema | core/setup_wizard.py |

### CDP (15 tools)
| Tool | Funcion | Modulo |
|------|---------|--------|
| cdp_discover | Escanear puertos CDP | core/cdp_manager.py |
| cdp_connect | Conectar WebSocket | core/cdp_manager.py |
| cdp_disconnect | Desconectar | core/cdp_manager.py |
| cdp_list_connections | Listar conexiones | core/cdp_manager.py |
| cdp_send | Comando CDP crudo | core/cdp_manager.py |
| cdp_click | Click invisible | core/cdp_manager.py |
| cdp_type_text | Escribir invisible | core/cdp_manager.py |
| cdp_key_combo | Teclas invisible | core/cdp_manager.py |
| cdp_screenshot | Screenshot oculta | core/cdp_manager.py |
| cdp_evaluate | Evaluar JavaScript | core/cdp_manager.py |
| cdp_get_dom | Arbol DOM | core/cdp_manager.py |
| cdp_click_selector | Click CSS selector | core/cdp_manager.py |
| cdp_ensure | Asegurar CDP disponible | core/cdp_manager.py |
| cdp_restart_confirmed | Reiniciar con CDP | core/cdp_manager.py |
| cdp_get_knowledge_base | Knowledge base CDP | core/cdp_manager.py |

---

## Auditoria v0.4.1 — 21 problemas resueltos

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

## Integration Tests (tests/test_integration.py)

17 tests across 7 scenarios:
1. **Background Mode Flow** — setup → open Notepad → move to agent screen → type → verify → restore focus → cleanup
2. **Audio Pipeline** — capture audio → verify WAV → speak TTS; transcription (whisper, skips if no model)
3. **Kill Switch Stops Scheduler** — schedule → kill switch → verify 0 runs → verify skip entries
4. **Memory Persistence** — save → recall → verify → delete → verify gone; list keys
5. **Focus Under Stress** — 5 consecutive tools with focus save/restore after each
6. **Security Chain** — safe echo OK → format C: BLOCKED → del /f BLOCKED → app_script injection BLOCKED
7. **Watcher + Scheduler** — watch_folder → create file → verify event → schedule → verify history

All tests have try/finally cleanup. Audio tests use asyncio.wait_for() timeouts.
