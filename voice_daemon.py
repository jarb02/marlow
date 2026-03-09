"""Marlow Voice Daemon — voice interaction via Bridge architecture.

Listens for wake word / push-to-talk, transcribes speech, executes goals
via HTTP daemon, responds via TTS. Uses the ConversationContext FSM for
multi-turn conversations with follow-up support.

Usage:
    python3 -c "from marlow.voice_daemon import main; main()"

/ Daemon de voz Marlow — interaccion por voz via Bridge architecture.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys

logger = logging.getLogger("marlow.voice_daemon")


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


async def run_voice_daemon():
    """Main entry point for the voice daemon."""
    # Add project to path
    project_dir = os.path.expanduser("~/marlow")
    if project_dir not in sys.path:
        sys.path.insert(0, project_dir)

    from marlow.bridges.voice.bridge import VoiceBridge
    from marlow.bridges.manager import BridgeManager
    from marlow.bridges.console.bridge import ConsoleBridge
    from marlow.platform.linux.tts import generate_clips

    # Load settings
    try:
        from marlow.core.settings import get_settings
        settings = get_settings()
        user_name = settings.user.name
        wake_word_enabled = settings.voice.wake_word
    except Exception:
        user_name = ""
        wake_word_enabled = True

    # Generate voice clips if needed
    generate_clips(user_name)

    # Setup bridges
    bridge_mgr = BridgeManager()
    voice_bridge = VoiceBridge()
    console_bridge = ConsoleBridge()
    bridge_mgr.register(voice_bridge)
    bridge_mgr.register(console_bridge)

    # Setup voice components
    info = voice_bridge.setup(wake_word_enabled=wake_word_enabled)

    print(f"Marlow Voice Daemon")
    print(f"  User: {user_name or '(not set)'}")
    print(f"  Mode: {info.get('mode', '?')}")
    print(f"  Wake word: {info.get('wake_word', 'disabled')}")
    print(f"  VAD: {info.get('vad', '?')}")
    print(f"  Hardware: {info.get('cores', '?')} cores, {info.get('ram_gb', '?')}GB RAM")
    print()

    # Notify via mako
    try:
        import subprocess
        subprocess.run(
            ["notify-send", "-a", "Marlow", "Marlow Voice",
             f"Modo: {info.get('mode', '?')}"],
            capture_output=True, timeout=3,
        )
    except Exception:
        pass

    # Goal callback for the voice bridge
    async def goal_callback(text: str, channel: str) -> dict:
        """Route goal to HTTP daemon."""
        return await _send_goal(text, channel)

    # Run the voice bridge main loop
    await voice_bridge.run(goal_callback)


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

    # Suppress noisy loggers
    logging.getLogger("onnxruntime").setLevel(logging.WARNING)

    try:
        asyncio.run(run_voice_daemon())
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
