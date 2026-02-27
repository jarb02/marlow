"""
Marlow OCR Tool

Extracts text from screen regions using Tesseract OCR.
Used as Step 2 in the smart_find escalation chain:
  UI Automation (0 tokens) → OCR (0 tokens) → Screenshot (~1,500 tokens)

Requires: Tesseract binary installed on Windows.
  winget install UB-Mannheim.TesseractOCR
  OR download from: https://github.com/UB-Mannheim/tesseract/wiki
"""

import io
import os
import base64
import logging
from typing import Optional

logger = logging.getLogger("marlow.tools.ocr")

# Common Tesseract install paths on Windows
_TESSERACT_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    os.path.expanduser(r"~\AppData\Local\Tesseract-OCR\tesseract.exe"),
]


def _find_tesseract() -> Optional[str]:
    """Find Tesseract binary on the system."""
    # Check if already on PATH
    import shutil
    path = shutil.which("tesseract")
    if path:
        return path

    # Check common install locations
    for p in _TESSERACT_PATHS:
        if os.path.isfile(p):
            return p

    return None


async def ocr_region(
    window_title: Optional[str] = None,
    region: Optional[dict] = None,
    language: str = "eng",
    preprocess: bool = True,
) -> dict:
    """
    Extract text from a window or screen region using OCR.

    Cost: 0 tokens (text only). Speed: ~200-500ms.
    Used as fallback when UI Automation can't find an element.

    Args:
        window_title: Window to OCR. If None with no region, uses full screen.
        region: Specific region: {"x": 0, "y": 0, "width": 800, "height": 600}.
        language: Tesseract language code (default: "eng").
        preprocess: Apply image preprocessing for better accuracy.

    Returns:
        Dictionary with extracted text, word-level data, and confidence.

    / Extrae texto de una ventana o región de pantalla usando OCR.
    / Costo: 0 tokens (solo texto). Velocidad: ~200-500ms.
    """
    # Check for pytesseract
    try:
        import pytesseract
    except ImportError:
        return {
            "error": "pytesseract not installed. Run: pip install pytesseract",
            "hint": "Also install Tesseract binary: winget install UB-Mannheim.TesseractOCR",
        }

    # Find and configure Tesseract binary
    tesseract_path = _find_tesseract()
    if not tesseract_path:
        return {
            "error": "Tesseract binary not found on this system.",
            "install_options": [
                "winget install UB-Mannheim.TesseractOCR",
                "choco install tesseract",
                "Download from: https://github.com/UB-Mannheim/tesseract/wiki",
            ],
            "hint": "After installing, restart your terminal so it's on PATH.",
        }

    pytesseract.pytesseract.tesseract_cmd = tesseract_path

    try:
        from PIL import Image

        # Get image from screenshot tool
        from marlow.tools.screenshot import take_screenshot
        screenshot_result = await take_screenshot(
            window_title=window_title,
            region=region,
            quality=95,  # High quality for OCR
        )

        if "error" in screenshot_result:
            return {"error": f"Screenshot failed: {screenshot_result['error']}"}

        # Decode base64 image
        image_data = base64.b64decode(screenshot_result["image_base64"])
        img = Image.open(io.BytesIO(image_data))

        # Preprocess for better OCR accuracy
        if preprocess:
            img = _preprocess_image(img)

        # Run OCR with word-level data
        data = pytesseract.image_to_data(img, lang=language, output_type=pytesseract.Output.DICT)

        # Extract words with confidence
        words = []
        for i in range(len(data["text"])):
            text = data["text"][i].strip()
            conf = int(data["conf"][i])
            if text and conf >= 0:  # -1 means no text detected
                words.append({
                    "text": text,
                    "confidence": conf,
                    "bbox": {
                        "x": data["left"][i],
                        "y": data["top"][i],
                        "width": data["width"][i],
                        "height": data["height"][i],
                    },
                })

        # Compute full text and average confidence
        full_text = pytesseract.image_to_string(img, lang=language).strip()
        avg_confidence = (
            sum(w["confidence"] for w in words) / len(words)
            if words else 0
        )

        return {
            "success": True,
            "text": full_text,
            "words": words,
            "word_count": len(words),
            "average_confidence": round(avg_confidence, 1),
            "language": language,
            "preprocessed": preprocess,
            "source_size": {
                "width": screenshot_result.get("width"),
                "height": screenshot_result.get("height"),
            },
        }

    except Exception as e:
        logger.error(f"OCR error: {e}")
        return {"error": str(e)}


def _preprocess_image(img) -> "Image":
    """
    Preprocess image for better OCR accuracy.
    Grayscale → 2x upscale → threshold.
    """
    from PIL import Image, ImageFilter

    # Convert to grayscale
    img = img.convert("L")

    # Upscale 2x for better small-text recognition
    img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)

    # Apply threshold for cleaner text
    img = img.point(lambda p: 255 if p > 128 else 0)

    return img
