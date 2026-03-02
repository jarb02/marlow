"""NegativeStateChecker — detects unexpected side effects after an action.

Compares pre/post WorldStateSnapshot to find regressions.
Returns a safety score in [0.0, 1.0].
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NegativeCheck:
    """Single negative-state check result."""

    name: str
    detected: bool
    penalty: float  # How much to subtract from safety score
    description: str = ""


class NegativeStateChecker:
    """Checks for unexpected side effects by comparing pre/post WorldState.

    Used for the safety dimension of ActionScore.
    """

    def check(
        self,
        state_before,
        state_after,
        expected_app: str = "",
    ) -> tuple[float, list[NegativeCheck]]:
        """Compare two WorldStateSnapshots for unexpected changes.

        Parameters
        ----------
        * **state_before**: WorldStateSnapshot before the action.
        * **state_after**: WorldStateSnapshot after the action.
        * **expected_app**: App name the action targeted (for context).

        Returns
        -------
        ``(safety_score, list_of_checks)`` — safety starts at 1.0,
        each detected issue subtracts a penalty.
        """
        checks: list[NegativeCheck] = []
        score = 1.0

        if state_before is None or state_after is None:
            return 0.85, [
                NegativeCheck("no_state", False, 0.0, "State not available"),
            ]

        # 1. Unexpected window change (-0.15)
        if (
            state_before.active_window_title != state_after.active_window_title
            and expected_app
            and expected_app.lower() not in state_after.active_window_title.lower()
        ):
            checks.append(NegativeCheck(
                "unexpected_window_change", True, 0.15,
                f"Window changed from '{state_before.active_window_title}' "
                f"to '{state_after.active_window_title}'",
            ))
            score -= 0.15
        else:
            checks.append(NegativeCheck(
                "unexpected_window_change", False, 0.0,
            ))

        # 2. Multiple new windows appeared (-0.10)
        before_count = state_before.window_count
        after_count = state_after.window_count
        if after_count > before_count + 1:
            checks.append(NegativeCheck(
                "multiple_windows_opened", True, 0.10,
                f"Windows increased from {before_count} to {after_count}",
            ))
            score -= 0.10
        else:
            checks.append(NegativeCheck(
                "multiple_windows_opened", False, 0.0,
            ))

        # 3. Target window disappeared (-0.50)
        if (
            expected_app
            and state_before.has_window(expected_app)
            and not state_after.has_window(expected_app)
        ):
            checks.append(NegativeCheck(
                "target_window_disappeared", True, 0.50,
                f"Expected window '{expected_app}' is gone",
            ))
            score -= 0.50
        else:
            checks.append(NegativeCheck(
                "target_window_disappeared", False, 0.0,
            ))

        # 4. Unexpected clipboard change (-0.05)
        if (
            state_before.clipboard_hash
            and state_after.clipboard_hash
            and state_before.clipboard_hash != state_after.clipboard_hash
        ):
            checks.append(NegativeCheck(
                "unexpected_clipboard_change", True, 0.05,
                "Clipboard content changed",
            ))
            score -= 0.05
        else:
            checks.append(NegativeCheck(
                "unexpected_clipboard_change", False, 0.0,
            ))

        return max(0.0, score), checks
