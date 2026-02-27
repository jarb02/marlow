"""
Marlow Voice Command Tool

Records microphone audio and transcribes it for voice commands.
MCP tool starts recording immediately when called by the AI —
no hotkey waiting needed.

Includes basic silence detection (RMS below threshold).

/ Graba audio del micrófono y lo transcribe para comandos de voz.
/ La herramienta MCP empieza a grabar inmediatamente cuando la llama el AI.
"""

import os
import struct
import logging
from typing import Optional

logger = logging.getLogger("marlow.tools.voice")

# RMS threshold for silence detection
SILENCE_RMS_THRESHOLD = 500


def _compute_rms(audio_path: str) -> float:
    """Compute RMS (root mean square) of a WAV file for silence detection."""
    import wave

    try:
        with wave.open(audio_path, "rb") as wf:
            n_frames = wf.getnframes()
            if n_frames == 0:
                return 0.0

            data = wf.readframes(n_frames)
            n_channels = wf.getnchannels()
            sample_width = wf.getsampwidth()

            # Only handle 16-bit audio
            if sample_width != 2:
                return -1.0

            # Unpack samples
            n_samples = len(data) // 2
            samples = struct.unpack(f"<{n_samples}h", data)

            # If stereo, average channels
            if n_channels == 2:
                samples = [
                    (samples[i] + samples[i + 1]) / 2
                    for i in range(0, len(samples) - 1, 2)
                ]

            # Compute RMS
            if not samples:
                return 0.0
            sum_sq = sum(s * s for s in samples)
            rms = (sum_sq / len(samples)) ** 0.5
            return rms

    except Exception as e:
        logger.debug(f"RMS computation error: {e}")
        return -1.0


async def listen_for_command(
    duration_seconds: int = 10,
    language: str = "auto",
    model_size: str = "base",
) -> dict:
    """
    Listen for a voice command via microphone.

    Records immediately (no hotkey wait), transcribes the audio,
    and returns the text. Basic silence detection warns if no
    speech was detected.

    Args:
        duration_seconds: How long to listen (max 60s). Default: 10.
        language: Language code or "auto". Default: "auto".
        model_size: Whisper model size. Default: "base".

    Returns:
        Dictionary with transcribed text, silence detection, and audio info.

    / Escucha un comando de voz via micrófono.
    / Graba inmediatamente, transcribe el audio, y devuelve el texto.
    """
    # Cap at 60 seconds for voice commands (not long recordings)
    duration_seconds = min(duration_seconds, 60)

    # Record from microphone
    from marlow.tools.audio import capture_mic_audio, transcribe_audio

    record_result = await capture_mic_audio(duration_seconds=duration_seconds)
    if "error" in record_result:
        return record_result

    audio_path = record_result["audio_path"]

    # Check for silence
    rms = _compute_rms(audio_path)
    is_silent = 0 <= rms < SILENCE_RMS_THRESHOLD

    if is_silent:
        # Still transcribe, but warn
        logger.info(f"Low audio level detected (RMS: {rms:.0f})")

    # Transcribe
    transcribe_result = await transcribe_audio(
        audio_path=audio_path,
        language=language,
        model_size=model_size,
    )

    # Clean up temp audio file
    try:
        os.unlink(audio_path)
    except Exception:
        pass

    if "error" in transcribe_result:
        return transcribe_result

    result = {
        "success": True,
        "text": transcribe_result.get("text", ""),
        "language": transcribe_result.get("language"),
        "duration_seconds": duration_seconds,
        "segments": transcribe_result.get("segments", []),
        "rms_level": round(rms, 1) if rms >= 0 else None,
    }

    if is_silent:
        result["silence_warning"] = (
            "Low audio level detected. No speech may have been captured. "
            "Check that the microphone is connected and not muted."
        )

    return result
