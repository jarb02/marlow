"""Marlow Voice Daemon — voice interaction via Bridge architecture.

Supports two engines:
- **gemini-live**: Streaming audio via Gemini Live API (default when API key present)
- **local**: Local pipeline (OpenWakeWord + whisper + Piper TTS)

Usage:
    python3 -c "from voice_daemon import main; main()"

/ Daemon de voz Marlow — interaccion por voz via Bridge architecture.
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


def _cleanup_voice_state():
    """Emergency cleanup -- runs on crash, exit, or signal."""
    try:
        _close_persistent_mic()
    except Exception:
        pass
    try:
        with open("/tmp/marlow-voice-state", "w") as f:
            f.write("idle")
    except Exception:
        pass
    try:
        os.unlink("/tmp/marlow-voice-trigger")
    except FileNotFoundError:
        pass


atexit.register(_cleanup_voice_state)
signal.signal(signal.SIGTERM, lambda *_: (_cleanup_voice_state(), sys.exit(0)))


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


async def _execute_tool(tool_name: str, args: dict) -> dict:
    """Execute a single tool via daemon HTTP /tool endpoint."""
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


async def _post_transcript(role: str, text: str):
    """Post a transcript entry to the daemon for sidebar display."""
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(
                "http://localhost:8420/transcript",
                json={"role": role, "text": text},
                timeout=aiohttp.ClientTimeout(total=5),
            )
    except Exception:
        pass  # Non-critical


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

STATE_FILE = "/tmp/marlow-voice-state"
TEXT_FILE = "/tmp/marlow-voice-text"


def _set_voice_state(state: str):
    """Write voice state for other processes (daemon, sidebar)."""
    try:
        with open(STATE_FILE, "w") as f:
            f.write(state)
    except Exception:
        pass


async def _run_gemini_mode(settings):
    """Run voice daemon in Gemini Live mode.

    Loop: wait for activation -> open Gemini session -> conversation -> repeat.
    """
    from marlow.bridges.voice.gemini_live import GeminiLiveVoiceBridge
    from marlow.platform.linux.tts import generate_clips

    user_name = settings.user.name or ""
    language = settings.user.language
    gemini_key = _get_gemini_api_key(settings)
    model = settings.gemini.model
    voice = settings.gemini.voice

    generate_clips(user_name)

    # Setup wake word (local, for activation only)
    wake_word = None
    if settings.voice.wake_word:
        try:
            from marlow.platform.linux.wake_word import WakeWordListener
            wake_word = WakeWordListener()
            if not wake_word.setup():
                wake_word = None
        except ImportError:
            pass

    logger.info(
        "Gemini Live mode: model=%s, wake_word=%s, user=%s, lang=%s",
        model, wake_word.model_name if wake_word else "disabled",
        user_name, language,
    )

    boot_time = time.monotonic()
    running = True
    _set_voice_state("idle")

    while running:
        # Wait for activation (NO local TTS — Gemini will greet naturally)
        activated = await _wait_for_activation_silent(
            wake_word, boot_time,
        )
        if not activated:
            continue

        # Signal "activating" so sidebar keeps button active during connect
        _set_voice_state("activating")

        # Open Gemini session
        bridge = GeminiLiveVoiceBridge(
            api_key=gemini_key,
            model=model,
            voice=voice,
            user_name=user_name,
            language=language,
            on_transcript=_post_transcript,
        )

        _set_voice_state("gemini-active")

        # Monitor trigger file + text injection file
        async def _monitor():
            trigger = "/tmp/marlow-voice-trigger"
            while bridge.is_active:
                # Check mic off
                try:
                    if os.path.exists(trigger):
                        with open(trigger) as f:
                            state = f.read().strip()
                        if state == "release":
                            os.unlink(trigger)
                            bridge.stop()
                            logger.info("Session stopped by sidebar mic button")
                            return
                except Exception:
                    pass

                # Check text injection from sidebar
                try:
                    if os.path.exists(TEXT_FILE):
                        with open(TEXT_FILE) as f:
                            text = f.read().strip()
                        os.unlink(TEXT_FILE)
                        if text:
                            logger.info("Text injection: %s", text[:60])
                            await bridge.send_text(text)
                except Exception:
                    pass

                await asyncio.sleep(0.1)

        monitor = asyncio.create_task(_monitor())

        try:
            await bridge.run_session(_execute_tool)
        except Exception as e:
            logger.error("Gemini session error: %s", e)
        finally:
            _set_voice_state("idle")
            monitor.cancel()
            try:
                await monitor
            except asyncio.CancelledError:
                pass
            # Clean up text file
            try:
                os.unlink(TEXT_FILE)
            except FileNotFoundError:
                pass

        logger.info("Session ended, returning to wake word listening")


# ─────────────────────────────────────────────────────────────
# Persistent mic for wake word detection (avoids ALSA log spam)
# ─────────────────────────────────────────────────────────────

_persistent_pya = None
_persistent_mic_stream = None


def _get_persistent_mic(frames_per_buffer: int):
    """Get or create a persistent PyAudio mic stream for wake word detection.

    Returns the stream, or None if mic unavailable.
    """
    global _persistent_pya, _persistent_mic_stream
    if _persistent_mic_stream is not None:
        return _persistent_mic_stream

    import pyaudio
    try:
        _persistent_pya = pyaudio.PyAudio()
        mic_info = _persistent_pya.get_default_input_device_info()
        _persistent_mic_stream = _persistent_pya.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=16000,
            input=True,
            input_device_index=int(mic_info["index"]),
            frames_per_buffer=frames_per_buffer,
        )
        logger.info("Persistent mic stream opened (device=%s)", mic_info.get("name", "?"))
        return _persistent_mic_stream
    except Exception as e:
        logger.warning("Failed to open persistent mic: %s", e)
        _close_persistent_mic()
        return None


def _close_persistent_mic():
    """Close persistent PyAudio mic stream."""
    global _persistent_pya, _persistent_mic_stream
    if _persistent_mic_stream:
        try:
            _persistent_mic_stream.stop_stream()
            _persistent_mic_stream.close()
        except Exception:
            pass
        _persistent_mic_stream = None
    if _persistent_pya:
        try:
            _persistent_pya.terminate()
        except Exception:
            pass
        _persistent_pya = None


async def _wait_for_activation_silent(wake_word, boot_time) -> bool:
    """Wait for activation without playing local TTS clips.

    For Gemini mode: Gemini itself will greet the user once the session opens.
    No local TTS = no voice mismatch.

    Uses persistent PyAudio/mic stream (initialized on first call, closed on
    activation or shutdown) to avoid ALSA log spam from repeated open/close.
    """
    import numpy as np

    trigger = "/tmp/marlow-voice-trigger"

    # Check trigger file first
    if os.path.exists(trigger):
        try:
            with open(trigger) as f:
                state = f.read().strip()
            if state == "press":
                os.unlink(trigger)
                # Close persistent mic before Gemini session opens its own
                _close_persistent_mic()
                return True
            elif state == "release":
                os.unlink(trigger)
        except Exception:
            pass

    # Grace period (10s after boot)
    if time.monotonic() - boot_time < 10:
        await asyncio.sleep(0.1)
        return False

    # Wake word detection (persistent mic — no ALSA spam)
    if wake_word and wake_word.available:
        from marlow.platform.linux.wake_word import WAKEWORD_CHUNK_SAMPLES

        stream = _get_persistent_mic(WAKEWORD_CHUNK_SAMPLES)
        if stream is None:
            await asyncio.sleep(0.1)
            return False

        try:
            loop = asyncio.get_event_loop()
            chunk_bytes = await loop.run_in_executor(
                None, stream.read, WAKEWORD_CHUNK_SAMPLES, False,
            )
            chunk = np.frombuffer(chunk_bytes, dtype=np.int16)

            if wake_word.process_chunk(chunk):
                _close_persistent_mic()
                return True
        except Exception:
            # Mic error — close and retry next iteration
            _close_persistent_mic()
    else:
        # No wake word — poll trigger file only
        await asyncio.sleep(0.05)

    return False


async def _wait_for_activation(wake_word, boot_time, play_clip) -> bool:
    """Wait for activation WITH local TTS clips (for local mode).

    Uses persistent PyAudio/mic stream to avoid ALSA log spam.
    """
    import numpy as np

    trigger = "/tmp/marlow-voice-trigger"

    if os.path.exists(trigger):
        try:
            with open(trigger) as f:
                state = f.read().strip()
            if state == "press":
                os.unlink(trigger)
                _close_persistent_mic()
                await play_clip("en_que_te_ayudo")
                return True
            elif state == "release":
                os.unlink(trigger)
        except Exception:
            pass

    if time.monotonic() - boot_time < 10:
        await asyncio.sleep(0.1)
        return False

    if wake_word and wake_word.available:
        from marlow.platform.linux.wake_word import WAKEWORD_CHUNK_SAMPLES

        stream = _get_persistent_mic(WAKEWORD_CHUNK_SAMPLES)
        if stream is None:
            await asyncio.sleep(0.1)
            return False

        try:
            loop = asyncio.get_event_loop()
            chunk_bytes = await loop.run_in_executor(
                None, stream.read, WAKEWORD_CHUNK_SAMPLES, False,
            )
            chunk = np.frombuffer(chunk_bytes, dtype=np.int16)

            if wake_word.process_chunk(chunk):
                _close_persistent_mic()
                await play_clip("si")
                return True
        except Exception:
            _close_persistent_mic()
    else:
        await asyncio.sleep(0.05)

    return False


# ─────────────────────────────────────────────────────────────
# Local mode (fallback — existing whisper + Piper pipeline)
# ─────────────────────────────────────────────────────────────

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

    # Engine selection
    use_gemini = False
    if engine == "gemini-live":
        use_gemini = bool(gemini_key)
        if not gemini_key:
            logger.warning("gemini-live requested but no API key found, falling back to local")
    elif engine == "auto":
        use_gemini = bool(gemini_key)
    # engine == "local" -> use_gemini stays False

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
