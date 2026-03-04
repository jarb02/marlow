# MARLOW

**Autonomous Windows Desktop Agent**

*AI that works beside you, not instead of you*

<p align="center">
  <a href="https://pypi.org/project/marlow-mcp/"><img src="https://img.shields.io/pypi/v/marlow-mcp?color=blue" alt="PyPI version"></a>
  <a href="https://pypi.org/project/marlow-mcp/"><img src="https://img.shields.io/pypi/pyversions/marlow-mcp" alt="Python versions"></a>
  <a href="https://github.com/jarb02/marlow/blob/main/LICENSE"><img src="https://img.shields.io/github/license/jarb02/marlow" alt="License"></a>
  <a href="https://github.com/jarb02/marlow"><img src="https://img.shields.io/badge/platform-Windows-0078D6" alt="Platform"></a>
</p>

> :book: [Version en espanol / Spanish version](README.es.md)

An autonomous agent that sees, understands, and operates any Windows application. Built on the Model Context Protocol (MCP), Marlow turns natural language goals into real desktop actions — without stealing your mouse or keyboard.

> **Active development project.** Marlow is ambitious and far from perfect. Some things work incredibly well, others are still finding their way. If you like the idea of a desktop agent that learns from its mistakes and plans like a video game character, you're in the right place.

---

## What it can do today

- **96 MCP tools** to control any Windows app
- **3-tier autonomous planning** — regex templates, GOAP with A* search, LLM planner
- **Silent methods** — UIA patterns, CDP for Electron, invisible COM. Never steals focus
- **Listens and speaks** — Neural VAD (Silero), TTS with Mexican Spanish voice (Jorge), ASR with Whisper + GPU auto-detect
- **Learns from every action** — EMA per app, adaptive timings, reliability tracking, error journal
- **P0-P4 interrupt system** inspired by real-time strategy games
- **EventBus** with 17 typed events and circuit breakers
- **Sensor Fusion** — UIA, OCR, CDP, and vision detections unified with IoU merge
- **Security from commit #1** — kill switch, blocked apps, prompt injection defense, dual plan review
- **Connects to Claude Desktop, VS Code, or any MCP client**
- **Zero telemetry** — everything stays on your machine

---

## How it works

Marlow has 4 layers, from concrete to abstract:

**1. MCP Tools (96 tools)** — Atomic actions: click, type, screenshot, OCR, CDP, audio, COM, watchers, schedulers. Each returns success or error, never crashes.

**2. Smart escalation** — Cascade from cheap to expensive: UIA tree (0 tokens, ~10ms) → OCR (0 tokens, ~200ms) → Screenshot + LLM (~1,500 tokens, last resort). If the cheap method works, the expensive one never runs.

**3. Autonomous kernel** — Hierarchical state machine (HSM) with 8 states, GoalEngine with 13 states and automatic replan, GOAP planner with A* search, PlanValidator with 3 validation layers. The kernel decides what to do, when to pause, and when to replan.

**4. Learning** — ReliabilityTracker (EMA per app), AdaptiveWaits (self-adjusting timings), Blackboard (shared state between components), DesktopWeather (desktop trends in 4 climates), PlanGranularity (reliable apps run fast, fragile apps verify more).

---

## Game AI patterns

Marlow uses proven Game AI patterns. This isn't marketing — these techniques have been working in AAA games for 20 years:

| Pattern | Origin | In Marlow |
|---------|--------|-----------|
| **Utility AI** | F.E.A.R. (2005) | `PreActionScorer` — evaluates each action with weights: reliability, urgency, relevance, cost |
| **GOAP** | F.E.A.R. (2005) | `GOAPPlanner` — 16 actions with preconditions/effects, A* search for the cheapest plan |
| **AI Director** | Left 4 Dead (2008) | `AdaptiveWaits` + `DesktopWeather` — adjusts difficulty and speed based on desktop state |
| **Blackboard** | Halo (2001) | `Blackboard` — centralized store where all components share state |
| **Priority Interrupts** | RTS games | `InterruptManager` — 5 priority levels with LIFO suspend/resume |

---

## Installation

```bash
pip install marlow-mcp
```

Add to your MCP client config:

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

> **Voice control:** Press `Ctrl+Shift+M` to talk to Marlow. Ask *"What can you do?"* to get started.

> **Note:** The `keyboard` library requires **administrator privileges** on Windows for global hotkeys (kill switch `Ctrl+Shift+Escape`, voice hotkey `Ctrl+Shift+M`).

### Optional extras

```bash
# OCR with Tesseract (requires installing the binary separately)
pip install marlow-mcp[ocr]
```

Audio, voice, and TTS are included in the main installation. `torch` is optional (~2GB) — if not installed, VAD falls back to RMS detection.

---

## Security

> Built from the first commit. Not 34 patches later.

| Layer | What it does |
|-------|-------------|
| **Kill Switch** | `Ctrl+Shift+Escape` stops EVERYTHING instantly |
| **Confirmation** | Every action requires approval (default for new users) |
| **Blocked apps** | Banks, password managers, authenticators — never accessed |
| **Blocked commands** | `format`, `del /f`, `rm -rf`, `shutdown`, `reg delete` — always blocked |
| **Sanitization** | Credit cards, SSN, passwords — redacted before reaching the LLM |
| **Anti prompt-injection** | 21 patterns detected, spotlighting, automatic neutralization |
| **Dual plan review** | Dangerous plans are blocked before execution |
| **Zero telemetry** | Your data never leaves your machine. **Ever.** |
| **Encrypted logs** | Complete audit trail with AES-256 |
| **Focus Guard** | Never steals your active window — save/restore on every tool call |

---

## Project status

| Metric | Value |
|--------|-------|
| MCP tools | 96 |
| Tests passing | 878 |
| Phases completed | 8 of 8 (Master Plan v7) |
| Python | 3.10+ (development on 3.14) |
| MCP SDK | v1.26.0 |
| Platform | Windows 11, dual monitor |

### Completed phases

| Phase | Name |
|-------|------|
| 1 | Perception: WindowTracker, DialogType, AppAwareness |
| 2 | Audio: Silero VAD, Piper TTS, GPU detect, Jorge voice |
| 3 | Game AI-A: PreActionScorer, InterruptManager, AdaptiveWaits |
| 4 | EventBus: 17 typed events, pub/sub, circuit breakers |
| 5 | Planning: GOAP A*, DesktopWeather, 3-tier planning |
| 6 | Security: injection defense, dual safety review |
| 7 | Learning: Blackboard, adaptive plan granularity |
| 8 | AI Vision: Sensor Fusion, Vision Pipeline |

---

## What's next

- **Shadow Mode** — Invisible Virtual Desktops + SendMessage + PrintWindow. The agent works on a desktop you can't see
- **Distributed training** — 3 nodes (workstation + laptop + future cloud) syncing knowledge
- **Real-data calibration** — Tune all thresholds with real usage data, not intuition
- **GPU acceleration** — Whisper on GPU, local VLM for vision, Moonshine for streaming ASR
- **PyPI release v0.20.0** — First public release of the complete agent

---

## Development

```bash
git clone https://github.com/jarb02/marlow.git
cd marlow
pip install -e ".[ocr]"
python -m pytest tests/ -v
```

---

## Contributing

Marlow is a personal project that turned into something bigger than expected. If you're interested:

- **Test and report bugs** — [Open an issue](https://github.com/jarb02/marlow/issues). Issues in Spanish are welcome
- **Share ideas** — If you have an idea for a Game AI pattern, a use case, or an improvement, I'd love to hear it
- **Use Marlow** — The best way to contribute is to use it and tell me what worked and what didn't

---

## License

MIT — Free and open source. See [LICENSE](LICENSE).

---

## Security Policy

Found a vulnerability? Please report it responsibly. See [SECURITY.md](.github/SECURITY.md).

---

<p align="center">
  <em>Marlow — Your friendly ghost in the machine</em>
</p>
