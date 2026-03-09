"""Bridge manager — routes responses to the correct channel.

The kernel calls bridge_manager.respond(channel, text) and the manager
dispatches to the right bridge implementation. Falls back to console.

/ Manager de bridges — enruta respuestas al canal correcto.
"""

from __future__ import annotations

import logging
from typing import Optional

from marlow.bridges.base import BridgeBase

logger = logging.getLogger("marlow.bridges.manager")


class BridgeManager:
    """Routes responses to the correct bridge based on channel."""

    def __init__(self):
        self.bridges: dict[str, BridgeBase] = {}

    def register(self, bridge: BridgeBase):
        """Register a bridge for a channel."""
        self.bridges[bridge.channel_name] = bridge
        logger.info("Bridge registered: %s", bridge.channel_name)

    def get(self, channel: str) -> Optional[BridgeBase]:
        """Get bridge by channel name."""
        return self.bridges.get(channel)

    @property
    def channels(self) -> list[str]:
        """List registered channel names."""
        return list(self.bridges.keys())

    async def respond(self, channel: str, text: str, **kwargs):
        """Send text response to the specified channel."""
        bridge = self.bridges.get(channel)
        if bridge:
            await bridge.send_text(text, **kwargs)
        elif "console" in self.bridges:
            await self.bridges["console"].send_text(text, **kwargs)
        else:
            logger.warning("No bridge for channel '%s', dropping response", channel)

    async def send_file(self, channel: str, file_path: str, **kwargs):
        """Send a file via the specified channel."""
        bridge = self.bridges.get(channel)
        if bridge:
            await bridge.send_file(file_path, **kwargs)
        elif "console" in self.bridges:
            await self.bridges["console"].notify(
                f"File ready: {file_path}", level="info",
            )

    async def send_photo(self, channel: str, image_bytes: bytes, **kwargs):
        """Send a photo via the specified channel."""
        bridge = self.bridges.get(channel)
        if bridge:
            await bridge.send_photo(image_bytes, **kwargs)

    async def notify(self, channel: str, message: str, level: str = "info", **kwargs):
        """Send a notification via the specified channel."""
        bridge = self.bridges.get(channel)
        if bridge:
            await bridge.notify(message, level=level, **kwargs)
        elif "console" in self.bridges:
            await self.bridges["console"].notify(message, level=level, **kwargs)

    async def ask(self, channel: str, question: str, options: list[str] = None, **kwargs) -> str:
        """Ask user via the specified channel and wait for response."""
        bridge = self.bridges.get(channel)
        if bridge:
            return await bridge.ask(question, options, **kwargs)
        return ""

    async def broadcast(self, text: str, **kwargs):
        """Send text to ALL registered bridges."""
        for bridge in self.bridges.values():
            try:
                await bridge.send_text(text, **kwargs)
            except Exception as e:
                logger.warning("Broadcast to %s failed: %s", bridge.channel_name, e)
