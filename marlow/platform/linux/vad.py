"""Voice Activity Detection with fallback chain.

Three backends: silero (best) -> webrtcvad (lightweight) -> energy (always works).
Auto-selects based on available dependencies.

/ Deteccion de actividad de voz con cadena de fallback.
"""

from __future__ import annotations

import logging
import struct
import time
from collections import deque

import numpy as np

logger = logging.getLogger("marlow.platform.linux.vad")

# Frame duration for webrtcvad (must be 10, 20, or 30 ms)
_WEBRTC_FRAME_MS = 30


class VoiceActivityDetector:
    """VAD with auto-fallback: silero -> webrtcvad -> energy."""

    def __init__(
        self,
        backend: str = "auto",
        silence_timeout: float = 1.5,
        sample_rate: int = 16000,
    ):
        self.sample_rate = sample_rate
        self.silence_timeout = silence_timeout
        self._backend_name = backend

        # Utterance buffer
        self._speech_buffer: list[bytes] = []
        self._is_speaking = False
        self._last_speech_time = 0.0

        # Select backend
        if backend == "auto":
            self._backend_name = self._auto_select()
        self._init_backend()
        logger.info("VAD backend: %s", self._backend_name)

    def _auto_select(self) -> str:
        """Pick the best available backend."""
        try:
            import silero_vad  # noqa: F401
            return "silero"
        except ImportError:
            pass
        try:
            import webrtcvad  # noqa: F401
            return "webrtc"
        except ImportError:
            pass
        return "energy"

    def _init_backend(self):
        if self._backend_name == "silero":
            self._init_silero()
        elif self._backend_name == "webrtc":
            self._init_webrtc()
        else:
            self._init_energy()

    def _init_silero(self):
        """Initialize Silero VAD (torch-based neural VAD)."""
        import torch
        model, utils = torch.hub.load(
            "snakers4/silero-vad", "silero_vad", trust_repo=True,
        )
        self._silero_model = model
        self._silero_get_speech = utils[0]

    def _init_webrtc(self):
        """Initialize webrtcvad with aggressiveness=2."""
        import webrtcvad
        self._webrtc = webrtcvad.Vad(2)
        # Number of silence frames to consider speech ended
        frame_samples = self.sample_rate * _WEBRTC_FRAME_MS // 1000
        self._webrtc_frame_bytes = frame_samples * 2  # 16-bit
        self._webrtc_silence_frames = int(
            self.silence_timeout * 1000 / _WEBRTC_FRAME_MS
        )
        self._webrtc_silence_count = 0
        self._webrtc_speech_count = 0
        # Require at least this many speech frames to start an utterance
        self._webrtc_min_speech = 3

    def _init_energy(self):
        """Initialize energy-based VAD with adaptive threshold."""
        self._energy_threshold = 500.0
        self._energy_history = deque(maxlen=50)
        self._energy_silence_frames = 0
        frames_per_sec = self.sample_rate * 2 / 960  # ~33 frames/sec at 960 bytes
        self._energy_silence_max = int(self.silence_timeout * frames_per_sec)

    def process_chunk(self, audio_chunk: bytes) -> str:
        """Process an audio chunk and return state.

        Returns: "speech", "silence", or "utterance_complete"
        """
        if self._backend_name == "silero":
            return self._process_silero(audio_chunk)
        elif self._backend_name == "webrtc":
            return self._process_webrtc(audio_chunk)
        else:
            return self._process_energy(audio_chunk)

    def _process_webrtc(self, chunk: bytes) -> str:
        """Process chunk with webrtcvad."""
        # webrtcvad needs exact frame sizes
        frame_size = self._webrtc_frame_bytes
        offset = 0
        any_speech = False

        while offset + frame_size <= len(chunk):
            frame = chunk[offset:offset + frame_size]
            try:
                is_speech = self._webrtc.is_speech(frame, self.sample_rate)
            except Exception:
                is_speech = False
            if is_speech:
                any_speech = True
            offset += frame_size

        if any_speech:
            self._webrtc_speech_count += 1
            self._webrtc_silence_count = 0
            self._last_speech_time = time.monotonic()

            if self._webrtc_speech_count >= self._webrtc_min_speech:
                if not self._is_speaking:
                    self._is_speaking = True
                self._speech_buffer.append(chunk)
                return "speech"
            else:
                self._speech_buffer.append(chunk)
                return "silence"  # accumulating but not confirmed yet
        else:
            self._webrtc_silence_count += 1
            if self._is_speaking:
                self._speech_buffer.append(chunk)
                if self._webrtc_silence_count >= self._webrtc_silence_frames:
                    self._is_speaking = False
                    self._webrtc_speech_count = 0
                    return "utterance_complete"
                return "speech"  # trailing silence within utterance
            return "silence"

    def _process_energy(self, chunk: bytes) -> str:
        """Process chunk with energy-based VAD."""
        n_samples = len(chunk) // 2
        if n_samples == 0:
            return "silence"

        samples = struct.unpack(f"<{n_samples}h", chunk)
        rms = (sum(s * s for s in samples) / n_samples) ** 0.5

        # Adaptive threshold: track ambient noise level
        self._energy_history.append(rms)
        if len(self._energy_history) >= 10:
            ambient = sorted(self._energy_history)[len(self._energy_history) // 4]
            self._energy_threshold = max(300, ambient * 3)

        is_speech = rms > self._energy_threshold

        if is_speech:
            self._energy_silence_frames = 0
            self._last_speech_time = time.monotonic()
            if not self._is_speaking:
                self._is_speaking = True
            self._speech_buffer.append(chunk)
            return "speech"
        else:
            if self._is_speaking:
                self._energy_silence_frames += 1
                self._speech_buffer.append(chunk)
                if self._energy_silence_frames >= self._energy_silence_max:
                    self._is_speaking = False
                    self._energy_silence_frames = 0
                    return "utterance_complete"
                return "speech"
            return "silence"

    def _process_silero(self, chunk: bytes) -> str:
        """Process chunk with Silero VAD."""
        import torch

        n_samples = len(chunk) // 2
        if n_samples == 0:
            return "silence"

        samples = struct.unpack(f"<{n_samples}h", chunk)
        audio = torch.FloatTensor(samples) / 32768.0

        prob = self._silero_model(audio, self.sample_rate).item()
        is_speech = prob > 0.5

        if is_speech:
            self._last_speech_time = time.monotonic()
            if not self._is_speaking:
                self._is_speaking = True
            self._speech_buffer.append(chunk)
            return "speech"
        else:
            if self._is_speaking:
                self._speech_buffer.append(chunk)
                elapsed = time.monotonic() - self._last_speech_time
                if elapsed >= self.silence_timeout:
                    self._is_speaking = False
                    return "utterance_complete"
                return "speech"
            return "silence"

    def get_utterance(self) -> np.ndarray:
        """Return the buffered utterance as a numpy float32 array."""
        if not self._speech_buffer:
            return np.array([], dtype=np.float32)

        raw = b"".join(self._speech_buffer)
        n_samples = len(raw) // 2
        samples = struct.unpack(f"<{n_samples}h", raw)
        return np.array(samples, dtype=np.float32) / 32768.0

    def reset(self):
        """Clear buffers for next utterance."""
        self._speech_buffer.clear()
        self._is_speaking = False
        self._last_speech_time = 0.0
        if self._backend_name == "webrtc":
            self._webrtc_silence_count = 0
            self._webrtc_speech_count = 0
        elif self._backend_name == "energy":
            self._energy_silence_frames = 0

    @property
    def backend_name(self) -> str:
        return self._backend_name
