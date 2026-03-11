"""Tests for Phase 4: Patterns and Proactivity.

Tests PatternDetector analysis, ProactiveEngine classification,
feedback loop, rate limiting, and fault tolerance.
"""

import asyncio
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marlow.kernel.pattern_detector import (
    Pattern,
    PatternDetector,
    _pattern_id,
)
from marlow.kernel.proactive_engine import (
    ActionClass,
    ProactiveConfig,
    ProactiveEngine,
)


def _run(coro):
    return asyncio.run(coro)


def _make_log_entry(
    tool_name="open_application",
    app_name="firefox",
    success=True,
    timestamp=None,
    parameters=None,
):
    """Create a fake action log entry."""
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).isoformat()
    return {
        "id": 1,
        "tool_name": tool_name,
        "app_name": app_name,
        "action_type": "tool",
        "parameters": parameters or {"application": "firefox"},
        "success": success,
        "score": 0.9,
        "duration_ms": 100,
        "error_message": None,
        "timestamp": timestamp,
    }


def _make_temporal_logs(tool="open_application", app="firefox", hour=9, minute=0, days=5):
    """Generate logs that form a temporal pattern."""
    logs = []
    base = datetime.now(timezone.utc).replace(hour=hour, minute=minute, second=0)
    for d in range(days):
        ts = base - timedelta(days=d)
        logs.append(_make_log_entry(
            tool_name=tool,
            app_name=app,
            timestamp=ts.strftime("%Y-%m-%dT%H:%M:%S.000000Z"),
            parameters={"application": app},
        ))
    return logs


def _make_sequential_logs(tool_a="open_application", tool_b="focus_window", count=5):
    """Generate logs that form a sequential pattern (A → B within 2 min)."""
    logs = []
    base = datetime.now(timezone.utc) - timedelta(hours=10)
    for i in range(count):
        ts_a = base + timedelta(hours=i)
        ts_b = ts_a + timedelta(seconds=120)
        logs.append(_make_log_entry(
            tool_name=tool_a, timestamp=ts_a.strftime("%Y-%m-%dT%H:%M:%S.000000Z"),
        ))
        logs.append(_make_log_entry(
            tool_name=tool_b, timestamp=ts_b.strftime("%Y-%m-%dT%H:%M:%S.000000Z"),
        ))
    return logs


# ══════════════════════════════════════════════════════════════
# 4.1: PatternDetector
# ══════════════════════════════════════════════════════════════


class TestPatternDetector:
    """Pattern detection from action logs."""

    def test_temporal_pattern_detected(self):
        """Regular action at same hour should be detected."""
        logs = _make_temporal_logs(hour=9, days=5)
        detector = PatternDetector()
        patterns = detector._detect_temporal(logs)
        assert len(patterns) >= 1
        p = patterns[0]
        assert p.type == "temporal"
        assert p.tool_name == "open_application"
        assert p.schedule_hour == 9
        assert p.occurrences >= 3

    def test_temporal_needs_minimum_occurrences(self):
        """Fewer than 3 occurrences should not produce a pattern."""
        logs = _make_temporal_logs(hour=9, days=2)
        detector = PatternDetector()
        patterns = detector._detect_temporal(logs)
        assert len(patterns) == 0

    def test_sequential_pattern_detected(self):
        """Frequent A → B pairs should be detected."""
        logs = _make_sequential_logs(count=5)
        detector = PatternDetector()
        patterns = detector._detect_sequential(logs)
        assert len(patterns) >= 1
        p = [p for p in patterns if p.trigger_tool == "open_application"]
        assert len(p) >= 1
        assert p[0].type == "sequential"
        assert p[0].gap_seconds > 0

    def test_contextual_pattern_detected(self):
        """Repeated tool+app combinations should be detected."""
        logs = [
            _make_log_entry(tool_name="type_text", app_name="vscode")
            for _ in range(10)
        ]
        detector = PatternDetector()
        patterns = detector._detect_contextual(logs)
        assert len(patterns) >= 1
        p = patterns[0]
        assert p.type == "contextual"
        assert p.context_app == "vscode"

    def test_failed_actions_excluded(self):
        """Failed actions should not form patterns."""
        logs = [
            _make_log_entry(tool_name="click", success=False)
            for _ in range(10)
        ]
        detector = PatternDetector()
        assert detector._detect_temporal(logs) == []
        assert detector._detect_sequential(logs) == []
        assert detector._detect_contextual(logs) == []

    def test_get_active_patterns(self):
        detector = PatternDetector(confidence_threshold=0.9)
        detector._patterns = {
            "a": Pattern(id="a", type="temporal", tool_name="x", confidence=0.95, active=True),
            "b": Pattern(id="b", type="temporal", tool_name="y", confidence=0.80, active=True),
            "c": Pattern(id="c", type="temporal", tool_name="z", confidence=0.99, active=False),
        }
        active = detector.get_active_patterns()
        assert len(active) == 1
        assert active[0].id == "a"

    def test_get_due_patterns(self):
        now = datetime.now()
        detector = PatternDetector(confidence_threshold=0.9)
        detector._patterns = {
            "a": Pattern(
                id="a", type="temporal", tool_name="x",
                confidence=0.95, active=True,
                schedule_days=[now.weekday()],
                schedule_hour=now.hour,
                schedule_minute=now.minute,
            ),
            "b": Pattern(
                id="b", type="temporal", tool_name="y",
                confidence=0.95, active=True,
                schedule_days=[now.weekday()],
                schedule_hour=(now.hour + 5) % 24,  # 5 hours away
                schedule_minute=0,
            ),
        }
        due = detector.get_due_patterns(now)
        assert len(due) == 1
        assert due[0].id == "a"

    def test_get_due_respects_day(self):
        """Pattern for a different day should not be due."""
        now = datetime.now()
        other_day = (now.weekday() + 3) % 7
        detector = PatternDetector(confidence_threshold=0.9)
        detector._patterns = {
            "a": Pattern(
                id="a", type="temporal", tool_name="x",
                confidence=0.95, active=True,
                schedule_days=[other_day],
                schedule_hour=now.hour,
                schedule_minute=now.minute,
            ),
        }
        assert detector.get_due_patterns(now) == []


# ══════════════════════════════════════════════════════════════
# 4.2: Feedback loop
# ══════════════════════════════════════════════════════════════


class TestFeedbackLoop:
    """Pattern feedback from user actions."""

    def test_approval_increases_confidence(self):
        detector = PatternDetector()
        detector._patterns = {
            "a": Pattern(id="a", type="temporal", tool_name="x", confidence=0.90, active=True),
        }
        detector.record_feedback("a", approved=True)
        assert detector._patterns["a"].confidence == pytest.approx(0.92, abs=0.001)
        assert detector._patterns["a"].approvals == 1

    def test_rejection_decreases_confidence(self):
        detector = PatternDetector()
        detector._patterns = {
            "a": Pattern(id="a", type="temporal", tool_name="x", confidence=0.90, active=True),
        }
        detector.record_feedback("a", approved=False)
        assert detector._patterns["a"].confidence == pytest.approx(0.85, abs=0.001)
        assert detector._patterns["a"].rejections == 1

    def test_three_rejections_deactivates(self):
        detector = PatternDetector()
        detector._patterns = {
            "a": Pattern(id="a", type="temporal", tool_name="x", confidence=0.90, active=True),
        }
        for _ in range(3):
            detector.record_feedback("a", approved=False)
        assert detector._patterns["a"].active is False
        assert detector._patterns["a"].confidence == 0.0

    def test_approval_resets_consecutive_rejections(self):
        detector = PatternDetector()
        detector._patterns = {
            "a": Pattern(id="a", type="temporal", tool_name="x", confidence=0.90, active=True),
        }
        detector.record_feedback("a", approved=False)
        detector.record_feedback("a", approved=False)
        assert detector._patterns["a"].consecutive_rejections == 2
        detector.record_feedback("a", approved=True)
        assert detector._patterns["a"].consecutive_rejections == 0

    def test_unknown_pattern_feedback_noop(self):
        detector = PatternDetector()
        detector.record_feedback("nonexistent", approved=True)  # should not crash


# ══════════════════════════════════════════════════════════════
# 4.3: ProactiveEngine classification
# ══════════════════════════════════════════════════════════════


class TestProactiveClassification:
    """Verify AUTO / SUGGEST / SKIP classification."""

    def _make_engine(self, gate_trust=1, gate_allowed=True, suggestion_only=False,
                     desktop_state="ESTABLE", windows=None):
        gate = AsyncMock()
        gate_result = MagicMock()
        gate_result.allowed = gate_allowed
        gate_result.trust_level = gate_trust
        gate_result.suggestion_only = suggestion_only
        gate.check = AsyncMock(return_value=gate_result)

        observer = MagicMock()
        obs_state = MagicMock()
        obs_state.desktop_state = desktop_state
        obs_state.windows = windows or {}
        observer.get_state = MagicMock(return_value=obs_state)

        engine = ProactiveEngine(
            security_gate=gate,
            desktop_observer=observer,
        )
        return engine

    def test_high_confidence_low_trust_is_auto(self):
        engine = self._make_engine(gate_trust=1, gate_allowed=True)
        pattern = Pattern(id="a", type="temporal", tool_name="open_application", confidence=0.96)
        result = _run(engine._classify(pattern))
        assert result == ActionClass.AUTO

    def test_medium_confidence_tier2_is_suggest(self):
        engine = self._make_engine(gate_trust=2, gate_allowed=False, suggestion_only=True)
        pattern = Pattern(id="a", type="temporal", tool_name="type_text", confidence=0.92)
        result = _run(engine._classify(pattern))
        assert result == ActionClass.SUGGEST

    def test_low_confidence_is_skip(self):
        engine = self._make_engine(gate_trust=1, gate_allowed=True)
        pattern = Pattern(id="a", type="temporal", tool_name="open_application", confidence=0.85)
        result = _run(engine._classify(pattern))
        assert result == ActionClass.SKIP

    def test_high_trust_is_skip(self):
        engine = self._make_engine(gate_trust=3, gate_allowed=False, suggestion_only=False)
        pattern = Pattern(id="a", type="temporal", tool_name="run_command", confidence=0.99)
        result = _run(engine._classify(pattern))
        assert result == ActionClass.SKIP

    def test_unstable_desktop_is_skip(self):
        engine = self._make_engine(gate_trust=0, gate_allowed=True, desktop_state="TORMENTA")
        pattern = Pattern(id="a", type="temporal", tool_name="list_windows", confidence=0.99)
        result = _run(engine._classify(pattern))
        assert result == ActionClass.SKIP

    def test_already_done_is_skip(self):
        from marlow.kernel.desktop_observer import WindowInfo
        windows = {1: WindowInfo(id=1, title="Mozilla Firefox", app_id="firefox")}
        engine = self._make_engine(gate_trust=1, gate_allowed=True, windows=windows)
        pattern = Pattern(
            id="a", type="temporal", tool_name="open_application",
            confidence=0.99, params={"application": "firefox"},
        )
        result = _run(engine._classify(pattern))
        assert result == ActionClass.SKIP


# ══════════════════════════════════════════════════════════════
# 4.4: Rate limiting and failure handling
# ══════════════════════════════════════════════════════════════


class TestRateLimiting:
    """Verify proactive rate limits and failure pauses."""

    def test_cooldown_blocks_evaluation(self):
        engine = ProactiveEngine(
            pattern_detector=MagicMock(),
            pipeline=MagicMock(),
            config=ProactiveConfig(cooldown_seconds=60),
        )
        engine._user_idle = True
        engine._last_action_time = time.time()  # just acted
        assert engine._should_evaluate() is False

    def test_max_per_hour_blocks(self):
        engine = ProactiveEngine(
            pattern_detector=MagicMock(),
            pipeline=MagicMock(),
            config=ProactiveConfig(max_per_hour=2, cooldown_seconds=0),
        )
        engine._user_idle = True
        now = time.time()
        engine._actions_this_hour = [now - 10, now - 5]
        assert engine._should_evaluate() is False

    def test_max_per_day_blocks(self):
        engine = ProactiveEngine(
            pattern_detector=MagicMock(),
            pipeline=MagicMock(),
            config=ProactiveConfig(max_per_day=1, cooldown_seconds=0),
        )
        engine._user_idle = True
        engine._actions_today = [time.time() - 100]
        assert engine._should_evaluate() is False

    def test_consecutive_failures_pause(self):
        engine = ProactiveEngine(
            config=ProactiveConfig(max_consecutive_failures=3, pause_after_failures_minutes=60),
        )
        pattern = Pattern(id="a", type="temporal", tool_name="x", confidence=0.99)
        for _ in range(3):
            engine._on_proactive_failure(pattern, "boom")
        assert engine._pause_until > time.time()
        assert engine._consecutive_failures == 3

    def test_success_resets_failure_counter(self):
        engine = ProactiveEngine(
            pattern_detector=MagicMock(),
            pipeline=AsyncMock(),
        )
        engine._consecutive_failures = 2
        pipeline_result = MagicMock()
        pipeline_result.success = True
        engine._pipeline.execute = AsyncMock(return_value=pipeline_result)
        pattern = Pattern(id="a", type="temporal", tool_name="x", confidence=0.99, params={})

        with patch.object(engine, '_notify'):
            _run(engine._execute_auto(pattern))

        assert engine._consecutive_failures == 0
        assert engine._total_auto == 1

    def test_disabled_blocks_evaluation(self):
        engine = ProactiveEngine(
            pattern_detector=MagicMock(),
            pipeline=MagicMock(),
            config=ProactiveConfig(enabled=False),
        )
        engine._user_idle = True
        assert engine._should_evaluate() is False

    def test_paused_blocks_evaluation(self):
        engine = ProactiveEngine(
            pattern_detector=MagicMock(),
            pipeline=MagicMock(),
        )
        engine._user_idle = True
        engine.pause()
        assert engine._should_evaluate() is False
        engine.resume()
        # Still needs cooldown to be 0 etc.


# ══════════════════════════════════════════════════════════════
# 4.4b: Implicit approval
# ══════════════════════════════════════════════════════════════


class TestImplicitApproval:
    """User actions matching pending suggestions count as approvals."""

    def test_matching_action_approves(self):
        detector = MagicMock()
        engine = ProactiveEngine(pattern_detector=detector)
        engine._pending_suggestions["p1"] = MagicMock(
            pattern_id="p1",
            tool_name="open_application",
            params={},
            suggested_at=time.time(),
        )

        event = MagicMock()
        event.tool_name = "open_application"
        _run(engine._on_action_completed(event))

        detector.record_feedback.assert_called_once_with("p1", approved=True)
        assert "p1" not in engine._pending_suggestions

    def test_non_matching_action_no_approval(self):
        detector = MagicMock()
        engine = ProactiveEngine(pattern_detector=detector)
        engine._pending_suggestions["p1"] = MagicMock(
            pattern_id="p1",
            tool_name="open_application",
            params={},
            suggested_at=time.time(),
        )

        event = MagicMock()
        event.tool_name = "click"
        _run(engine._on_action_completed(event))

        detector.record_feedback.assert_not_called()

    def test_expired_suggestion_not_approved(self):
        detector = MagicMock()
        engine = ProactiveEngine(pattern_detector=detector)
        engine._pending_suggestions["p1"] = MagicMock(
            pattern_id="p1",
            tool_name="open_application",
            params={},
            suggested_at=time.time() - 700,  # 11+ min ago (expired)
        )

        event = MagicMock()
        event.tool_name = "open_application"
        _run(engine._on_action_completed(event))

        detector.record_feedback.assert_not_called()


# ══════════════════════════════════════════════════════════════
# 4.5: Fault tolerance
# ══════════════════════════════════════════════════════════════


class TestFaultTolerance:
    """Resilience to errors."""

    def test_no_detector_safe(self):
        engine = ProactiveEngine()
        assert engine._should_evaluate() is False

    def test_no_pipeline_safe(self):
        engine = ProactiveEngine(pattern_detector=MagicMock())
        engine._user_idle = True
        assert engine._should_evaluate() is False

    def test_gate_crash_skips(self):
        gate = AsyncMock()
        gate.check = AsyncMock(side_effect=RuntimeError("gate on fire"))
        engine = ProactiveEngine(security_gate=gate)
        pattern = Pattern(id="a", type="temporal", tool_name="x", confidence=0.99)
        result = _run(engine._classify(pattern))
        assert result == ActionClass.SKIP

    def test_get_stats_always_works(self):
        engine = ProactiveEngine()
        stats = engine.get_stats()
        assert isinstance(stats, dict)
        assert "enabled" in stats

    def test_stop_sets_stopping(self):
        engine = ProactiveEngine()
        engine.stop()
        assert engine._stopping is True

    def test_pattern_detector_empty_logs(self):
        detector = PatternDetector()
        _run(detector._scan())  # no log_repo — should not crash

    def test_pattern_id_deterministic(self):
        a = _pattern_id("temporal", "click", "extra")
        b = _pattern_id("temporal", "click", "extra")
        assert a == b

    def test_pattern_id_different_inputs(self):
        a = _pattern_id("temporal", "click", "a")
        b = _pattern_id("temporal", "click", "b")
        assert a != b
