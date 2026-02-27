"""
Marlow Audio Tools

Capture system audio (WASAPI loopback), microphone audio,
and transcribe using faster-whisper (CPU, int8).

All blocking operations run in a thread executor to avoid
blocking the MCP event loop.

Audio files stored in ~/.marlow/audio/, auto-cleaned after 1 hour.

/ Captura audio del sistema (WASAPI loopback), micrófono,
/ y transcribe usando faster-whisper (CPU, int8).
"""

import os
import time
import wave
import asyncio
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("marlow.tools.audio")

# Audio storage directory
AUDIO_DIR = Path.home() / ".marlow" / "audio"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

# Maximum recording duration (seconds)
MAX_DURATION = 300

# Module-level whisper model cache
_whisper_model = None
_whisper_model_size = None


def _cleanup_old_audio(max_age_seconds: int = 3600):
    """Remove audio files older than max_age_seconds (default: 1 hour)."""
    now = time.time()
    try:
        for f in AUDIO_DIR.iterdir():
            if f.suffix in (".wav", ".tmp") and (now - f.stat().st_mtime) > max_age_seconds:
                f.unlink(missing_ok=True)
                logger.debug(f"Cleaned up old audio: {f.name}")
    except Exception as e:
        logger.debug(f"Audio cleanup error: {e}")


def _generate_filename(prefix: str) -> Path:
    """Generate a timestamped filename for audio."""
    ts = time.strftime("%Y%m%d_%H%M%S")
    return AUDIO_DIR / f"{prefix}_{ts}.wav"


async def capture_system_audio(duration_seconds: int = 10) -> dict:
    """
    Capture system audio (what you hear) via WASAPI loopback.

    Records audio output from speakers/headphones. Useful for
    capturing audio from videos, calls, or any app playing sound.

    Args:
        duration_seconds: How long to record (max 300s). Default: 10.

    Returns:
        Dictionary with audio file path, duration, and format info.

    / Captura audio del sistema (lo que escuchas) via WASAPI loopback.
    """
    duration_seconds = min(duration_seconds, MAX_DURATION)
    _cleanup_old_audio()

    try:
        import pyaudiowpatch as pyaudio
    except ImportError:
        return {
            "error": "PyAudioWPatch not installed. Run: pip install PyAudioWPatch",
            "hint": "Required for system audio capture on Windows (WASAPI loopback).",
        }

    output_path = _generate_filename("system")

    def _record():
        p = pyaudio.PyAudio()

        try:
            # Find WASAPI loopback device
            wasapi_info = p.get_host_api_info_by_type(pyaudio.paWASAPI)
            default_speakers = p.get_device_info_by_index(
                wasapi_info["defaultOutputDevice"]
            )

            # Find the loopback device for the default speakers
            loopback = None
            for i in range(p.get_device_count()):
                dev = p.get_device_info_by_index(i)
                if (dev["name"].startswith(default_speakers["name"])
                        and dev.get("isLoopbackDevice", False)):
                    loopback = dev
                    break

            if not loopback:
                return {"error": "No WASAPI loopback device found. Check audio settings."}

            channels = int(loopback["maxInputChannels"])
            rate = int(loopback["defaultSampleRate"])

            stream = p.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=rate,
                input=True,
                input_device_index=int(loopback["index"]),
                frames_per_buffer=1024,
            )

            frames = []
            num_frames = int(rate / 1024 * duration_seconds)
            for _ in range(num_frames):
                data = stream.read(1024, exception_on_overflow=False)
                frames.append(data)

            stream.stop_stream()
            stream.close()

            # Save to WAV
            with wave.open(str(output_path), "wb") as wf:
                wf.setnchannels(channels)
                wf.setsampwidth(p.get_sample_size(pyaudio.paInt16))
                wf.setframerate(rate)
                wf.writeframes(b"".join(frames))

            return {
                "success": True,
                "audio_path": str(output_path),
                "duration_seconds": duration_seconds,
                "channels": channels,
                "sample_rate": rate,
                "format": "wav",
                "size_kb": round(output_path.stat().st_size / 1024, 1),
                "device": loopback["name"],
            }

        finally:
            p.terminate()

    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, _record)
        return result
    except Exception as e:
        logger.error(f"System audio capture error: {e}")
        return {"error": str(e)}


async def capture_mic_audio(duration_seconds: int = 10) -> dict:
    """
    Capture microphone audio.

    Records from the default input device (microphone).

    Args:
        duration_seconds: How long to record (max 300s). Default: 10.

    Returns:
        Dictionary with audio file path, duration, and format info.

    / Captura audio del micrófono.
    """
    duration_seconds = min(duration_seconds, MAX_DURATION)
    _cleanup_old_audio()

    try:
        import sounddevice as sd
        import soundfile as sf
    except ImportError:
        return {
            "error": "sounddevice/soundfile not installed. Run: pip install sounddevice soundfile",
        }

    output_path = _generate_filename("mic")
    sample_rate = 16000  # 16kHz is optimal for speech recognition

    def _record():
        recording = sd.rec(
            int(duration_seconds * sample_rate),
            samplerate=sample_rate,
            channels=1,
            dtype="int16",
        )
        sd.wait()  # Block until recording is done

        sf.write(str(output_path), recording, sample_rate)

        return {
            "success": True,
            "audio_path": str(output_path),
            "duration_seconds": duration_seconds,
            "channels": 1,
            "sample_rate": sample_rate,
            "format": "wav",
            "size_kb": round(output_path.stat().st_size / 1024, 1),
        }

    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, _record)
        return result
    except Exception as e:
        logger.error(f"Mic audio capture error: {e}")
        return {"error": str(e)}


def _is_model_cached(model_size: str) -> bool:
    """Check if a whisper model is already downloaded locally."""
    try:
        from huggingface_hub import try_to_load_from_cache
        # faster-whisper uses CTranslate2 converted models from huggingface
        repo_id = f"Systran/faster-whisper-{model_size}"
        result = try_to_load_from_cache(repo_id, "model.bin")
        return result is not None
    except Exception:
        return False


async def download_whisper_model(model_size: str = "base") -> dict:
    """
    Pre-download a Whisper model so transcription doesn't timeout.

    Downloads the model (~75MB tiny, ~150MB base, ~500MB small, ~1.5GB medium)
    to the local cache. After downloading, transcribe_audio will start instantly.

    Args:
        model_size: Model to download: "tiny", "base", "small", "medium".

    Returns:
        Dictionary with download status and model info.

    / Pre-descarga un modelo Whisper para que la transcripcion no de timeout.
    """
    valid_sizes = ("tiny", "base", "small", "medium")
    if model_size not in valid_sizes:
        return {"error": f"Invalid model_size. Choose from: {valid_sizes}"}

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return {"error": "faster-whisper not installed. Run: pip install faster-whisper"}

    already_cached = _is_model_cached(model_size)
    if already_cached:
        return {
            "success": True,
            "model": model_size,
            "status": "already_downloaded",
            "hint": "Model is cached locally. transcribe_audio will start instantly.",
        }

    size_estimates = {"tiny": "~75MB", "base": "~150MB", "small": "~500MB", "medium": "~1.5GB"}

    logger.info(
        f"Downloading whisper model '{model_size}' ({size_estimates.get(model_size, '?')})..."
    )

    def _download():
        start = time.time()
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        elapsed = round(time.time() - start, 1)

        # Cache it for transcribe_audio
        global _whisper_model, _whisper_model_size
        _whisper_model = model
        _whisper_model_size = model_size

        return {
            "success": True,
            "model": model_size,
            "status": "downloaded",
            "download_time_seconds": elapsed,
            "hint": "Model cached. transcribe_audio will now start instantly.",
        }

    try:
        loop = asyncio.get_running_loop()
        # 10-minute timeout for large model downloads
        result = await asyncio.wait_for(
            loop.run_in_executor(None, _download),
            timeout=600,
        )
        return result
    except asyncio.TimeoutError:
        return {"error": f"Download timed out after 10 minutes for model '{model_size}'."}
    except Exception as e:
        logger.error(f"Model download error: {e}")
        return {"error": str(e)}


async def transcribe_audio(
    audio_path: str,
    language: str = "auto",
    model_size: str = "base",
) -> dict:
    """
    Transcribe an audio file using faster-whisper (CPU, int8).

    Args:
        audio_path: Path to WAV audio file.
        language: Language code (e.g., "en", "es") or "auto" for detection.
        model_size: Whisper model size: "tiny", "base", "small", "medium".
                   Default: "base" (good accuracy/speed balance on CPU).

    Returns:
        Dictionary with transcribed text, language, and segments.

    / Transcribe un archivo de audio usando faster-whisper (CPU, int8).
    """
    if not os.path.isfile(audio_path):
        return {"error": f"Audio file not found: {audio_path}"}

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return {
            "error": "faster-whisper not installed. Run: pip install faster-whisper",
        }

    global _whisper_model, _whisper_model_size

    first_load = _whisper_model is None or _whisper_model_size != model_size

    def _transcribe():
        global _whisper_model, _whisper_model_size

        # Cache model to avoid reloading
        if _whisper_model is None or _whisper_model_size != model_size:
            if not _is_model_cached(model_size):
                logger.info(
                    f"Downloading whisper model '{model_size}' (first time, ~150MB)..."
                )
            else:
                logger.info(f"Loading whisper model: {model_size} (CPU, int8)")

            _whisper_model = WhisperModel(
                model_size,
                device="cpu",
                compute_type="int8",
            )
            _whisper_model_size = model_size

        # Transcribe
        lang = None if language == "auto" else language
        segments, info = _whisper_model.transcribe(
            audio_path,
            language=lang,
            beam_size=5,
        )

        # Collect segments
        result_segments = []
        full_text_parts = []
        for segment in segments:
            result_segments.append({
                "start": round(segment.start, 2),
                "end": round(segment.end, 2),
                "text": segment.text.strip(),
            })
            full_text_parts.append(segment.text.strip())

        full_text = " ".join(full_text_parts)

        return {
            "success": True,
            "text": full_text,
            "language": info.language,
            "language_probability": round(info.language_probability, 3),
            "duration_seconds": round(info.duration, 2),
            "segments": result_segments,
            "segment_count": len(result_segments),
            "model": model_size,
        }

    try:
        loop = asyncio.get_running_loop()
        # 5 minutes timeout: covers first-time model download + transcription
        # Subsequent calls use the cached model and are much faster
        result = await asyncio.wait_for(
            loop.run_in_executor(None, _transcribe),
            timeout=300,
        )
        if first_load:
            result["note"] = "Model loaded for the first time. Future calls will be faster."
        return result
    except asyncio.TimeoutError:
        return {
            "error": "Transcription timed out (model may still be downloading).",
            "hint": "Use download_whisper_model() first to pre-download the model.",
        }
    except Exception as e:
        logger.error(f"Transcription error: {e}")
        return {"error": str(e)}
