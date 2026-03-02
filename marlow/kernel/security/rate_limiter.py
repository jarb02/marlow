"""RateLimiter — monotonic-time rate limiting that cannot be overridden.

Uses time.monotonic() to prevent clock manipulation. The Kernel
cannot bypass these limits.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitResult:
    """Result of a rate-limit check."""

    allowed: bool
    limit_name: str = ""
    current: int = 0
    maximum: int = 0
    retry_after_seconds: float = 0.0


class RateLimiter:
    """Rate limiting that CANNOT be overridden by the Kernel.

    Uses monotonic time to prevent clock manipulation.

    Parameters
    ----------
    * **max_actions_per_minute** (int): Max tool executions per minute.
    * **max_commands_per_minute** (int): Max shell commands per minute.
    * **max_llm_calls_per_hour** (int): Max LLM API calls per hour.
    * **max_files_per_goal** (int): Max file modifications per goal.
    """

    def __init__(
        self,
        max_actions_per_minute: int = 30,
        max_commands_per_minute: int = 5,
        max_llm_calls_per_hour: int = 50,
        max_files_per_goal: int = 50,
    ):
        self._action_timestamps: deque[float] = deque()
        self._command_timestamps: deque[float] = deque()
        self._llm_timestamps: deque[float] = deque()
        self._file_mod_counts: dict[str, int] = {}

        self.max_actions_per_minute = max_actions_per_minute
        self.max_commands_per_minute = max_commands_per_minute
        self.max_llm_calls_per_hour = max_llm_calls_per_hour
        self.max_files_per_goal = max_files_per_goal

    # ── Internal helpers ──

    @staticmethod
    def _clean_window(timestamps: deque[float], window_seconds: float) -> None:
        """Remove timestamps older than the window."""
        cutoff = time.monotonic() - window_seconds
        while timestamps and timestamps[0] < cutoff:
            timestamps.popleft()

    # ── Check methods ──

    def check_action(self) -> RateLimitResult:
        """Check if we can execute another action."""
        self._clean_window(self._action_timestamps, 60.0)
        current = len(self._action_timestamps)
        if current >= self.max_actions_per_minute:
            oldest = self._action_timestamps[0]
            retry_after = 60.0 - (time.monotonic() - oldest)
            return RateLimitResult(
                allowed=False,
                limit_name="actions_per_minute",
                current=current,
                maximum=self.max_actions_per_minute,
                retry_after_seconds=max(0.0, retry_after),
            )
        return RateLimitResult(
            allowed=True,
            limit_name="actions_per_minute",
            current=current,
            maximum=self.max_actions_per_minute,
        )

    def check_command(self) -> RateLimitResult:
        """Check if we can execute another system command."""
        self._clean_window(self._command_timestamps, 60.0)
        current = len(self._command_timestamps)
        if current >= self.max_commands_per_minute:
            oldest = self._command_timestamps[0]
            retry_after = 60.0 - (time.monotonic() - oldest)
            return RateLimitResult(
                allowed=False,
                limit_name="commands_per_minute",
                current=current,
                maximum=self.max_commands_per_minute,
                retry_after_seconds=max(0.0, retry_after),
            )
        return RateLimitResult(
            allowed=True,
            limit_name="commands_per_minute",
            current=current,
            maximum=self.max_commands_per_minute,
        )

    def check_llm_call(self) -> RateLimitResult:
        """Check if we can make another LLM call."""
        self._clean_window(self._llm_timestamps, 3600.0)
        current = len(self._llm_timestamps)
        if current >= self.max_llm_calls_per_hour:
            oldest = self._llm_timestamps[0]
            retry_after = 3600.0 - (time.monotonic() - oldest)
            return RateLimitResult(
                allowed=False,
                limit_name="llm_calls_per_hour",
                current=current,
                maximum=self.max_llm_calls_per_hour,
                retry_after_seconds=max(0.0, retry_after),
            )
        return RateLimitResult(
            allowed=True,
            limit_name="llm_calls_per_hour",
            current=current,
            maximum=self.max_llm_calls_per_hour,
        )

    def check_file_modification(self, goal_id: str) -> RateLimitResult:
        """Check if this goal has modified too many files."""
        current = self._file_mod_counts.get(goal_id, 0)
        if current >= self.max_files_per_goal:
            return RateLimitResult(
                allowed=False,
                limit_name="files_per_goal",
                current=current,
                maximum=self.max_files_per_goal,
            )
        return RateLimitResult(
            allowed=True,
            limit_name="files_per_goal",
            current=current,
            maximum=self.max_files_per_goal,
        )

    # ── Record methods ──

    def record_action(self) -> None:
        """Record an action execution timestamp."""
        self._action_timestamps.append(time.monotonic())

    def record_command(self) -> None:
        """Record a command execution timestamp."""
        self._command_timestamps.append(time.monotonic())

    def record_llm_call(self) -> None:
        """Record an LLM call timestamp."""
        self._llm_timestamps.append(time.monotonic())

    def record_file_modification(self, goal_id: str) -> None:
        """Record a file modification for a goal."""
        self._file_mod_counts[goal_id] = (
            self._file_mod_counts.get(goal_id, 0) + 1
        )

    # ── Stats ──

    def get_stats(self) -> dict:
        """Current usage stats for monitoring."""
        self._clean_window(self._action_timestamps, 60.0)
        self._clean_window(self._command_timestamps, 60.0)
        self._clean_window(self._llm_timestamps, 3600.0)
        return {
            "actions_last_minute": len(self._action_timestamps),
            "commands_last_minute": len(self._command_timestamps),
            "llm_calls_last_hour": len(self._llm_timestamps),
            "file_mods_by_goal": dict(self._file_mod_counts),
        }
