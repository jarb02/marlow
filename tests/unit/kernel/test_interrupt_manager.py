"""Tests for marlow.kernel.interrupt_manager — Priority Stack."""

import time
import pytest
from marlow.kernel.interrupt_manager import (
    Priority,
    Interrupt,
    SuspendedTask,
    InterruptManager,
)


# ------------------------------------------------------------------
# Priority enum
# ------------------------------------------------------------------

class TestPriority:
    def test_priority_enum_ordering(self):
        assert Priority.P0_CRITICAL < Priority.P1_HIGH
        assert Priority.P1_HIGH < Priority.P2_MEDIUM
        assert Priority.P2_MEDIUM < Priority.P3_LOW
        assert Priority.P3_LOW < Priority.P4_NOISE

    def test_priority_int_values(self):
        assert int(Priority.P0_CRITICAL) == 0
        assert int(Priority.P4_NOISE) == 4


# ------------------------------------------------------------------
# Interrupt dataclass
# ------------------------------------------------------------------

class TestInterrupt:
    def test_interrupt_dataclass(self):
        i = Interrupt(Priority.P1_HIGH, "dialog", "Save changes?")
        assert i.priority == Priority.P1_HIGH
        assert i.source == "dialog"
        assert i.description == "Save changes?"
        assert isinstance(i.data, dict)
        assert i.timestamp > 0

    def test_interrupt_is_blocking_p0(self):
        i = Interrupt(Priority.P0_CRITICAL, "crash", "App crashed")
        assert i.is_blocking is True

    def test_interrupt_is_blocking_p1(self):
        i = Interrupt(Priority.P1_HIGH, "dialog", "Error dialog")
        assert i.is_blocking is True

    def test_interrupt_not_blocking_p2(self):
        i = Interrupt(Priority.P2_MEDIUM, "focus_lost", "Focus lost")
        assert i.is_blocking is False

    def test_interrupt_not_blocking_p3(self):
        i = Interrupt(Priority.P3_LOW, "notification", "Update available")
        assert i.is_blocking is False


# ------------------------------------------------------------------
# SuspendedTask dataclass
# ------------------------------------------------------------------

class TestSuspendedTask:
    def test_suspended_task_dataclass(self):
        t = SuspendedTask(
            goal_id="goal-1", step_index=3, tool_name="click",
            params={"x": 100}, expected_app="notepad",
        )
        assert t.goal_id == "goal-1"
        assert t.step_index == 3
        assert t.tool_name == "click"
        assert t.params == {"x": 100}
        assert t.expected_app == "notepad"
        assert t.interrupt is None

    def test_suspended_task_age(self):
        t = SuspendedTask(
            goal_id="g", step_index=0, tool_name="click",
            params={}, expected_app="app",
            suspended_at=time.time() - 5.0,
        )
        assert t.age_seconds >= 4.9


# ------------------------------------------------------------------
# InterruptManager — should_interrupt
# ------------------------------------------------------------------

class TestShouldInterrupt:
    def setup_method(self):
        self.mgr = InterruptManager(cooldown=0.0)  # No cooldown for tests

    def test_should_interrupt_p0_always(self):
        """P0 ALWAYS interrupts regardless of anything."""
        self.mgr.set_current_priority(Priority.P0_CRITICAL)
        i = Interrupt(Priority.P0_CRITICAL, "crash", "Fatal")
        assert self.mgr.should_interrupt(i) is True

    def test_should_interrupt_p3_never(self):
        self.mgr.set_current_priority(Priority.P4_NOISE)
        i = Interrupt(Priority.P3_LOW, "notification", "Update")
        assert self.mgr.should_interrupt(i) is False

    def test_should_interrupt_p4_never(self):
        self.mgr.set_current_priority(Priority.P4_NOISE)
        i = Interrupt(Priority.P4_NOISE, "tooltip", "Hover")
        assert self.mgr.should_interrupt(i) is False

    def test_should_interrupt_cooldown(self):
        mgr = InterruptManager(cooldown=10.0)  # 10s cooldown
        mgr.set_current_priority(Priority.P4_NOISE)
        # Record a recent interrupt time
        mgr._last_interrupt_time = time.time()
        i = Interrupt(Priority.P1_HIGH, "dialog", "Error")
        # Should be rejected due to cooldown (P1 is not P0)
        assert mgr.should_interrupt(i) is False

    def test_should_interrupt_hysteresis(self):
        """P2 cannot interrupt a P2 task (needs HYSTERESIS=1 level higher)."""
        self.mgr.set_current_priority(Priority.P2_MEDIUM)
        i = Interrupt(Priority.P2_MEDIUM, "focus_lost", "Focus")
        assert self.mgr.should_interrupt(i) is False

    def test_should_interrupt_hysteresis_pass(self):
        """P1 can interrupt a P4 task (well above hysteresis)."""
        self.mgr.set_current_priority(Priority.P4_NOISE)
        i = Interrupt(Priority.P1_HIGH, "dialog", "Error")
        assert self.mgr.should_interrupt(i) is True

    def test_should_interrupt_stack_full(self):
        mgr = InterruptManager(cooldown=0.0, max_stack_depth=2)
        mgr.set_current_priority(Priority.P4_NOISE)
        # Fill the stack
        for idx in range(2):
            mgr.suspend_task(
                f"g{idx}", idx, "click", {}, "app",
                Interrupt(Priority.P1_HIGH, "dialog", f"Dialog {idx}"),
            )
        # Stack is full — P1 should be rejected (not P0)
        i = Interrupt(Priority.P1_HIGH, "dialog", "Another dialog")
        assert mgr.should_interrupt(i) is False

    def test_p0_ignores_stack_full(self):
        """P0 still interrupts even when stack is full."""
        mgr = InterruptManager(cooldown=0.0, max_stack_depth=1)
        mgr.suspend_task(
            "g", 0, "click", {}, "app",
            Interrupt(Priority.P1_HIGH, "dialog", "D"),
        )
        i = Interrupt(Priority.P0_CRITICAL, "crash", "Fatal")
        assert mgr.should_interrupt(i) is True


# ------------------------------------------------------------------
# InterruptManager — suspend/resume
# ------------------------------------------------------------------

class TestSuspendResume:
    def setup_method(self):
        self.mgr = InterruptManager(cooldown=0.0)

    def test_suspend_and_resume(self):
        interrupt = Interrupt(Priority.P1_HIGH, "dialog", "Save?")
        self.mgr.suspend_task("goal-1", 5, "type_text", {"text": "hi"}, "notepad", interrupt)
        assert self.mgr.stack_depth == 1
        assert self.mgr.has_suspended_tasks is True

        task = self.mgr.resume_task()
        assert task is not None
        assert task.goal_id == "goal-1"
        assert task.step_index == 5
        assert task.tool_name == "type_text"
        assert task.params == {"text": "hi"}
        assert task.expected_app == "notepad"
        assert task.interrupt is interrupt
        assert self.mgr.stack_depth == 0

    def test_resume_empty_stack(self):
        assert self.mgr.resume_task() is None

    def test_stack_depth(self):
        assert self.mgr.stack_depth == 0
        for i in range(3):
            self.mgr.suspend_task(
                f"g{i}", i, "click", {}, "app",
                Interrupt(Priority.P1_HIGH, "dialog", f"D{i}"),
            )
        assert self.mgr.stack_depth == 3

    def test_lifo_order(self):
        """Stack is LIFO — most recently suspended resumes first."""
        for i in range(3):
            self.mgr.suspend_task(
                f"g{i}", i, "click", {}, "app",
                Interrupt(Priority.P1_HIGH, "dialog", f"D{i}"),
            )
        t2 = self.mgr.resume_task()
        assert t2.goal_id == "g2"
        t1 = self.mgr.resume_task()
        assert t1.goal_id == "g1"
        t0 = self.mgr.resume_task()
        assert t0.goal_id == "g0"

    def test_clear_stack(self):
        for i in range(3):
            self.mgr.suspend_task(
                f"g{i}", i, "click", {}, "app",
                Interrupt(Priority.P1_HIGH, "dialog", f"D{i}"),
            )
        self.mgr.clear_stack()
        assert self.mgr.stack_depth == 0
        assert self.mgr.has_suspended_tasks is False


# ------------------------------------------------------------------
# InterruptManager — classify_event
# ------------------------------------------------------------------

class TestClassifyEvent:
    def setup_method(self):
        self.mgr = InterruptManager()

    def test_classify_crash(self):
        i = self.mgr.classify_event("crash", "Notepad")
        assert i.priority == Priority.P0_CRITICAL
        assert i.source == "crash"

    def test_classify_not_responding(self):
        i = self.mgr.classify_event("not_responding", "Chrome")
        assert i.priority == Priority.P0_CRITICAL

    def test_classify_fatal_message(self):
        i = self.mgr.classify_event("unknown", "App", "Fatal error occurred")
        assert i.priority == Priority.P0_CRITICAL

    def test_classify_dialog_error(self):
        i = self.mgr.classify_event("dialog", "Error - File not found")
        assert i.priority == Priority.P1_HIGH
        assert i.source == "dialog"

    def test_classify_dialog_file_exists(self):
        i = self.mgr.classify_event("dialog", "Confirm", "File already exists, replace?")
        assert i.priority == Priority.P1_HIGH

    def test_classify_dialog_save_changes(self):
        i = self.mgr.classify_event("dialog", "Notepad", "Do you want to save changes?")
        assert i.priority == Priority.P1_HIGH

    def test_classify_dialog_generic(self):
        i = self.mgr.classify_event("dialog", "About Notepad", "Version 11.0")
        assert i.priority == Priority.P2_MEDIUM

    def test_classify_focus_lost(self):
        i = self.mgr.classify_event("focus_lost", "Notepad")
        assert i.priority == Priority.P2_MEDIUM
        assert i.source == "focus_lost"

    def test_classify_window_disappeared(self):
        i = self.mgr.classify_event("window_disappeared", "Notepad")
        assert i.priority == Priority.P2_MEDIUM

    def test_classify_notification(self):
        i = self.mgr.classify_event("notification", "Windows Update")
        assert i.priority == Priority.P3_LOW

    def test_classify_window_appeared_notification(self):
        i = self.mgr.classify_event("window_appeared", "Update Available")
        assert i.priority == Priority.P3_LOW

    def test_classify_window_appeared_normal(self):
        i = self.mgr.classify_event("window_appeared", "New Document")
        assert i.priority == Priority.P2_MEDIUM

    def test_classify_unknown_event(self):
        i = self.mgr.classify_event("hover", "tooltip text")
        assert i.priority == Priority.P4_NOISE


# ------------------------------------------------------------------
# InterruptManager — history and rate
# ------------------------------------------------------------------

class TestHistory:
    def setup_method(self):
        self.mgr = InterruptManager(cooldown=0.0)

    def test_get_interrupt_rate(self):
        # Suspend 3 tasks (each records an interrupt)
        for i in range(3):
            self.mgr.suspend_task(
                f"g{i}", i, "click", {}, "app",
                Interrupt(Priority.P1_HIGH, "dialog", f"D{i}"),
            )
        rate = self.mgr.get_interrupt_rate(60)
        assert rate == pytest.approx(3.0, abs=0.1)  # 3 per 60s = 3.0/min

    def test_get_recent_interrupts(self):
        for i in range(3):
            self.mgr.suspend_task(
                f"g{i}", i, "click", {}, "app",
                Interrupt(Priority.P1_HIGH, "dialog", f"D{i}"),
            )
        recent = self.mgr.get_recent_interrupts(60)
        assert len(recent) == 3

    def test_get_interrupt_rate_zero_seconds(self):
        rate = self.mgr.get_interrupt_rate(0)
        assert rate == 0.0
