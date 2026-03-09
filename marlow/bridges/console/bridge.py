"""Console bridge — terminal output + mako notifications.

Default bridge for CLI/wofi/terminal interactions.

/ Bridge de consola — terminal + notificaciones mako.
"""

from __future__ import annotations

import logging
import subprocess
from typing import Optional

from marlow.bridges.base import BridgeBase

logger = logging.getLogger("marlow.bridges.console")


class ConsoleBridge(BridgeBase):
    """Console interaction: print to terminal, notify via mako."""

    @property
    def channel_name(self) -> str:
        return "console"

    async def send_text(self, text: str, **kwargs):
        """Print text and send mako notification."""
        print(f"[Marlow] {text}")
        _mako_notify(text)

    async def send_file(self, file_path: str, caption: str = "", **kwargs):
        msg = caption or f"Archivo: {file_path}"
        print(f"[Marlow] {msg}")
        _mako_notify(msg)

    async def send_photo(self, image_bytes: bytes, caption: str = "", **kwargs):
        msg = caption or "Captura disponible"
        print(f"[Marlow] {msg}")
        _mako_notify(msg)

    async def notify(self, message: str, level: str = "info", **kwargs):
        urgency = "critical" if level == "error" else "normal"
        _mako_notify(message, urgency=urgency)
        print(f"[Marlow:{level}] {message}")

    async def ask(self, question: str, options: Optional[list[str]] = None, **kwargs) -> str:
        """Ask via terminal (blocking input)."""
        print(f"[Marlow] {question}")
        if options:
            for i, opt in enumerate(options):
                print(f"  {i + 1}. {opt}")
        try:
            return input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            return ""


def _mako_notify(message: str, urgency: str = "normal"):
    """Send a desktop notification via notify-send (mako/dunst compatible)."""
    try:
        subprocess.run(
            ["notify-send", "-a", "Marlow", "-u", urgency, "Marlow", message],
            capture_output=True, timeout=3,
        )
    except Exception:
        pass  # No notification daemon — that's fine
