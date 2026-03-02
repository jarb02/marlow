"""ActionScorer — evaluates every action with 4 dimensions.

Pipeline:
1. Score execution (from ToolResult.success)
2. Score outcome (from success_check if available)
3. Score safety (from NegativeStateChecker)
4. Score efficiency (from duration vs expected)
5. Combine via weighted geometric mean
6. Determine verdict
7. Record to ReliabilityTracker
"""

from __future__ import annotations

from dataclasses import dataclass

from .dimensions import (
    score_efficiency,
    score_execution,
    score_outcome,
    score_outcome_confidence,
    weighted_geometric_mean,
)
from .negative_checker import NegativeStateChecker
from .reliability import ReliabilityTracker

# Score weights (Research #4)
DEFAULT_WEIGHTS: dict[str, float] = {
    "execution": 0.20,
    "outcome": 0.50,
    "safety": 0.20,
    "efficiency": 0.10,
}


@dataclass(frozen=True)
class ActionScore:
    """Result of scoring an action across 4 dimensions."""

    execution: float  # 0.0-1.0: Did tool execute without error?
    outcome: float  # 0.0-1.0: Did state change as expected?
    safety: float  # 0.0-1.0: No unexpected side effects?
    efficiency: float  # 0.0-1.0: Reasonable speed?
    composite: float  # Weighted geometric mean of above
    confidence: float  # 0.0-1.0: How sure are we?
    method: str  # "programmatic" | "visual" | "llm" | "hybrid"
    details: dict  # Extra info (negative checks, timing, etc.)


class StepVerdict:
    """What the Decision Loop should do next based on the score."""

    STEP_OK = "step_ok"  # >= 0.80: proceed to next step
    STEP_PARTIAL = "step_partial"  # >= 0.50: proceed but note the issue
    STEP_RETRY = "step_retry"  # >= 0.30: retry this step
    STEP_ALTERNATIVE = "step_alternative"  # < 0.30: try alternative
    STEP_FAILED = "step_failed"  # 0.0: unrecoverable


class ActionScorer:
    """Scores every action with 4 dimensions + geometric mean composite.

    Parameters
    ----------
    * **reliability_tracker** (ReliabilityTracker or None):
        Shared tracker instance. Created if not provided.
    * **weights** (dict or None):
        Dimension weights. Uses DEFAULT_WEIGHTS if not provided.
    """

    def __init__(
        self,
        reliability_tracker: ReliabilityTracker | None = None,
        weights: dict[str, float] | None = None,
    ):
        self._checker = NegativeStateChecker()
        self._reliability = reliability_tracker or ReliabilityTracker()
        self._weights = weights or DEFAULT_WEIGHTS.copy()

    def score(
        self,
        tool_name: str,
        tool_success: bool,
        tool_error: str | None = None,
        check_passed: bool | None = None,
        state_before=None,
        state_after=None,
        expected_app: str = "",
        duration_ms: float = 0.0,
        expected_duration_ms: float = 3000.0,
        app_name: str = "",
    ) -> ActionScore:
        """Score an action across all 4 dimensions.

        Parameters
        ----------
        * **tool_name** (str): MCP tool that was executed.
        * **tool_success** (bool): Whether ToolResult.success was True.
        * **tool_error** (str or None): Error message if any.
        * **check_passed** (bool or None): Result of success_check.
        * **state_before/state_after**: WorldStateSnapshots.
        * **expected_app** (str): App the action targeted.
        * **duration_ms** (float): Wall-clock execution time.
        * **expected_duration_ms** (float): Baseline for efficiency.
        * **app_name** (str): App name for reliability tracking.
        """
        # 1. Execution
        exec_score = score_execution(tool_success, tool_error)

        # 2. Outcome
        out_score = score_outcome(check_passed, tool_success)
        confidence = score_outcome_confidence(check_passed, tool_success)

        # 3. Safety
        safety_score, negative_checks = self._checker.check(
            state_before, state_after, expected_app,
        )

        # 4. Efficiency
        eff_score = score_efficiency(duration_ms, expected_duration_ms)

        # 5. Composite (weighted geometric mean)
        scores_weights = [
            (exec_score, self._weights["execution"]),
            (out_score, self._weights["outcome"]),
            (safety_score, self._weights["safety"]),
            (eff_score, self._weights["efficiency"]),
        ]
        composite = weighted_geometric_mean(scores_weights)

        # 6. Build ActionScore
        action_score = ActionScore(
            execution=round(exec_score, 4),
            outcome=round(out_score, 4),
            safety=round(safety_score, 4),
            efficiency=round(eff_score, 4),
            composite=round(composite, 4),
            confidence=round(confidence, 4),
            method="programmatic",
            details={
                "negative_checks": [
                    {
                        "name": c.name,
                        "detected": c.detected,
                        "penalty": c.penalty,
                    }
                    for c in negative_checks
                    if c.detected
                ],
                "tool_error": tool_error,
                "duration_ms": duration_ms,
            },
        )

        # 7. Record to reliability tracker
        self._reliability.record(tool_name, composite, app_name)

        return action_score

    def decide(self, score: ActionScore) -> str:
        """Determine what the Decision Loop should do next.

        Returns a StepVerdict constant.
        """
        if score.composite >= 0.80:
            return StepVerdict.STEP_OK
        elif score.composite >= 0.50:
            return StepVerdict.STEP_PARTIAL
        elif score.composite >= 0.30:
            return StepVerdict.STEP_RETRY
        elif score.composite > 0.0:
            return StepVerdict.STEP_ALTERNATIVE
        else:
            return StepVerdict.STEP_FAILED

    @property
    def reliability(self) -> ReliabilityTracker:
        """Access the shared reliability tracker."""
        return self._reliability
