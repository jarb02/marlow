"""Linux OCRProvider — Tesseract OCR with bounding boxes.

Captures screenshot via platform.screen, crops if needed,
runs Tesseract via pytesseract.image_to_data() to get
word-level text + bounding boxes + confidence.

Tested on Fedora 43 + tesseract 5.5.2.

/ OCRProvider Linux — Tesseract OCR con bounding boxes.
"""

from __future__ import annotations

import io
import logging
import subprocess
from typing import Optional

from marlow.platform.base import OCRProvider

logger = logging.getLogger("marlow.platform.linux.ocr")


class TesseractOCRProvider(OCRProvider):
    """OCR via Tesseract on Linux."""

    def __init__(self, screen_provider=None):
        self._screen = screen_provider

    def ocr_region(
        self,
        window_title: Optional[str] = None,
        region: Optional[tuple[int, int, int, int]] = None,
        language: str = "eng",
    ) -> dict:
        try:
            import pytesseract
            from PIL import Image
        except ImportError as e:
            return {"success": False, "error": f"Missing dependency: {e}"}

        # Capture screenshot
        if self._screen is None:
            return {"success": False, "error": "No screen provider configured"}

        try:
            png_bytes = self._screen.screenshot(
                window_title=window_title,
                region=region if not window_title else None,
            )
        except Exception as e:
            return {"success": False, "error": f"Screenshot failed: {e}"}

        if not png_bytes:
            return {"success": False, "error": "Screenshot returned empty data"}

        # Load image
        img = Image.open(io.BytesIO(png_bytes))

        # Crop to region if both region and no window_title
        # (window_title already captures the right area)
        if region and not window_title:
            x, y, w, h = region
            img = img.crop((x, y, x + w, y + h))

        # Run Tesseract with word-level data
        try:
            data = pytesseract.image_to_data(
                img, lang=language, output_type=pytesseract.Output.DICT,
            )
        except pytesseract.TesseractNotFoundError:
            return {"success": False, "error": "tesseract not installed"}
        except Exception as e:
            return {"success": False, "error": f"Tesseract error: {e}"}

        # Build word list with bounding boxes
        words = []
        text_parts = []
        n_boxes = len(data.get("text", []))

        for i in range(n_boxes):
            text = data["text"][i].strip()
            conf = int(data["conf"][i])
            if not text or conf < 0:
                continue
            words.append({
                "text": text,
                "x": data["left"][i],
                "y": data["top"][i],
                "width": data["width"][i],
                "height": data["height"][i],
                "confidence": conf,
            })
            text_parts.append(text)

        full_text = " ".join(text_parts)

        return {
            "success": True,
            "text": full_text,
            "words": words,
            "word_count": len(words),
            "language": language,
            "engine": "tesseract",
            "image_size": {"width": img.width, "height": img.height},
        }

    def list_languages(self) -> list[str]:
        try:
            r = subprocess.run(
                ["tesseract", "--list-langs"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode != 0:
                return []
            langs = []
            for line in r.stdout.splitlines():
                line = line.strip()
                # Skip the header line "List of available languages"
                if line and not line.startswith("List of"):
                    langs.append(line)
            return langs
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []


if __name__ == "__main__":
    from marlow.platform.linux.screenshot import GrimScreenCapture

    screen = GrimScreenCapture()
    provider = TesseractOCRProvider(screen_provider=screen)

    print("=== TesseractOCRProvider self-test ===")

    # 1. List languages
    print("\n--- 1. list_languages ---")
    langs = provider.list_languages()
    print(f"  Languages: {langs}")
    if langs:
        print("  PASS")
    else:
        print("  WARNING: no languages found")

    # 2. Full-screen OCR
    print("\n--- 2. ocr_region (full screen) ---")
    result = provider.ocr_region()
    if result["success"]:
        print(f"  Text ({len(result['text'])} chars): {result['text'][:100]}...")
        print(f"  Words: {result['word_count']}")
        print(f"  Image: {result['image_size']}")
        if result["word_count"] > 0:
            print(f"  First word: {result['words'][0]}")
        print("  PASS")
    else:
        print(f"  Error: {result['error']}")
        print("  FAIL")

    print("\nPASS: TesseractOCRProvider self-test complete")
