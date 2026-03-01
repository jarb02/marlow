"""
Help and capabilities discovery for Marlow MCP Server.

Provides tools for MCP clients to discover all available tools
and query the current system state.
"""

import random

from marlow import __version__

# ─────────────────────────────────────────────────────────────
# Tools Catalog — pure data, no external imports
# ─────────────────────────────────────────────────────────────

_TOOLS_CATALOG = [
    {
        "name": "Core",
        "tools": [
            {
                "name": "get_ui_tree",
                "description_en": "Read Windows UI Automation Accessibility Tree (0 tokens, auto depth per framework)",
                "description_es": "Lee el arbol de accesibilidad de UI Automation (0 tokens, profundidad auto por framework)",
                "params": ["window_title", "max_depth", "include_invisible"],
            },
            {
                "name": "take_screenshot",
                "description_en": "Screenshot of screen, window, or region (~1,500 tokens)",
                "description_es": "Captura de pantalla, ventana o region (~1,500 tokens)",
                "params": ["window_title", "region", "quality"],
            },
            {
                "name": "click",
                "description_en": "Click element by name (silent) or coordinates",
                "description_es": "Click por nombre (silencioso) o coordenadas",
                "params": ["element_name", "window_title", "x", "y", "button", "double_click"],
            },
            {
                "name": "type_text",
                "description_en": "Type text into element by name or at cursor",
                "description_es": "Escribir texto en elemento por nombre o en cursor",
                "params": ["text", "element_name", "window_title", "clear_first"],
            },
            {
                "name": "press_key",
                "description_en": "Press a keyboard key",
                "description_es": "Presionar una tecla del teclado",
                "params": ["key", "times"],
            },
            {
                "name": "hotkey",
                "description_en": "Execute keyboard shortcut (e.g., Ctrl+C)",
                "description_es": "Ejecutar atajo de teclado (ej. Ctrl+C)",
                "params": ["keys"],
            },
            {
                "name": "list_windows",
                "description_en": "List all open windows with titles and positions",
                "description_es": "Listar ventanas abiertas con titulos y posiciones",
                "params": ["include_minimized"],
            },
            {
                "name": "focus_window",
                "description_en": "Bring a window to the foreground",
                "description_es": "Traer una ventana al frente",
                "params": ["window_title"],
            },
            {
                "name": "manage_window",
                "description_en": "Move, resize, minimize, maximize, or close a window",
                "description_es": "Mover, redimensionar, minimizar, maximizar o cerrar ventana",
                "params": ["window_title", "action", "x", "y", "width", "height"],
            },
        ],
    },
    {
        "name": "System",
        "tools": [
            {
                "name": "run_command",
                "description_en": "Execute PowerShell/CMD command (destructive blocked)",
                "description_es": "Ejecutar comando PowerShell/CMD (destructivos bloqueados)",
                "params": ["command", "shell", "timeout"],
            },
            {
                "name": "open_application",
                "description_en": "Open application by name or path",
                "description_es": "Abrir aplicacion por nombre o ruta",
                "params": ["app_name", "app_path"],
            },
            {
                "name": "clipboard",
                "description_en": "Read or write system clipboard",
                "description_es": "Leer o escribir portapapeles del sistema",
                "params": ["action", "text"],
            },
            {
                "name": "system_info",
                "description_en": "Get OS, CPU, RAM, disk usage, top processes",
                "description_es": "Obtener info de OS, CPU, RAM, disco, procesos",
                "params": [],
            },
            {
                "name": "run_app_script",
                "description_en": "COM automation for Office/Adobe apps (sandboxed, invisible by default)",
                "description_es": "Automatizacion COM para apps Office/Adobe (sandboxed, invisible por default)",
                "params": ["app_name", "script", "timeout", "visible"],
            },
        ],
    },
    {
        "name": "Background",
        "tools": [
            {
                "name": "setup_background_mode",
                "description_en": "Configure dual monitor or offscreen background mode",
                "description_es": "Configurar modo background dual monitor u offscreen",
                "params": ["preferred_mode"],
            },
            {
                "name": "move_to_agent_screen",
                "description_en": "Move window to agent workspace (second monitor)",
                "description_es": "Mover ventana al workspace del agente (segundo monitor)",
                "params": ["window_title"],
            },
            {
                "name": "move_to_user_screen",
                "description_en": "Move window back to user's primary monitor",
                "description_es": "Devolver ventana al monitor principal del usuario",
                "params": ["window_title"],
            },
            {
                "name": "get_agent_screen_state",
                "description_en": "List windows on agent screen",
                "description_es": "Listar ventanas en pantalla del agente",
                "params": [],
            },
        ],
    },
    {
        "name": "Audio",
        "tools": [
            {
                "name": "capture_system_audio",
                "description_en": "Record system audio via WASAPI loopback",
                "description_es": "Grabar audio del sistema via WASAPI loopback",
                "params": ["duration_seconds"],
            },
            {
                "name": "capture_mic_audio",
                "description_en": "Record microphone audio",
                "description_es": "Grabar audio del microfono",
                "params": ["duration_seconds"],
            },
            {
                "name": "transcribe_audio",
                "description_en": "Transcribe audio file (faster-whisper CPU)",
                "description_es": "Transcribir archivo de audio (faster-whisper CPU)",
                "params": ["audio_path", "language", "model_size"],
            },
            {
                "name": "download_whisper_model",
                "description_en": "Pre-download Whisper model to avoid timeout",
                "description_es": "Pre-descargar modelo Whisper para evitar timeout",
                "params": ["model_size"],
            },
            {
                "name": "listen_for_command",
                "description_en": "Listen for voice command via mic + transcription",
                "description_es": "Escuchar comando de voz via mic + transcripcion",
                "params": ["duration_seconds", "language", "model_size"],
            },
            {
                "name": "speak",
                "description_en": "Text-to-speech with edge-tts neural voices",
                "description_es": "Texto a voz con voces neurales edge-tts",
                "params": ["text", "language", "voice", "rate"],
            },
            {
                "name": "speak_and_listen",
                "description_en": "Speak text, then listen for voice response",
                "description_es": "Hablar texto, luego escuchar respuesta de voz",
                "params": ["text", "timeout", "language", "voice"],
            },
        ],
    },
    {
        "name": "Intelligence",
        "tools": [
            {
                "name": "smart_find",
                "description_en": "Find UI element: UIA fuzzy -> OCR -> screenshot escalation",
                "description_es": "Buscar elemento UI: UIA fuzzy -> OCR -> screenshot (escalamiento)",
                "params": ["target", "window_title", "click_if_found"],
            },
            {
                "name": "find_elements",
                "description_en": "Multi-property fuzzy search for UI elements (top 5 candidates)",
                "description_es": "Busqueda fuzzy multi-propiedad para elementos UI (top 5 candidatos)",
                "params": ["query", "window_title", "control_type"],
            },
            {
                "name": "detect_app_framework",
                "description_en": "Detect UI framework of a window (Electron, WPF, WinUI, etc.)",
                "description_es": "Detectar framework UI de una ventana (Electron, WPF, WinUI, etc.)",
                "params": ["window_title"],
            },
            {
                "name": "visual_diff",
                "description_en": "Capture 'before' state for visual comparison",
                "description_es": "Capturar estado 'antes' para comparacion visual",
                "params": ["window_title", "description"],
            },
            {
                "name": "visual_diff_compare",
                "description_en": "Compare before/after, return change percentage",
                "description_es": "Comparar antes/despues, retornar porcentaje de cambio",
                "params": ["diff_id"],
            },
            {
                "name": "ocr_region",
                "description_en": "Extract text via Windows OCR (primary) or Tesseract (fallback)",
                "description_es": "Extraer texto via Windows OCR (primario) o Tesseract (fallback)",
                "params": ["window_title", "region", "language", "engine"],
            },
            {
                "name": "list_ocr_languages",
                "description_en": "List available OCR languages per engine",
                "description_es": "Listar idiomas OCR disponibles por motor",
                "params": [],
            },
        ],
    },
    {
        "name": "CDP",
        "tools": [
            {
                "name": "cdp_discover",
                "description_en": "Scan localhost ports for apps with CDP enabled",
                "description_es": "Escanear puertos localhost buscando apps con CDP habilitado",
                "params": ["port_start", "port_end"],
            },
            {
                "name": "cdp_connect",
                "description_en": "Connect to a CDP endpoint on a given port",
                "description_es": "Conectar a un endpoint CDP en un puerto dado",
                "params": ["port"],
            },
            {
                "name": "cdp_disconnect",
                "description_en": "Disconnect from a CDP endpoint",
                "description_es": "Desconectar de un endpoint CDP",
                "params": ["port"],
            },
            {
                "name": "cdp_list_connections",
                "description_en": "List all active CDP connections",
                "description_es": "Listar todas las conexiones CDP activas",
                "params": [],
            },
            {
                "name": "cdp_send",
                "description_en": "Send a raw CDP command (advanced)",
                "description_es": "Enviar un comando CDP crudo (avanzado)",
                "params": ["port", "method", "params"],
            },
            {
                "name": "cdp_click",
                "description_en": "Click at page coordinates via CDP (invisible)",
                "description_es": "Click en coordenadas de pagina via CDP (invisible)",
                "params": ["port", "x", "y"],
            },
            {
                "name": "cdp_type_text",
                "description_en": "Type text via CDP (invisible)",
                "description_es": "Escribir texto via CDP (invisible)",
                "params": ["port", "text"],
            },
            {
                "name": "cdp_key_combo",
                "description_en": "Press key combination via CDP (invisible)",
                "description_es": "Combinacion de teclas via CDP (invisible)",
                "params": ["port", "key", "modifiers"],
            },
            {
                "name": "cdp_screenshot",
                "description_en": "Take screenshot via CDP (works even if window is hidden)",
                "description_es": "Screenshot via CDP (funciona aunque ventana este oculta)",
                "params": ["port", "format"],
            },
            {
                "name": "cdp_evaluate",
                "description_en": "Evaluate JavaScript expression in page context via CDP",
                "description_es": "Evaluar expresion JavaScript en contexto de pagina via CDP",
                "params": ["port", "expression"],
            },
            {
                "name": "cdp_get_dom",
                "description_en": "Get DOM tree of the page via CDP",
                "description_es": "Obtener arbol DOM de la pagina via CDP",
                "params": ["port", "depth"],
            },
            {
                "name": "cdp_click_selector",
                "description_en": "Click element by CSS selector via CDP (invisible)",
                "description_es": "Click en elemento por selector CSS via CDP (invisible)",
                "params": ["port", "css_selector"],
            },
            {
                "name": "cdp_ensure",
                "description_en": "Ensure CDP is available for an Electron app (proposes restart if needed)",
                "description_es": "Asegurar CDP disponible para app Electron (propone restart si necesario)",
                "params": ["app_name", "preferred_port"],
            },
            {
                "name": "cdp_restart_confirmed",
                "description_en": "Restart app with CDP after user confirmation",
                "description_es": "Reiniciar app con CDP despues de confirmacion del usuario",
                "params": ["app_name", "port"],
            },
            {
                "name": "cdp_get_knowledge_base",
                "description_en": "Get CDP knowledge base (apps, ports, restart history)",
                "description_es": "Obtener knowledge base CDP (apps, puertos, historial de restarts)",
                "params": [],
            },
        ],
    },
    {
        "name": "Memory",
        "tools": [
            {
                "name": "memory_save",
                "description_en": "Save persistent key-value data across sessions",
                "description_es": "Guardar datos clave-valor persistentes entre sesiones",
                "params": ["key", "value", "category"],
            },
            {
                "name": "memory_recall",
                "description_en": "Recall stored memories by key or category",
                "description_es": "Recuperar memorias almacenadas por clave o categoria",
                "params": ["key", "category"],
            },
            {
                "name": "memory_delete",
                "description_en": "Delete a specific memory",
                "description_es": "Eliminar una memoria especifica",
                "params": ["key", "category"],
            },
            {
                "name": "memory_list",
                "description_en": "List all memories organized by category",
                "description_es": "Listar todas las memorias por categoria",
                "params": [],
            },
        ],
    },
    {
        "name": "Clipboard",
        "tools": [
            {
                "name": "clipboard_history",
                "description_en": "Monitor and search clipboard history",
                "description_es": "Monitorear y buscar historial del portapapeles",
                "params": ["action", "search", "limit"],
            },
        ],
    },
    {
        "name": "Web",
        "tools": [
            {
                "name": "scrape_url",
                "description_en": "Extract content from URL (text/links/tables/html)",
                "description_es": "Extraer contenido de URL (texto/links/tablas/html)",
                "params": ["url", "selector", "format"],
            },
        ],
    },
    {
        "name": "Extensions",
        "tools": [
            {
                "name": "extensions_list",
                "description_en": "List installed extensions with permissions",
                "description_es": "Listar extensiones instaladas con permisos",
                "params": [],
            },
            {
                "name": "extensions_install",
                "description_en": "Install extension from pip",
                "description_es": "Instalar extension desde pip",
                "params": ["package"],
            },
            {
                "name": "extensions_uninstall",
                "description_en": "Uninstall an extension",
                "description_es": "Desinstalar una extension",
                "params": ["name"],
            },
            {
                "name": "extensions_audit",
                "description_en": "Audit extension security and permissions",
                "description_es": "Auditar seguridad y permisos de extension",
                "params": ["name"],
            },
        ],
    },
    {
        "name": "Automation",
        "tools": [
            {
                "name": "watch_folder",
                "description_en": "Monitor folder for file changes (watchdog)",
                "description_es": "Monitorear carpeta por cambios (watchdog)",
                "params": ["path", "events", "recursive"],
            },
            {
                "name": "unwatch_folder",
                "description_en": "Stop monitoring a folder",
                "description_es": "Detener monitoreo de carpeta",
                "params": ["watch_id"],
            },
            {
                "name": "get_watch_events",
                "description_en": "Get detected filesystem events",
                "description_es": "Obtener eventos del filesystem detectados",
                "params": ["watch_id", "limit", "since"],
            },
            {
                "name": "list_watchers",
                "description_en": "List all active folder watchers",
                "description_es": "Listar todos los watchers activos",
                "params": [],
            },
            {
                "name": "schedule_task",
                "description_en": "Schedule a recurring command",
                "description_es": "Programar un comando recurrente",
                "params": ["name", "command", "interval_seconds", "shell", "max_runs"],
            },
            {
                "name": "list_scheduled_tasks",
                "description_en": "List scheduled tasks with status",
                "description_es": "Listar tareas programadas con estado",
                "params": [],
            },
            {
                "name": "remove_task",
                "description_en": "Remove a scheduled task",
                "description_es": "Eliminar una tarea programada",
                "params": ["task_name"],
            },
            {
                "name": "get_task_history",
                "description_en": "Get task execution history",
                "description_es": "Obtener historial de ejecucion de tareas",
                "params": ["task_name", "limit"],
            },
        ],
    },
    {
        "name": "Adaptive",
        "tools": [
            {
                "name": "get_suggestions",
                "description_en": "Detect repeating action patterns and suggest them",
                "description_es": "Detectar patrones de acciones repetitivas y sugerirlos",
                "params": [],
            },
            {
                "name": "accept_suggestion",
                "description_en": "Accept a pattern suggestion",
                "description_es": "Aceptar una sugerencia de patron",
                "params": ["pattern_id"],
            },
            {
                "name": "dismiss_suggestion",
                "description_en": "Dismiss a pattern suggestion",
                "description_es": "Descartar una sugerencia de patron",
                "params": ["pattern_id"],
            },
        ],
    },
    {
        "name": "Workflow",
        "tools": [
            {
                "name": "workflow_record",
                "description_en": "Start recording a workflow",
                "description_es": "Comenzar a grabar un workflow",
                "params": ["name"],
            },
            {
                "name": "workflow_stop",
                "description_en": "Stop recording and save workflow",
                "description_es": "Detener grabacion y guardar workflow",
                "params": [],
            },
            {
                "name": "workflow_run",
                "description_en": "Replay a saved workflow with safety checks",
                "description_es": "Reproducir un workflow guardado con checks de seguridad",
                "params": ["name"],
            },
            {
                "name": "workflow_list",
                "description_en": "List all saved workflows",
                "description_es": "Listar todos los workflows guardados",
                "params": [],
            },
            {
                "name": "workflow_delete",
                "description_en": "Delete a saved workflow",
                "description_es": "Eliminar un workflow guardado",
                "params": ["name"],
            },
        ],
    },
    {
        "name": "Self-Improve",
        "tools": [
            {
                "name": "get_error_journal",
                "description_en": "Show error journal (which methods fail/work per app)",
                "description_es": "Mostrar diario de errores (que metodos fallan/funcionan por app)",
                "params": ["window"],
            },
            {
                "name": "clear_error_journal",
                "description_en": "Clear error journal entries for an app or all",
                "description_es": "Limpiar entradas del diario de errores por app o todas",
                "params": ["window"],
            },
        ],
    },
    {
        "name": "Wait",
        "tools": [
            {
                "name": "wait_for_element",
                "description_en": "Wait for a UI element to appear (polls UIA tree)",
                "description_es": "Esperar a que un elemento UI aparezca (encuesta UIA tree)",
                "params": ["name", "window_title", "timeout", "interval"],
            },
            {
                "name": "wait_for_text",
                "description_en": "Wait for text to appear on screen via OCR",
                "description_es": "Esperar a que un texto aparezca en pantalla via OCR",
                "params": ["text", "window_title", "timeout", "interval"],
            },
            {
                "name": "wait_for_window",
                "description_en": "Wait for a window to appear by title",
                "description_es": "Esperar a que una ventana aparezca por titulo",
                "params": ["title", "timeout", "interval"],
            },
            {
                "name": "wait_for_idle",
                "description_en": "Wait for screen/window to stop changing (idle)",
                "description_es": "Esperar a que la pantalla/ventana deje de cambiar (idle)",
                "params": ["window_title", "timeout", "stable_seconds"],
            },
        ],
    },
    {
        "name": "UX",
        "tools": [
            {
                "name": "set_agent_screen_only",
                "description_en": "Enable/disable auto-redirect of windows to agent screen",
                "description_es": "Activar/desactivar redireccion automatica al monitor del agente",
                "params": ["enabled"],
            },
            {
                "name": "toggle_voice_overlay",
                "description_en": "Show/hide floating voice control overlay window",
                "description_es": "Mostrar/ocultar ventana flotante de control de voz",
                "params": ["visible"],
            },
        ],
    },
    {
        "name": "Security",
        "tools": [
            {
                "name": "kill_switch",
                "description_en": "Emergency stop: halt all automation",
                "description_es": "Parada de emergencia: detener toda automatizacion",
                "params": ["action"],
            },
            {
                "name": "restore_user_focus",
                "description_en": "Restore focus to user's previously active window",
                "description_es": "Restaurar foco a la ventana activa previa del usuario",
                "params": [],
            },
            {
                "name": "get_voice_hotkey_status",
                "description_en": "Check voice hotkey status (Ctrl+Shift+M)",
                "description_es": "Verificar estado del hotkey de voz (Ctrl+Shift+M)",
                "params": [],
            },
        ],
    },
    {
        "name": "Help",
        "tools": [
            {
                "name": "get_capabilities",
                "description_en": "List all Marlow tools organized by category",
                "description_es": "Listar todas las herramientas de Marlow por categoria",
                "params": ["category"],
            },
            {
                "name": "get_version",
                "description_en": "Get Marlow version and system state",
                "description_es": "Obtener version de Marlow y estado del sistema",
                "params": [],
            },
            {
                "name": "run_diagnostics",
                "description_en": "Run system diagnostics for troubleshooting",
                "description_es": "Ejecutar diagnosticos del sistema para troubleshooting",
                "params": [],
            },
            {
                "name": "get_inspiration",
                "description_en": "Get ideas for what Marlow can do (random tips and examples)",
                "description_es": "Obtener ideas de lo que Marlow puede hacer (tips y ejemplos aleatorios)",
                "params": ["count"],
            },
        ],
    },
]

_TOTAL_TOOLS = sum(len(cat["tools"]) for cat in _TOOLS_CATALOG)


# ─────────────────────────────────────────────────────────────
# MCP Tools
# ─────────────────────────────────────────────────────────────

async def get_capabilities(category: str | None = None) -> dict:
    """
    List all Marlow MCP tools organized by category.

    / Retorna catalogo completo o filtrado por categoria.
    """
    if category:
        # Filter to a single category (case-insensitive)
        match = None
        for cat in _TOOLS_CATALOG:
            if cat["name"].lower() == category.lower():
                match = cat
                break
        if not match:
            available = [c["name"] for c in _TOOLS_CATALOG]
            return {
                "error": f"Unknown category: '{category}'. Available: {available}",
            }
        return {
            "success": True,
            "total_tools": len(match["tools"]),
            "categories": [match],
        }

    return {
        "success": True,
        "total_tools": _TOTAL_TOOLS,
        "tip": "Press Ctrl+Shift+M to activate voice control. You can ask Marlow anything by voice.",
        "categories": _TOOLS_CATALOG,
    }


async def get_version(
    safety_status: dict,
    background_mode: str | None,
    voice_hotkey_active: bool,
) -> dict:
    """
    Get Marlow version, tool count, and current system state.

    / Retorna version, conteo de tools, y estado del sistema.

    Parameters are passed by server.py dispatch (decoupled from internals).
    """
    return {
        "success": True,
        "version": __version__,
        "total_tools": _TOTAL_TOOLS,
        "system": {
            "kill_switch_active": safety_status.get("kill_switch_active", False),
            "confirmation_mode": safety_status.get("confirmation_mode", "unknown"),
            "actions_this_minute": safety_status.get("actions_this_minute", 0),
            "max_actions_per_minute": safety_status.get("max_actions_per_minute", 30),
            "background_mode": background_mode,
            "voice_hotkey_active": voice_hotkey_active,
        },
    }


# ─────────────────────────────────────────────────────────────
# Inspiration Tips
# ─────────────────────────────────────────────────────────────

_INSPIRATION_TIPS = [
    {
        "title": "Voice Control",
        "tip": "Press Ctrl+Shift+M to talk to Marlow by voice. Ask 'What can you do?' to get started.",
        "tools": ["listen_for_command", "speak", "speak_and_listen"],
    },
    {
        "title": "Background Mode",
        "tip": "If you have two monitors, Marlow can work on the second screen while you use the first. Try 'setup background mode'.",
        "tools": ["setup_background_mode", "move_to_agent_screen"],
    },
    {
        "title": "Automate Electron Apps",
        "tip": "Marlow can control VS Code, Discord, Slack, and Figma invisibly via Chrome DevTools Protocol. No mouse or keyboard needed.",
        "tools": ["cdp_ensure", "cdp_click", "cdp_type", "cdp_evaluate"],
    },
    {
        "title": "Record & Replay Workflows",
        "tip": "Record a sequence of actions and replay it later. Great for repetitive tasks like daily reports or data entry.",
        "tools": ["workflow_record", "workflow_stop", "workflow_run"],
    },
    {
        "title": "COM Automation",
        "tip": "Script Excel, Word, PowerPoint, Photoshop, and other Office/Adobe apps directly. Runs invisibly by default.",
        "tools": ["run_app_script"],
    },
    {
        "title": "Smart Find",
        "tip": "Can't find a button? smart_find escalates from UI Automation to OCR to screenshot analysis automatically.",
        "tools": ["smart_find", "find_elements"],
    },
    {
        "title": "Watch Folders",
        "tip": "Monitor a folder for new files and react automatically. Useful for download folders, inboxes, or build outputs.",
        "tools": ["watch_folder", "get_watch_events"],
    },
    {
        "title": "Schedule Tasks",
        "tip": "Run commands on a recurring schedule. Check disk usage every hour, pull git repos, or generate reports.",
        "tools": ["schedule_task", "get_task_history"],
    },
    {
        "title": "Visual Diff",
        "tip": "Capture a 'before' screenshot, make changes, then compare. Marlow tells you exactly what changed and by how much.",
        "tools": ["visual_diff", "visual_diff_compare"],
    },
    {
        "title": "Persistent Memory",
        "tip": "Marlow remembers things across sessions. Save project paths, preferences, or notes and recall them later.",
        "tools": ["memory_save", "memory_recall"],
    },
    {
        "title": "Web Scraping",
        "tip": "Extract text, links, or tables from any public URL. Great for monitoring prices, docs, or changelogs.",
        "tools": ["scrape_url"],
    },
    {
        "title": "Wait for Anything",
        "tip": "Wait for a window to open, a button to appear, or text to show up on screen before continuing. No more guessing with sleep timers.",
        "tools": ["wait_for_element", "wait_for_text", "wait_for_window", "wait_for_idle"],
    },
    {
        "title": "Audio Transcription",
        "tip": "Record system audio or microphone, then transcribe it locally with Whisper. Meeting notes, lecture summaries, or audio logs.",
        "tools": ["capture_system_audio", "capture_mic_audio", "transcribe_audio"],
    },
    {
        "title": "Clipboard History",
        "tip": "Start clipboard monitoring to keep a history of everything you copy. Search through past clips to find what you need.",
        "tools": ["clipboard_history"],
    },
    {
        "title": "Pattern Detection",
        "tip": "Marlow watches for repeating action patterns and suggests automating them. The more you use it, the smarter it gets.",
        "tools": ["get_suggestions", "accept_suggestion"],
    },
]


async def get_inspiration(count: int = 3) -> dict:
    """
    Return random tips and ideas for what Marlow can do.

    / Retorna tips e ideas aleatorias de lo que Marlow puede hacer.
    """
    count = max(1, min(count, len(_INSPIRATION_TIPS)))

    # Always include voice control tip first
    voice_tip = _INSPIRATION_TIPS[0]
    others = random.sample(_INSPIRATION_TIPS[1:], min(count - 1, len(_INSPIRATION_TIPS) - 1))
    selected = [voice_tip] + others

    return {
        "success": True,
        "total_tools": _TOTAL_TOOLS,
        "tips": selected,
    }
