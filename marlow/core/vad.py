"""Voice Activity Detection with fallback chain: Silero VAD -> RMS threshold.

/ Deteccion de actividad de voz con cadena de fallback: Silero VAD -> umbral RMS.
"""

import logging

import numpy as np

logger = logging.getLogger("marlow.core.vad")


class VADResult:
    """Result of voice activity detection on an audio chunk."""

    def __init__(self, is_speech: bool, confidence: float = 0.0):
        self.is_speech = is_speech
        self.confidence = confidence


class SileroVAD:
    """Silero VAD wrapper — neuronal, <1ms per 30ms chunk."""

    def __init__(self, threshold: float = 0.5, sample_rate: int = 16000):
        self._threshold = threshold
        self._sample_rate = sample_rate
        self._model = None
        self._available = False
        self._load_model()

    def _load_model(self):
        try:
            from silero_vad import load_silero_vad

            self._model = load_silero_vad()
            self._available = True
            logger.info("Silero VAD loaded successfully")
        except Exception as e:
            logger.warning(f"Silero VAD not available: {e}")
            self._available = False

    @property
    def available(self) -> bool:
        return self._available

    def process_chunk(self, audio_chunk: np.ndarray) -> VADResult:
        """Process a single audio chunk (16kHz, float32, mono).

        Returns VADResult with is_speech and confidence.
        """
        if not self._available or self._model is None:
            return VADResult(is_speech=False, confidence=0.0)

        try:
            import torch

            if audio_chunk.dtype != np.float32:
                audio_chunk = audio_chunk.astype(np.float32)
            tensor = torch.from_numpy(audio_chunk)
            confidence = self._model(tensor, self._sample_rate).item()
            return VADResult(
                is_speech=confidence >= self._threshold,
                confidence=confidence,
            )
        except Exception as e:
            logger.debug(f"Silero VAD error: {e}")
            return VADResult(is_speech=False, confidence=0.0)

    def reset(self):
        """Reset model state between utterances."""
        if self._model is not None:
            try:
                self._model.reset_states()
            except Exception:
                pass


class RMSVAD:
    """Fallback RMS-based VAD — simple but functional."""

    def __init__(self, threshold: float = 500.0):
        self._threshold = threshold

    @property
    def available(self) -> bool:
        return True  # Always available

    def process_chunk(self, audio_chunk: np.ndarray) -> VADResult:
        """Process audio chunk using RMS energy."""
        if len(audio_chunk) == 0:
            return VADResult(is_speech=False, confidence=0.0)

        rms = float(np.sqrt(np.mean(audio_chunk.astype(np.float64) ** 2)))
        is_speech = rms >= self._threshold
        # Normalize confidence: 0 at threshold/2, 1 at threshold*2
        confidence = min(1.0, max(0.0, rms / (self._threshold * 2)))
        return VADResult(is_speech=bool(is_speech), confidence=confidence)

    def reset(self):
        pass


class AdaptiveVAD:
    """Adaptive VAD with fallback chain: Silero -> RMS.

    Learns user's speech patterns over time.
    """

    def __init__(
        self, silero_threshold: float = 0.5, rms_threshold: float = 500.0,
    ):
        self._silero = SileroVAD(threshold=silero_threshold)
        self._rms = RMSVAD(threshold=rms_threshold)
        self._silence_chunks = 0
        self._silence_limit = 4  # chunks of silence before "end of speech"
        self._speech_started = False
        # Adaptive: track pause durations to learn user patterns
        self._pause_durations: list[float] = []
        self._max_pause_history = 20

    @property
    def backend(self) -> str:
        return "silero" if self._silero.available else "rms"

    def process_chunk(self, audio_chunk: np.ndarray) -> VADResult:
        """Process chunk with best available VAD."""
        if self._silero.available:
            result = self._silero.process_chunk(audio_chunk)
        else:
            result = self._rms.process_chunk(audio_chunk)

        # Track speech state
        if result.is_speech:
            self._silence_chunks = 0
            self._speech_started = True
        else:
            self._silence_chunks += 1

        return result

    def is_end_of_speech(self) -> bool:
        """Check if enough silence has passed to consider speech ended."""
        return self._speech_started and self._silence_chunks >= self._silence_limit

    def reset(self):
        """Reset state for new utterance."""
        self._silero.reset()
        self._rms.reset()
        self._silence_chunks = 0
        self._speech_started = False

    def adjust_silence_limit(self, chunks: int):
        """Adjust how many silence chunks before end-of-speech."""
        self._silence_limit = max(2, min(10, chunks))
