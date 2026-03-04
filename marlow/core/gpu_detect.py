"""GPU auto-detection — detects CUDA and recommends config for whisper/VLM.

/ Deteccion automatica de GPU — detecta CUDA y recomienda config.
"""

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("marlow.core.gpu_detect")


@dataclass(frozen=True)
class GPUInfo:
    available: bool
    name: str = ""
    vram_gb: float = 0.0
    cuda_version: str = ""

    @property
    def can_run_whisper_large(self) -> bool:
        """Whisper large-v3 needs ~3GB VRAM."""
        return self.available and self.vram_gb >= 3.0

    @property
    def can_run_vlm(self) -> bool:
        """Vision Language Models need ~6GB+ VRAM."""
        return self.available and self.vram_gb >= 6.0

    @property
    def recommended_whisper_config(self) -> dict:
        """Return recommended faster-whisper config based on hardware."""
        if self.available and self.vram_gb >= 3.0:
            return {
                "device": "cuda",
                "compute_type": "float16",
                "model_size": "large-v3",
            }
        elif self.available:
            return {
                "device": "cuda",
                "compute_type": "int8",
                "model_size": "base",
            }
        else:
            return {
                "device": "cpu",
                "compute_type": "int8",
                "model_size": "base",
            }


def detect_gpu() -> GPUInfo:
    """Detect available GPU and its capabilities."""
    try:
        import torch

        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            vram_bytes = torch.cuda.get_device_properties(0).total_mem
            vram_gb = round(vram_bytes / (1024**3), 1)
            cuda_ver = torch.version.cuda or ""
            info = GPUInfo(
                available=True,
                name=name,
                vram_gb=vram_gb,
                cuda_version=cuda_ver,
            )
            logger.info(
                "GPU detected: %s (%.1fGB VRAM, CUDA %s)",
                name, vram_gb, cuda_ver,
            )
            return info
    except ImportError:
        logger.debug("torch not installed — no GPU detection")
    except Exception as e:
        logger.debug("GPU detection failed: %s", e)

    return GPUInfo(available=False)


# Module-level cached instance
_gpu_info: Optional[GPUInfo] = None


def get_gpu_info() -> GPUInfo:
    """Get cached GPU info (detected once per session)."""
    global _gpu_info
    if _gpu_info is None:
        _gpu_info = detect_gpu()
    return _gpu_info
