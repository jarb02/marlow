"""Blackboard — centralized key-value store shared by all Kernel components.

Features:
- Typed namespaces: goal.*, world.*, app.*, config.*
- TTL support: entries auto-expire
- Change listeners: async callbacks on key changes
- Snapshot/restore for task suspend/resume

Based on Game AI Blackboard pattern (Halo, Unreal Engine BT).

/ Almacen centralizado key-value con namespaces, TTL y listeners.
"""

import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger("marlow.kernel.blackboard")


@dataclass(frozen=True)
class BlackboardEntry:
    """A single entry in the blackboard."""

    key: str
    value: Any
    source: str = ""          # who wrote it
    timestamp: float = 0.0
    ttl: float = 0.0          # time-to-live in seconds (0 = forever)

    @property
    def is_expired(self) -> bool:
        if self.ttl <= 0:
            return False
        return time.time() - self.timestamp > self.ttl


# Type for change listeners
ChangeListener = Callable[[str, Any, Any], Awaitable[None]]  # key, old_value, new_value


class Blackboard:
    """Centralized key-value store shared by all Kernel components.

    Usage::

        bb = Blackboard()
        bb.set("goal.current", "Open Notepad", source="goal_engine")
        bb.set("world.active_app", "Notepad", source="window_tracker")

        current_goal = bb.get("goal.current")
        all_world = bb.get_namespace("world")
    """

    def __init__(self):
        self._data: dict[str, BlackboardEntry] = {}
        self._listeners: dict[str, list[ChangeListener]] = defaultdict(list)
        self._history: list[tuple[float, str, str]] = []
        self._max_history = 200

    def set(
        self, key: str, value: Any, source: str = "", ttl: float = 0.0,
    ) -> Optional[Any]:
        """Set a value. Returns the old value (or None)."""
        old_entry = self._data.get(key)
        old_value = old_entry.value if old_entry else None

        self._data[key] = BlackboardEntry(
            key=key,
            value=value,
            source=source,
            timestamp=time.time(),
            ttl=ttl,
        )

        self._history.append((time.time(), key, "set"))
        if len(self._history) > self._max_history:
            self._history.pop(0)

        return old_value

    async def set_async(
        self, key: str, value: Any, source: str = "", ttl: float = 0.0,
    ) -> Optional[Any]:
        """Set a value and notify async listeners."""
        old_value = self.set(key, value, source, ttl)
        await self._notify_listeners(key, old_value, value)
        return old_value

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value. Returns default if not found or expired."""
        entry = self._data.get(key)
        if entry is None:
            return default
        if entry.is_expired:
            del self._data[key]
            return default
        return entry.value

    def get_entry(self, key: str) -> Optional[BlackboardEntry]:
        """Get the full entry (with metadata)."""
        entry = self._data.get(key)
        if entry and entry.is_expired:
            del self._data[key]
            return None
        return entry

    def has(self, key: str) -> bool:
        """Check if a key exists and is not expired."""
        entry = self._data.get(key)
        if entry is None:
            return False
        if entry.is_expired:
            del self._data[key]
            return False
        return True

    def delete(self, key: str) -> bool:
        """Delete a key. Returns True if it existed."""
        if key in self._data:
            del self._data[key]
            self._history.append((time.time(), key, "delete"))
            return True
        return False

    def get_namespace(self, prefix: str) -> dict[str, Any]:
        """Get all non-expired values in a namespace.

        ``get_namespace("world")`` returns all keys starting with ``world.``
        """
        self._cleanup_expired()
        result = {}
        ns = prefix if prefix.endswith(".") else prefix + "."
        for key, entry in self._data.items():
            if key.startswith(ns) and not entry.is_expired:
                short_key = key[len(ns):]
                result[short_key] = entry.value
        return result

    def on_change(self, pattern: str, listener: ChangeListener):
        """Register a listener for key changes.

        Pattern can be exact key or namespace prefix:
        - ``"goal.current"`` — exact key
        - ``"world."`` — all keys in world namespace
        - ``"*"`` — all keys
        """
        self._listeners[pattern].append(listener)

    async def _notify_listeners(self, key: str, old_value: Any, new_value: Any):
        """Notify matching listeners of a change."""
        for pattern, callbacks in self._listeners.items():
            if self._pattern_matches(pattern, key):
                for callback in callbacks:
                    try:
                        await callback(key, old_value, new_value)
                    except Exception as e:
                        logger.error(
                            "Blackboard listener error for %s: %s", pattern, e,
                        )

    @staticmethod
    def _pattern_matches(pattern: str, key: str) -> bool:
        if pattern == "*":
            return True
        if pattern.endswith("."):
            return key.startswith(pattern)
        return pattern == key

    def snapshot(self) -> dict[str, Any]:
        """Take a snapshot of all non-expired data.

        Used for task suspend/resume.
        """
        self._cleanup_expired()
        return {k: e.value for k, e in self._data.items()}

    def restore(self, snapshot: dict[str, Any], source: str = "restore"):
        """Restore from a snapshot."""
        for key, value in snapshot.items():
            self.set(key, value, source=source)

    def _cleanup_expired(self):
        """Remove all expired entries."""
        expired = [k for k, e in self._data.items() if e.is_expired]
        for k in expired:
            del self._data[k]

    def clear(self, namespace: str = ""):
        """Clear all keys, or all keys in a namespace."""
        if not namespace:
            self._data.clear()
        else:
            ns = namespace if namespace.endswith(".") else namespace + "."
            to_delete = [k for k in self._data if k.startswith(ns)]
            for k in to_delete:
                del self._data[k]

    @property
    def size(self) -> int:
        """Number of non-expired entries."""
        self._cleanup_expired()
        return len(self._data)

    def keys(self) -> list[str]:
        """All non-expired keys."""
        self._cleanup_expired()
        return list(self._data.keys())

    def format_for_planner(self, namespaces: Optional[list[str]] = None) -> str:
        """Format blackboard state for LLM planner context."""
        self._cleanup_expired()
        if namespaces is None:
            namespaces = ["goal", "world", "app"]

        lines: list[str] = []
        for ns in namespaces:
            data = self.get_namespace(ns)
            if data:
                lines.append(f"[{ns}]")
                for k, v in data.items():
                    lines.append(f"  {k}: {v}")

        return "\n".join(lines) if lines else "Blackboard empty."
