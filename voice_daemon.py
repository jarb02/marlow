"""Marlow Voice Daemon — listens for voice commands and executes goals.

Separate process, communicates with the HTTP daemon at localhost:8420.
Adaptive: auto-selects ASR model, VAD backend, TTS based on hardware.

Usage:
    python3 -m marlow.voice_daemon
    python3 -m marlow.voice_daemon --push-to-talk   (Super+V mode)
    python3 -m marlow.voice_daemon --always-listen   (continuous VAD)

/ Daemon de voz Marlow — escucha comandos y ejecuta goals via HTTP.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import struct
import subprocess
import sys
import time

import numpy as np

logger = logging.getLogger("marlow.voice_daemon")

# Mic capture settings
SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_DURATION_MS = 30  # 30ms chunks for webrtcvad compatibility
CHUNK_SAMPLES = SAMPLE_RATE * CHUNK_DURATION_MS // 1000
CHUNK_BYTES = CHUNK_SAMPLES * 2  # 16-bit mono


class VoiceDaemon:
    """Voice input → ASR → GoalEngine → TTS output."""

    def __init__(self, mode: str = "always-listen"):
        self.mode = mode
        self.running = False
        self._asr_model = None
        self._caps = None
        self._vad = None
        self._pyaudio = None
        self._stream = None

    def _get_caps(self):
        if self._caps is None:
            from marlow.platform.linux.voice_capabilities import get_voice_capabilities
            self._caps = get_voice_capabilities()
        return self._caps

    def _ensure_asr(self):
        """Lazy-load whisper model on first use."""
        if self._asr_model is not None:
            return
        from faster_whisper import WhisperModel

        caps = self._get_caps()
        model_name = caps.recommended_whisper_model()
        compute = caps.recommended_compute_type()
        device = "cuda" if caps.has_gpu else "cpu"

        logger.info("Loading whisper: model=%s, device=%s, compute=%s",
                     model_name, device, compute)
        start = time.monotonic()
        self._asr_model = WhisperModel(
            model_name, device=device, compute_type=compute,
        )
        elapsed = time.monotonic() - start
        logger.info("Whisper loaded in %.1fs", elapsed)

    def _ensure_vad(self):
        if self._vad is None:
            from marlow.platform.linux.vad import VoiceActivityDetector
            self._vad = VoiceActivityDetector(backend="auto")
        return self._vad

    def _open_mic(self):
        """Open PyAudio mic stream."""
        if self._stream is not None:
            return
        import pyaudio
        self._pyaudio = pyaudio.PyAudio()
        self._stream = self._pyaudio.open(
            format=pyaudio.paInt16,
            channels=CHANNELS,
            rate=SAMPLE_RATE,
            input=True,
            frames_per_buffer=CHUNK_SAMPLES,
        )
        logger.info("Mic stream opened: %dHz, %dch, %dms chunks",
                     SAMPLE_RATE, CHANNELS, CHUNK_DURATION_MS)

    def _close_mic(self):
        if self._stream:
            self._stream.stop_stream()
            self._stream.close()
            self._stream = None
        if self._pyaudio:
            self._pyaudio.terminate()
            self._pyaudio = None

    # Common whisper hallucinations (garbage audio → phantom text)
    _HALLUCINATIONS = {
        "suscríbete", "subtítulos", "amara.org", "subtitulado por",
        "gracias por ver", "thanks for watching", "subscribe",
        "like and subscribe", "subtítulos por la comunidad",
        "música", "aplausos", "risas",
    }

    async def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe audio array to text."""
        self._ensure_asr()
        loop = asyncio.get_event_loop()

        def _run():
            segments, info = self._asr_model.transcribe(
                audio, language="es", beam_size=5, vad_filter=True,
            )
            text = " ".join(s.text for s in segments).strip()

            # Filter hallucinations
            lower = text.lower()
            for halluc in VoiceDaemon._HALLUCINATIONS:
                if halluc in lower:
                    logger.warning("Filtered hallucination: '%s'", text)
                    return ""
            return text

        start = time.monotonic()
        text = await loop.run_in_executor(None, _run)
        elapsed = time.monotonic() - start
        logger.info("Transcribed in %.1fs: '%s'", elapsed, text)
        return text

    async def speak(self, text: str):
        """Speak text via TTS (Piper primary, edge-tts fallback)."""
        from marlow.platform.linux.tts import speak as tts_speak
        try:
            result = await tts_speak(text=text, language="es")
            if "error" in result:
                logger.warning("TTS error: %s", result["error"])
        except Exception as e:
            logger.warning("TTS failed: %s", e)

    async def send_goal(self, text: str) -> dict:
        """Send goal to daemon via HTTP."""
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                resp = await session.post(
                    "http://localhost:8420/goal",
                    json={"text": text},
                    timeout=aiohttp.ClientTimeout(total=120),
                )
                return await resp.json()
        except Exception as e:
            logger.error("Goal request failed: %s", e)
            return {"error": str(e)}

    async def capture_utterance(self) -> np.ndarray | None:
        """Capture mic audio until VAD says utterance is complete."""
        self._open_mic()
        vad = self._ensure_vad()
        vad.reset()

        loop = asyncio.get_event_loop()
        max_duration = 30.0  # max 30 seconds per utterance
        start = time.monotonic()

        while time.monotonic() - start < max_duration:
            # Read chunk in executor to not block async loop
            chunk = await loop.run_in_executor(
                None, self._stream.read, CHUNK_SAMPLES, False,
            )
            state = vad.process_chunk(chunk)

            if state == "utterance_complete":
                audio = vad.get_utterance()
                vad.reset()
                if len(audio) > SAMPLE_RATE * 0.3:  # at least 300ms
                    return audio
                logger.debug("Utterance too short (%.1fs), ignoring",
                             len(audio) / SAMPLE_RATE)
                continue  # keep listening

        # Timeout — return whatever we have if speaking
        if vad._is_speaking:
            audio = vad.get_utterance()
            vad.reset()
            if len(audio) > SAMPLE_RATE * 0.5:
                return audio

        vad.reset()
        return None

    async def run(self):
        """Main voice loop."""
        self.running = True
        caps = self._get_caps()

        print(f"Marlow Voice Daemon")
        print(f"  Hardware: {caps.cpu_cores} cores, {caps.ram_gb}GB RAM, "
              f"GPU: {caps.has_gpu}, mic: {caps.has_mic}")
        print(f"  ASR: {caps.recommended_whisper_model()} "
              f"({caps.recommended_compute_type()})")
        print(f"  VAD: {caps.recommended_vad()}")
        print(f"  TTS: {caps.recommended_tts()}")
        print(f"  Mode: {self.mode}")
        print()

        if not caps.has_mic:
            print("WARNING: No microphone detected. Voice input may not work.")

        # Notify via mako if available
        try:
            subprocess.run(
                ["notify-send", "-a", "Marlow", "Marlow Voice", "Listening..."],
                capture_output=True, timeout=3,
            )
        except Exception:
            pass

        if self.mode == "push-to-talk":
            await self._run_push_to_talk()
        else:
            await self._run_always_listen()

    async def _run_always_listen(self):
        """Continuous VAD: listen for speech, transcribe, execute."""
        print("Listening... (speak to activate, Ctrl+C to quit)")

        while self.running:
            try:
                audio = await self.capture_utterance()
                if audio is None:
                    continue

                text = await self.transcribe(audio)
                if not text or len(text.strip()) < 3:
                    continue

                print(f"\n  Heard: {text}")
                await self.speak("Entendido")

                result = await self.send_goal(text)
                status = result.get("status", "unknown")
                summary = result.get("summary", result.get("error", "Listo"))

                print(f"  Result: {status}")
                if status == "error":
                    await self.speak(f"Error: {summary}")
                else:
                    await self.speak(summary if len(summary) < 200 else "Listo")

            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error("Voice loop error: %s", e)
                await asyncio.sleep(1)

        self._close_mic()
        print("\nVoice daemon stopped.")

    async def _run_push_to_talk(self):
        """Push-to-talk via Super+V: press to start, release to stop."""
        try:
            from marlow.platform.linux.voice_hotkey import PushToTalkListener
        except ImportError as e:
            print(f"Push-to-talk not available: {e}")
            print("Falling back to always-listen mode.")
            await self._run_always_listen()
            return

        listener = PushToTalkListener()
        print("Push-to-talk ready. Press Super+V to speak, Ctrl+C to quit.")

        loop = asyncio.get_event_loop()

        while self.running:
            try:
                # Wait for key press in executor
                pressed = await loop.run_in_executor(None, listener.wait_for_press)
                if not pressed:
                    continue

                print("\n  Recording... (release Super+V to stop)")
                self._open_mic()
                chunks: list[bytes] = []
                start = time.monotonic()

                # Record until key release or max 30s
                while time.monotonic() - start < 30.0:
                    is_held = listener.is_held()
                    if not is_held:
                        break
                    chunk = await loop.run_in_executor(
                        None, self._stream.read, CHUNK_SAMPLES, False,
                    )
                    chunks.append(chunk)

                if not chunks:
                    continue

                raw = b"".join(chunks)
                n_samples = len(raw) // 2
                samples = struct.unpack(f"<{n_samples}h", raw)
                audio = np.array(samples, dtype=np.float32) / 32768.0

                duration = len(audio) / SAMPLE_RATE
                print(f"  Captured {duration:.1f}s")

                if duration < 0.3:
                    print("  Too short, ignoring.")
                    continue

                text = await self.transcribe(audio)
                if not text or len(text.strip()) < 3:
                    print("  No speech detected.")
                    continue

                print(f"  Heard: {text}")
                await self.speak("Entendido")

                result = await self.send_goal(text)
                status = result.get("status", "unknown")
                summary = result.get("summary", result.get("error", "Listo"))
                print(f"  Result: {status}")

                if status != "error":
                    await self.speak(summary if len(summary) < 200 else "Listo")
                else:
                    await self.speak(f"Error: {summary}")

            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error("Push-to-talk error: %s", e)
                await asyncio.sleep(0.5)

        listener.close()
        self._close_mic()
        print("\nVoice daemon stopped.")


def main():
    parser = argparse.ArgumentParser(description="Marlow Voice Daemon")
    parser.add_argument(
        "--push-to-talk", action="store_true",
        help="Use Super+V push-to-talk instead of continuous listening",
    )
    parser.add_argument(
        "--always-listen", action="store_true", default=True,
        help="Continuously listen via VAD (default)",
    )
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

    mode = "push-to-talk" if args.push_to_talk else "always-listen"
    daemon = VoiceDaemon(mode=mode)
    try:
        asyncio.run(daemon.run())
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
