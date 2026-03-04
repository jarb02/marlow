"""Tests for marlow.core.piper_tts — Piper TTS offline engine."""

import os
import pytest
from marlow.core.piper_tts import PiperTTSEngine


class TestPiperTTSEngine:
    def test_piper_engine_init(self):
        """Init should not crash even without models."""
        engine = PiperTTSEngine()
        assert isinstance(engine, PiperTTSEngine)

    def test_piper_available_property(self):
        """available should be a bool."""
        engine = PiperTTSEngine()
        assert isinstance(engine.available, bool)

    def test_piper_list_voices(self):
        """list_voices should return a dict."""
        engine = PiperTTSEngine()
        voices = engine.list_voices()
        assert isinstance(voices, dict)
        # All values should be strings (model stem names)
        for lang, name in voices.items():
            assert isinstance(lang, str)
            assert isinstance(name, str)

    def test_piper_synthesize_no_voice_for_lang(self):
        """Synthesize with unavailable language returns None."""
        engine = PiperTTSEngine()
        result = engine.synthesize("hello", language="zh")
        assert result is None

    def test_piper_voice_dir_path(self):
        """VOICE_DIR should point to ~/.marlow/piper_voices."""
        expected = os.path.expanduser("~/.marlow/piper_voices")
        assert PiperTTSEngine.VOICE_DIR == expected

    def test_piper_language_mapping(self):
        """VOICE_MAP should have es and en."""
        assert "es" in PiperTTSEngine.VOICE_MAP
        assert "en" in PiperTTSEngine.VOICE_MAP
        assert PiperTTSEngine.VOICE_MAP["es"] == "es_MX"
        assert PiperTTSEngine.VOICE_MAP["en"] == "en_US"

    def test_piper_synthesize_disabled(self):
        """If Piper is not available, synthesize returns None."""
        engine = PiperTTSEngine()
        engine._available = False
        result = engine.synthesize("test text", language="es")
        assert result is None

    def test_piper_detects_installed_voices(self):
        """If voice models exist, they should be detected."""
        engine = PiperTTSEngine()
        if engine.available:
            voices = engine.list_voices()
            assert len(voices) > 0
            # Each detected voice should have a 2-char lang key
            for lang in voices:
                assert len(lang) == 2
