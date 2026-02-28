"""
Marlow TTS Tools

Text-to-speech with two engines:
1. Primary: edge-tts — Microsoft Edge neural voices (high quality,
   async, no Windows voice packs needed). Requires internet.
2. Fallback: pyttsx3 — Windows SAPI5 (offline, lower quality).

Audio playback via Windows MCI API (ctypes) — plays MP3 natively,
zero external deps for playback.

/ Text-to-speech con dos motores:
/ 1. Primario: edge-tts — voces neurales de Microsoft Edge (alta calidad).
/ 2. Fallback: pyttsx3 — SAPI5 de Windows (offline, menor calidad).
"""

import os
import time
import ctypes
import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger("marlow.tools.tts")

# Audio storage directory (reuse marlow audio dir)
_TTS_DIR = Path.home() / ".marlow" / "audio"
_TTS_DIR.mkdir(parents=True, exist_ok=True)

# Common Spanish words for language detection
_SPANISH_WORDS = {
    "que", "como", "para", "pero", "hola", "gracias", "por",
    "favor", "bien", "esta", "este", "esto", "son", "los",
    "las", "una", "uno", "del", "con", "sin", "mas", "tiene",
    "puede", "quiero", "necesito", "donde", "cuando", "porque",
    "ahora", "aqui", "todo", "nada", "muy", "algo", "tambien",
    "siempre", "nunca", "bueno", "malo", "hacer", "saber",
    "soy", "eres", "somos", "tengo", "vamos", "mira", "dime",
}

# ── Edge-TTS voice mapping ──
_EDGE_VOICES = {
    "es": "es-MX-DaliaNeural",
    "en": "en-US-JennyNeural",
}

_EDGE_VOICE_ALIASES = {
    # Spanish
    "dalia": "es-MX-DaliaNeural",
    "jorge": "es-MX-JorgeNeural",
    "elvira": "es-ES-ElviraNeural",
    "alvaro": "es-ES-AlvaroNeural",
    # English
    "jenny": "en-US-JennyNeural",
    "guy": "en-US-GuyNeural",
    "sonia": "en-GB-SoniaNeural",
    "ryan": "en-GB-RyanNeural",
}

# ── SAPI5 voice keywords (pyttsx3 fallback) ──
_SAPI5_KEYWORDS = {
    "es": ["spanish", "español", "sabina", "helena", "pablo"],
    "en": ["english", "david", "zira", "mark", "hazel"],
}


# ─────────────────────────────────────────────────────────────
# Language Detection
# ─────────────────────────────────────────────────────────────

def _detect_language(text: str) -> str:
    """
    Detect if text is Spanish or English.
    Checks for Spanish-specific characters and common words.

    Returns:
        "es" or "en".

    / Detecta si el texto es español o ingles.
    """
    spanish_chars = set("ñ¿¡áéíóúü")
    if any(c in spanish_chars for c in text.lower()):
        return "es"

    # Strip punctuation before word matching
    import re
    clean = re.sub(r"[^\w\s]", "", text.lower())
    words = set(clean.split())
    matches = words & _SPANISH_WORDS
    if len(matches) >= 2:
        return "es"

    return "en"


# ─────────────────────────────────────────────────────────────
# Audio Playback — Windows MCI API (plays MP3/WAV natively)
# ─────────────────────────────────────────────────────────────

def _play_audio_mci(file_path: str) -> bool:
    """
    Play an audio file (MP3 or WAV) using Windows MCI API via ctypes.
    Blocks until playback finishes. Zero external dependencies.

    / Reproduce audio (MP3 o WAV) usando la API MCI de Windows.
    """
    try:
        winmm = ctypes.windll.winmm
        # Use short alias to avoid path issues
        alias = "marlow_tts"
        buf = ctypes.create_unicode_buffer(256)

        # Close any previous instance
        winmm.mciSendStringW(f"close {alias}", None, 0, 0)

        # Open the file
        cmd_open = f'open "{file_path}" type mpegvideo alias {alias}'
        err = winmm.mciSendStringW(cmd_open, None, 0, 0)
        if err != 0:
            winmm.mciGetErrorStringW(err, buf, 256)
            logger.debug(f"MCI open error: {buf.value}")
            return False

        # Play and wait for completion
        err = winmm.mciSendStringW(f"play {alias} wait", None, 0, 0)
        if err != 0:
            winmm.mciGetErrorStringW(err, buf, 256)
            logger.debug(f"MCI play error: {buf.value}")

        # Close
        winmm.mciSendStringW(f"close {alias}", None, 0, 0)
        return err == 0

    except Exception as e:
        logger.error(f"MCI playback error: {e}")
        return False


def _cleanup_tts_files(max_age_seconds: int = 600):
    """Remove TTS audio files older than max_age (default: 10 minutes)."""
    now = time.time()
    try:
        for f in _TTS_DIR.iterdir():
            if f.name.startswith("tts_") and (now - f.stat().st_mtime) > max_age_seconds:
                f.unlink(missing_ok=True)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# Edge-TTS Engine (primary — high quality neural voices)
# ─────────────────────────────────────────────────────────────

def _resolve_edge_voice(language: str, voice_name: Optional[str] = None) -> str:
    """
    Resolve an edge-tts voice ID from language or alias.

    / Resuelve un voice ID de edge-tts desde idioma o alias.
    """
    if voice_name:
        # Check aliases first (user-friendly names like "dalia", "jenny")
        alias_match = _EDGE_VOICE_ALIASES.get(voice_name.lower())
        if alias_match:
            return alias_match
        # If it looks like a full voice ID (contains "-"), use as-is
        if "-" in voice_name:
            return voice_name

    # Default by language
    return _EDGE_VOICES.get(language, _EDGE_VOICES["en"])


async def _speak_edge_tts(
    text: str,
    language: str,
    voice_name: Optional[str],
    rate: int,
) -> dict:
    """
    Generate speech with edge-tts and play via MCI.

    / Genera voz con edge-tts y reproduce via MCI.
    """
    try:
        import edge_tts
    except ImportError:
        return {"error": "edge-tts not installed. Run: pip install edge-tts"}

    voice_id = _resolve_edge_voice(language, voice_name)

    # edge-tts rate: "+0%" is normal, "+50%" is faster, "-50%" is slower
    # Convert from WPM-ish (175 default) to percentage offset
    rate_offset = round((rate - 175) / 175 * 100)
    rate_str = f"{rate_offset:+d}%"

    # Generate MP3 to temp file
    ts = time.strftime("%Y%m%d_%H%M%S")
    output_path = _TTS_DIR / f"tts_{ts}.mp3"

    try:
        communicate = edge_tts.Communicate(text, voice_id, rate=rate_str)
        await communicate.save(str(output_path))
    except Exception as e:
        logger.warning(f"edge-tts generation failed: {e}")
        return {"error": f"edge-tts failed: {e}", "_fallback": True}

    if not output_path.exists() or output_path.stat().st_size == 0:
        return {"error": "edge-tts produced empty audio", "_fallback": True}

    # Play via MCI in executor (blocking call)
    loop = asyncio.get_running_loop()
    played = await loop.run_in_executor(None, _play_audio_mci, str(output_path))

    # Cleanup old files
    _cleanup_tts_files()

    if not played:
        return {"error": "MCI playback failed", "_fallback": True}

    return {
        "success": True,
        "engine": "edge-tts",
        "text": text,
        "language": language,
        "voice": voice_id,
        "rate": rate,
        "rate_str": rate_str,
        "char_count": len(text),
    }


# ─────────────────────────────────────────────────────────────
# pyttsx3 Engine (fallback — offline SAPI5)
# ─────────────────────────────────────────────────────────────

def _select_sapi5_voice(engine: object, language: str, voice_name: Optional[str] = None):
    """
    Select a SAPI5 voice by language or specific name.

    / Selecciona una voz SAPI5 por idioma o nombre especifico.
    """
    voices = engine.getProperty("voices")
    if not voices:
        return

    if voice_name:
        name_lower = voice_name.lower()
        for v in voices:
            if name_lower in v.name.lower():
                engine.setProperty("voice", v.id)
                return

    keywords = _SAPI5_KEYWORDS.get(language, _SAPI5_KEYWORDS["en"])
    for v in voices:
        v_lower = v.name.lower()
        for kw in keywords:
            if kw in v_lower:
                engine.setProperty("voice", v.id)
                return


async def _speak_pyttsx3(
    text: str,
    language: str,
    voice_name: Optional[str],
    rate: int,
) -> dict:
    """
    Speak via pyttsx3 SAPI5 (offline fallback).
    Fresh engine per call to avoid COM threading deadlocks.

    / Habla via pyttsx3 SAPI5 (fallback offline).
    """
    def _speak_sync():
        try:
            import pyttsx3
        except ImportError:
            return {"error": "pyttsx3 not installed. Run: pip install pyttsx3"}

        try:
            engine = pyttsx3.init()
            engine.setProperty("rate", rate)
            _select_sapi5_voice(engine, language, voice_name)

            selected_voice = "default"
            try:
                voices = engine.getProperty("voices")
                current_id = engine.getProperty("voice")
                for v in voices:
                    if v.id == current_id:
                        selected_voice = v.name
                        break
            except Exception:
                pass

            engine.say(text)
            engine.runAndWait()
            engine.stop()

            return {
                "success": True,
                "engine": "pyttsx3 (offline fallback)",
                "text": text,
                "language": language,
                "voice": selected_voice,
                "rate": rate,
                "char_count": len(text),
            }
        except Exception as e:
            logger.error(f"pyttsx3 error: {e}")
            return {"error": str(e)}

    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _speak_sync)
    except Exception as e:
        logger.error(f"pyttsx3 executor error: {e}")
        return {"error": str(e)}


# ─────────────────────────────────────────────────────────────
# Public MCP Tools
# ─────────────────────────────────────────────────────────────

async def speak(
    text: str,
    language: str = "auto",
    voice: Optional[str] = None,
    rate: int = 175,
) -> dict:
    """
    Speak text aloud using text-to-speech.

    Primary engine: edge-tts (Microsoft neural voices, high quality).
    Fallback: pyttsx3 SAPI5 (offline, if edge-tts fails / no internet).

    Args:
        text: Text to speak aloud.
        language: "auto" (detect), "es" (Spanish), or "en" (English).
        voice: Voice name or alias. Edge-tts aliases: "dalia", "jorge",
               "elvira" (Spanish), "jenny", "guy", "sonia" (English).
               Full IDs also accepted (e.g., "es-MX-DaliaNeural").
        rate: Speech rate in words per minute (default: 175).

    Returns:
        Dict with success status, engine used, detected language, voice, etc.

    / Habla texto en voz alta. Motor primario: edge-tts. Fallback: pyttsx3.
    """
    if not text or not text.strip():
        return {"error": "No text provided"}

    detected_lang = _detect_language(text) if language == "auto" else language

    # Try edge-tts first (high quality neural voices)
    result = await _speak_edge_tts(text, detected_lang, voice, rate)

    # Fallback to pyttsx3 if edge-tts failed
    if result.get("_fallback") or "error" in result:
        edge_error = result.get("error", "unknown")
        logger.info(f"edge-tts failed ({edge_error}), falling back to pyttsx3")
        result = await _speak_pyttsx3(text, detected_lang, voice, rate)
        if "success" in result:
            result["note"] = f"Used offline fallback (edge-tts error: {edge_error})"

    return result


async def speak_and_listen(
    text: str,
    timeout: int = 10,
    language: str = "auto",
    voice: Optional[str] = None,
) -> dict:
    """
    Speak text, then listen for a voice response.
    Combines speak() + listen_for_command() for conversational flows.

    Args:
        text: Text to speak first.
        timeout: How long to listen after speaking (default: 10s, max: 60s).
        language: "auto", "es", or "en".
        voice: Voice name or alias.

    Returns:
        Dict with spoke/heard results.

    / Habla texto, luego escucha una respuesta de voz.
    """
    # Step 1: Speak
    speak_result = await speak(text=text, language=language, voice=voice)
    if "error" in speak_result:
        return speak_result

    detected_lang = speak_result.get("language", "auto")

    # Step 2: Listen
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
                "voice": speak_result.get("voice", "default"),
                "engine": speak_result.get("engine"),
            },
            "heard": {"error": listen_result["error"]},
        }

    return {
        "success": True,
        "spoke": {
            "text": text,
            "language": detected_lang,
            "voice": speak_result.get("voice", "default"),
            "engine": speak_result.get("engine"),
        },
        "heard": {
            "text": listen_result.get("text", ""),
            "language": listen_result.get("language"),
            "silence_warning": listen_result.get("silence_warning"),
        },
    }
