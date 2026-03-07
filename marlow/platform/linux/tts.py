"""Linux TTS — edge-tts + Piper with Linux audio playback.

Replaces Windows MCI playback with pw-play/aplay/mpv.
Drops pyttsx3 (SAPI5) fallback — not available on Linux.

/ TTS Linux — edge-tts + Piper con reproduccion de audio Linux.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("marlow.platform.linux.tts")

_TTS_DIR = Path.home() / ".marlow" / "audio"
_TTS_DIR.mkdir(parents=True, exist_ok=True)


def _play_audio_linux(file_path: str) -> bool:
    """Play an audio file using available Linux player.

    Tries pw-play (PipeWire), then aplay (ALSA WAV only),
    then mpv (universal), then paplay (PulseAudio).
    """
    is_wav = file_path.lower().endswith(".wav")

    # Try players in order of preference
    players = []
    if is_wav:
        players.append(("pw-play", [file_path]))
        players.append(("aplay", [file_path]))
    players.append(("mpv", ["--no-video", "--really-quiet", file_path]))
    players.append(("paplay", [file_path]))  # PulseAudio (WAV only)
    if not is_wav:
        players.append(("pw-play", [file_path]))

    for cmd, args in players:
        if shutil.which(cmd):
            try:
                result = subprocess.run(
                    [cmd] + args,
                    capture_output=True, timeout=60,
                )
                if result.returncode == 0:
                    return True
                logger.debug("%s failed with exit code %d", cmd, result.returncode)
            except subprocess.TimeoutExpired:
                logger.debug("%s timed out", cmd)
            except Exception as e:
                logger.debug("%s error: %s", cmd, e)

    logger.error("No audio player available. Install: pw-play, aplay, or mpv")
    return False


def _cleanup_tts_files(max_age_seconds: int = 600):
    """Remove TTS audio files older than max_age."""
    now = time.time()
    try:
        for f in _TTS_DIR.iterdir():
            if f.name.startswith("tts_") and (now - f.stat().st_mtime) > max_age_seconds:
                f.unlink(missing_ok=True)
    except Exception:
        pass


async def speak(
    text: str,
    language: str = "auto",
    voice: Optional[str] = None,
    rate: int = 175,
) -> dict:
    """Speak text aloud. edge-tts (primary) -> Piper (offline fallback).

    / Habla texto en voz alta. edge-tts (primario) -> Piper (fallback offline).
    """
    if not text or not text.strip():
        return {"error": "No text provided"}

    # Import language detection from the shared tts module
    from marlow.tools.tts import _detect_language, _resolve_edge_voice, _EDGE_VOICES

    detected_lang = _detect_language(text) if language == "auto" else language

    # Try edge-tts first
    result = await _speak_edge_tts_linux(text, detected_lang, voice, rate)

    # Fallback: Piper TTS (offline)
    if result.get("_fallback") or "error" in result:
        edge_error = result.get("error", "unknown")
        logger.info("edge-tts failed (%s), trying Piper TTS", edge_error)
        result = await _speak_piper_linux(text, detected_lang, rate)
        if "success" in result:
            result["note"] = f"Used Piper offline (edge-tts error: {edge_error})"

    return result


async def speak_and_listen(
    text: str,
    timeout: int = 10,
    language: str = "auto",
    voice: Optional[str] = None,
) -> dict:
    """Speak text, then listen for a voice response.

    / Habla texto, luego escucha una respuesta de voz.
    """
    speak_result = await speak(text=text, language=language, voice=voice)
    if "error" in speak_result:
        return speak_result

    detected_lang = speak_result.get("language", "auto")

    from marlow.tools.voice import listen_for_command
    listen_result = await listen_for_command(
        duration_seconds=min(timeout, 60),
        language=detected_lang,
    )

    if "error" in listen_result:
        return {
            "success": False,
            "spoke": {
                "text": text,
                "language": detected_lang,
                "engine": speak_result.get("engine"),
            },
            "heard": {"error": listen_result["error"]},
        }

    return {
        "success": True,
        "spoke": {
            "text": text,
            "language": detected_lang,
            "engine": speak_result.get("engine"),
        },
        "heard": {
            "text": listen_result.get("text", ""),
            "language": listen_result.get("language"),
        },
    }


async def _speak_edge_tts_linux(
    text: str, language: str, voice_name: Optional[str], rate: int,
) -> dict:
    """Generate speech with edge-tts and play via Linux player."""
    try:
        import edge_tts
    except ImportError:
        return {"error": "edge-tts not installed. Run: pip install edge-tts", "_fallback": True}

    from marlow.tools.tts import _resolve_edge_voice

    voice_id = _resolve_edge_voice(language, voice_name)
    rate_offset = round((rate - 175) / 175 * 100)
    rate_str = f"{rate_offset:+d}%"

    ts = time.strftime("%Y%m%d_%H%M%S")
    output_path = _TTS_DIR / f"tts_{ts}.mp3"

    try:
        communicate = edge_tts.Communicate(text, voice_id, rate=rate_str)
        await communicate.save(str(output_path))
    except Exception as e:
        logger.warning("edge-tts generation failed: %s", e)
        return {"error": f"edge-tts failed: {e}", "_fallback": True}

    if not output_path.exists() or output_path.stat().st_size == 0:
        return {"error": "edge-tts produced empty audio", "_fallback": True}

    loop = asyncio.get_running_loop()
    played = await loop.run_in_executor(None, _play_audio_linux, str(output_path))
    _cleanup_tts_files()

    if not played:
        return {"error": "Linux audio playback failed", "_fallback": True}

    return {
        "success": True,
        "engine": "edge-tts",
        "text": text,
        "language": language,
        "voice": voice_id,
        "rate": rate,
        "char_count": len(text),
    }


async def _speak_piper_linux(text: str, language: str, rate: int) -> dict:
    """Speak via Piper TTS with Linux playback."""
    from marlow.tools.tts import _get_piper

    piper = _get_piper()
    if not piper.available:
        return {"error": "Piper TTS not available", "_fallback": True}

    def _synth():
        ts = time.strftime("%Y%m%d_%H%M%S")
        out = str(_TTS_DIR / f"tts_piper_{ts}.wav")
        return piper.synthesize(text, language=language, output_path=out)

    try:
        loop = asyncio.get_running_loop()
        wav_path = await loop.run_in_executor(None, _synth)
    except Exception as e:
        return {"error": f"Piper synthesis failed: {e}", "_fallback": True}

    if not wav_path:
        return {"error": "Piper produced no audio", "_fallback": True}

    loop = asyncio.get_running_loop()
    played = await loop.run_in_executor(None, _play_audio_linux, wav_path)

    if not played:
        return {"error": "Linux playback of Piper audio failed", "_fallback": True}

    return {
        "success": True,
        "engine": "piper",
        "text": text,
        "language": language,
        "char_count": len(text),
    }
