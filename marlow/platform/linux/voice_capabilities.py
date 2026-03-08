"""Adaptive voice capabilities detection.

Auto-detects hardware and recommends ASR model, VAD backend, TTS engine,
and compute type. Marlow OS works on any hardware — from RPi to workstation.

/ Deteccion adaptativa de capacidades de voz.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess

logger = logging.getLogger("marlow.platform.linux.voice_capabilities")


class VoiceCapabilities:
    """Detect hardware and recommend voice stack configuration."""

    def __init__(self):
        self.has_gpu = self._detect_gpu()
        self.ram_gb = self._get_ram_gb()
        self.cpu_cores = os.cpu_count() or 2
        self.has_mic = self._detect_microphone()
        self._logged = False

    def log_capabilities(self):
        """Log detected capabilities once."""
        if self._logged:
            return
        self._logged = True
        logger.info(
            "VoiceCapabilities: cores=%d, ram=%.1fGB, gpu=%s, mic=%s",
            self.cpu_cores, self.ram_gb, self.has_gpu, self.has_mic,
        )
        logger.info(
            "Recommendations: whisper=%s, compute=%s, vad=%s, tts=%s",
            self.recommended_whisper_model(),
            self.recommended_compute_type(),
            self.recommended_vad(),
            self.recommended_tts(),
        )

    def recommended_whisper_model(self) -> str:
        """Pick the right model for this hardware."""
        if self.has_gpu:
            return "large-v3"
        if self.ram_gb >= 16 and self.cpu_cores >= 8:
            return "base"
        if self.ram_gb >= 8:
            return "small"
        return "tiny"

    def recommended_compute_type(self) -> str:
        if self.has_gpu:
            return "float16"
        return "int8"

    def recommended_vad(self) -> str:
        """Pick VAD based on available dependencies."""
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

    def recommended_tts(self) -> str:
        """Pick TTS: Piper first (offline), edge-tts if internet available."""
        if shutil.which("piper"):
            return "piper"
        try:
            import edge_tts  # noqa: F401
            return "edge-tts"
        except ImportError:
            pass
        if shutil.which("espeak-ng"):
            return "espeak-ng"
        return "none"

    @staticmethod
    def _detect_gpu() -> bool:
        """Check for CUDA GPU via nvidia-smi or torch."""
        if shutil.which("nvidia-smi"):
            try:
                r = subprocess.run(
                    ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                    capture_output=True, text=True, timeout=5,
                )
                if r.returncode == 0 and r.stdout.strip():
                    return True
            except Exception:
                pass
        try:
            import torch
            return torch.cuda.is_available()
        except ImportError:
            pass
        return False

    @staticmethod
    def _get_ram_gb() -> float:
        """Read total RAM from /proc/meminfo."""
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        kb = int(line.split()[1])
                        return round(kb / 1024 / 1024, 1)
        except Exception:
            pass
        return 4.0  # conservative fallback

    @staticmethod
    def _detect_microphone() -> bool:
        """Check if a microphone is available via PipeWire/PulseAudio."""
        try:
            r = subprocess.run(
                ["pactl", "list", "sources", "short"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                for line in r.stdout.splitlines():
                    # Skip monitors (they're output loopback, not mics)
                    if ".monitor" not in line and "RUNNING" in line:
                        return True
                    if ".monitor" not in line and "IDLE" in line:
                        return True
                # If any non-monitor source exists, mic is probably available
                for line in r.stdout.splitlines():
                    if ".monitor" not in line and line.strip():
                        return True
        except Exception:
            pass
        return False


# Singleton for lazy access
_instance: VoiceCapabilities | None = None


def get_voice_capabilities() -> VoiceCapabilities:
    """Get or create the singleton VoiceCapabilities instance."""
    global _instance
    if _instance is None:
        _instance = VoiceCapabilities()
        _instance.log_capabilities()
    return _instance
