"""Linux TTS — Piper (default offline) + edge-tts (online) + espeak-ng (emergency).

Phase 9.5 rewrite: settings-driven, pre-generated clips for instant feedback,
Piper with high-quality es_MX-claude-high model as primary engine.

/ TTS Linux — Piper (default offline) + edge-tts (online) + espeak-ng (emergencia).
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("marlow.platform.linux.tts")

_TTS_DIR = Path.home() / ".marlow" / "audio"
_TTS_DIR.mkdir(parents=True, exist_ok=True)

# Piper voice models directory
_PIPER_VOICES_DIR = Path.home() / ".local" / "share" / "piper-voices"

# Pre-generated clips directory
_CLIPS_DIR = Path.home() / ".config" / "marlow" / "voice_clips"


# ─────────────────────────────────────────────────────────────
# Audio playback
# ─────────────────────────────────────────────────────────────

def _play_audio_linux(file_path: str) -> bool:
    """Play an audio file using available Linux player."""
    is_wav = file_path.lower().endswith(".wav")

    players = []
    if is_wav:
        players.append(("pw-play", [file_path]))
        players.append(("aplay", [file_path]))
    players.append(("mpv", ["--no-video", "--really-quiet", file_path]))
    players.append(("paplay", [file_path]))
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
                logger.debug("%s failed: exit %d", cmd, result.returncode)
            except subprocess.TimeoutExpired:
                logger.debug("%s timed out", cmd)
            except Exception as e:
                logger.debug("%s error: %s", cmd, e)

    logger.error("No audio player available")
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


# ─────────────────────────────────────────────────────────────
# Pre-generated clips — instant playback (~0ms generation)
# ─────────────────────────────────────────────────────────────

async def play_clip(clip_name: str) -> bool:
    """Play a pre-generated voice clip by name. Returns True if played."""
    clip_path = _CLIPS_DIR / f"{clip_name}.wav"
    if not clip_path.exists():
        logger.debug("Clip not found: %s", clip_path)
        return False
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _play_audio_linux, str(clip_path))


def generate_clips(user_name: str = ""):
    """Generate static voice clips using Piper for instant playback.

    Call during onboarding or when user changes name.
    """
    _CLIPS_DIR.mkdir(parents=True, exist_ok=True)

    phrases = {
        "si": f"Sí, {user_name}" if user_name else "Sí",
        "dame_un_momento": f"Dame un momento, {user_name}" if user_name else "Dame un momento",
        "listo": "Listo",
        "entendido": "Entendido",
        "no_pude": "No pude hacerlo",
        "en_que_te_ayudo": "¿En qué te puedo ayudar?",
        "buscando": "Déjame buscar eso",
        "abriendo": "Abriendo",
        "un_segundo": "Un segundo",
        "tomando_mas": "Esto está tomando un poco más",
    }

    model_path = _find_piper_model()
    if not model_path:
        logger.warning("No Piper model found, cannot generate clips")
        return

    generated = 0
    for name, text in phrases.items():
        out_path = _CLIPS_DIR / f"{name}.wav"
        if out_path.exists():
            continue  # Don't regenerate existing clips
        try:
            result = subprocess.run(
                ["piper", "-m", str(model_path), "-f", str(out_path)],
                input=text, capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0:
                generated += 1
            else:
                logger.warning("Clip generation failed for '%s': %s", name, result.stderr[:100])
        except Exception as e:
            logger.warning("Clip generation error for '%s': %s", name, e)

    logger.info("Generated %d voice clips in %s", generated, _CLIPS_DIR)


def regenerate_clips(user_name: str):
    """Regenerate personalized clips (after name change)."""
    # Delete existing personalized clips
    for name in ("si", "dame_un_momento"):
        clip = _CLIPS_DIR / f"{name}.wav"
        clip.unlink(missing_ok=True)
    generate_clips(user_name)


# ─────────────────────────────────────────────────────────────
# Piper TTS — default offline engine
# ─────────────────────────────────────────────────────────────

def _find_piper_model(voice_name: str = "") -> Optional[Path]:
    """Find a Piper ONNX model file.

    Checks settings for preferred voice, falls back to auto-detection.
    """
    if not voice_name:
        try:
            from marlow.core.settings import get_settings
            voice_name = get_settings().tts.piper_voice
        except Exception:
            voice_name = ""

    # Search order: exact match, then es_MX high, then es_MX medium, then any es_
    search_names = []
    if voice_name:
        search_names.append(voice_name)
    search_names.extend(["es_MX-claude-high", "es_MX-ald-medium"])

    for name in search_names:
        onnx = _PIPER_VOICES_DIR / f"{name}.onnx"
        if onnx.exists():
            return onnx

    # Last resort: any es_ model in the directory
    if _PIPER_VOICES_DIR.exists():
        for f in sorted(_PIPER_VOICES_DIR.iterdir()):
            if f.name.startswith("es_") and f.name.endswith(".onnx"):
                return f

    return None


async def _speak_piper(text: str, language: str) -> dict:
    """Synthesize and play via Piper TTS."""
    if not shutil.which("piper"):
        return {"error": "piper not installed", "_fallback": True}

    model_path = _find_piper_model()
    if not model_path:
        return {"error": "No Piper Spanish model found", "_fallback": True}

    ts = time.strftime("%Y%m%d_%H%M%S")
    output_path = _TTS_DIR / f"tts_piper_{ts}.wav"

    def _synth():
        r = subprocess.run(
            ["piper", "-m", str(model_path), "-f", str(output_path)],
            input=text, capture_output=True, text=True, timeout=30,
        )
        return r.returncode == 0

    loop = asyncio.get_running_loop()
    start = time.monotonic()
    ok = await loop.run_in_executor(None, _synth)
    synth_time = time.monotonic() - start

    if not ok or not output_path.exists() or output_path.stat().st_size == 0:
        return {"error": "Piper synthesis failed", "_fallback": True}

    played = await loop.run_in_executor(None, _play_audio_linux, str(output_path))
    _cleanup_tts_files()

    if not played:
        return {"error": "Audio playback failed after Piper synthesis", "_fallback": True}

    return {
        "success": True,
        "engine": "piper",
        "model": model_path.stem,
        "text": text,
        "language": language,
        "char_count": len(text),
        "synth_time_s": round(synth_time, 2),
    }


# ─────────────────────────────────────────────────────────────
# edge-tts — online engine (higher quality when internet available)
# ─────────────────────────────────────────────────────────────

async def _speak_edge_tts(text: str, language: str, voice_name: Optional[str], rate: int) -> dict:
    """Generate speech with edge-tts and play."""
    try:
        import edge_tts
    except ImportError:
        return {"error": "edge-tts not installed", "_fallback": True}

    # Resolve voice
    if not voice_name:
        try:
            from marlow.core.settings import get_settings
            voice_name = get_settings().tts.edge_tts_voice
        except Exception:
            voice_name = "es-MX-JorgeNeural"

    rate_offset = round((rate - 175) / 175 * 100)
    rate_str = f"{rate_offset:+d}%"

    ts = time.strftime("%Y%m%d_%H%M%S")
    output_path = _TTS_DIR / f"tts_edge_{ts}.mp3"

    try:
        communicate = edge_tts.Communicate(text, voice_name, rate=rate_str)
        await communicate.save(str(output_path))
    except Exception as e:
        logger.debug("edge-tts failed: %s", e)
        return {"error": f"edge-tts failed: {e}", "_fallback": True}

    if not output_path.exists() or output_path.stat().st_size == 0:
        return {"error": "edge-tts produced empty audio", "_fallback": True}

    loop = asyncio.get_running_loop()
    played = await loop.run_in_executor(None, _play_audio_linux, str(output_path))
    _cleanup_tts_files()

    if not played:
        return {"error": "Audio playback failed after edge-tts", "_fallback": True}

    return {
        "success": True,
        "engine": "edge-tts",
        "text": text,
        "language": language,
        "voice": voice_name,
        "rate": rate,
        "char_count": len(text),
    }


# ─────────────────────────────────────────────────────────────
# espeak-ng — emergency fallback (always available)
# ─────────────────────────────────────────────────────────────

async def _speak_espeak(text: str, language: str) -> dict:
    """Speak via espeak-ng — robotic but universal."""
    if not shutil.which("espeak-ng"):
        return {"error": "espeak-ng not installed"}

    lang_code = "es" if language.startswith("es") else language

    def _run():
        return subprocess.run(
            ["espeak-ng", "-v", lang_code, text],
            capture_output=True, timeout=30,
        )

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, _run)
        if result.returncode == 0:
            return {
                "success": True,
                "engine": "espeak-ng",
                "text": text,
                "language": language,
                "char_count": len(text),
            }
        return {"error": f"espeak-ng failed: exit {result.returncode}"}
    except Exception as e:
        return {"error": f"espeak-ng error: {e}"}


# ─────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────

def _detect_language(text: str) -> str:
    """Simple language detection: Spanish if common Spanish chars/words present."""
    text_lower = text.lower()
    es_indicators = ["á", "é", "í", "ó", "ú", "ñ", "¿", "¡",
                     " el ", " la ", " de ", " que ", " en ", " es ",
                     " un ", " por ", " con ", " para "]
    score = sum(1 for ind in es_indicators if ind in text_lower)
    return "es" if score >= 2 else "en"


def _resolve_engine_order() -> list[str]:
    """Get TTS engine preference from settings."""
    try:
        from marlow.core.settings import get_settings
        engine = get_settings().tts.engine
    except Exception:
        engine = "auto"

    if engine == "auto":
        return ["piper", "edge-tts", "espeak"]
    elif engine == "piper":
        return ["piper", "edge-tts", "espeak"]
    elif engine == "edge-tts":
        return ["edge-tts", "piper", "espeak"]
    elif engine == "espeak":
        return ["espeak"]
    else:
        return ["piper", "edge-tts", "espeak"]


async def speak(
    text: str,
    language: str = "auto",
    voice: Optional[str] = None,
    rate: int = 175,
) -> dict:
    """Speak text aloud using the configured TTS chain.

    Default chain: Piper (offline) → edge-tts (online) → espeak-ng (emergency).
    """
    if not text or not text.strip():
        return {"error": "No text provided"}

    detected_lang = _detect_language(text) if language == "auto" else language
    engines = _resolve_engine_order()
    last_error = ""

    for engine in engines:
        if engine == "piper":
            result = await _speak_piper(text, detected_lang)
        elif engine == "edge-tts":
            result = await _speak_edge_tts(text, detected_lang, voice, rate)
        elif engine == "espeak":
            result = await _speak_espeak(text, detected_lang)
        else:
            continue

        if result.get("success"):
            result["language"] = detected_lang
            return result

        last_error = result.get("error", "unknown")
        logger.info("%s failed (%s), trying next engine", engine, last_error)

    return {"error": f"All TTS engines failed. Last: {last_error}"}


async def speak_and_listen(
    text: str,
    timeout: int = 10,
    language: str = "auto",
    voice: Optional[str] = None,
) -> dict:
    """Speak text, then listen for a voice response."""
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
