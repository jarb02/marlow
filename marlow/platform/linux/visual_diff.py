"""Linux VisualDiffProvider — before/after screenshot comparison.

Captures screenshots via platform screen provider and compares
pixel-level differences using PIL.

/ Proveedor de diff visual Linux — comparacion pixel a pixel.
"""

from __future__ import annotations

import base64
import io
import logging
import time
import uuid
from datetime import datetime
from typing import Optional

from marlow.platform.base import VisualDiffProvider

logger = logging.getLogger("marlow.platform.linux.visual_diff")


class LinuxVisualDiffProvider(VisualDiffProvider):
    """Before/after screenshot comparison using PIL."""

    def __init__(self, screen=None):
        self._screen = screen
        self._states: dict[str, dict] = {}
        self._max_age = 300  # 5 minutes

    def capture_before(
        self,
        window_title: Optional[str] = None,
        label: Optional[str] = None,
    ) -> dict:
        self._cleanup()

        if not self._screen:
            return {"error": "No screen provider available"}

        try:
            png_bytes = self._screen.screenshot(window_title=window_title)
        except Exception as e:
            return {"error": f"Screenshot failed: {e}"}

        diff_id = label or uuid.uuid4().hex[:8]
        self._states[diff_id] = {
            "before_png": png_bytes,
            "window": window_title,
            "_created": time.time(),
        }

        return {
            "success": True,
            "diff_id": diff_id,
            "status": "before_captured",
            "window": window_title,
            "size_bytes": len(png_bytes),
            "timestamp": datetime.now().isoformat(),
        }

    def compare(
        self,
        diff_id: str,
        window_title: Optional[str] = None,
    ) -> dict:
        if diff_id not in self._states:
            return {
                "error": f"No 'before' state for diff_id: {diff_id}. "
                         "It may have expired (5 min max).",
            }

        state = self._states.pop(diff_id)

        if not self._screen:
            return {"error": "No screen provider available"}

        # Use same window as before if not specified
        win = window_title or state.get("window")

        try:
            after_png = self._screen.screenshot(window_title=win)
        except Exception as e:
            return {"error": f"After screenshot failed: {e}"}

        try:
            from PIL import Image, ImageChops

            before_img = Image.open(io.BytesIO(state["before_png"]))
            after_img = Image.open(io.BytesIO(after_png))

            # Resize if dimensions differ
            if before_img.size != after_img.size:
                after_img = after_img.resize(before_img.size)

            before_rgb = before_img.convert("RGB")
            after_rgb = after_img.convert("RGB")

            # Pixel difference
            diff = ImageChops.difference(before_rgb, after_rgb)
            diff_pixels = sum(1 for p in diff.getdata() if sum(p) > 30)
            total_pixels = before_rgb.width * before_rgb.height
            change_pct = round(
                (diff_pixels / total_pixels) * 100, 2
            ) if total_pixels > 0 else 0

            # Bounding box of changed area
            bbox = diff.getbbox()
            changed_region = None
            if bbox:
                changed_region = {
                    "x": bbox[0], "y": bbox[1],
                    "width": bbox[2] - bbox[0],
                    "height": bbox[3] - bbox[1],
                }

            # Generate diff image: highlight changes in red
            diff_img = after_rgb.copy()
            diff_data = list(diff.getdata())
            result_data = list(diff_img.getdata())
            for i, px in enumerate(diff_data):
                if sum(px) > 30:
                    result_data[i] = (255, 0, 0)
            diff_img.putdata(result_data)

            buf = io.BytesIO()
            diff_img.save(buf, format="PNG")
            diff_b64 = base64.b64encode(buf.getvalue()).decode()

            return {
                "success": True,
                "diff_id": diff_id,
                "changed": change_pct > 0.5,
                "change_percent": change_pct,
                "changed_pixels": diff_pixels,
                "total_pixels": total_pixels,
                "changed_region": changed_region,
                "before_size": f"{before_rgb.width}x{before_rgb.height}",
                "after_size": f"{after_rgb.width}x{after_rgb.height}",
                "diff_image": diff_b64,
            }

        except Exception as e:
            logger.error("Visual diff comparison error: %s", e)
            return {"error": str(e)}

    def _cleanup(self) -> None:
        now = time.time()
        expired = [
            k for k, v in self._states.items()
            if now - v["_created"] > self._max_age
        ]
        for k in expired:
            del self._states[k]
