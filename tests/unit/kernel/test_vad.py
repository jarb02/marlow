"""Tests for marlow.core.vad — Voice Activity Detection with fallback chain."""

import pytest
import numpy as np

from marlow.core.vad import VADResult, RMSVAD, SileroVAD, AdaptiveVAD

# Check if Silero/torch are available
try:
    import torch
    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False


class TestVADResult:
    def test_vad_result_dataclass(self):
        r = VADResult(is_speech=True, confidence=0.85)
        assert r.is_speech is True
        assert r.confidence == 0.85

    def test_vad_result_defaults(self):
        r = VADResult(is_speech=False)
        assert r.confidence == 0.0


class TestRMSVAD:
    def test_rms_vad_silence(self):
        """Zero audio should not be detected as speech."""
        vad = RMSVAD(threshold=500.0)
        silence = np.zeros(8000, dtype=np.int16)
        result = vad.process_chunk(silence)
        assert result.is_speech is False
        assert result.confidence == 0.0

    def test_rms_vad_speech(self):
        """Loud audio should be detected as speech."""
        vad = RMSVAD(threshold=500.0)
        loud = np.full(8000, 5000, dtype=np.int16)
        result = vad.process_chunk(loud)
        assert result.is_speech is True
        assert result.confidence > 0.0

    def test_rms_vad_confidence_range(self):
        """Confidence should be clamped between 0 and 1."""
        vad = RMSVAD(threshold=500.0)
        # Very loud — confidence should cap at 1.0
        very_loud = np.full(8000, 30000, dtype=np.int16)
        result = vad.process_chunk(very_loud)
        assert 0.0 <= result.confidence <= 1.0

        # Quiet — confidence should be near 0
        quiet = np.full(8000, 100, dtype=np.int16)
        result = vad.process_chunk(quiet)
        assert 0.0 <= result.confidence <= 1.0

    def test_rms_vad_always_available(self):
        vad = RMSVAD()
        assert vad.available is True

    def test_rms_vad_empty_chunk(self):
        vad = RMSVAD()
        result = vad.process_chunk(np.array([], dtype=np.int16))
        assert result.is_speech is False
        assert result.confidence == 0.0

    def test_rms_vad_reset_noop(self):
        """Reset should not raise."""
        vad = RMSVAD()
        vad.reset()  # Should be a no-op


class TestSileroVAD:
    @pytest.mark.skipif(not _HAS_TORCH, reason="torch not installed")
    def test_silero_vad_available(self):
        vad = SileroVAD()
        assert vad.available is True

    @pytest.mark.skipif(not _HAS_TORCH, reason="torch not installed")
    def test_silero_vad_silence(self):
        """Zero audio should not be detected as speech by Silero."""
        vad = SileroVAD(sample_rate=16000)
        # Silero needs 512 samples minimum at 16kHz
        silence = np.zeros(512, dtype=np.float32)
        result = vad.process_chunk(silence)
        assert result.is_speech is False

    @pytest.mark.skipif(not _HAS_TORCH, reason="torch not installed")
    def test_silero_vad_reset(self):
        """Reset should not raise."""
        vad = SileroVAD()
        vad.reset()


class TestAdaptiveVAD:
    def test_adaptive_vad_backend(self):
        """Should report which backend is active."""
        vad = AdaptiveVAD()
        assert vad.backend in ("silero", "rms")

    def test_adaptive_vad_end_of_speech(self):
        """After speech followed by silence chunks, should detect end."""
        vad = AdaptiveVAD(rms_threshold=500.0)
        # Force RMS backend by mocking silero as unavailable
        vad._silero._available = False

        # Simulate speech chunks
        loud = np.full(8000, 5000, dtype=np.int16).astype(np.float32)
        vad.process_chunk(loud)
        vad.process_chunk(loud)
        assert vad.is_end_of_speech() is False

        # Simulate silence chunks (4 = default limit)
        silence = np.zeros(8000, dtype=np.float32)
        for _ in range(4):
            vad.process_chunk(silence)
        assert vad.is_end_of_speech() is True

    def test_adaptive_vad_not_end_before_speech(self):
        """Should not report end of speech if no speech was detected yet."""
        vad = AdaptiveVAD(rms_threshold=500.0)
        vad._silero._available = False

        silence = np.zeros(8000, dtype=np.float32)
        for _ in range(10):
            vad.process_chunk(silence)
        assert vad.is_end_of_speech() is False

    def test_adaptive_vad_reset(self):
        """Reset should clear speech state."""
        vad = AdaptiveVAD(rms_threshold=500.0)
        vad._silero._available = False

        # Simulate speech then silence
        loud = np.full(8000, 5000, dtype=np.float32)
        vad.process_chunk(loud)
        silence = np.zeros(8000, dtype=np.float32)
        for _ in range(4):
            vad.process_chunk(silence)
        assert vad.is_end_of_speech() is True

        # Reset
        vad.reset()
        assert vad.is_end_of_speech() is False
        assert vad._speech_started is False
        assert vad._silence_chunks == 0

    def test_adjust_silence_limit(self):
        """Silence limit should be clamped between 2 and 10."""
        vad = AdaptiveVAD()
        vad.adjust_silence_limit(6)
        assert vad._silence_limit == 6

        vad.adjust_silence_limit(1)
        assert vad._silence_limit == 2  # clamped min

        vad.adjust_silence_limit(20)
        assert vad._silence_limit == 10  # clamped max
