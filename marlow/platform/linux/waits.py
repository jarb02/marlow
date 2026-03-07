"""Linux WaitProvider — poll-based waiting for UI conditions.

Polls AT-SPI2, OCR, window list, and screenshot comparison
to wait for elements, text, windows, or screen stability.

All methods are async to avoid blocking the MCP event loop.

/ WaitProvider Linux — espera por condiciones de UI.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Optional

from marlow.platform.base import WaitProvider

logger = logging.getLogger("marlow.platform.linux.waits")


class LinuxWaitProvider(WaitProvider):
    """Poll-based waits using AT-SPI2, OCR, Sway IPC, and grim."""

    def __init__(self, ui_tree=None, ocr=None, windows=None, screen=None):
        self._ui_tree = ui_tree
        self._ocr = ocr
        self._windows = windows
        self._screen = screen

    async def wait_for_element(
        self,
        name: Optional[str] = None,
        role: Optional[str] = None,
        window_title: Optional[str] = None,
        timeout: float = 10,
        interval: float = 0.5,
    ) -> dict:
        if not name and not role:
            return {"found": False, "error": "Must provide name or role", "elapsed": 0}

        timeout = min(max(timeout, 1), 120)
        interval = min(max(interval, 0.2), 10)
        t0 = time.monotonic()
        deadline = t0 + timeout

        while time.monotonic() < deadline:
            # Try AT-SPI2
            if self._ui_tree:
                results = self._ui_tree.find_elements(
                    name=name, role=role, window_title=window_title,
                )
                if results:
                    best = results[0]
                    if best.get("score", 0) > 0.5:
                        return {
                            "found": True,
                            "element": best,
                            "elapsed": round(time.monotonic() - t0, 2),
                            "method": "atspi",
                        }

            # OCR fallback for text-based element search
            if name and self._ocr:
                try:
                    ocr_result = self._ocr.ocr_region(
                        window_title=window_title, language="eng",
                    )
                    if ocr_result.get("success"):
                        for w in ocr_result.get("words", []):
                            if name.lower() in w["text"].lower():
                                return {
                                    "found": True,
                                    "element": {
                                        "name": w["text"],
                                        "role": "ocr_text",
                                        "bounds": {
                                            "x": w["x"], "y": w["y"],
                                            "w": w["width"], "h": w["height"],
                                        },
                                    },
                                    "elapsed": round(time.monotonic() - t0, 2),
                                    "method": "ocr",
                                }
                except Exception:
                    pass

            await asyncio.sleep(interval)

        return {
            "found": False,
            "elapsed": round(time.monotonic() - t0, 2),
            "error": f"Timeout after {timeout}s waiting for element: "
                     f"name={name!r} role={role!r}",
        }

    async def wait_for_text(
        self,
        text: str,
        window_title: Optional[str] = None,
        timeout: float = 10,
        interval: float = 0.5,
    ) -> dict:
        if not text:
            return {"found": False, "error": "Must provide text", "elapsed": 0}

        timeout = min(max(timeout, 1), 120)
        interval = min(max(interval, 0.2), 10)
        t0 = time.monotonic()
        deadline = t0 + timeout
        text_lower = text.lower()

        while time.monotonic() < deadline:
            if self._ocr:
                try:
                    result = self._ocr.ocr_region(
                        window_title=window_title, language="eng",
                    )
                    if result.get("success"):
                        full_text = result.get("text", "")
                        if text_lower in full_text.lower():
                            # Find the matching word(s) for bounds
                            bounds = None
                            for w in result.get("words", []):
                                if text_lower in w["text"].lower():
                                    bounds = {
                                        "x": w["x"], "y": w["y"],
                                        "w": w["width"], "h": w["height"],
                                    }
                                    break
                            return {
                                "found": True,
                                "text": text,
                                "bounds": bounds,
                                "elapsed": round(time.monotonic() - t0, 2),
                            }
                except Exception:
                    pass

            await asyncio.sleep(interval)

        return {
            "found": False,
            "text": text,
            "elapsed": round(time.monotonic() - t0, 2),
            "error": f"Timeout after {timeout}s waiting for text: {text!r}",
        }

    async def wait_for_window(
        self,
        title: str,
        timeout: float = 10,
        interval: float = 0.5,
    ) -> dict:
        if not title:
            return {"found": False, "error": "Must provide title", "elapsed": 0}

        timeout = min(max(timeout, 1), 120)
        interval = min(max(interval, 0.2), 10)
        t0 = time.monotonic()
        deadline = t0 + timeout
        title_lower = title.lower()

        while time.monotonic() < deadline:
            if self._windows:
                windows = self._windows.list_windows()
                for w in windows:
                    win_title = (w.title or "").lower()
                    if title_lower in win_title:
                        return {
                            "found": True,
                            "window": {
                                "identifier": w.identifier,
                                "title": w.title,
                                "app_name": w.app_name,
                                "pid": w.pid,
                            },
                            "elapsed": round(time.monotonic() - t0, 2),
                        }

            await asyncio.sleep(interval)

        return {
            "found": False,
            "elapsed": round(time.monotonic() - t0, 2),
            "error": f"Timeout after {timeout}s waiting for window: {title!r}",
        }

    async def wait_for_idle(
        self,
        window_title: Optional[str] = None,
        timeout: float = 10,
        threshold: float = 0.95,
    ) -> dict:
        timeout = min(max(timeout, 1), 120)
        threshold = min(max(threshold, 0.5), 1.0)
        t0 = time.monotonic()
        deadline = t0 + timeout

        if not self._screen:
            return {"idle": False, "error": "No screen provider", "elapsed": 0}

        prev_hash = None
        similarity = 0.0

        while time.monotonic() < deadline:
            try:
                png = self._screen.screenshot(window_title=window_title)
                curr_hash = hashlib.md5(png).digest()

                if prev_hash is not None:
                    if curr_hash == prev_hash:
                        similarity = 1.0
                    else:
                        # Byte-level similarity as approximation
                        matching = sum(a == b for a, b in zip(
                            prev_hash, curr_hash))
                        similarity = matching / len(curr_hash)

                    if similarity >= threshold:
                        return {
                            "idle": True,
                            "similarity": round(similarity, 4),
                            "elapsed": round(time.monotonic() - t0, 2),
                        }

                prev_hash = curr_hash
            except Exception as e:
                logger.debug("wait_for_idle screenshot failed: %s", e)

            await asyncio.sleep(0.5)

        return {
            "idle": False,
            "similarity": round(similarity, 4),
            "elapsed": round(time.monotonic() - t0, 2),
            "error": f"Timeout after {timeout}s waiting for idle "
                     f"(last similarity: {similarity:.4f})",
        }
