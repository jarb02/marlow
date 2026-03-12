"""Marlow Voice Daemon — Gemini Live voice interaction.

Simple flow:
    Sidebar mic button ON  → opens Gemini Live session
    Sidebar mic button OFF → closes session

Audio bidirectional: mic → Gemini, Gemini → speaker.
Tool calls executed via HTTP POST to daemon.

Usage:
    python3 -c "from voice_daemon import main; main()"

/ Daemon de voz Marlow — audio bidireccional con Gemini Live.
"""

from __future__ import annotations

import argparse
import asyncio
import atexit
import logging
import os
import signal
import sys
import time

logger = logging.getLogger("marlow.voice_daemon")

STATE_FILE = "/tmp/marlow-voice-state"
TRIGGER_FILE = "/tmp/marlow-voice-trigger"


def _cleanup():
    """Emergency cleanup on exit or signal."""
    try:
        with open(STATE_FILE, "w") as f:
            f.write("idle")
    except Exception:
        pass
    try:
        os.unlink(TRIGGER_FILE)
    except FileNotFoundError:
        pass


atexit.register(_cleanup)
signal.signal(signal.SIGTERM, lambda *_: (_cleanup(), sys.exit(0)))


def _set_state(state: str):
    """Write voice state: 'active' or 'idle'."""
    try:
        with open(STATE_FILE, "w") as f:
            f.write(state)
    except Exception:
        pass


async def _execute_tool(tool_name: str, args: dict) -> dict:
    """Execute a tool via daemon HTTP /tool endpoint."""
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            resp = await session.post(
                "http://localhost:8420/tool",
                json={"tool": tool_name, "params": args},
                timeout=aiohttp.ClientTimeout(total=60),
            )
            return await resp.json()
    except Exception as e:
        logger.error("Tool execution failed (%s): %s", tool_name, e)
        return {"success": False, "error": str(e)}


def _get_gemini_api_key(settings) -> str:
    """Get Gemini API key from settings or environment."""
    key = settings.secrets.gemini_api_key
    if not key:
        key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        key = os.environ.get("MARLOW_GEMINI_API_KEY", "")
    return key


# ─────────────────────────────────────────────────────────────
# Gemini Live mode
# ─────────────────────────────────────────────────────────────


async def _wait_for_trigger() -> bool:
    """Block until sidebar writes 'press' to trigger file.

    Returns True when activation is requested.
    """
    while True:
        try:
            if os.path.exists(TRIGGER_FILE):
                with open(TRIGGER_FILE) as f:
                    state = f.read().strip()
                if state == "press":
                    os.unlink(TRIGGER_FILE)
                    return True
                elif state == "release":
                    os.unlink(TRIGGER_FILE)
        except Exception:
            pass
        await asyncio.sleep(0.2)


async def _watch_for_stop(bridge) -> None:
    """Watch for mic-off signal from sidebar."""
    # Wait for session to become active (set inside run_session)
    while not bridge.is_active:
        await asyncio.sleep(0.1)
    while bridge.is_active:
        try:
            if os.path.exists(TRIGGER_FILE):
                with open(TRIGGER_FILE) as f:
                    if f.read().strip() == "release":
                        os.unlink(TRIGGER_FILE)
                        bridge.stop()
                        logger.info("Session stopped by user")
                        return
        except Exception:
            pass
        await asyncio.sleep(0.2)


async def _run_gemini_mode(settings):
    """Main loop: wait for trigger → open session → repeat."""
    from marlow.bridges.voice.gemini_live import GeminiLiveVoiceBridge

    user_name = settings.user.name or ""
    language = settings.user.language
    gemini_key = _get_gemini_api_key(settings)
    model = settings.gemini.model
    voice = settings.gemini.voice

    logger.info(
        "Gemini Live mode ready: model=%s, user=%s, lang=%s",
        model, user_name, language,
    )

    _set_state("idle")

    while True:
        await _wait_for_trigger()
        logger.info("Session opening")

        bridge = GeminiLiveVoiceBridge(
            api_key=gemini_key,
            model=model,
            voice=voice,
            user_name=user_name,
            language=language,
        )

        _set_state("active")
        watcher = asyncio.create_task(_watch_for_stop(bridge))

        try:
            await bridge.run_session(_execute_tool)
        except Exception as e:
            logger.error("Session error: %s", e)
        finally:
            _set_state("idle")
            watcher.cancel()
            try:
                await watcher
            except asyncio.CancelledError:
                pass

        logger.info("Session closed")


# ─────────────────────────────────────────────────────────────
# Local mode (fallback — existing whisper + Piper pipeline)
# ─────────────────────────────────────────────────────────────

async def _send_goal(text: str, channel: str = "voice") -> dict:
    """Send goal to daemon via HTTP."""
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            resp = await session.post(
                "http://localhost:8420/goal",
                json={"goal": text, "channel": channel},
                timeout=aiohttp.ClientTimeout(total=120),
            )
            return await resp.json()
    except Exception as e:
        logger.error("Goal request failed: %s", e)
        return {"success": False, "errors": [str(e)]}


async def _run_local_mode(settings):
    """Run voice daemon in local mode (whisper + Piper)."""
    from marlow.bridges.voice.bridge import VoiceBridge
    from marlow.bridges.manager import BridgeManager
    from marlow.bridges.console.bridge import ConsoleBridge
    from marlow.platform.linux.tts import generate_clips

    user_name = settings.user.name or ""
    wake_word_enabled = settings.voice.wake_word

    generate_clips(user_name)

    bridge_mgr = BridgeManager()
    voice_bridge = VoiceBridge()
    console_bridge = ConsoleBridge()
    bridge_mgr.register(voice_bridge)
    bridge_mgr.register(console_bridge)

    info = voice_bridge.setup(wake_word_enabled=wake_word_enabled)

    logger.info("Local mode: %s", info)

    async def goal_callback(text: str, channel: str) -> dict:
        return await _send_goal(text, channel)

    await voice_bridge.run(goal_callback)


# ─────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────

async def run_voice_daemon():
    """Main entry point — choose engine and run."""
    project_dir = os.path.expanduser("~/marlow")
    if project_dir not in sys.path:
        sys.path.insert(0, project_dir)

    from marlow.core.settings import get_settings

    settings = get_settings()
    engine = settings.voice.engine
    gemini_key = _get_gemini_api_key(settings)

    use_gemini = False
    if engine == "gemini-live":
        use_gemini = bool(gemini_key)
        if not gemini_key:
            logger.warning("gemini-live requested but no API key, falling back to local")
    elif engine == "auto":
        use_gemini = bool(gemini_key)

    user_name = settings.user.name or "(not set)"
    mode = "gemini-live" if use_gemini else "local"

    print(f"Marlow Voice Daemon")
    print(f"  User: {user_name}")
    print(f"  Engine: {mode}")
    if use_gemini:
        print(f"  Model: {settings.gemini.model}")
        print(f"  Voice: {settings.gemini.voice or '(default)'}")
    print()

    if use_gemini:
        await _run_gemini_mode(settings)
    else:
        await _run_local_mode(settings)


def main():
    parser = argparse.ArgumentParser(description="Marlow Voice Daemon")
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("onnxruntime").setLevel(logging.WARNING)

    try:
        asyncio.run(run_voice_daemon())
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
