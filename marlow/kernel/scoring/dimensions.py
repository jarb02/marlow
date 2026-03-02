"""Individual dimension scoring functions.

Each returns a float in [0.0, 1.0]. Used by ActionScorer to build
the composite score via weighted geometric mean.
"""

from __future__ import annotations

import math


def score_execution(tool_result_success: bool, error: str | None = None) -> float:
    """Did the tool execute without error?

    Returns
    -------
    1.0 = success, 0.0 = exception/crash, 0.3 = partial (timeout/warning).
    """
    if tool_result_success:
        return 1.0
    if error and ("timeout" in error.lower() or "warning" in error.lower()):
        return 0.3  # Partial — ran but had issues
    return 0.0


def score_outcome(check_passed: bool | None, tool_success: bool) -> float:
    """Did the state change as expected?

    Conflict resolution matrix (Research #4)::

        ToolResult | Check | Score | Confidence
        OK         | PASS  | 1.0   | 0.95
        OK         | FAIL  | 0.1   | 0.90  ← tool lied, check sees reality
        FAIL       | PASS  | 0.9   | 0.75  ← tool errored but it worked
        FAIL       | FAIL  | 0.0   | 0.95
        OK         | None  | 0.85  | 0.70  ← no check, assume mostly ok
        FAIL       | None  | 0.1   | 0.70
    """
    if check_passed is None:
        return 0.85 if tool_success else 0.1
    if check_passed and tool_success:
        return 1.0
    if check_passed and not tool_success:
        return 0.9  # Worked despite tool error
    if not check_passed and tool_success:
        return 0.1  # Tool said OK but check failed — trust the check
    return 0.0  # Both failed


def score_outcome_confidence(
    check_passed: bool | None, tool_success: bool,
) -> float:
    """Confidence in the outcome score."""
    if check_passed is None:
        return 0.70
    if check_passed == tool_success:
        return 0.95
    if check_passed and not tool_success:
        return 0.75
    return 0.90  # tool OK but check failed


def score_efficiency(
    duration_ms: float, expected_ms: float = 3000.0,
) -> float:
    """Was the action reasonably fast?

    Returns
    -------
    1.0 = faster than expected, 0.5 ~ 2x expected,
    0.1 ~ 10x expected (very slow).
    """
    if duration_ms <= 0 or expected_ms <= 0:
        return 0.5
    ratio = duration_ms / expected_ms
    if ratio <= 1.0:
        return 1.0
    # Logarithmic decay: slow actions get progressively penalized
    return max(0.1, 1.0 / (1.0 + math.log2(ratio)))


def weighted_geometric_mean(scores: list[tuple[float, float]]) -> float:
    """Weighted geometric mean with epsilon protection.

    Parameters
    ----------
    * **scores**: list of ``(score, weight)`` tuples.

    Key property: if any dimension is 0, composite approaches 0.
    This is correct — a failed outcome should tank the score even
    if execution was fine.
    """
    EPSILON = 0.001
    total_weight = sum(w for _, w in scores)
    if total_weight == 0:
        return 0.0

    log_sum = sum(
        w * math.log(max(s, 0.0) + EPSILON) for s, w in scores
    )
    return math.exp(log_sum / total_weight) - EPSILON
