"""
Marlow OCR Tool

Extracts text from screen regions using Windows OCR (primary) or Tesseract (fallback).
Used as Step 2 in the smart_find escalation chain:
  UI Automation (0 tokens) → OCR (0 tokens) → Screenshot (~1,500 tokens)

Primary engine: Windows.Media.Ocr (built-in Windows 10/11, zero external deps)
Fallback engine: Tesseract (requires binary install)

/ Motor principal: Windows.Media.Ocr (integrado en Windows 10/11, cero deps)
/ Motor fallback: Tesseract (requiere instalar binario)
"""

import io
import os
import base64
import logging
import time
from typing import Optional

logger = logging.getLogger("marlow.tools.ocr")

# Common Tesseract install paths on Windows (for fallback)
_TESSERACT_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    os.path.expanduser(r"~\AppData\Local\Tesseract-OCR\tesseract.exe"),
]


def _find_tesseract() -> Optional[str]:
    """Find Tesseract binary on the system."""
    import shutil
    path = shutil.which("tesseract")
    if path:
        return path

    for p in _TESSERACT_PATHS:
        if os.path.isfile(p):
            return p

    return None


def _windows_ocr_available() -> bool:
    """Check if Windows OCR API is available."""
    try:
        from winrt.windows.media.ocr import OcrEngine  # noqa: F401
        return True
    except ImportError:
        return False


async def _ocr_windows(img: "Image.Image", language: Optional[str] = None) -> dict:
    """
    Run OCR using Windows.Media.Ocr API.

    Args:
        img: PIL Image to OCR (any mode — will be converted to RGBA).
        language: BCP-47 language tag (e.g., "en-US", "es-MX"). If None, auto-detects.

    Returns:
        Dictionary with text, words (with bounding boxes), and metadata.

    / Ejecuta OCR usando la API Windows.Media.Ocr.
    """
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.storage.streams import InMemoryRandomAccessStream, DataWriter

    # Create engine
    engine = None
    resolved_language = None

    if language:
        try:
            from winrt.windows.globalization import Language
            lang_obj = Language(language)
            if OcrEngine.is_language_supported(lang_obj):
                engine = OcrEngine.try_create_from_language(lang_obj)
                resolved_language = language
        except Exception:
            pass

    if engine is None:
        engine = OcrEngine.try_create_from_user_profile_languages()
        resolved_language = "auto"

    if engine is None:
        return {"error": "Windows OCR: could not create engine (no OCR languages installed)"}

    # Convert PIL image to PNG bytes and load via BitmapDecoder
    # / Convertir imagen PIL a bytes PNG y cargar via BitmapDecoder
    rgba_img = img.convert("RGBA")
    buf = io.BytesIO()
    rgba_img.save(buf, format="PNG")
    png_bytes = buf.getvalue()

    stream = InMemoryRandomAccessStream()
    try:
        writer = DataWriter(stream)
        writer.write_bytes(png_bytes)
        await writer.store_async()
        writer.detach_stream()
        stream.seek(0)

        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()

        # Run OCR
        result = await engine.recognize_async(bitmap)

        # Extract words with bounding boxes
        words = []
        for line in result.lines:
            for word in line.words:
                r = word.bounding_rect
                words.append({
                    "text": word.text,
                    "x": round(r.x),
                    "y": round(r.y),
                    "width": round(r.width),
                    "height": round(r.height),
                })

        return {
            "success": True,
            "engine": "windows_ocr",
            "text": result.text.strip(),
            "words": words,
            "word_count": len(words),
            "language": resolved_language,
        }

    finally:
        stream.close()


async def _ocr_tesseract(img: "Image.Image", language: str = "eng", preprocess: bool = True) -> dict:
    """
    Run OCR using Tesseract as fallback engine.

    / Ejecuta OCR usando Tesseract como motor fallback.
    """
    try:
        import pytesseract
    except ImportError:
        return {
            "error": "Tesseract fallback: pytesseract not installed. Run: pip install pytesseract",
            "hint": "Also install Tesseract binary: winget install UB-Mannheim.TesseractOCR",
        }

    tesseract_path = _find_tesseract()
    if not tesseract_path:
        return {
            "error": "Tesseract binary not found on this system.",
            "install_options": [
                "winget install UB-Mannheim.TesseractOCR",
                "choco install tesseract",
            ],
        }

    pytesseract.pytesseract.tesseract_cmd = tesseract_path

    if preprocess:
        img = _preprocess_image(img)

    data = pytesseract.image_to_data(img, lang=language, output_type=pytesseract.Output.DICT)

    words = []
    for i in range(len(data["text"])):
        text = data["text"][i].strip()
        conf = int(data["conf"][i])
        if text and conf >= 0:
            words.append({
                "text": text,
                "x": data["left"][i],
                "y": data["top"][i],
                "width": data["width"][i],
                "height": data["height"][i],
                "confidence": conf,
            })

    full_text = pytesseract.image_to_string(img, lang=language).strip()
    avg_conf = sum(w["confidence"] for w in words) / len(words) if words else 0

    return {
        "success": True,
        "engine": "tesseract",
        "text": full_text,
        "words": words,
        "word_count": len(words),
        "average_confidence": round(avg_conf, 1),
        "language": language,
        "preprocessed": preprocess,
    }


def _preprocess_image(img) -> "Image":
    """
    Preprocess image for better Tesseract accuracy.
    Grayscale → 2x upscale → threshold.
    """
    from PIL import Image

    img = img.convert("L")
    img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
    img = img.point(lambda p: 255 if p > 128 else 0)
    return img


async def ocr_region(
    window_title: Optional[str] = None,
    region: Optional[dict] = None,
    language: Optional[str] = None,
    engine: Optional[str] = None,
) -> dict:
    """
    Extract text from a window or screen region using OCR.

    Cost: 0 tokens (text only). Speed: ~50-200ms (Windows OCR) or ~200-500ms (Tesseract).
    Used as fallback when UI Automation can't find an element.

    Args:
        window_title: Window to OCR. If None with no region, uses full screen.
        region: Specific region: {"x": 0, "y": 0, "width": 800, "height": 600}.
        language: Language for OCR.
                  - Windows OCR: BCP-47 tag (e.g., "en-US", "es-MX") or None for auto.
                  - Tesseract: ISO 639-3 code (e.g., "eng", "spa").
        engine: Force engine: "windows" or "tesseract". If None, auto-selects
                (Windows OCR primary, Tesseract fallback).

    Returns:
        Dictionary with extracted text, word-level bounding boxes, engine used.

    / Extrae texto de una ventana o region de pantalla usando OCR.
    / Motor principal: Windows OCR (~50-200ms). Fallback: Tesseract (~200-500ms).
    """
    try:
        from PIL import Image

        # Take screenshot
        from marlow.tools.screenshot import take_screenshot
        screenshot_result = await take_screenshot(
            window_title=window_title,
            region=region,
            quality=95,
        )

        if "error" in screenshot_result:
            return {"error": f"Screenshot failed: {screenshot_result['error']}"}

        # Decode base64 image
        image_data = base64.b64decode(screenshot_result["image_base64"])
        img = Image.open(io.BytesIO(image_data))

        source_size = {
            "width": screenshot_result.get("width"),
            "height": screenshot_result.get("height"),
        }

        # Select engine
        # / Seleccionar motor: Windows OCR primario, Tesseract fallback
        use_windows = engine != "tesseract" and _windows_ocr_available()
        use_tesseract = engine == "tesseract"

        result = None
        t0 = time.perf_counter()

        if use_windows and not use_tesseract:
            result = await _ocr_windows(img, language=language)

            # If Windows OCR failed, try Tesseract as fallback
            if "error" in result and engine != "windows":
                logger.warning(f"Windows OCR failed: {result['error']}, trying Tesseract...")
                tess_lang = _bcp47_to_tesseract(language) if language else "eng"
                result = await _ocr_tesseract(img, language=tess_lang)

        elif use_tesseract:
            tess_lang = _bcp47_to_tesseract(language) if language else "eng"
            result = await _ocr_tesseract(img, language=tess_lang)

        else:
            # No Windows OCR available, try Tesseract directly
            tess_lang = _bcp47_to_tesseract(language) if language else "eng"
            result = await _ocr_tesseract(img, language=tess_lang)

        elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)

        if "error" in result:
            return result

        result["elapsed_ms"] = elapsed_ms
        result["source_size"] = source_size
        return result

    except Exception as e:
        logger.error(f"OCR error: {e}")
        return {"error": str(e)}


def _bcp47_to_tesseract(bcp47: Optional[str]) -> str:
    """
    Convert BCP-47 language tag to Tesseract language code.
    E.g., "en-US" → "eng", "es-MX" → "spa".

    / Convierte tag BCP-47 a codigo Tesseract.
    """
    if not bcp47:
        return "eng"

    mapping = {
        "en": "eng", "es": "spa", "fr": "fra", "de": "deu",
        "it": "ita", "pt": "por", "ja": "jpn", "ko": "kor",
        "zh": "chi_sim", "ru": "rus", "ar": "ara",
    }

    # Try full match first (e.g., "zh-TW" → "chi_tra" could be added)
    prefix = bcp47.split("-")[0].lower()
    return mapping.get(prefix, bcp47)


async def list_ocr_languages() -> dict:
    """
    List available OCR languages for each engine.

    Returns:
        Dictionary with available languages per engine.

    / Lista los idiomas OCR disponibles por motor.
    """
    result = {"engines": {}}

    # Windows OCR languages
    if _windows_ocr_available():
        try:
            from winrt.windows.media.ocr import OcrEngine
            langs = OcrEngine.available_recognizer_languages
            result["engines"]["windows_ocr"] = {
                "available": True,
                "languages": [lang.language_tag for lang in langs],
            }
        except Exception as e:
            result["engines"]["windows_ocr"] = {"available": False, "error": str(e)}
    else:
        result["engines"]["windows_ocr"] = {
            "available": False,
            "install": "pip install winrt-Windows.Media.Ocr winrt-Windows.Graphics.Imaging winrt-Windows.Storage.Streams winrt-runtime",
        }

    # Tesseract languages
    tess_path = _find_tesseract()
    if tess_path:
        try:
            import pytesseract
            pytesseract.pytesseract.tesseract_cmd = tess_path
            langs = pytesseract.get_languages()
            result["engines"]["tesseract"] = {
                "available": True,
                "path": tess_path,
                "languages": langs,
            }
        except Exception as e:
            result["engines"]["tesseract"] = {"available": True, "path": tess_path, "error": str(e)}
    else:
        result["engines"]["tesseract"] = {
            "available": False,
            "install": "winget install UB-Mannheim.TesseractOCR",
        }

    return result
