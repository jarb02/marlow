"""PreActionScorer — Utility AI pattern for pre-execution evaluation.

Evaluates candidate actions BEFORE execution using 4 weighted
considerations combined via weighted geometric mean:

    reliability (0.30) — historical EMA from ReliabilityTracker
    urgency    (0.30) — retry count + time pressure
    relevance  (0.25) — context match (target app, element type)
    cost       (0.15) — token/time cost of the action

Inercia bonus (1.2x) for the currently executing tool prevents
unnecessary thrashing between equivalent alternatives.

/ Evalua acciones candidatas ANTES de ejecutarlas con Utility AI.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from .dimensions import weighted_geometric_mean
from .reliability import ReliabilityTracker


# Default weights — sum to 1.0
WEIGHTS: dict[str, float] = {
    "reliability": 0.30,
    "urgency": 0.30,
    "relevance": 0.25,
    "cost": 0.15,
}

# Bonus multiplier for the action currently in progress (prevents thrashing)
INERCIA_MULTIPLIER = 1.2


@dataclass(frozen=True)
class PreActionScore:
    """Score for a candidate action before execution."""

    tool_name: str
    reliability: float  # 0.0-1.0: historical success rate
    urgency: float  # 0.0-1.0: how urgent is this action?
    relevance: float  # 0.0-1.0: does it match current context?
    cost: float  # 0.0-1.0: inverted cost (1.0 = cheap, 0.0 = expensive)
    composite: float  # Weighted geometric mean
    inercia_bonus: bool  # Was inercia applied?


class PreActionScorer:
    """Evaluate candidate actions before execution.

    Parameters
    ----------
    * **reliability_tracker** (ReliabilityTracker or None):
        Shared tracker for historical scores. Created if not provided.
    * **weights** (dict or None):
        Custom dimension weights. Uses module WEIGHTS if not provided.
    """

    def __init__(
        self,
        reliability_tracker: ReliabilityTracker | None = None,
        weights: dict[str, float] | None = None,
    ):
        self._reliability = reliability_tracker or ReliabilityTracker()
        self._weights = weights or WEIGHTS.copy()

    def score(
        self,
        tool_name: str,
        context: dict,
        current_tool: str = "",
        app_name: str = "",
    ) -> PreActionScore:
        """Score a single candidate action.

        Parameters
        ----------
        * **tool_name** (str): Candidate tool to evaluate.
        * **context** (dict): Current context with keys:
            - retry_count (int): How many retries so far.
            - target_app (str): App the action targets.
            - element_type (str): UI element type (button, edit, etc.).
            - has_timeout (bool): Is there time pressure?
            - estimated_tokens (int): Token cost estimate.
            - estimated_ms (int): Time cost estimate.
        * **current_tool** (str): Tool currently executing (for inercia).
        * **app_name** (str): App name for reliability lookup.
        """
        rel = self._get_reliability(tool_name, app_name)
        urg = self._calc_urgency(context)
        rev = self._calc_relevance(tool_name, context)
        cst = self._calc_cost(context)

        scores_weights = [
            (rel, self._weights["reliability"]),
            (urg, self._weights["urgency"]),
            (rev, self._weights["relevance"]),
            (cst, self._weights["cost"]),
        ]
        composite = weighted_geometric_mean(scores_weights)

        # Inercia bonus: 20% boost for current tool to prevent thrashing
        has_inercia = bool(current_tool and current_tool == tool_name)
        if has_inercia:
            composite = min(1.0, composite * INERCIA_MULTIPLIER)

        return PreActionScore(
            tool_name=tool_name,
            reliability=round(rel, 4),
            urgency=round(urg, 4),
            relevance=round(rev, 4),
            cost=round(cst, 4),
            composite=round(composite, 4),
            inercia_bonus=has_inercia,
        )

    def rank_actions(
        self,
        candidates: list[str],
        context: dict,
        current_tool: str = "",
        app_name: str = "",
    ) -> list[PreActionScore]:
        """Score and rank multiple candidates, best first.

        Returns sorted list of PreActionScore (highest composite first).
        """
        scored = [
            self.score(tool_name, context, current_tool, app_name)
            for tool_name in candidates
        ]
        scored.sort(key=lambda s: s.composite, reverse=True)
        return scored

    # ------------------------------------------------------------------
    # Dimension calculators
    # ------------------------------------------------------------------

    def _get_reliability(self, tool_name: str, app_name: str) -> float:
        """Fetch historical reliability from tracker.

        Returns 0.5 (neutral) if no data available.
        """
        return self._reliability.get_reliability(tool_name, app_name)

    def _calc_urgency(self, context: dict) -> float:
        """Higher urgency = more retries or time pressure.

        Formula: base 0.5, +0.15 per retry (capped at 1.0),
        +0.2 if has_timeout.
        """
        retry_count = context.get("retry_count", 0)
        has_timeout = context.get("has_timeout", False)

        urgency = 0.5 + 0.15 * retry_count
        if has_timeout:
            urgency += 0.2
        return min(1.0, urgency)

    def _calc_relevance(self, tool_name: str, context: dict) -> float:
        """How well does the tool match the current context?

        - target_app match: 0.8 base
        - element_type match: +0.2
        - No context: 0.5 (neutral)
        """
        target_app = context.get("target_app", "")
        element_type = context.get("element_type", "")

        if not target_app and not element_type:
            return 0.5  # No context — neutral

        relevance = 0.5

        # Tool-app affinity (simple heuristic)
        if target_app:
            relevance = 0.8

        # Element type bonus
        if element_type:
            # Click tools are relevant for buttons, type tools for edits
            click_tools = {"click", "som_click", "cdp_click", "cdp_click_selector"}
            type_tools = {"type_text", "cdp_type_text"}
            button_types = {"button", "menuitem", "hyperlink", "checkbox", "radio"}
            edit_types = {"edit", "document", "text", "combobox"}

            if tool_name in click_tools and element_type.lower() in button_types:
                relevance += 0.2
            elif tool_name in type_tools and element_type.lower() in edit_types:
                relevance += 0.2

        return min(1.0, relevance)

    def _calc_cost(self, context: dict) -> float:
        """Inverted cost score: 1.0 = cheap, 0.0 = very expensive.

        Based on estimated_tokens and estimated_ms.
        Token cost: 0 tokens = 1.0, 1500+ tokens = 0.3
        Time cost: 0ms = 1.0, 10000+ ms = 0.3
        """
        tokens = context.get("estimated_tokens", 0)
        ms = context.get("estimated_ms", 0)

        # Token cost (0 tokens = free = 1.0)
        if tokens <= 0:
            token_cost = 1.0
        elif tokens < 500:
            token_cost = 0.8
        elif tokens < 1500:
            token_cost = 0.5
        else:
            token_cost = 0.3

        # Time cost
        if ms <= 0:
            time_cost = 1.0
        elif ms < 1000:
            time_cost = 0.9
        elif ms < 5000:
            time_cost = 0.6
        else:
            time_cost = 0.3

        # Average both dimensions
        return (token_cost + time_cost) / 2.0

    @property
    def reliability(self) -> ReliabilityTracker:
        """Access the shared reliability tracker."""
        return self._reliability

    # ── EventBus handler ──

    async def on_action_result(self, event) -> None:
        """EventBus handler for action.completed / action.failed.

        Updates the ReliabilityTracker EMA from action outcomes.
        Completed+success -> score 1.0, failed -> score 0.0.
        """
        from marlow.kernel.events import ActionCompleted, ActionFailed

        try:
            if isinstance(event, ActionCompleted):
                score = 1.0 if event.success else 0.0
                self._reliability.record(event.tool_name, score)
            elif isinstance(event, ActionFailed):
                self._reliability.record(event.tool_name, 0.0)
        except Exception:
            pass
