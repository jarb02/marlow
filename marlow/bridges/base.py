"""Bridge base class — ABC for all interaction channels.

Every bridge (voice, sidebar, Telegram, console) implements this
interface so the kernel can respond without knowing the channel.

/ Clase base para todos los canales de interaccion.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class BridgeBase(ABC):
    """Base class for all interaction bridges."""

    @property
    @abstractmethod
    def channel_name(self) -> str:
        """Unique channel identifier: 'voice', 'sidebar', 'telegram', 'console'."""

    @abstractmethod
    async def send_text(self, text: str, **kwargs):
        """Send text response to user via this channel."""

    @abstractmethod
    async def send_file(self, file_path: str, caption: str = "", **kwargs):
        """Send a file to user via this channel."""

    @abstractmethod
    async def send_photo(self, image_bytes: bytes, caption: str = "", **kwargs):
        """Send an image to user via this channel."""

    @abstractmethod
    async def notify(self, message: str, level: str = "info", **kwargs):
        """Send a notification (progress, error, status)."""

    @abstractmethod
    async def ask(self, question: str, options: Optional[list[str]] = None, **kwargs) -> str:
        """Ask user a question and wait for response."""
