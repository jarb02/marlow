"""Tests for marlow.kernel.scoring.pre_scorer — PreActionScorer (Utility AI)."""

import pytest
from marlow.kernel.scoring.pre_scorer import (
    PreActionScore,
    PreActionScorer,
    WEIGHTS,
    INERCIA_MULTIPLIER,
)
from marlow.kernel.scoring.reliability import ReliabilityTracker


class TestPreActionScore:
    """Dataclass tests."""

    def test_dataclass_fields(self):
        s = PreActionScore(
            tool_name="click",
            reliability=0.9,
            urgency=0.5,
            relevance=0.8,
            cost=1.0,
            composite=0.75,
            inercia_bonus=False,
        )
        assert s.tool_name == "click"
        assert s.reliability == 0.9
        assert s.urgency == 0.5
        assert s.relevance == 0.8
        assert s.cost == 1.0
        assert s.composite == 0.75
        assert s.inercia_bonus is False

    def test_frozen(self):
        s = PreActionScore(
            tool_name="click", reliability=0.9, urgency=0.5,
            relevance=0.8, cost=1.0, composite=0.75, inercia_bonus=False,
        )
        with pytest.raises(AttributeError):
            s.composite = 0.99


class TestWeights:
    """Weight configuration tests."""

    def test_weights_sum_to_one(self):
        assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9

    def test_weights_keys(self):
        assert set(WEIGHTS.keys()) == {"reliability", "urgency", "relevance", "cost"}

    def test_inercia_multiplier(self):
        assert INERCIA_MULTIPLIER == 1.2


class TestPreActionScorer:
    """Core scoring logic."""

    def setup_method(self):
        self.tracker = ReliabilityTracker()
        self.scorer = PreActionScorer(reliability_tracker=self.tracker)

    def test_score_returns_pre_action_score(self):
        result = self.scorer.score("click", {})
        assert isinstance(result, PreActionScore)

    def test_score_unknown_tool_reliability_neutral(self):
        """Unknown tool gets 0.5 reliability (neutral)."""
        result = self.scorer.score("unknown_tool", {})
        assert result.reliability == 0.5

    def test_score_known_tool_reliability(self):
        """Tool with history uses EMA score."""
        # Record enough samples to pass min_samples threshold
        for _ in range(5):
            self.tracker.record("click", 0.9, "notepad")
        result = self.scorer.score("click", {}, app_name="notepad")
        assert result.reliability > 0.7

    def test_inercia_bonus_applied(self):
        """Current tool gets inercia bonus."""
        result_with = self.scorer.score("click", {}, current_tool="click")
        result_without = self.scorer.score("click", {}, current_tool="type_text")
        assert result_with.inercia_bonus is True
        assert result_without.inercia_bonus is False
        assert result_with.composite > result_without.composite

    def test_inercia_bonus_capped_at_one(self):
        """Inercia bonus cannot exceed 1.0."""
        # High scores across all dimensions
        for _ in range(5):
            self.tracker.record("click", 1.0, "app")
        ctx = {"retry_count": 3, "has_timeout": True, "target_app": "app",
               "element_type": "button", "estimated_tokens": 0, "estimated_ms": 0}
        result = self.scorer.score("click", ctx, current_tool="click", app_name="app")
        assert result.composite <= 1.0

    def test_urgency_increases_with_retries(self):
        ctx0 = {"retry_count": 0}
        ctx3 = {"retry_count": 3}
        r0 = self.scorer.score("click", ctx0)
        r3 = self.scorer.score("click", ctx3)
        assert r3.urgency > r0.urgency

    def test_urgency_timeout_boost(self):
        ctx_no = {"retry_count": 0, "has_timeout": False}
        ctx_yes = {"retry_count": 0, "has_timeout": True}
        r_no = self.scorer.score("click", ctx_no)
        r_yes = self.scorer.score("click", ctx_yes)
        assert r_yes.urgency > r_no.urgency

    def test_urgency_capped_at_one(self):
        ctx = {"retry_count": 100, "has_timeout": True}
        result = self.scorer.score("click", ctx)
        assert result.urgency <= 1.0

    def test_relevance_click_button(self):
        """Click tool + button element = high relevance."""
        ctx = {"target_app": "notepad", "element_type": "button"}
        result = self.scorer.score("click", ctx)
        assert result.relevance == 1.0

    def test_relevance_type_edit(self):
        """Type tool + edit element = high relevance."""
        ctx = {"target_app": "notepad", "element_type": "edit"}
        result = self.scorer.score("type_text", ctx)
        assert result.relevance == 1.0

    def test_relevance_no_context_neutral(self):
        """No context = neutral 0.5."""
        result = self.scorer.score("click", {})
        assert result.relevance == 0.5

    def test_cost_free_action(self):
        """Zero tokens + zero ms = cheapest (1.0)."""
        ctx = {"estimated_tokens": 0, "estimated_ms": 0}
        result = self.scorer.score("click", ctx)
        assert result.cost == 1.0

    def test_cost_expensive_action(self):
        """High tokens + high ms = expensive."""
        ctx = {"estimated_tokens": 2000, "estimated_ms": 15000}
        result = self.scorer.score("click", ctx)
        assert result.cost < 0.5

    def test_composite_in_range(self):
        """Composite always in [0, 1]."""
        contexts = [
            {},
            {"retry_count": 5, "has_timeout": True},
            {"estimated_tokens": 5000, "estimated_ms": 30000},
        ]
        for ctx in contexts:
            result = self.scorer.score("click", ctx)
            assert 0.0 <= result.composite <= 1.0


class TestRankActions:
    """rank_actions() tests."""

    def setup_method(self):
        self.tracker = ReliabilityTracker()
        self.scorer = PreActionScorer(reliability_tracker=self.tracker)

    def test_rank_returns_sorted_list(self):
        candidates = ["click", "type_text", "hotkey"]
        ranked = self.scorer.rank_actions(candidates, {})
        assert len(ranked) == 3
        # Sorted by composite descending
        for i in range(len(ranked) - 1):
            assert ranked[i].composite >= ranked[i + 1].composite

    def test_rank_inercia_favors_current(self):
        """Current tool should rank higher with inercia."""
        # Give all tools equal reliability
        for tool in ["click", "type_text"]:
            for _ in range(5):
                self.tracker.record(tool, 0.8)

        ctx = {"target_app": "notepad", "element_type": "button"}
        ranked = self.scorer.rank_actions(
            ["click", "type_text"], ctx, current_tool="type_text",
        )
        # type_text should get inercia bonus
        type_score = next(s for s in ranked if s.tool_name == "type_text")
        assert type_score.inercia_bonus is True

    def test_rank_empty_candidates(self):
        ranked = self.scorer.rank_actions([], {})
        assert ranked == []

    def test_custom_weights(self):
        """Custom weights should be respected."""
        custom = {"reliability": 0.10, "urgency": 0.10, "relevance": 0.10, "cost": 0.70}
        scorer = PreActionScorer(weights=custom)
        # Expensive action should score much lower with cost-heavy weights
        ctx_cheap = {"estimated_tokens": 0, "estimated_ms": 0}
        ctx_expensive = {"estimated_tokens": 3000, "estimated_ms": 20000}
        r_cheap = scorer.score("click", ctx_cheap)
        r_expensive = scorer.score("click", ctx_expensive)
        assert r_cheap.composite > r_expensive.composite
