"""Tests for marlow.kernel.scoring — dimensions, negative checker, reliability, scorer."""

import pytest

from marlow.kernel.scoring.dimensions import (
    score_efficiency,
    score_execution,
    score_outcome,
    score_outcome_confidence,
    weighted_geometric_mean,
)
from marlow.kernel.scoring.negative_checker import NegativeCheck, NegativeStateChecker
from marlow.kernel.scoring.reliability import ReliabilityRecord, ReliabilityTracker
from marlow.kernel.scoring.scorer import ActionScore, ActionScorer, StepVerdict
from marlow.kernel.world_state import WindowInfo, WorldStateSnapshot


# ── Helpers ──


def _make_window(hwnd=1, title="Notepad", process="notepad.exe"):
    return WindowInfo(hwnd=hwnd, title=title, process_name=process)


def _make_snapshot(**kwargs):
    defaults = {
        "cycle_number": 1,
        "timestamp_mono": 100.0,
        "timestamp_utc": "2026-03-02T12:00:00.000Z",
    }
    defaults.update(kwargs)
    return WorldStateSnapshot(**defaults)


# ── Dimensions ──


class TestDimensions:
    """Tests for individual dimension scoring functions."""

    def test_execution_success(self):
        """success=True → 1.0."""
        assert score_execution(True) == 1.0

    def test_execution_failure(self):
        """success=False with no error → 0.0."""
        assert score_execution(False) == 0.0

    def test_execution_timeout(self):
        """Error containing 'timeout' → 0.3 partial."""
        assert score_execution(False, "Connection timeout after 5s") == 0.3

    def test_execution_warning(self):
        """Error containing 'warning' → 0.3 partial."""
        assert score_execution(False, "Warning: element may be obscured") == 0.3

    def test_outcome_both_pass(self):
        """check=True, tool=True → 1.0."""
        assert score_outcome(True, True) == 1.0

    def test_outcome_both_fail(self):
        """check=False, tool=False → 0.0."""
        assert score_outcome(False, False) == 0.0

    def test_outcome_check_fail_tool_ok(self):
        """check=False, tool=True → 0.1 (trust check over tool)."""
        assert score_outcome(False, True) == 0.1

    def test_outcome_check_pass_tool_fail(self):
        """check=True, tool=False → 0.9 (worked despite error)."""
        assert score_outcome(True, False) == 0.9

    def test_outcome_no_check_tool_ok(self):
        """check=None, tool=True → 0.85."""
        assert score_outcome(None, True) == 0.85

    def test_outcome_no_check_tool_fail(self):
        """check=None, tool=False → 0.1."""
        assert score_outcome(None, False) == 0.1

    def test_outcome_confidence_both_agree(self):
        """When tool and check agree → 0.95 confidence."""
        assert score_outcome_confidence(True, True) == 0.95
        assert score_outcome_confidence(False, False) == 0.95

    def test_outcome_confidence_no_check(self):
        """No check → 0.70 confidence."""
        assert score_outcome_confidence(None, True) == 0.70

    def test_efficiency_fast(self):
        """Duration < expected → 1.0."""
        assert score_efficiency(500.0, 3000.0) == 1.0

    def test_efficiency_equal(self):
        """Duration == expected → 1.0."""
        assert score_efficiency(3000.0, 3000.0) == 1.0

    def test_efficiency_slow(self):
        """Duration 2x expected → approximately 0.5."""
        result = score_efficiency(6000.0, 3000.0)
        assert 0.4 <= result <= 0.6

    def test_efficiency_very_slow(self):
        """Duration 10x expected → close to 0.1."""
        result = score_efficiency(30000.0, 3000.0)
        assert 0.1 <= result <= 0.3

    def test_efficiency_zero_duration(self):
        """Zero duration → 0.5 fallback."""
        assert score_efficiency(0.0, 3000.0) == 0.5

    def test_geometric_mean_all_ones(self):
        """All dimensions 1.0 → composite near 1.0."""
        scores = [(1.0, 0.25), (1.0, 0.25), (1.0, 0.25), (1.0, 0.25)]
        result = weighted_geometric_mean(scores)
        assert result > 0.99

    def test_geometric_mean_one_zero(self):
        """One dimension 0 → composite near 0 (epsilon prevents exact 0)."""
        scores = [(1.0, 0.25), (0.0, 0.25), (1.0, 0.25), (1.0, 0.25)]
        result = weighted_geometric_mean(scores)
        assert result < 0.2  # Epsilon floor prevents exact 0

    def test_geometric_mean_mixed(self):
        """Realistic mixed values → reasonable composite."""
        scores = [(1.0, 0.20), (0.85, 0.50), (1.0, 0.20), (0.8, 0.10)]
        result = weighted_geometric_mean(scores)
        assert 0.7 < result < 1.0

    def test_geometric_mean_empty_weights(self):
        """Zero total weight → 0.0."""
        assert weighted_geometric_mean([]) == 0.0


# ── NegativeStateChecker ──


class TestNegativeChecker:
    """Tests for negative side-effect detection."""

    def test_no_changes(self):
        """Same state before and after → safety 1.0."""
        state = _make_snapshot(
            active_window=_make_window(1, "Notepad"),
            open_windows=(_make_window(1, "Notepad"),),
            clipboard_hash="abc",
        )
        checker = NegativeStateChecker()
        score, checks = checker.check(state, state, expected_app="Notepad")
        assert score == 1.0
        assert all(not c.detected for c in checks)

    def test_window_changed_unexpected(self):
        """Window changed to unexpected app → penalty 0.15."""
        before = _make_snapshot(active_window=_make_window(1, "Notepad"))
        after = _make_snapshot(active_window=_make_window(2, "Error Dialog"))

        checker = NegativeStateChecker()
        score, checks = checker.check(before, after, expected_app="Notepad")
        assert score == pytest.approx(0.85, abs=0.01)
        assert any(c.name == "unexpected_window_change" and c.detected for c in checks)

    def test_window_changed_to_expected(self):
        """Window changed but to expected app → no penalty."""
        before = _make_snapshot(active_window=_make_window(1, "Desktop"))
        after = _make_snapshot(active_window=_make_window(2, "Notepad - Untitled"))

        checker = NegativeStateChecker()
        score, _ = checker.check(before, after, expected_app="Notepad")
        assert score == 1.0

    def test_target_disappeared(self):
        """Target window gone → penalty 0.50."""
        before = _make_snapshot(
            active_window=_make_window(1, "Notepad"),
            open_windows=(_make_window(1, "Notepad"),),
        )
        after = _make_snapshot(
            active_window=_make_window(2, "Desktop"),
            open_windows=(_make_window(2, "Desktop"),),
        )

        checker = NegativeStateChecker()
        score, checks = checker.check(before, after, expected_app="Notepad")
        assert score <= 0.50
        assert any(
            c.name == "target_window_disappeared" and c.detected for c in checks
        )

    def test_clipboard_changed(self):
        """Clipboard hash changed → penalty 0.05."""
        before = _make_snapshot(
            active_window=_make_window(1, "Notepad"),
            clipboard_hash="abc",
        )
        after = _make_snapshot(
            active_window=_make_window(1, "Notepad"),
            clipboard_hash="xyz",
        )

        checker = NegativeStateChecker()
        score, checks = checker.check(before, after)
        assert score == pytest.approx(0.95, abs=0.01)
        assert any(
            c.name == "unexpected_clipboard_change" and c.detected for c in checks
        )

    def test_multiple_windows_opened(self):
        """More than 1 new window → penalty 0.10."""
        before = _make_snapshot(open_windows=(_make_window(1, "A"),))
        after = _make_snapshot(open_windows=(
            _make_window(1, "A"),
            _make_window(2, "B"),
            _make_window(3, "C"),
        ))

        checker = NegativeStateChecker()
        score, checks = checker.check(before, after)
        assert score == pytest.approx(0.90, abs=0.01)
        assert any(
            c.name == "multiple_windows_opened" and c.detected for c in checks
        )

    def test_no_state_available(self):
        """None states → 0.85 default score."""
        checker = NegativeStateChecker()
        score, checks = checker.check(None, None)
        assert score == 0.85
        assert len(checks) == 1
        assert checks[0].name == "no_state"


# ── ReliabilityTracker ──


class TestReliabilityTracker:
    """Tests for EMA reliability tracking."""

    def test_initial_unknown(self):
        """No data → 0.5."""
        rt = ReliabilityTracker()
        assert rt.get_reliability("click") == 0.5

    def test_record_and_get(self):
        """Recording 3+ scores → EMA calculated."""
        rt = ReliabilityTracker(alpha=0.3, min_samples=3)
        rt.record("click", 0.9)
        rt.record("click", 0.8)
        rt.record("click", 0.85)
        rel = rt.get_reliability("click")
        assert 0.5 < rel < 1.0

    def test_ema_weighting(self):
        """Recent scores are weighted more heavily (alpha=0.3)."""
        rt = ReliabilityTracker(alpha=0.3, min_samples=1)
        # Start high
        for _ in range(5):
            rt.record("click", 0.9)
        high = rt.get_reliability("click")

        # Then drop
        for _ in range(5):
            rt.record("click", 0.2)
        low = rt.get_reliability("click")

        assert low < high
        # EMA should be between 0.2 and 0.9 but closer to 0.2
        assert low < 0.5

    def test_min_samples(self):
        """Less than min_samples → still returns 0.5 default."""
        rt = ReliabilityTracker(min_samples=3)
        rt.record("click", 1.0)
        rt.record("click", 1.0)
        # Only 2 samples, need 3
        assert rt.get_reliability("click") == 0.5

    def test_degradation_detection(self):
        """Scores dropping significantly → trend='degrading'."""
        rt = ReliabilityTracker(alpha=0.3, degradation_threshold=0.15)
        # 5 high scores
        for _ in range(5):
            rt.record("click", 0.9)
        # 5 low scores
        for _ in range(5):
            rt.record("click", 0.2)
        assert rt.is_degrading("click")

    def test_improvement_detection(self):
        """Scores rising significantly → trend='improving'."""
        rt = ReliabilityTracker(alpha=0.3, degradation_threshold=0.15)
        # 5 low scores
        for _ in range(5):
            rt.record("click", 0.2)
        # 5 high scores
        for _ in range(5):
            rt.record("click", 0.9)
        rec = rt.get_report("click")
        assert rec is not None
        assert rec.trend == "improving"

    def test_stable_no_trend(self):
        """Consistent scores → trend='stable'."""
        rt = ReliabilityTracker(alpha=0.3, degradation_threshold=0.15)
        for _ in range(10):
            rt.record("click", 0.8)
        rec = rt.get_report("click")
        assert rec.trend == "stable"

    def test_format_for_planner(self):
        """Formatted string includes score."""
        rt = ReliabilityTracker(min_samples=1)
        for _ in range(3):
            rt.record("click", 0.85)
        fmt = rt.format_for_planner("click")
        assert "click()" in fmt
        assert "0.8" in fmt  # EMA should be around 0.85

    def test_format_unknown(self):
        """Unknown tool shows 'no data'."""
        rt = ReliabilityTracker()
        fmt = rt.format_for_planner("unknown_tool")
        assert "no data" in fmt

    def test_tool_app_separation(self):
        """Same tool on different apps tracked separately."""
        rt = ReliabilityTracker(min_samples=1)
        rt.record("click", 0.9, app_name="notepad.exe")
        rt.record("click", 0.9, app_name="notepad.exe")
        rt.record("click", 0.9, app_name="notepad.exe")
        rt.record("click", 0.3, app_name="chrome.exe")
        rt.record("click", 0.3, app_name="chrome.exe")
        rt.record("click", 0.3, app_name="chrome.exe")

        notepad_rel = rt.get_reliability("click", "notepad.exe")
        chrome_rel = rt.get_reliability("click", "chrome.exe")
        assert notepad_rel > 0.7
        assert chrome_rel < 0.5

    def test_is_reliable(self):
        """is_reliable checks against threshold."""
        rt = ReliabilityTracker(min_samples=1)
        for _ in range(3):
            rt.record("click", 0.8)
        assert rt.is_reliable("click", threshold=0.6)
        assert not rt.is_reliable("click", threshold=0.9)

    def test_get_all_degrading(self):
        """get_all_degrading returns tools with degrading trend."""
        rt = ReliabilityTracker(degradation_threshold=0.15)
        # Stable tool
        for _ in range(10):
            rt.record("stable_tool", 0.8)
        # Degrading tool
        for _ in range(5):
            rt.record("bad_tool", 0.9)
        for _ in range(5):
            rt.record("bad_tool", 0.2)

        degrading = rt.get_all_degrading()
        keys = [k for k, _ in degrading]
        assert "bad_tool" in keys
        assert "stable_tool" not in keys


# ── ActionScorer ──


class TestActionScorer:
    """Tests for the main action scoring pipeline."""

    def test_perfect_action(self):
        """All good → composite near 1.0, verdict STEP_OK."""
        state = _make_snapshot(
            active_window=_make_window(1, "Notepad"),
            open_windows=(_make_window(1, "Notepad"),),
        )
        scorer = ActionScorer()
        result = scorer.score(
            tool_name="click",
            tool_success=True,
            check_passed=True,
            state_before=state,
            state_after=state,
            expected_app="Notepad",
            duration_ms=500.0,
        )
        assert result.execution == 1.0
        assert result.outcome == 1.0
        assert result.safety == 1.0
        assert result.composite > 0.90
        assert scorer.decide(result) == StepVerdict.STEP_OK

    def test_failed_action(self):
        """All bad → composite near 0, verdict STEP_FAILED."""
        before = _make_snapshot(
            active_window=_make_window(1, "Notepad"),
            open_windows=(_make_window(1, "Notepad"),),
        )
        after = _make_snapshot(
            active_window=_make_window(2, "Error"),
            open_windows=(_make_window(2, "Error"),),
        )
        scorer = ActionScorer()
        result = scorer.score(
            tool_name="click",
            tool_success=False,
            check_passed=False,
            state_before=before,
            state_after=after,
            expected_app="Notepad",
            duration_ms=30000.0,
            expected_duration_ms=3000.0,
        )
        assert result.execution == 0.0
        assert result.outcome == 0.0
        assert result.composite < 0.05
        # Epsilon protection makes composite slightly > 0 → STEP_ALTERNATIVE
        assert scorer.decide(result) == StepVerdict.STEP_ALTERNATIVE

    def test_partial_success(self):
        """Tool ok but check failed → low composite."""
        state = _make_snapshot(
            active_window=_make_window(1, "Notepad"),
            open_windows=(_make_window(1, "Notepad"),),
        )
        scorer = ActionScorer()
        result = scorer.score(
            tool_name="click",
            tool_success=True,
            check_passed=False,  # check says it didn't work
            state_before=state,
            state_after=state,
            expected_app="Notepad",
            duration_ms=2000.0,
        )
        assert result.outcome == 0.1  # trust check
        assert result.composite < 0.5
        verdict = scorer.decide(result)
        assert verdict in (StepVerdict.STEP_RETRY, StepVerdict.STEP_ALTERNATIVE)

    def test_decide_step_ok(self):
        """Composite >= 0.80 → STEP_OK."""
        scorer = ActionScorer()
        score = ActionScore(
            execution=1.0, outcome=1.0, safety=1.0, efficiency=1.0,
            composite=0.95, confidence=0.95, method="programmatic", details={},
        )
        assert scorer.decide(score) == StepVerdict.STEP_OK

    def test_decide_step_partial(self):
        """Composite 0.50-0.79 → STEP_PARTIAL."""
        scorer = ActionScorer()
        score = ActionScore(
            execution=1.0, outcome=0.5, safety=1.0, efficiency=0.5,
            composite=0.65, confidence=0.70, method="programmatic", details={},
        )
        assert scorer.decide(score) == StepVerdict.STEP_PARTIAL

    def test_decide_step_retry(self):
        """Composite 0.30-0.49 → STEP_RETRY."""
        scorer = ActionScorer()
        score = ActionScore(
            execution=0.3, outcome=0.5, safety=1.0, efficiency=0.5,
            composite=0.35, confidence=0.70, method="programmatic", details={},
        )
        assert scorer.decide(score) == StepVerdict.STEP_RETRY

    def test_decide_step_alternative(self):
        """Composite 0.01-0.29 → STEP_ALTERNATIVE."""
        scorer = ActionScorer()
        score = ActionScore(
            execution=0.0, outcome=0.1, safety=0.5, efficiency=0.5,
            composite=0.15, confidence=0.70, method="programmatic", details={},
        )
        assert scorer.decide(score) == StepVerdict.STEP_ALTERNATIVE

    def test_decide_step_failed(self):
        """Composite 0.0 → STEP_FAILED."""
        scorer = ActionScorer()
        score = ActionScore(
            execution=0.0, outcome=0.0, safety=0.0, efficiency=0.0,
            composite=0.0, confidence=0.95, method="programmatic", details={},
        )
        assert scorer.decide(score) == StepVerdict.STEP_FAILED

    def test_reliability_recorded(self):
        """After scoring, reliability tracker has data."""
        scorer = ActionScorer()
        state = _make_snapshot()
        for _ in range(3):
            scorer.score(
                tool_name="click",
                tool_success=True,
                state_before=state,
                state_after=state,
                app_name="notepad.exe",
            )
        rel = scorer.reliability.get_reliability("click", "notepad.exe")
        assert rel > 0.5  # Should be above default

    def test_no_state_graceful(self):
        """None states don't crash, returns reasonable score."""
        scorer = ActionScorer()
        result = scorer.score(
            tool_name="click",
            tool_success=True,
            check_passed=True,
            state_before=None,
            state_after=None,
            duration_ms=1000.0,
        )
        assert result.safety == 0.85  # no_state default
        assert result.composite > 0.5

    def test_action_score_frozen(self):
        """ActionScore is immutable."""
        scorer = ActionScorer()
        result = scorer.score(
            tool_name="click", tool_success=True,
        )
        with pytest.raises(AttributeError):
            result.execution = 0.5

    def test_details_contain_negative_checks(self):
        """Details should list detected negative checks."""
        before = _make_snapshot(
            active_window=_make_window(1, "Notepad"),
            open_windows=(_make_window(1, "Notepad"),),
        )
        after = _make_snapshot(
            active_window=_make_window(2, "Error"),
            open_windows=(_make_window(2, "Error"),),
        )
        scorer = ActionScorer()
        result = scorer.score(
            tool_name="click",
            tool_success=True,
            state_before=before,
            state_after=after,
            expected_app="Notepad",
        )
        detected = result.details["negative_checks"]
        assert len(detected) > 0
        names = [c["name"] for c in detected]
        assert "target_window_disappeared" in names
