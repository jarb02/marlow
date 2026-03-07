"""Linux ClipboardProvider — wl-copy / wl-paste on Wayland.

Uses wl-clipboard tools for get/set. Maintains an in-memory
history since wl-clipboard has no native history support.

/ Proveedor de clipboard Linux — wl-copy/wl-paste en Wayland.
"""

from __future__ import annotations

import logging
import subprocess
import time
from datetime import datetime

from marlow.platform.base import ClipboardProvider

logger = logging.getLogger("marlow.platform.linux.clipboard")


class WaylandClipboardProvider(ClipboardProvider):
    """Clipboard via wl-copy/wl-paste with in-memory history."""

    def __init__(self, max_history: int = 100):
        self._history: list[dict] = []
        self._max_history = max_history

    def get_clipboard(self) -> str:
        try:
            result = subprocess.run(
                ["wl-paste", "--no-newline"],
                capture_output=True, text=True, timeout=5,
            )
            text = result.stdout if result.returncode == 0 else ""
            if text:
                self._add_to_history(text)
            return text
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.error("wl-paste failed: %s", e)
            return ""

    def set_clipboard(self, text: str) -> bool:
        try:
            # wl-copy forks to serve clipboard — use --foreground is NOT what
            # we want (it blocks). Default mode forks and returns immediately,
            # but subprocess.run waits for the forked child's stdout pipe to close.
            # Use Popen instead to avoid the timeout.
            proc = subprocess.Popen(
                ["wl-copy", "--", text],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # wl-copy with text as argument forks immediately
            proc.wait(timeout=3)
            ok = proc.returncode == 0
            if ok:
                self._add_to_history(text)
            return ok
        except subprocess.TimeoutExpired:
            # wl-copy forked and is serving clipboard — that's success
            self._add_to_history(text)
            return True
        except FileNotFoundError as e:
            logger.error("wl-copy not found: %s", e)
            return False

    def get_clipboard_history(self) -> list[dict]:
        return list(self._history)

    def _add_to_history(self, text: str) -> None:
        # Deduplicate: don't add if same as last entry
        if self._history and self._history[-1]["content"] == text[:500]:
            return
        self._history.append({
            "content": text[:500],
            "timestamp": datetime.now().isoformat(),
            "length": len(text),
        })
        while len(self._history) > self._max_history:
            self._history.pop(0)
