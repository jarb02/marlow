"""Tests for marlow.kernel.events — Typed Events."""

import pytest
from marlow.kernel.events import (
    Event,
    EventPriority,
    GoalStarted,
    GoalCompleted,
    ActionStarting,
    ActionFailed,
    DialogDetected,
    KillSwitchActivated,
    ALL_EVENT_TYPES,
    ALL_CATEGORIES,
)


class TestEvent:
    def test_event_base_fields(self):
        e = Event(event_type="test.event", source="unit_test", correlation_id="abc")
        assert e.event_type == "test.event"
        assert e.source == "unit_test"
        assert e.correlation_id == "abc"
        assert e.priority == EventPriority.NORMAL
        assert isinstance(e.data, dict)
        assert e.timestamp > 0

    def test_event_frozen(self):
        e = Event(event_type="test.event")
        with pytest.raises(AttributeError):
            e.event_type = "changed"

    def test_event_category(self):
        assert Event(event_type="goal.started").category == "goal"
        assert Event(event_type="action.completed").category == "action"
        assert Event(event_type="system.kill_switch").category == "system"

    def test_event_category_no_dot(self):
        assert Event(event_type="simple").category == "simple"


class TestGoalEvents:
    def test_goal_started_defaults(self):
        e = GoalStarted(goal_text="Open Notepad")
        assert e.event_type == "goal.started"
        assert e.goal_text == "Open Notepad"
        assert e.category == "goal"

    def test_goal_completed_defaults(self):
        e = GoalCompleted(goal_text="Done", success=True, steps_executed=3)
        assert e.event_type == "goal.completed"
        assert e.success is True
        assert e.steps_executed == 3


class TestActionEvents:
    def test_action_starting_defaults(self):
        e = ActionStarting(tool_name="click", pre_score=0.85)
        assert e.event_type == "action.starting"
        assert e.tool_name == "click"
        assert e.pre_score == 0.85

    def test_action_failed_priority_high(self):
        e = ActionFailed(tool_name="click", error="timeout")
        assert e.priority == EventPriority.HIGH


class TestWorldEvents:
    def test_dialog_detected_priority_high(self):
        e = DialogDetected(dialog_title="Error", dialog_type="error")
        assert e.priority == EventPriority.HIGH
        assert e.event_type == "world.dialog_detected"


class TestSystemEvents:
    def test_kill_switch_priority_critical(self):
        e = KillSwitchActivated()
        assert e.priority == EventPriority.CRITICAL
        assert e.event_type == "system.kill_switch"


class TestConvenience:
    def test_all_event_types_list(self):
        assert len(ALL_EVENT_TYPES) == 22
        assert "goal.started" in ALL_EVENT_TYPES
        assert "system.kill_switch" in ALL_EVENT_TYPES
        assert "audio.tts_completed" in ALL_EVENT_TYPES

    def test_all_categories(self):
        assert set(ALL_CATEGORIES) == {"goal", "action", "world", "system", "audio"}
