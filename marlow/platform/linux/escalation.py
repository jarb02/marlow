"""Linux EscalationProvider — AT-SPI2 -> OCR -> screenshot.

Finds UI elements using escalating strategies:
1. AT-SPI2 accessibility tree (fast, structured, zero cost)
2. OCR text search (finds visible text not in a11y tree)
3. Screenshot fallback (returns image for LLM analysis)

/ EscalationProvider Linux — AT-SPI2 -> OCR -> screenshot.
"""

from __future__ import annotations

import base64
import logging
from typing import Optional

from marlow.platform.base import EscalationProvider

logger = logging.getLogger("marlow.platform.linux.escalation")


class LinuxEscalationProvider(EscalationProvider):
    """Escalating element search: AT-SPI2 -> OCR -> screenshot."""

    def __init__(self, ui_tree=None, ocr=None, screen=None):
        self._ui_tree = ui_tree
        self._ocr = ocr
        self._screen = screen

    def smart_find(
        self,
        name: Optional[str] = None,
        role: Optional[str] = None,
        window_title: Optional[str] = None,
    ) -> dict:
        if not name and not role:
            return {"success": False, "error": "Must provide name or role"}

        # ── Level 1: AT-SPI2 tree search ──
        if self._ui_tree is not None:
            try:
                results = self._ui_tree.find_elements(
                    name=name,
                    role=role,
                    window_title=window_title,
                )
                if results:
                    best = results[0]
                    score = best.get("score", 0.0)
                    if score > 0.7 or (not name and results):
                        # Role-only searches don't have name score
                        if not name:
                            score = 1.0
                        return {
                            "success": True,
                            "method": "atspi",
                            "element": best,
                            "confidence": score,
                            "total_matches": len(results),
                        }
                    # Low-confidence match — continue to OCR
                    logger.debug("AT-SPI2 match score %.2f < 0.7, trying OCR", score)
            except Exception as e:
                logger.debug("AT-SPI2 search failed: %s", e)

        # ── Level 2: OCR text search ──
        if name and self._ocr is not None:
            try:
                ocr_result = self._ocr.ocr_region(
                    window_title=window_title,
                    language="eng",
                )
                if ocr_result.get("success"):
                    match = self._find_text_in_ocr(name, ocr_result.get("words", []))
                    if match:
                        return {
                            "success": True,
                            "method": "ocr",
                            "element": match,
                            "confidence": match.get("confidence", 0) / 100.0,
                            "ocr_word_count": ocr_result.get("word_count", 0),
                        }
            except Exception as e:
                logger.debug("OCR search failed: %s", e)

        # ── Level 3: Screenshot fallback ──
        if self._screen is not None:
            try:
                png_bytes = self._screen.screenshot(window_title=window_title)
                if png_bytes:
                    return {
                        "success": False,
                        "method": "screenshot",
                        "requires_vision": True,
                        "image_base64": base64.b64encode(png_bytes).decode(),
                        "message": f"Could not find '{name or role}' via AT-SPI2 or OCR. "
                                   f"Screenshot attached for visual analysis.",
                        "confidence": 0.0,
                    }
            except Exception as e:
                logger.debug("Screenshot fallback failed: %s", e)

        return {
            "success": False,
            "method": "none",
            "error": f"Element not found: name={name!r} role={role!r}",
            "confidence": 0.0,
        }

    @staticmethod
    def _find_text_in_ocr(
        target: str, words: list[dict],
    ) -> Optional[dict]:
        """Search OCR words for target text. Handles multi-word matches."""
        target_lower = target.lower().strip()
        target_parts = target_lower.split()

        if not target_parts:
            return None

        # Single-word search
        if len(target_parts) == 1:
            for w in words:
                if target_lower in w["text"].lower():
                    return {
                        "name": w["text"],
                        "role": "ocr_text",
                        "bounds": {
                            "x": w["x"], "y": w["y"],
                            "w": w["width"], "h": w["height"],
                        },
                        "confidence": w["confidence"],
                    }
            return None

        # Multi-word: find consecutive words matching the phrase
        for i in range(len(words) - len(target_parts) + 1):
            matched = True
            for j, part in enumerate(target_parts):
                if part not in words[i + j]["text"].lower():
                    matched = False
                    break
            if matched:
                # Merge bounding boxes
                first = words[i]
                last = words[i + len(target_parts) - 1]
                x = first["x"]
                y = min(words[i + k]["y"] for k in range(len(target_parts)))
                x2 = last["x"] + last["width"]
                y2 = max(words[i + k]["y"] + words[i + k]["height"]
                         for k in range(len(target_parts)))
                merged_text = " ".join(words[i + k]["text"]
                                       for k in range(len(target_parts)))
                avg_conf = sum(words[i + k]["confidence"]
                               for k in range(len(target_parts))) / len(target_parts)
                return {
                    "name": merged_text,
                    "role": "ocr_text",
                    "bounds": {"x": x, "y": y, "w": x2 - x, "h": y2 - y},
                    "confidence": avg_conf,
                }

        return None


if __name__ == "__main__":
    from marlow.platform.linux.screenshot import GrimScreenCapture
    from marlow.platform.linux.ui_tree import AtSpiUITreeProvider
    from marlow.platform.linux.ocr import TesseractOCRProvider

    screen = GrimScreenCapture()
    ui_tree = AtSpiUITreeProvider()
    ocr = TesseractOCRProvider(screen_provider=screen)
    provider = LinuxEscalationProvider(ui_tree=ui_tree, ocr=ocr, screen=screen)

    print("=== LinuxEscalationProvider self-test ===")

    # 1. Find by AT-SPI2 (should find buttons)
    print("\n--- 1. smart_find(name='Reload') [expect: atspi] ---")
    r = provider.smart_find(name="Reload")
    print(f"  success={r.get('success')} method={r.get('method')} "
          f"confidence={r.get('confidence', 0):.2f}")
    if r.get("success"):
        print(f"  element: {r['element'].get('name')} [{r['element'].get('role')}]")

    # 2. Find text only visible in page content (should escalate to OCR)
    print("\n--- 2. smart_find(name='Example Domain') [expect: ocr] ---")
    r = provider.smart_find(name="Example Domain")
    print(f"  success={r.get('success')} method={r.get('method')} "
          f"confidence={r.get('confidence', 0):.2f}")
    if r.get("success"):
        elem = r.get("element", {})
        print(f"  element: {elem.get('name')} bounds={elem.get('bounds')}")

    # 3. Find something that doesn't exist (should fallback to screenshot)
    print("\n--- 3. smart_find(name='xyznonexistent') [expect: screenshot] ---")
    r = provider.smart_find(name="xyznonexistent")
    print(f"  success={r.get('success')} method={r.get('method')}")
    if r.get("requires_vision"):
        img_len = len(r.get("image_base64", ""))
        print(f"  screenshot attached: {img_len} chars base64")

    print("\nPASS: LinuxEscalationProvider self-test complete")
