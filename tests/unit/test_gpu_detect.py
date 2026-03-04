"""Tests for marlow.core.gpu_detect — GPU auto-detection."""

import pytest
from marlow.core.gpu_detect import GPUInfo, detect_gpu, get_gpu_info
import marlow.core.gpu_detect as gpu_mod


class TestGPUInfo:
    def test_gpu_info_dataclass(self):
        info = GPUInfo(available=True, name="RTX 4080", vram_gb=16.0, cuda_version="12.4")
        assert info.available is True
        assert info.name == "RTX 4080"
        assert info.vram_gb == 16.0
        assert info.cuda_version == "12.4"

    def test_gpu_info_no_gpu(self):
        info = GPUInfo(available=False)
        assert info.available is False
        assert info.name == ""
        assert info.vram_gb == 0.0

    def test_gpu_info_recommended_config_no_gpu(self):
        info = GPUInfo(available=False)
        config = info.recommended_whisper_config
        assert config["device"] == "cpu"
        assert config["compute_type"] == "int8"
        assert config["model_size"] == "base"

    def test_gpu_info_recommended_config_large_gpu(self):
        info = GPUInfo(available=True, name="RTX 4080 SUPER", vram_gb=16.0)
        config = info.recommended_whisper_config
        assert config["device"] == "cuda"
        assert config["compute_type"] == "float16"
        assert config["model_size"] == "large-v3"

    def test_gpu_info_recommended_config_small_gpu(self):
        info = GPUInfo(available=True, name="GTX 1050", vram_gb=2.0)
        config = info.recommended_whisper_config
        assert config["device"] == "cuda"
        assert config["compute_type"] == "int8"
        assert config["model_size"] == "base"

    def test_can_run_whisper_large(self):
        assert GPUInfo(available=True, vram_gb=4.0).can_run_whisper_large is True
        assert GPUInfo(available=True, vram_gb=2.0).can_run_whisper_large is False
        assert GPUInfo(available=False).can_run_whisper_large is False

    def test_can_run_vlm(self):
        assert GPUInfo(available=True, vram_gb=8.0).can_run_vlm is True
        assert GPUInfo(available=True, vram_gb=4.0).can_run_vlm is False
        assert GPUInfo(available=False).can_run_vlm is False


class TestDetectGPU:
    def test_detect_gpu_returns_gpuinfo(self):
        info = detect_gpu()
        assert isinstance(info, GPUInfo)

    def test_get_gpu_info_cached(self):
        # Reset cache
        gpu_mod._gpu_info = None
        info1 = get_gpu_info()
        info2 = get_gpu_info()
        assert info1 is info2  # Same object (cached)
        # Cleanup
        gpu_mod._gpu_info = None
