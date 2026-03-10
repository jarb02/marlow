"""Voice bridge — mic -> ASR -> goal, result -> TTS.

Implements BridgeBase for voice interaction. Manages the conversation
FSM, wake word detection, VAD, ASR, and TTS output.

/ Bridge de voz — mic -> ASR -> goal, resultado -> TTS.
"""

from __future__ import annotations

import asyncio
import logging
import struct
import time
from typing import Optional

import numpy as np

from marlow.bridges.base import BridgeBase
from marlow.bridges.voice.conversation_state import (
    ConversationContext,
    ConversationState,
    classify_intent_type,
    get_feedback_phrase,
)

logger = logging.getLogger("marlow.bridges.voice")

SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_DURATION_MS = 30
CHUNK_SAMPLES = SAMPLE_RATE * CHUNK_DURATION_MS // 1000


class VoiceBridge(BridgeBase):
    """Voice interaction bridge: mic input + TTS output."""

    @property
    def channel_name(self) -> str:
        return "voice"

    def __init__(self):
        self.context = ConversationContext()
        self._wake_word = None
        self._vad = None
        self._asr_model = None
        self._pyaudio = None
        self._stream = None
        self._caps = None
        self._running = False
        self._progress_cooldown = 0.0

    # ── BridgeBase implementation ──

    async def send_text(self, text: str, **kwargs):
        """Speak text via TTS."""
        from marlow.platform.linux.tts import speak
        await speak(text)

    async def send_file(self, file_path: str, caption: str = "", **kwargs):
        """Announce file via TTS (voice can't send files)."""
        await self.send_text(caption or f"Archivo listo: {file_path}")

    async def send_photo(self, image_bytes: bytes, caption: str = "", **kwargs):
        """Announce photo via TTS."""
        await self.send_text(caption or "Captura lista")

    async def notify(self, message: str, level: str = "info", **kwargs):
        """Speak notification if important, otherwise log."""
        if level in ("error", "warning"):
            await self.send_text(message)
        else:
            logger.info("Voice notification: %s", message)

    async def ask(self, question: str, options: Optional[list[str]] = None, **kwargs) -> str:
        """Ask via TTS, listen for response."""
        await self.send_text(question)
        self.context.state = ConversationState.FOLLOW_UP
        self.context.touch()

        audio = await self._capture_utterance()
        if audio is None:
            return ""

        text = await self._transcribe(audio)
        return text or ""

    # ── Setup ──

    def _get_caps(self):
        if self._caps is None:
            from marlow.platform.linux.voice_capabilities import get_voice_capabilities
            self._caps = get_voice_capabilities()
        return self._caps

    def setup(self, wake_word_enabled: bool = True) -> dict:
        """Initialize all voice components."""
        caps = self._get_caps()
        info = {
            "cores": caps.cpu_cores,
            "ram_gb": caps.ram_gb,
            "gpu": caps.has_gpu,
            "mic": caps.has_mic,
        }

        # Load user name from settings
        try:
            from marlow.core.settings import get_settings
            self.context.user_name = get_settings().user.name
        except Exception:
            pass

        # Setup wake word
        if wake_word_enabled:
            try:
                from marlow.platform.linux.wake_word import WakeWordListener
                self._wake_word = WakeWordListener()
                if self._wake_word.setup():
                    info["wake_word"] = self._wake_word.model_name
                else:
                    self._wake_word = None
                    info["wake_word"] = "unavailable"
            except ImportError:
                info["wake_word"] = "not installed"

        # Setup VAD
        try:
            from marlow.platform.linux.vad import VoiceActivityDetector
            self._vad = VoiceActivityDetector(backend="auto")
            info["vad"] = "ready"
        except Exception as e:
            info["vad"] = f"failed: {e}"

        info["mode"] = "wake-word" if self._wake_word else "push-to-talk"
        logger.info("VoiceBridge setup: %s", info)
        return info

    # ── Mic ──

    def _open_mic(self):
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

    def _close_mic(self):
        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._pyaudio:
            try:
                self._pyaudio.terminate()
            except Exception:
                pass
            self._pyaudio = None

    # ── ASR ──

    def _ensure_asr(self):
        if self._asr_model is not None:
            return
        from faster_whisper import WhisperModel
        caps = self._get_caps()
        model_name = caps.recommended_whisper_model()
        compute = caps.recommended_compute_type()
        device = "cuda" if caps.has_gpu else "cpu"
        logger.info("Loading whisper: %s/%s/%s", model_name, device, compute)
        start = time.monotonic()
        self._asr_model = WhisperModel(model_name, device=device, compute_type=compute)
        logger.info("Whisper loaded in %.1fs", time.monotonic() - start)

    _HALLUCINATIONS = {
        "suscribete", "subtitulos", "amara.org", "subtitulado por",
        "gracias por ver", "thanks for watching", "subscribe",
        "like and subscribe", "musica", "aplausos", "risas",
    }

    async def _transcribe(self, audio: np.ndarray) -> str:
        """Transcribe audio to text."""
        self._ensure_asr()
        loop = asyncio.get_event_loop()

        def _run():
            segments, info = self._asr_model.transcribe(
                audio, language="es", beam_size=5, vad_filter=True,
                initial_prompt="Marlow, busca, abre, cierra, muestra, clima, archivo, pantalla",
            )
            text = " ".join(s.text for s in segments).strip()
            lower = text.lower()
            for h in VoiceBridge._HALLUCINATIONS:
                if h in lower:
                    logger.warning("Filtered hallucination: '%s'", text)
                    return ""
            return _correct_transcription(text)

        text = await loop.run_in_executor(None, _run)
        return text

    # ── Capture ──

    async def _capture_utterance(self) -> Optional[np.ndarray]:
        """Capture speech via VAD until utterance complete."""
        self._open_mic()
        if not self._vad:
            return None

        self._vad.reset()
        loop = asyncio.get_event_loop()
        max_duration = 30.0
        start = time.monotonic()

        while time.monotonic() - start < max_duration:
            chunk = await loop.run_in_executor(
                None, self._stream.read, CHUNK_SAMPLES, False,
            )
            state = self._vad.process_chunk(chunk)

            if state == "utterance_complete":
                audio = self._vad.get_utterance()
                self._vad.reset()
                if len(audio) > SAMPLE_RATE * 0.3:
                    return audio
                continue

        if self._vad._is_speaking:
            audio = self._vad.get_utterance()
            self._vad.reset()
            if len(audio) > SAMPLE_RATE * 0.5:
                return audio

        self._vad.reset()
        return None

    # ── Push-to-talk capture ──

    async def _capture_push_to_talk(self) -> Optional[np.ndarray]:
        """Record while Super+V is held."""
        from marlow.platform.linux.voice_hotkey import PushToTalkListener
        listener = PushToTalkListener()
        loop = asyncio.get_event_loop()

        pressed = await loop.run_in_executor(None, listener.wait_for_press)
        if not pressed:
            listener.close()
            return None

        self._open_mic()
        chunks: list[bytes] = []
        start = time.monotonic()

        while time.monotonic() - start < 30.0:
            if not listener.is_held():
                break
            chunk = await loop.run_in_executor(
                None, self._stream.read, CHUNK_SAMPLES, False,
            )
            chunks.append(chunk)

        listener.close()

        if not chunks:
            return None

        raw = b"".join(chunks)
        n_samples = len(raw) // 2
        samples = struct.unpack(f"<{n_samples}h", raw)
        audio = np.array(samples, dtype=np.float32) / 32768.0

        if len(audio) / SAMPLE_RATE < 0.3:
            return None

        return audio

    # ── Main loop ──

    async def run(self, goal_callback):
        """Main voice interaction loop.

        goal_callback: async callable(goal_text, channel) -> dict
            Sends a goal to the kernel and returns the result.
        """
        self._running = True
        self._boot_time = time.monotonic()
        from marlow.platform.linux.tts import play_clip

        logger.info("VoiceBridge running (state: %s)", self.context.state.value)

        while self._running:
            try:
                # Check timeouts
                if self.context.is_active and self.context.is_expired():
                    self.context.on_timeout()
                    continue

                state = self.context.state

                if state == ConversationState.IDLE:
                    await self._handle_idle()

                elif state == ConversationState.LISTENING:
                    await self._handle_listening(goal_callback)

                elif state == ConversationState.FOLLOW_UP:
                    await self._handle_follow_up(goal_callback)

                elif state == ConversationState.ERROR:
                    await asyncio.sleep(1)
                    if self.context.is_expired():
                        self.context.on_timeout()

                else:
                    await asyncio.sleep(0.1)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Voice loop error: %s", e)
                self.context.on_error(str(e))
                await asyncio.sleep(1)

        self._close_mic()
        logger.info("VoiceBridge stopped")

    async def _handle_idle(self):
        """IDLE: listen for wake word or push-to-talk."""
        from marlow.platform.linux.tts import play_clip
        from marlow.platform.linux.wake_word import WAKEWORD_CHUNK_SAMPLES

        # Check push-to-talk trigger file
        import os
        trigger = "/tmp/marlow-voice-trigger"
        if os.path.exists(trigger):
            try:
                with open(trigger) as f:
                    state = f.read().strip()
                if state == "press":
                    os.unlink(trigger)
                    await play_clip("en_que_te_ayudo")
                    self.context.on_wake_word("voice")
                    # State is now LISTENING — next loop iteration
                    # _handle_listening() will capture via VAD and execute
                    return
                elif state == "release":
                    os.unlink(trigger)
            except Exception:
                pass

        # Wake word detection (if available)
        # Grace period: skip wake word for 10s after boot to avoid false triggers
        if hasattr(self, '_boot_time') and time.monotonic() - self._boot_time < 10:
            await asyncio.sleep(0.1)
            return

        if self._wake_word and self._wake_word.available:
            self._open_mic()
            loop = asyncio.get_event_loop()
            try:
                chunk_bytes = await loop.run_in_executor(
                    None, self._stream.read, WAKEWORD_CHUNK_SAMPLES, False,
                )
                chunk = np.frombuffer(chunk_bytes, dtype=np.int16)
                if self._wake_word.process_chunk(chunk):
                    await play_clip("si")
                    self.context.on_wake_word("voice")
                    return
            except Exception:
                pass
        else:
            # No wake word — poll trigger file only
            await asyncio.sleep(0.05)

    async def _handle_listening(self, goal_callback):
        """LISTENING: capture speech via VAD, transcribe, execute."""
        from marlow.platform.linux.tts import play_clip, speak

        audio = await self._capture_utterance()
        if audio is None:
            if self.context.is_expired():
                self.context.on_timeout()
            return

        text = await self._transcribe(audio)
        if not text or len(text.strip()) < 3:
            return

        # Check for cancel commands
        lower = text.lower().strip()
        if lower in ("cancelar", "cancela", "stop", "para", "nada", "olvidalo"):
            self.context.on_cancel()
            await play_clip("listo")
            return

        self.context.on_speech_end(text)

        # Feedback while processing
        intent_type = classify_intent_type(text)
        feedback = get_feedback_phrase(intent_type)
        await speak(feedback)

        # Execute goal
        result = await goal_callback(text, "voice")
        self.context.on_goal_complete(result)

        # Respond
        success = result.get("success", False)
        summary = result.get("result_summary", "")

        if success and summary:
            await speak(summary)
            # Check if follow-up is appropriate
            needs_follow = self._should_follow_up(text, result)
            self.context.on_response_spoken(needs_follow_up=needs_follow)
        elif success:
            await play_clip("listo")
            self.context.on_response_spoken(needs_follow_up=False)
        else:
            error_msg = result.get("errors", [""])[0] if result.get("errors") else ""
            if error_msg:
                logger.error("Goal execution error: %s", error_msg)
            await speak("No pude completar esa tarea. \xbfQuieres que lo intente de nuevo?")
            self.context.on_response_spoken(needs_follow_up=False)

    async def _handle_follow_up(self, goal_callback):
        """FOLLOW_UP: listen without wake word for continuation."""
        audio = await self._capture_utterance()
        if audio is None:
            if self.context.is_expired():
                self.context.on_timeout()
            return

        text = await self._transcribe(audio)
        if not text or len(text.strip()) < 2:
            return

        lower = text.lower().strip()

        # Handle yes/no responses
        if lower in ("si", "sip", "yes", "dale", "va", "ok"):
            if self.context.pending_window_id:
                # Move shadow window to user
                result = await goal_callback(
                    f"move_to_user window_id={self.context.pending_window_id}",
                    "voice",
                )
                from marlow.platform.linux.tts import play_clip
                await play_clip("listo")
                self.context.reset()
                return

        if lower in ("no", "nah", "nel", "cancelar", "nada"):
            from marlow.platform.linux.tts import play_clip
            await play_clip("listo")
            self.context.reset()
            return

        # New command in follow-up (no wake word needed)
        self.context.on_speech_end(text)
        await self._handle_listening(goal_callback)

    def _should_follow_up(self, intent: str, result: dict) -> bool:
        """Determine if we should enter follow-up mode after a response."""
        lower = intent.lower()
        # Search queries naturally lead to "want to see it?"
        if any(w in lower for w in ("busca", "search", "clima", "weather")):
            return True
        # If shadow window was involved
        if result.get("result_summary", "").lower().count("shadow") > 0:
            return True
        return False

    def stop(self):
        """Stop the voice bridge."""
        self._running = False
        if self._wake_word:
            self._wake_word.close()
        self._close_mic()


# ─────────────────────────────────────────────────────────────
# Post-processing corrections (Task 10)
# ─────────────────────────────────────────────────────────────

_CORRECTIONS = {
    "pusca": "busca",
    "vuska": "busca",
    "habre": "abre",
    "sierra": "cierra",
    "muestrame": "muestrame",
    "pantaya": "pantalla",
    "archibo": "archivo",
}


def _correct_transcription(text: str) -> str:
    """Fix common whisper misheard words in Spanish commands."""
    words = text.split()
    corrected = [_CORRECTIONS.get(w.lower(), w) for w in words]
    return " ".join(corrected)
