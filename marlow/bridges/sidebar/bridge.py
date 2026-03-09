"""Sidebar bridge — WebSocket connection to sidebar GTK window.

For now, uses mako notifications as fallback since the sidebar
connects directly to the daemon's HTTP API. The bridge is used
when the kernel needs to push messages to the sidebar.

/ Bridge de sidebar — WebSocket al sidebar GTK.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from marlow.bridges.base import BridgeBase

logger = logging.getLogger("marlow.bridges.sidebar")


class SidebarBridge(BridgeBase):
    """Sidebar interaction bridge: push messages to GTK sidebar."""

    def __init__(self):
        self._ws_clients: list = []  # Future: WebSocket connections

    @property
    def channel_name(self) -> str:
        return "sidebar"

    async def send_text(self, text: str, **kwargs):
        """Push text message to sidebar."""
        await self._push_event({
            "type": "message",
            "role": "marlow",
            "text": text,
        })

    async def send_file(self, file_path: str, caption: str = "", **kwargs):
        await self._push_event({
            "type": "file",
            "path": file_path,
            "caption": caption,
        })

    async def send_photo(self, image_bytes: bytes, caption: str = "", **kwargs):
        await self._push_event({
            "type": "photo",
            "caption": caption,
        })

    async def notify(self, message: str, level: str = "info", **kwargs):
        await self._push_event({
            "type": "notification",
            "message": message,
            "level": level,
        })

    async def ask(self, question: str, options: Optional[list[str]] = None, **kwargs) -> str:
        """Ask via sidebar — push question and wait for response."""
        await self._push_event({
            "type": "ask",
            "question": question,
            "options": options or [],
        })
        # Future: wait for WebSocket response
        return ""

    async def _push_event(self, event: dict):
        """Push event to all connected sidebar WebSocket clients."""
        if not self._ws_clients:
            logger.debug("No sidebar clients connected, event dropped: %s",
                         event.get("type"))
            return

        data = json.dumps(event)
        for ws in list(self._ws_clients):
            try:
                await ws.send_str(data)
            except Exception:
                self._ws_clients.remove(ws)

    def register_ws(self, ws):
        """Register a WebSocket connection from the sidebar."""
        self._ws_clients.append(ws)

    def unregister_ws(self, ws):
        """Unregister a WebSocket connection."""
        if ws in self._ws_clients:
            self._ws_clients.remove(ws)
