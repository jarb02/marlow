"""Piper TTS — high quality offline neural TTS.

Used as fallback when edge-tts (online) is not available.
Chain: edge-tts (online) -> Piper (offline, neural) -> pyttsx3 (last resort).

/ Piper TTS — TTS neuronal offline de alta calidad.
/ Usado como fallback cuando edge-tts no esta disponible.
"""

import logging
import os
import wave
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger("marlow.core.piper_tts")


class PiperTTSEngine:
    """Piper TTS — high quality offline neural TTS.

    Used as fallback when edge-tts (online) is not available.
    """

    VOICE_DIR = os.path.expanduser("~/.marlow/piper_voices")

    # Voice preferences by language
    VOICE_MAP = {
        "es": "es_MX",  # Mexican Spanish
        "en": "en_US",   # American English
    }

    def __init__(self):
        self._available = False
        self._voices: dict[str, Path] = {}  # lang -> model path
        self._piper_voice_class = None
        self._check_availability()

    def _check_availability(self):
        """Check if Piper is installed and voices are available."""
        try:
            from piper import PiperVoice
            self._piper_voice_class = PiperVoice
        except ImportError:
            try:
                from piper.voice import PiperVoice
                self._piper_voice_class = PiperVoice
            except ImportError:
                logger.info("Piper TTS not installed")
                return

        # Scan for available voice models
        voice_dir = Path(self.VOICE_DIR)
        if voice_dir.exists():
            for onnx_file in voice_dir.glob("*.onnx"):
                config = Path(str(onnx_file) + ".json")
                if config.exists():
                    # Extract language from filename (es_MX-name-quality.onnx)
                    name = onnx_file.stem
                    parts = name.split("-")
                    if parts:
                        lang_code = parts[0][:2]  # "es" from "es_MX"
                        self._voices[lang_code] = onnx_file
                        logger.info("Piper voice found: %s (%s)", name, lang_code)

        self._available = len(self._voices) > 0
        if self._available:
            logger.info("Piper TTS ready with %d voice(s)", len(self._voices))
        else:
            logger.info("Piper TTS: no voice models found")

    @property
    def available(self) -> bool:
        return self._available

    def synthesize(
        self, text: str, language: str = "es", output_path: Optional[str] = None,
    ) -> Optional[str]:
        """Synthesize text to a WAV file.

        Returns path to WAV file, or None on failure.
        """
        if not self._available:
            return None

        lang = language[:2].lower()
        model_path = self._voices.get(lang)
        if not model_path:
            logger.warning("No Piper voice for language: %s", lang)
            return None

        try:
            voice = self._piper_voice_class.load(str(model_path))

            if output_path is None:
                fd, output_path = tempfile.mkstemp(suffix=".wav", prefix="piper_")
                os.close(fd)

            with wave.open(output_path, "wb") as wav_file:
                voice.synthesize(text, wav_file)

            logger.debug("Piper synthesized to %s", output_path)
            return output_path

        except Exception as e:
            logger.error("Piper TTS failed: %s", e)
            return None

    def list_voices(self) -> dict[str, str]:
        """Return available voices as {language: model_name}."""
        return {lang: path.stem for lang, path in self._voices.items()}
