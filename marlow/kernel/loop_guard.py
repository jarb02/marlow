"""4-layer protection against infinite loops in the Decision Loop.

Layers:
1. Max iterations — hard limit per goal (default 25)
2. Repetition detector — same action 3x in a row → break
3. No-progress detector — 5 cycles without state change → break
4. Token budget — total LLM tokens consumed (default 50K)
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class LoopGuardResult:
    """Result of a loop-guard check."""

    should_continue: bool
    reason: str = ""
    layer: str = ""  # max_iterations | repetition | no_progress | token_budget
    suggestion: str = ""  # What to do instead


class LoopGuard:
    """4-layer anti-loop protection.

    Parameters
    ----------
    * **max_iterations** (int): Hard limit per goal.
    * **max_repetitions** (int): Same action repeated N times → break.
    * **max_no_progress** (int): Cycles without state change → break.
    * **max_tokens** (int): Total LLM tokens budget.
    """

    def __init__(
        self,
        max_iterations: int = 25,
        max_repetitions: int = 3,
        max_no_progress: int = 5,
        max_tokens: int = 50_000,
    ):
        self._max_iterations = max_iterations
        self._max_repetitions = max_repetitions
        self._max_no_progress = max_no_progress
        self._max_tokens = max_tokens

        self._iteration_count = 0
        self._recent_actions: deque[str] = deque(maxlen=10)
        self._recent_fingerprints: deque[str] = deque(maxlen=10)
        self._tokens_used = 0
        self._no_progress_count = 0
        self._last_fingerprint: Optional[str] = None

    def check(
        self, action_signature: str, state_fingerprint: str,
    ) -> LoopGuardResult:
        """Check all 4 layers. Call this BEFORE each cycle.

        Parameters
        ----------
        * **action_signature** (str):
            String describing the action (e.g. ``"click:Save_button"``).
        * **state_fingerprint** (str):
            Hash from ``WorldStateSnapshot.fingerprint()``.
        """
        self._iteration_count += 1
        self._recent_actions.append(action_signature)
        self._recent_fingerprints.append(state_fingerprint)

        # Layer 1: Max iterations
        if self._iteration_count >= self._max_iterations:
            return LoopGuardResult(
                should_continue=False,
                reason=(
                    f"Max iterations reached: "
                    f"{self._iteration_count}/{self._max_iterations}"
                ),
                layer="max_iterations",
                suggestion="Abort goal or replan with different approach",
            )

        # Layer 2: Repetition detector
        if len(self._recent_actions) >= self._max_repetitions:
            last_n = list(self._recent_actions)[-self._max_repetitions:]
            if len(set(last_n)) == 1:
                return LoopGuardResult(
                    should_continue=False,
                    reason=(
                        f"Same action repeated {self._max_repetitions}x: "
                        f"{last_n[0]}"
                    ),
                    layer="repetition",
                    suggestion="Try alternative action or skip step",
                )

        # Layer 3: No progress
        if state_fingerprint == self._last_fingerprint:
            self._no_progress_count += 1
        else:
            self._no_progress_count = 0
        self._last_fingerprint = state_fingerprint

        if self._no_progress_count >= self._max_no_progress:
            return LoopGuardResult(
                should_continue=False,
                reason=(
                    f"No state change in {self._no_progress_count} cycles"
                ),
                layer="no_progress",
                suggestion=(
                    "Replan \u2014 current approach is not making progress"
                ),
            )

        # Layer 4: Token budget
        if self._tokens_used >= self._max_tokens:
            return LoopGuardResult(
                should_continue=False,
                reason=(
                    f"Token budget exhausted: "
                    f"{self._tokens_used}/{self._max_tokens}"
                ),
                layer="token_budget",
                suggestion="Complete with best-effort or abort",
            )

        return LoopGuardResult(should_continue=True)

    def record_tokens(self, tokens: int) -> None:
        """Record LLM tokens consumed."""
        self._tokens_used += tokens

    def reset(self) -> None:
        """Reset for a new goal."""
        self._iteration_count = 0
        self._recent_actions.clear()
        self._recent_fingerprints.clear()
        self._tokens_used = 0
        self._no_progress_count = 0
        self._last_fingerprint = None

    @property
    def stats(self) -> dict:
        """Current guard statistics."""
        return {
            "iterations": self._iteration_count,
            "tokens_used": self._tokens_used,
            "no_progress_streak": self._no_progress_count,
            "last_actions": list(self._recent_actions),
        }
