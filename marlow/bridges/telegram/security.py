"""Telegram security — auth, whitelist, rate limiting.

4 layers: chat_id whitelist, optional passphrase, rate limiting, token security.

/ Seguridad Telegram — auth, whitelist, rate limiting.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict

logger = logging.getLogger("marlow.bridges.telegram.security")


class TelegramSecurity:
    """Security layer for Telegram bridge."""

    def __init__(
        self,
        authorized_ids: list[int] = None,
        require_passphrase: bool = False,
        passphrase: str = "",
        max_per_minute: int = 30,
    ):
        self.authorized_ids: set[int] = set(authorized_ids or [])
        self.require_passphrase = require_passphrase
        self.passphrase = passphrase
        self.max_per_minute = max_per_minute
        self._rate_counts: dict[int, list[float]] = defaultdict(list)
        self._pending_auth: set[int] = set()

    def is_authorized(self, chat_id: int) -> bool:
        """Check if a chat_id is in the whitelist."""
        if not self.authorized_ids:
            return True  # No whitelist = accept all (first-time setup)
        return chat_id in self.authorized_ids

    def authorize(self, chat_id: int):
        """Add a chat_id to the whitelist."""
        self.authorized_ids.add(chat_id)
        self._pending_auth.discard(chat_id)
        logger.info("Authorized chat_id: %d", chat_id)

    def check_rate_limit(self, chat_id: int) -> bool:
        """Check rate limit. Returns True if within limit."""
        now = time.time()
        # Clean old entries
        self._rate_counts[chat_id] = [
            t for t in self._rate_counts[chat_id] if now - t < 60
        ]
        if len(self._rate_counts[chat_id]) >= self.max_per_minute:
            return False
        self._rate_counts[chat_id].append(now)
        return True

    def check_passphrase(self, chat_id: int, text: str) -> bool:
        """Check if text matches passphrase for pending auth."""
        if not self.require_passphrase:
            return True
        if text.strip() == self.passphrase:
            self.authorize(chat_id)
            return True
        return False

    def needs_passphrase(self, chat_id: int) -> bool:
        """Check if chat_id needs passphrase verification."""
        return (
            self.require_passphrase
            and chat_id not in self.authorized_ids
        )
