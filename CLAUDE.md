# Marlow Agent — Instructions for Claude Code

## Architecture
- Python autonomous agent with 101 tools on Linux (Fedora 43 + Sway or Marlow Compositor)
- Kernel: HSM, GoalEngine (13 states), GOAP planner, LLM planner (Claude Sonnet), EventBus, ActionScorer
- Platform layer auto-detects: compositor backend (IPC) > Sway backend (i3ipc/wtype/grim) > Windows
- Daemon on localhost:8420 with HTTP API + WebSocket (POST /goal, GET /status, /health, /history, /ws)
- Entry points: server_linux.py (MCP), daemon_linux.py (HTTP), autonomous_linux.py (CLI)

## Phase 9.5: Bridges Architecture
All interaction channels implement BridgeBase ABC in marlow/bridges/:
- **voice/bridge.py** — VoiceBridge: mic -> wake word/PTT -> VAD -> ASR -> goal -> TTS
- **sidebar/app.py** — GTK4+WebKit6 sidebar chat window, connects via HTTP+WebSocket
- **sidebar/onboarding.py** — First-boot wizard (name, API key, Telegram setup)
- **telegram/bridge.py** — TelegramBridge: text/voice messages -> goals -> bot responses
- **console/bridge.py** — ConsoleBridge: terminal output + mako notifications
- **manager.py** — BridgeManager routes responses to correct channel

### Conversation FSM (voice/conversation_state.py)
7 states: IDLE -> LISTENING -> PROCESSING -> RESPONDING -> FOLLOW_UP -> DISAMBIGUATING -> ERROR
Key feature: FOLLOW_UP mode allows conversation without wake word (30s timeout).

### Config (core/settings.py)
TOML-based: ~/.config/marlow/config.toml (settings) + secrets.toml (API keys, chmod 600).
get_settings() singleton. Env vars override file values. Sections: user, voice, tts, whisper, sidebar, telegram, privacy.

### TTS Chain (platform/linux/tts.py)
1. Piper es_MX-claude-high (offline, ~1.4s for long sentence)
2. edge-tts Jorge (online, better quality when internet available)
3. espeak-ng (emergency fallback)
Pre-generated clips in ~/.config/marlow/voice_clips/ for instant feedback.

### Wake Word (platform/linux/wake_word.py)
OpenWakeWord with hey_jarvis as phonetic proxy. Custom model at ~/.config/marlow/models/marlow.onnx.

### OCR Summary (kernel/ocr_summary.py)
Intent-aware LLM prompt for natural Spanish TTS-ready summaries of screen content.

## Development Rules
- Think through the problem before writing code. Understand root cause first.
- Never modify original Windows files (server.py, integration.py, world_state.py)
- All Linux code is parallel: server_linux.py, integration_linux.py, world_state_linux.py
- Platform providers use ABCs from platform/base.py — always implement the full interface
- Test with compositor running when possible (auto-detection picks compositor backend)

## Testing
- SSH: ssh josemarlow@192.168.5.107
- Project: ~/marlow, branch linux-mvp
- Daemon: python3 -c "from marlow.daemon_linux import main; main()"
- CLI: python3 ~/marlow-cli.py "goal text"
- Sway env vars needed for some tests (SWAYSOCK, WAYLAND_DISPLAY)

## Git
- Email: jarb02@users.noreply.github.com
- Never include Co-authored-by or Claude references
- Push to: git@github.com:jarb02/marlow.git (linux-mvp branch)

## StepContext (b7e4b07)
GoalEngine passes runtime values between steps using $variable references.

## Shadow Mode Interaction (Phase 7)
Two patterns: A (quick URL) and B (interactive) via launch_in_shadow.
Compositor input routing focuses target window_id on agent_seat before input.
