"""Tests for marlow.kernel.event_bus — EventBus Core."""

import asyncio
import pytest
from marlow.kernel.event_bus import EventBus, Subscription
from marlow.kernel.events import (
    Event,
    EventPriority,
    GoalStarted,
    GoalCompleted,
    GoalFailed,
    ActionCompleted,
    ActionFailed,
    KillSwitchActivated,
)


@pytest.fixture
def bus():
    return EventBus()


class TestSubscription:
    def test_matches_exact(self):
        sub = Subscription("goal.started", handler=None)
        assert sub.matches("goal.started") is True
        assert sub.matches("goal.failed") is False

    def test_matches_wildcard_category(self):
        sub = Subscription("goal.*", handler=None)
        assert sub.matches("goal.started") is True
        assert sub.matches("goal.completed") is True
        assert sub.matches("action.started") is False

    def test_matches_wildcard_suffix(self):
        sub = Subscription("*.failed", handler=None)
        assert sub.matches("action.failed") is True
        assert sub.matches("goal.failed") is True
        assert sub.matches("goal.started") is False

    def test_matches_wildcard_all(self):
        sub = Subscription("*", handler=None)
        assert sub.matches("goal.started") is True
        assert sub.matches("anything") is True

    def test_circuit_broken_threshold(self):
        sub = Subscription("*", handler=None)
        assert sub.is_circuit_broken is False
        sub.error_count = 3
        assert sub.is_circuit_broken is True


class TestEventBusPubSub:
    @pytest.mark.asyncio
    async def test_subscribe_and_publish(self, bus):
        received = []

        async def handler(event):
            received.append(event)

        bus.subscribe("goal.started", handler, "test")
        await bus.publish(GoalStarted(goal_text="Open Notepad"))
        assert len(received) == 1
        assert received[0].goal_text == "Open Notepad"

    @pytest.mark.asyncio
    async def test_wildcard_category(self, bus):
        received = []

        async def handler(event):
            received.append(event)

        bus.subscribe("goal.*", handler, "test")
        await bus.publish(GoalStarted(goal_text="A"))
        await bus.publish(GoalCompleted(goal_text="B"))
        await bus.publish(ActionCompleted(tool_name="click"))  # should NOT match
        assert len(received) == 2

    @pytest.mark.asyncio
    async def test_wildcard_suffix(self, bus):
        received = []

        async def handler(event):
            received.append(event)

        bus.subscribe("*.failed", handler, "test")
        await bus.publish(ActionFailed(tool_name="click", error="timeout"))
        await bus.publish(GoalFailed(goal_text="X", error="err"))
        await bus.publish(GoalStarted(goal_text="Y"))  # should NOT match
        assert len(received) == 2

    @pytest.mark.asyncio
    async def test_wildcard_all(self, bus):
        received = []

        async def handler(event):
            received.append(event)

        bus.subscribe("*", handler, "catch_all")
        await bus.publish(GoalStarted(goal_text="A"))
        await bus.publish(ActionCompleted(tool_name="B"))
        await bus.publish(KillSwitchActivated())
        assert len(received) == 3

    @pytest.mark.asyncio
    async def test_exact_match_only(self, bus):
        received = []

        async def handler(event):
            received.append(event)

        bus.subscribe("goal.started", handler, "test")
        await bus.publish(GoalCompleted(goal_text="Done"))  # should NOT match
        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_no_match_no_call(self, bus):
        received = []

        async def handler(event):
            received.append(event)

        bus.subscribe("audio.*", handler, "test")
        await bus.publish(GoalStarted(goal_text="X"))
        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_unsubscribe(self, bus):
        received = []

        async def handler(event):
            received.append(event)

        sub = bus.subscribe("goal.*", handler, "test")
        await bus.publish(GoalStarted(goal_text="A"))
        assert len(received) == 1

        bus.unsubscribe(sub)
        await bus.publish(GoalStarted(goal_text="B"))
        assert len(received) == 1  # no new events

    @pytest.mark.asyncio
    async def test_multiple_subscribers_same_pattern(self, bus):
        received_a = []
        received_b = []

        async def handler_a(event):
            received_a.append(event)

        async def handler_b(event):
            received_b.append(event)

        bus.subscribe("goal.*", handler_a, "A")
        bus.subscribe("goal.*", handler_b, "B")
        await bus.publish(GoalStarted(goal_text="X"))
        assert len(received_a) == 1
        assert len(received_b) == 1

    @pytest.mark.asyncio
    async def test_publish_order(self, bus):
        """Subscribers are called in registration order."""
        order = []

        async def first(event):
            order.append("first")

        async def second(event):
            order.append("second")

        bus.subscribe("goal.*", first, "first")
        bus.subscribe("goal.*", second, "second")
        await bus.publish(GoalStarted(goal_text="X"))
        assert order == ["first", "second"]


class TestCircuitBreaker:
    @pytest.mark.asyncio
    async def test_circuit_breaker_after_3_errors(self, bus):
        call_count = 0

        async def bad_handler(event):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("boom")

        bus.subscribe("goal.*", bad_handler, "bad")
        # 3 errors -> circuit broken
        for _ in range(3):
            await bus.publish(GoalStarted(goal_text="X"))
        assert call_count == 3

        # 4th publish: handler is circuit-broken, not called
        await bus.publish(GoalStarted(goal_text="Y"))
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_circuit_breaker_reset_on_success(self, bus):
        fail = True

        async def flaky_handler(event):
            if fail:
                raise RuntimeError("fail")

        sub = bus.subscribe("goal.*", flaky_handler, "flaky")
        # 2 errors
        await bus.publish(GoalStarted(goal_text="X"))
        await bus.publish(GoalStarted(goal_text="X"))
        assert sub.error_count == 2

        # Success resets counter
        fail = False
        await bus.publish(GoalStarted(goal_text="X"))
        assert sub.error_count == 0

    @pytest.mark.asyncio
    async def test_reset_circuit_breaker(self, bus):
        async def bad_handler(event):
            raise RuntimeError("fail")

        bus.subscribe("goal.*", bad_handler, "bad")
        for _ in range(3):
            await bus.publish(GoalStarted(goal_text="X"))

        # Circuit is broken
        subs = [s for s in bus._subscriptions if s.subscriber_name == "bad"]
        assert subs[0].is_circuit_broken is True

        # Reset
        bus.reset_circuit_breaker("bad")
        assert subs[0].error_count == 0
        assert subs[0].is_circuit_broken is False


class TestPauseResume:
    @pytest.mark.asyncio
    async def test_pause_drops_events(self, bus):
        received = []

        async def handler(event):
            received.append(event)

        bus.subscribe("goal.*", handler, "test")
        bus.pause()
        assert bus.is_paused is True
        await bus.publish(GoalStarted(goal_text="dropped"))
        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_resume_dispatches_again(self, bus):
        received = []

        async def handler(event):
            received.append(event)

        bus.subscribe("goal.*", handler, "test")
        bus.pause()
        await bus.publish(GoalStarted(goal_text="dropped"))
        bus.resume()
        assert bus.is_paused is False
        await bus.publish(GoalStarted(goal_text="received"))
        assert len(received) == 1
        assert received[0].goal_text == "received"


class TestHistory:
    @pytest.mark.asyncio
    async def test_event_history(self, bus):
        await bus.publish(GoalStarted(goal_text="A"))
        await bus.publish(GoalCompleted(goal_text="B"))
        history = bus.get_history()
        assert len(history) == 2

    @pytest.mark.asyncio
    async def test_event_history_filtered(self, bus):
        await bus.publish(GoalStarted(goal_text="A"))
        await bus.publish(ActionCompleted(tool_name="click"))
        history = bus.get_history("goal.*")
        assert len(history) == 1
        assert history[0].event_type == "goal.started"

    @pytest.mark.asyncio
    async def test_max_history_trimming(self, bus):
        for i in range(250):
            await bus.publish(Event(event_type="test.event"))
        history = bus.get_history()
        assert len(history) <= EventBus.MAX_HISTORY

    @pytest.mark.asyncio
    async def test_event_stats(self, bus):
        async def noop(event):
            pass

        bus.subscribe("goal.*", noop, "s1")
        await bus.publish(GoalStarted(goal_text="A"))
        await bus.publish(GoalStarted(goal_text="B"))
        stats = bus.get_stats()
        assert stats["total_events"] == 2
        assert stats["event_types"]["goal.started"] == 2
        assert stats["subscriptions"] == 1
        assert stats["active_subscriptions"] == 1
        assert stats["circuit_broken"] == 0
        assert stats["paused"] is False


class TestClear:
    @pytest.mark.asyncio
    async def test_clear(self, bus):
        async def noop(event):
            pass

        bus.subscribe("*", noop, "test")
        await bus.publish(GoalStarted(goal_text="A"))
        bus.clear()
        assert bus.get_history() == []
        assert bus.get_stats()["total_events"] == 0
        assert bus.get_stats()["subscriptions"] == 0
