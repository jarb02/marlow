"""
Marlow Setup Wizard & Diagnostics

First-use experience: detects hardware, pre-downloads models, configures defaults.
Runs automatically on first launch, never again (unless setup_complete.json deleted).

Also provides run_diagnostics() MCP tool for troubleshooting.
"""

import json
import logging
import sys
import time
from pathlib import Path

from marlow.core.config import CONFIG_DIR, CONFIG_FILE, MarlowConfig

logger = logging.getLogger("marlow.setup")

SETUP_FILE = CONFIG_DIR / "setup_complete.json"


def is_first_run() -> bool:
    """Check if this is the first time Marlow is running."""
    return not SETUP_FILE.exists()


def run_setup_wizard() -> dict:
    """
    Run the first-use setup wizard. 8 steps, each logs progress.
    Synchronous — called from main() before the event loop starts.
    Never raises — catches all errors per step.

    / Wizard de primera ejecucion: detecta hardware, pre-descarga modelos.
    """
    results = {}
    logger.info("=" * 50)
    logger.info("  Marlow First-Use Setup Wizard")
    logger.info("=" * 50)

    # ── Step 1: Python version ──
    try:
        ver = sys.version_info
        if ver >= (3, 10):
            results["python"] = {
                "status": "ok",
                "detail": f"Python {ver.major}.{ver.minor}.{ver.micro}",
            }
            logger.info(f"  [1/8] Python version: {ver.major}.{ver.minor}.{ver.micro}")
        else:
            results["python"] = {
                "status": "warning",
                "detail": f"Python {ver.major}.{ver.minor} — 3.10+ recommended",
            }
            logger.warning(f"  [1/8] Python {ver.major}.{ver.minor} — 3.10+ recommended")
    except Exception as e:
        results["python"] = {"status": "warning", "detail": str(e)}
        logger.warning(f"  [1/8] Python check failed: {e}")

    # ── Step 2: Detect monitors ──
    try:
        from marlow.tools import background
        monitors = background._manager._enumerate_monitors()
        count = len(monitors)
        results["monitors"] = {
            "status": "ok",
            "detail": f"{count} monitor(s) detected",
            "count": count,
        }
        if count >= 2:
            logger.info(f"  [2/8] Monitors: {count} detected — dual monitor mode available")
        else:
            logger.info(f"  [2/8] Monitors: {count} detected — offscreen mode available")
    except Exception as e:
        results["monitors"] = {"status": "warning", "detail": str(e)}
        logger.warning(f"  [2/8] Monitor detection failed: {e}")

    # ── Step 3: Detect microphone ──
    try:
        import sounddevice as sd
        devices = sd.query_devices(kind="input")
        if devices is not None:
            name = devices.get("name", "Unknown") if isinstance(devices, dict) else "Available"
            results["microphone"] = {"status": "ok", "detail": name}
            logger.info(f"  [3/8] Microphone: {name}")
        else:
            results["microphone"] = {"status": "warning", "detail": "No input device found"}
            logger.warning("  [3/8] Microphone: no input device found")
    except Exception as e:
        results["microphone"] = {"status": "skip", "detail": str(e)}
        logger.warning(f"  [3/8] Microphone detection failed: {e}")

    # ── Step 4: Detect OCR engines ──
    try:
        from marlow.tools.ocr import _windows_ocr_available, _find_tesseract
        ocr_engines = []
        if _windows_ocr_available():
            ocr_engines.append("windows_ocr")
        tess_path = _find_tesseract()
        if tess_path:
            ocr_engines.append(f"tesseract ({tess_path})")
        if ocr_engines:
            results["ocr"] = {"status": "ok", "detail": ", ".join(ocr_engines)}
            logger.info(f"  [4/8] OCR engines: {', '.join(ocr_engines)}")
        else:
            results["ocr"] = {
                "status": "warning",
                "detail": "No OCR engines available (install winrt-Windows.Media.Ocr or Tesseract)",
            }
            logger.warning("  [4/8] OCR: no engines available")
    except Exception as e:
        results["ocr"] = {"status": "skip", "detail": str(e)}
        logger.warning(f"  [4/8] OCR check failed: {e}")

    # ── Step 5: Detect TTS engines ──
    try:
        tts_engines = []
        try:
            import edge_tts  # noqa: F401
            tts_engines.append("edge-tts")
        except ImportError:
            pass
        try:
            import pyttsx3  # noqa: F401
            tts_engines.append("pyttsx3")
        except ImportError:
            pass

        if tts_engines:
            results["tts"] = {"status": "ok", "detail": ", ".join(tts_engines)}
            logger.info(f"  [5/8] TTS engines: {', '.join(tts_engines)}")
        else:
            results["tts"] = {"status": "warning", "detail": "No TTS engine found"}
            logger.warning("  [5/8] TTS: no engines available")
    except Exception as e:
        results["tts"] = {"status": "warning", "detail": str(e)}
        logger.warning(f"  [5/8] TTS check failed: {e}")

    # ── Step 6: Pre-download Whisper model ──
    try:
        import asyncio as _aio
        from marlow.tools import audio

        logger.info("  [6/8] Whisper model: checking cache...")
        loop = _aio.new_event_loop()
        try:
            result = loop.run_until_complete(
                _aio.wait_for(audio.download_whisper_model("base"), timeout=120)
            )
            if result.get("success"):
                results["whisper"] = {"status": "ok", "detail": "base model ready"}
                logger.info("  [6/8] Whisper model: base model cached")
            elif result.get("already_cached"):
                results["whisper"] = {"status": "ok", "detail": "base model already cached"}
                logger.info("  [6/8] Whisper model: already cached")
            else:
                results["whisper"] = {
                    "status": "warning",
                    "detail": result.get("error", "download issue"),
                }
                logger.warning(f"  [6/8] Whisper model: {result.get('error', 'issue')}")
        finally:
            loop.close()
    except Exception as e:
        results["whisper"] = {"status": "skip", "detail": str(e)}
        logger.warning(f"  [6/8] Whisper model download failed: {e}")

    # ── Step 7: Create default config ──
    try:
        if not CONFIG_FILE.exists():
            config = MarlowConfig()
            config.save()
            results["config"] = {"status": "ok", "detail": "Default config created"}
            logger.info(f"  [7/8] Config: created at {CONFIG_FILE}")
        else:
            results["config"] = {"status": "ok", "detail": "Config already exists"}
            logger.info(f"  [7/8] Config: already exists at {CONFIG_FILE}")
    except Exception as e:
        results["config"] = {"status": "warning", "detail": str(e)}
        logger.warning(f"  [7/8] Config creation failed: {e}")

    # ── Step 8: Summary + save setup marker ──
    ok_count = sum(1 for v in results.values() if v["status"] == "ok")
    warn_count = sum(1 for v in results.values() if v["status"] == "warning")
    skip_count = sum(1 for v in results.values() if v["status"] == "skip")

    summary = f"{ok_count} OK, {warn_count} warnings, {skip_count} skipped"
    results["summary"] = summary

    try:
        SETUP_FILE.parent.mkdir(parents=True, exist_ok=True)
        setup_data = {
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "results": results,
        }
        with open(SETUP_FILE, "w", encoding="utf-8") as f:
            json.dump(setup_data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"  Failed to save setup marker: {e}")

    logger.info(f"  [8/8] Setup complete: {summary}")
    logger.info("=" * 50)

    return results


async def run_diagnostics() -> dict:
    """
    Run system diagnostics and return structured results.
    MCP tool for troubleshooting — same checks as wizard but async + extra info.

    / Ejecutar diagnosticos del sistema para troubleshooting.
    """
    components = {}

    # ── Python ──
    ver = sys.version_info
    components["python"] = {
        "status": "ok" if ver >= (3, 10) else "warning",
        "version": f"{ver.major}.{ver.minor}.{ver.micro}",
        "path": sys.executable,
    }

    # ── Monitors ──
    try:
        from marlow.tools import background
        monitors = background._manager._enumerate_monitors()
        components["monitors"] = {
            "status": "ok",
            "count": len(monitors),
            "background_mode": background._manager.mode,
            "agent_screen_only": background.is_background_mode_active(),
        }
    except Exception as e:
        components["monitors"] = {"status": "error", "detail": str(e)}

    # ── Microphone ──
    try:
        import sounddevice as sd
        devices = sd.query_devices(kind="input")
        if devices is not None:
            name = devices.get("name", "Unknown") if isinstance(devices, dict) else "Available"
            components["microphone"] = {"status": "ok", "device": name}
        else:
            components["microphone"] = {"status": "warning", "detail": "No input device"}
    except Exception as e:
        components["microphone"] = {"status": "error", "detail": str(e)}

    # ── OCR engines ──
    try:
        from marlow.tools.ocr import _windows_ocr_available, _find_tesseract
        ocr_engines = {}
        ocr_engines["windows_ocr"] = _windows_ocr_available()
        tess_path = _find_tesseract()
        ocr_engines["tesseract"] = tess_path
        has_any = ocr_engines["windows_ocr"] or tess_path
        components["ocr"] = {
            "status": "ok" if has_any else "warning",
            "engines": ocr_engines,
            "note": None if has_any else "Install winrt-Windows.Media.Ocr or Tesseract",
        }
    except Exception as e:
        components["ocr"] = {"status": "error", "detail": str(e)}

    # ── TTS ──
    tts_engines = []
    try:
        import edge_tts  # noqa: F401
        tts_engines.append("edge-tts")
    except ImportError:
        pass
    try:
        import pyttsx3  # noqa: F401
        tts_engines.append("pyttsx3")
    except ImportError:
        pass
    components["tts"] = {
        "status": "ok" if tts_engines else "warning",
        "engines": tts_engines,
    }

    # ── Whisper ──
    try:
        from faster_whisper import WhisperModel  # noqa: F401
        components["whisper"] = {"status": "ok", "detail": "faster-whisper available"}
    except ImportError:
        components["whisper"] = {"status": "warning", "detail": "faster-whisper not installed"}

    # ── System info ──
    try:
        import platform
        import psutil
        components["system"] = {
            "status": "ok",
            "os": platform.platform(),
            "cpu": platform.processor() or "Unknown",
            "ram_gb": round(psutil.virtual_memory().total / (1024**3), 1),
            "disk_free_gb": round(psutil.disk_usage("/").free / (1024**3), 1),
        }
    except Exception as e:
        components["system"] = {"status": "error", "detail": str(e)}

    # ── Safety ──
    try:
        from marlow.core.config import MarlowConfig
        cfg = MarlowConfig.load()
        components["safety"] = {
            "status": "ok",
            "confirmation_mode": cfg.security.confirmation_mode,
            "kill_switch_enabled": cfg.security.kill_switch_enabled,
            "rate_limit": cfg.security.max_actions_per_minute,
        }
    except Exception as e:
        components["safety"] = {"status": "error", "detail": str(e)}

    # ── Summary ──
    ok_count = sum(1 for v in components.values() if v.get("status") == "ok")
    total = len(components)
    summary = f"{ok_count}/{total} OK"

    return {
        "success": True,
        "components": components,
        "summary": summary,
        "setup_completed": SETUP_FILE.exists(),
    }
