# Marlow Agent — Instructions for Claude Code

## Architecture
- Python autonomous agent with 101 tools on Linux (Fedora 43 + Sway or Marlow Compositor)
- Kernel: HSM, GoalEngine (13 states), GOAP planner, LLM planner (Claude Sonnet), EventBus, ActionScorer
- Platform layer auto-detects: compositor backend (IPC) > Sway backend (i3ipc/wtype/grim) > Windows
- Daemon on localhost:8420 with HTTP API + WebSocket (POST /goal, GET /status, /health, /history, /ws, POST /tool, POST /transcript)
- Entry points: server_linux.py (MCP), daemon_linux.py (HTTP), autonomous_linux.py (CLI)

## Phase 9.5 COMPLETE: Gemini Unified Brain

ALL user interaction goes through Gemini as the single brain:
- **Text (sidebar, console, telegram):** GeminiTextBridge -> Gemini API (google-genai SDK, regular chat with function calling)
- **Voice:** GeminiLiveVoiceBridge -> Gemini Live API (bidirectional audio streaming + function calling)
- **Claude Opus:** Reserved ONLY for internal complex planning tasks (GoalEngine fallback)
- **GoalEngine:** Fallback path when Gemini unavailable (429 rate limit, no API key)

### Shared Tool Schema (bridges/tools_schema.py)
Single source of truth for both voice and text channels:
- `build_tool_declarations()` — 11 Gemini function declarations: launch_in_shadow, move_to_user, open_application, close_window, list_windows, focus_window, take_screenshot, run_command, type_text, press_key, hotkey
- `build_system_prompt(user_name, language)` — identical Marlow personality for all channels
- `resolve_tool_call(name, args)` — alias mapping: close_window->manage_window, minimize_window->manage_window, etc.
- `TOOL_ALIASES` dict

### GeminiTextBridge (bridges/gemini_text.py)
- Uses `google.genai.Client` with `aio.chats.create()` for multi-turn conversation
- 30-minute inactivity timeout resets chat session
- Function calling loop (max 8 rounds) with `resolve_tool_call()` for aliases
- Smart 429 handling: parses retry delay from API, returns friendly message if >10s
- `_compact_result()` truncates large tool results for Gemini
- `send_message(text) -> str` is the main entry point

### GeminiLiveVoiceBridge (bridges/voice/gemini_live.py)
- Bidirectional audio streaming via WebSocket
- Gemini handles VAD, speech recognition, conversation, and voice output
- Same tools and system prompt as text bridge (via tools_schema.py)
- Runs in separate voice_daemon.py process, executes tools via HTTP POST /tool on daemon

### Daemon Text Flow (daemon_linux.py)
1. `_process_text(goal_text, channel)` — unified entry for all text
2. Primary: `self._gemini_text.send_message()` with function calling
3. Fallback: `_handle_fallback()` -> GoalEngine + templates (if Gemini unavailable)
4. `_execute_tool_direct()` — tool executor callback for GeminiTextBridge
5. `_sanitize_error()` — converts raw GoalEngine errors to friendly Spanish
6. launch_in_shadow auto-falls back to open_application when compositor not running (Sway mode)

## Bridges Architecture
All interaction channels implement BridgeBase ABC in marlow/bridges/:
- **voice/bridge.py** — VoiceBridge: mic -> wake word/PTT -> VAD -> ASR -> goal -> TTS
- **voice/gemini_live.py** — GeminiLiveVoiceBridge: bidirectional audio + function calling
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
get_settings() singleton. Env vars override file values.
Sections: user, voice, tts, whisper, sidebar, telegram, privacy, gemini.
GeminiSettings: model (audio), text_model (text), api_key in secrets.toml.

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

## Known Issues

1. **Tool error messages when compositor down:** When running on Sway (no compositor), tools like list_windows return `{"success": true, "count": 0, "windows": []}` (compositor IPC returns 0 windows because socket not found) instead of explaining that the compositor is not running. Gemini sees "0 windows" and tells user there are no windows open, when actually Sway windows exist but the compositor backend can't see them. The platform auto-detection should fall back to Sway backend for window listing when compositor IPC fails.

2. **GoalEngine fallback raw errors (partially fixed):** When Gemini is unavailable (429 rate limit exhausted) and GoalEngine takes over, plan validation errors like "unknown tool wait_for_idle" were shown raw to the user. Fixed with `_sanitize_error()` in daemon_linux.py. Remaining issue: `p.waits` and `p.ocr` are None on the laptop (providers not initialized), so template plans referencing wait_for_idle/ocr_region always fail validation. Template planner should only generate steps using registered tools.
