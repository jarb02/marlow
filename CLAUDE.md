# Marlow Agent — Instructions for Claude Code

## Architecture
- Python autonomous agent with 101 tools on Linux (Fedora 43 + Sway or Marlow Compositor)
- Kernel: HSM, GoalEngine (13 states), GOAP planner, LLM planner (Claude Sonnet), EventBus, ActionScorer
- Platform layer auto-detects: compositor backend (IPC) > Sway backend (i3ipc/wtype/grim) > Windows
- Daemon on localhost:8420 with HTTP API (POST /goal, GET /status, /health, /history)
- Entry points: server_linux.py (MCP), daemon_linux.py (HTTP), autonomous_linux.py (CLI)

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
