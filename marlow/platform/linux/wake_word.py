"""Wake word detection — "Marlow" via OpenWakeWord.

Listens continuously at ~1-2% CPU for the wake word.
Falls back to push-to-talk only if wake word model unavailable.

/ Deteccion de wake word "Marlow" via OpenWakeWord.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger("marlow.platform.linux.wake_word")

# Custom model path (if trained)
_CUSTOM_MODEL_DIR = Path.home() / ".config" / "marlow" / "models"

# Detection threshold — higher = fewer false positives
DEFAULT_THRESHOLD = 0.5

# Chunk size for wake word: 80ms at 16kHz = 1280 samples
WAKEWORD_CHUNK_SAMPLES = 1280


class WakeWordListener:
    """Continuous wake word listener using OpenWakeWord.

    Uses "hey_jarvis" as phonetic proxy for "Marlow" until a custom
    model is trained. Threshold tuned for low false-positive rate.
    """

    def __init__(self, threshold: float = DEFAULT_THRESHOLD):
        self.threshold = threshold
        self._model = None
        self._model_name: str = ""
        self._available = False
        self._stop = False

    def setup(self) -> bool:
        """Load the wake word model. Returns True if ready."""
        try:
            from openwakeword.model import Model
        except ImportError:
            logger.warning("openwakeword not installed")
            return False

        model_path = self._find_model()
        if not model_path:
            logger.warning("No wake word model found")
            return False

        try:
            self._model = Model(wakeword_model_paths=[str(model_path)])
            self._model_name = list(self._model.models.keys())[0]
            self._available = True
            logger.info("Wake word ready: model=%s, threshold=%.2f",
                        self._model_name, self.threshold)
            return True
        except Exception as e:
            logger.error("Failed to load wake word model: %s", e)
            return False

    def _find_model(self) -> Optional[Path]:
        """Find the best available wake word model.

        Priority: custom 'marlow' model > hey_jarvis (phonetic proxy).
        """
        # Check for custom trained model
        if _CUSTOM_MODEL_DIR.exists():
            for name in ("marlow.onnx", "hey_marlow.onnx"):
                custom = _CUSTOM_MODEL_DIR / name
                if custom.exists():
                    logger.info("Using custom wake word model: %s", custom)
                    return custom

        # Fall back to built-in models
        try:
            import openwakeword
            pkg_dir = Path(openwakeword.__file__).parent
            models_dir = pkg_dir / "resources" / "models"

            # hey_jarvis is phonetically closest to "Marlow" among builtins
            for name in ("hey_jarvis_v0.1.onnx", "hey_marvin_v0.1.onnx"):
                model = models_dir / name
                if model.exists():
                    logger.info("Using built-in wake word model: %s", name)
                    return model
        except Exception:
            pass

        return None

    @property
    def available(self) -> bool:
        return self._available

    @property
    def model_name(self) -> str:
        return self._model_name

    def process_chunk(self, audio_chunk: np.ndarray) -> bool:
        """Process a chunk of audio, return True if wake word detected.

        audio_chunk: int16 numpy array, 1280 samples (80ms at 16kHz).
        """
        if not self._available or self._model is None:
            return False

        predictions = self._model.predict(audio_chunk)
        score = predictions.get(self._model_name, 0.0)

        if score >= self.threshold:
            logger.info("Wake word detected! score=%.3f (threshold=%.2f)",
                        score, self.threshold)
            # Reset model to avoid repeated triggers
            self._model.reset()
            return True

        return False

    def reset(self):
        """Reset the model state (call after detection to avoid echo)."""
        if self._model:
            self._model.reset()

    def stop(self):
        """Signal the listener to stop."""
        self._stop = True

    def close(self):
        """Clean up resources."""
        self._stop = True
        self._model = None
        self._available = False
