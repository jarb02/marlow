"""Tests for EventBus integration in AutonomousMarlow."""

import pytest
from marlow.kernel.integration import AutonomousMarlow
from marlow.kernel.event_bus import EventBus
from marlow.kernel.events import Event


class TestEventBusIntegration:
    def test_event_bus_accessible(self):
        """AutonomousMarlow exposes an EventBus via property."""
        m = AutonomousMarlow()
        assert m.event_bus is not None
        assert isinstance(m.event_bus, EventBus)

    @pytest.mark.asyncio
    async def test_publish_does_not_crash_without_subscribers(self):
        """Publishing with no subscribers is a no-op."""
        bus = EventBus()
        await bus.publish(Event(event_type="test.event"))
        # No crash — pass

    @pytest.mark.asyncio
    async def test_publish_error_does_not_block_execution(self):
        """A failing handler doesn't prevent other handlers from running."""
        bus = EventBus()
        received = []

        async def bad_handler(event):
            raise RuntimeError("boom")

        async def good_handler(event):
            received.append(event)

        bus.subscribe("test.*", bad_handler, "bad")
        bus.subscribe("test.*", good_handler, "good")
        await bus.publish(Event(event_type="test.event"))
        # good_handler still called despite bad_handler raising
        assert len(received) == 1
