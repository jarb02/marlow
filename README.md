# Marlow

> **AI that works beside you, not instead of you.**

<p align="center">
  <img src="https://raw.githubusercontent.com/jarb02/marlow/main/assets/banner.png" alt="Marlow Banner" width="600">
</p>

<p align="center">
  <a href="https://pypi.org/project/marlow-mcp/"><img src="https://img.shields.io/pypi/v/marlow-mcp?color=blue" alt="PyPI version"></a>
  <a href="https://pypi.org/project/marlow-mcp/"><img src="https://img.shields.io/pypi/pyversions/marlow-mcp" alt="Python versions"></a>
  <a href="https://github.com/jarb02/marlow/blob/main/LICENSE"><img src="https://img.shields.io/github/license/jarb02/marlow" alt="License"></a>
  <a href="https://github.com/jarb02/marlow"><img src="https://img.shields.io/badge/platform-Windows-0078D6" alt="Platform"></a>
</p>

<p align="center">
  <a href="#-quick-start">Quick Start</a> &bull;
  <a href="#-features">Features</a> &bull;
  <a href="#-security">Security</a> &bull;
  <a href="#-vs-competition">vs Competition</a> &bull;
  <a href="#-español">Español</a>
</p>

---

## The Problem

Existing Windows automation MCP servers take over your mouse and keyboard. **You stop working so the AI can work.**

## The Solution

Marlow works **in parallel** with you. Background mode on a second monitor. Real-time audio. CDP automation for Electron apps. Security from commit #1. And yes, it speaks Spanish.

---

## Quick Start

```bash
pip install marlow-mcp
```

Add to your MCP client config file:

```json
{
  "mcpServers": {
    "marlow": {
      "command": "marlow"
    }
  }
}
```

Restart your MCP client. Done.

> **Voice Control:** Press `Ctrl+Shift+M` to talk to Marlow. Ask *"What can you do?"* to get started. No typing needed.

### Optional extras

```bash
# Voice features (mic capture, Whisper transcription, system audio)
pip install marlow-mcp[audio]

# OCR via Tesseract (requires Tesseract binary installed separately)
pip install marlow-mcp[ocr]

# Everything
pip install marlow-mcp[audio,ocr]
```

> **Note:** The `keyboard` library requires **administrator privileges** on Windows for global hotkeys (kill switch, voice hotkey). Run your MCP client as admin, or the kill switch (`Ctrl+Shift+Escape`) and voice hotkey (`Ctrl+Shift+M`) won't register.

---

## Features

### 87 MCP Tools

| Category | Tools | Description |
|----------|-------|-------------|
| **Vision** | `get_ui_tree` | Read any window's UI structure — **0 tokens**, adaptive depth per framework |
| **Capture** | `take_screenshot` | Screen, window, or region capture |
| **Mouse** | `click` | Click by element name (silent invoke) or coordinates |
| **Keyboard** | `type_text`, `press_key`, `hotkey` | Type and shortcuts — silent mode for Notepad Win11 |
| **Windows** | `list_windows`, `focus_window`, `manage_window` | Full window management |
| **System** | `run_command`, `open_application`, `clipboard`, `system_info` | Shell, apps, clipboard |
| **Safety** | `kill_switch` | Emergency stop — halt everything instantly |
| **OCR** | `ocr_region`, `smart_find` | Extract text from images; UIA -> OCR -> screenshot escalation |
| **Background** | `setup_background_mode`, `move_to_agent_screen`, `move_to_user_screen`, `get_agent_screen_state`, `set_agent_screen_only` | Dual-monitor agent workspace |
| **Audio** | `capture_system_audio`, `capture_mic_audio`, `transcribe_audio`, `download_whisper_model` | WASAPI loopback + mic + Whisper CPU |
| **Voice** | `listen_for_command`, `speak`, `speak_and_listen`, `get_voice_hotkey_status`, `toggle_voice_overlay` | Voice control with edge-tts neural voices |
| **COM** | `run_app_script` | Script Office, Photoshop, Adobe — sandboxed, invisible by default |
| **Visual Diff** | `visual_diff`, `visual_diff_compare` | Before/after pixel comparison |
| **Memory** | `memory_save`, `memory_recall`, `memory_delete`, `memory_list` | Persistent key-value store across sessions |
| **Clipboard** | `clipboard_history` | Clipboard monitoring with search |
| **Scraper** | `scrape_url` | Web scraping (httpx + BeautifulSoup), private IPs blocked |
| **Extensions** | `extensions_list`, `extensions_install`, `extensions_uninstall`, `extensions_audit` | Plugin system with sandboxed permissions |
| **Watcher** | `watch_folder`, `unwatch_folder`, `get_watch_events`, `list_watchers` | File system monitoring (watchdog) |
| **Scheduler** | `schedule_task`, `list_scheduled_tasks`, `remove_task`, `get_task_history` | Recurring tasks with safety checks |
| **Adaptive** | `get_suggestions`, `accept_suggestion`, `dismiss_suggestion` | Detects repetitive patterns, suggests automation |
| **Workflows** | `workflow_record`, `workflow_stop`, `workflow_run`, `workflow_list`, `workflow_delete` | Record and replay tool sequences |
| **Self-Improve** | `get_error_journal`, `clear_error_journal` | Learns from failures per tool + app |
| **Smart Wait** | `wait_for_element`, `wait_for_text`, `wait_for_window`, `wait_for_idle` | Intelligent polling with timeout |
| **CDP** | `cdp_discover`, `cdp_connect`, `cdp_disconnect`, `cdp_list`, `cdp_send`, `cdp_click`, `cdp_type`, `cdp_key_combo`, `cdp_screenshot`, `cdp_evaluate`, `cdp_get_dom`, `cdp_click_selector`, `cdp_ensure`, `cdp_restart_confirmed`, `cdp_get_knowledge_base` | Chrome DevTools Protocol for Electron/CEF apps — 100% invisible |
| **Focus** | `restore_user_focus` | Manually restore focus if lost |
| **Help** | `get_capabilities`, `get_version` | Tool catalog and version info |

### Background Mode (Silent Methods)

Marlow tries **silent methods first** — clicking, typing, and interacting with apps **without taking your mouse or keyboard**:

```
invoke()        -> Click buttons without moving mouse
SetValue()      -> Type without keyboard simulation
select()        -> Pick menu items silently
toggle()        -> Check/uncheck boxes silently
```

If silent methods don't work for an app, Marlow falls back to real input automatically.

### CDP Manager (Electron/CEF Apps)

100% invisible automation for Electron apps (VS Code, Discord, Slack, Figma, etc.) via Chrome DevTools Protocol:

- **Auto-discovery** of CDP ports across running apps
- **Invisible input** — clicks and typing via JavaScript injection, not mouse/keyboard
- **Auto-restart** with user confirmation — proposes restart with `--remote-debugging-port`
- **Knowledge base** — remembers successful CDP configurations per app

### Adaptive UIA Tree

`get_ui_tree` auto-detects the UI framework per window and adjusts tree depth:

| Framework | Depth | Examples |
|-----------|-------|---------|
| WinUI3, UWP, Win32, WPF | 15 | Notepad, Explorer, Settings |
| WinForms | 12 | Legacy .NET apps |
| Chromium, Edge WebView2 | 8 | Chrome, Edge, Teams |
| Electron, CEF | 5 | VS Code, Discord, Slack |

---

## Security — Our #1 Differentiator

> **Built from commit #1. Not 34 patches later.**

| Layer | What it does |
|-------|-------------|
| **Kill Switch** | `Ctrl+Shift+Escape` stops ALL automation instantly |
| **Confirmation Mode** | Every action requires approval (default for new users) |
| **Blocked Apps** | Banking, password managers, auth apps — never accessed |
| **Blocked Commands** | `format`, `del /f`, `rm -rf`, `shutdown` — always blocked |
| **Data Sanitization** | Credit cards, SSNs, passwords — redacted before sending to AI |
| **Zero Telemetry** | No data ever leaves your machine. **Ever.** |
| **Encrypted Logs** | Full audit trail with AES-256 encryption |
| **Focus Guard** | Never steals your active window — save/restore on every tool call |
| **Rate Limiter** | Max 30 actions/minute, thread-safe |
| **Extension Sandbox** | Extensions declare permissions in manifest, enforced at runtime |

---

## vs Competition

| Feature | Windows-MCP | MCPControl | sbroenne | **Marlow** |
|---------|------------|-----------|---------|-----------|
| Desktop control | Yes | Yes | Yes | Yes |
| Tools | ~10 | ~8 | ~12 | **87** |
| Silent/background methods | No | No | No | **Yes** |
| CDP (Electron apps) | No | No | No | **Yes** |
| Kill switch | No | No | No | **Yes** |
| Data sanitization | No | No | No | **Yes** |
| Confirmation mode | No | No | No | **Yes** |
| Blocked apps list | No | No | No | **Yes** |
| Voice control + TTS | No | No | No | **Yes** |
| Workflow record/replay | No | No | No | **Yes** |
| Extension system | No | No | No | **Yes** |
| Telemetry | Unclear | No | No | **Never** |
| Encrypted logs | No | No | No | **Yes** |
| Spanish docs | No | No | No | **Yes** |

---

## Configuration

Marlow creates `~/.marlow/config.json` on first run:

```json
{
  "security": {
    "confirmation_mode": "all",
    "kill_switch_enabled": true,
    "max_actions_per_minute": 30
  },
  "automation": {
    "default_backend": "uia",
    "prefer_silent_methods": true,
    "agent_screen_only": true
  },
  "language": "auto"
}
```

### Confirmation Modes

| Mode | Behavior | Recommended for |
|------|----------|----------------|
| `all` | Every action needs approval | New users (default) |
| `sensitive` | Only destructive/sensitive actions | Regular users |
| `autonomous` | No confirmation needed | Power users |

---

## Development

```bash
git clone https://github.com/jarb02/marlow.git
cd marlow
pip install -e ".[audio,ocr]"
python -m pytest tests/ -v
```

---

## License

MIT — Free and open source. See [LICENSE](LICENSE).

---

## Security Policy

Found a vulnerability? Please report it responsibly. See [SECURITY.md](.github/SECURITY.md).

---

<a name="-español"></a>

## Español

### Marlow — AI que trabaja a tu lado, no en tu lugar.

Los servidores MCP de automatizacion para Windows existentes toman control de tu mouse y teclado. **Tu dejas de trabajar para que el AI trabaje.**

Marlow trabaja **en paralelo** contigo. Modo background en segundo monitor. Audio en tiempo real. Automatizacion CDP para apps Electron. Seguridad desde el primer commit. Y si, habla español.

### Instalacion rapida

```bash
pip install marlow-mcp

# Voice features (captura de audio, transcripcion Whisper)
pip install marlow-mcp[audio]
```

> **Control por voz:** Presiona `Ctrl+Shift+M` para hablar con Marlow. Pregunta *"Que puedes hacer?"* para empezar.

### 87 Herramientas MCP

Vision, captura, mouse, teclado, ventanas, sistema, seguridad, OCR, background dual-monitor, audio, voz, COM automation, visual diff, memoria persistente, clipboard, scraper, extensiones, watcher, scheduler, patrones adaptativos, workflows, auto-mejora, esperas inteligentes, CDP para apps Electron, y mas.

### Seguridad — Nuestro Diferenciador #1

- **Kill Switch:** `Ctrl+Shift+Escape` detiene TODO inmediatamente
- **Modo Confirmacion:** Cada accion requiere aprobacion (por defecto)
- **Apps Bloqueadas:** Bancos, gestores de contraseñas — nunca se acceden
- **Comandos Bloqueados:** Comandos destructivos siempre bloqueados
- **Sanitizacion de Datos:** Tarjetas de credito, SSN, contraseñas — redactados antes de enviar al AI
- **Cero Telemetria:** Tus datos nunca salen de tu maquina. **Nunca.**

### Issues en español?

Si! Los issues en español son bienvenidos. Usa la etiqueta `español` al crear tu issue.

---

<p align="center">
  <img src="https://raw.githubusercontent.com/jarb02/marlow/main/assets/logo.png" alt="Marlow" width="120">
  <br>
  <em>Marlow — Your friendly ghost in the machine</em>
</p>
