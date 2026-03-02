"""Three-tier memory system: short-term (RAM) + mid-term (24h) + long-term (permanent).

Short-term lives in a RAM deque (maxlen=50) for instant access during
active goals. Mid-term persists to SQLite with auto-expiry. Long-term
is permanent with relevance-based retrieval.
"""

from __future__ import annotations

import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from .db.repositories import Memory, MemoryRepository


@dataclass
class ShortTermEntry:
    """RAM-only entry. Lightweight, no persistence."""

    content: dict
    category: str  # action|observation|tool_result|context
    timestamp: float  # time.monotonic()
    tool_name: str = ""
    goal_id: str = ""


class MemorySystem:
    """Unified memory interface across three tiers.

    Parameters
    ----------
    * **memory_repo** (MemoryRepository):
        Repository backed by state.db for mid/long-term storage.
    """

    def __init__(self, memory_repo: MemoryRepository):
        self._repo = memory_repo
        self._short_term: deque[ShortTermEntry] = deque(maxlen=50)

    # ── Short-term (RAM) ──

    def remember_short(
        self,
        content: dict,
        category: str = "observation",
        tool_name: str = "",
        goal_id: str = "",
    ) -> None:
        """Add to short-term buffer. No SQLite, no await."""
        self._short_term.append(ShortTermEntry(
            content=content,
            category=category,
            timestamp=time.monotonic(),
            tool_name=tool_name,
            goal_id=goal_id,
        ))

    def get_recent_actions(self, n: int = 10) -> list[ShortTermEntry]:
        """Last N entries from short-term, newest first."""
        items = list(self._short_term)
        items.reverse()
        return items[:n]

    def get_short_term_for_goal(self, goal_id: str) -> list[ShortTermEntry]:
        """All short-term entries for a specific goal."""
        return [e for e in self._short_term if e.goal_id == goal_id]

    def clear_short_term(self) -> None:
        """Discard all short-term entries."""
        self._short_term.clear()

    @property
    def short_term_count(self) -> int:
        """Number of entries in the short-term buffer."""
        return len(self._short_term)

    # ── Mid-term (SQLite, auto-expiry) ──

    async def remember_mid(
        self,
        content: dict,
        category: str,
        tags: list[str] | None = None,
        ttl_hours: float = 24.0,
    ) -> None:
        """Store in mid-term memory. Auto-expires after ttl_hours."""
        expires = (
            datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
        ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        memory = Memory(
            id=uuid.uuid4().hex[:12],
            tier="mid",
            category=category,
            content=content,
            relevance=1.0,
            tags=tags or [],
            expires_at=expires,
        )
        await self._repo.store(memory)

    # ── Long-term (SQLite, permanent) ──

    async def remember_long(
        self,
        content: dict,
        category: str,
        tags: list[str] | None = None,
        relevance: float = 1.0,
    ) -> None:
        """Store in long-term memory. Never expires."""
        memory = Memory(
            id=uuid.uuid4().hex[:12],
            tier="long",
            category=category,
            content=content,
            relevance=relevance,
            tags=tags or [],
            expires_at=None,
        )
        await self._repo.store(memory)

    # ── Recall (search across tiers) ──

    async def recall(
        self,
        tier: str,
        category: str | None = None,
        limit: int = 20,
    ) -> list[Memory]:
        """Recall from SQLite memories, ordered by relevance."""
        return await self._repo.recall(
            tier=tier, category=category, limit=limit,
        )

    async def recall_by_tags(
        self, tags: list[str], limit: int = 10
    ) -> list[Memory]:
        """Search memories that contain ANY of the given tags."""
        # Fetch a broad set and filter in Python (tags are JSON arrays)
        mid = await self._repo.recall(tier="mid", limit=200)
        long = await self._repo.recall(tier="long", limit=200)
        all_memories = mid + long

        tag_set = set(tags)
        matched = [
            m for m in all_memories
            if tag_set.intersection(m.tags)
        ]
        # Sort by relevance descending
        matched.sort(key=lambda m: m.relevance, reverse=True)
        return matched[:limit]

    # ── Lifecycle ──

    async def persist_short_term(self) -> int:
        """Save short-term entries to mid-term on shutdown.

        Returns the number of entries persisted.
        """
        count = 0
        for entry in self._short_term:
            await self.remember_mid(
                content=entry.content,
                category="recovered_{}".format(entry.category),
                tags=["shutdown_persist"],
                ttl_hours=1.0,
            )
            count += 1
        return count

    async def cleanup(self) -> int:
        """Expire mid-term memories and decay relevance.

        Returns the number of expired entries deleted.
        """
        deleted = await self._repo.cleanup_expired()
        await self._repo.decay_relevance(0.95)
        return deleted
