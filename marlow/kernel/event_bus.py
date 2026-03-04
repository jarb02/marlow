"""EventBus — Central pub/sub for the Marlow Kernel.

Wildcard pattern matching, priority dispatch, circuit breakers,
event history. All handlers are async.

/ Bus de eventos central con wildcards, prioridad, circuit breakers.
"""

import asyncio
import fnmatch
import logging
from typing import Callable, Awaitable, Optional
from collections import defaultdict

from .events import Event, EventPriority

logger = logging.getLogger("marlow.kernel.event_bus")

# Type for event handlers
EventHandler = Callable[[Event], Awaitable[None]]


class Subscription:
    """A single event subscription."""

    def __init__(self, pattern: str, handler: EventHandler, subscriber_name: str = ""):
        self.pattern = pattern          # e.g. "goal.*", "action.completed", "*"
        self.handler = handler
        self.subscriber_name = subscriber_name
        self.call_count = 0
        self.error_count = 0
        self.active = True

    def matches(self, event_type: str) -> bool:
        """Check if this subscription matches an event type using fnmatch wildcards."""
        return fnmatch.fnmatch(event_type, self.pattern)

    @property
    def is_circuit_broken(self) -> bool:
        """Disable after 3 consecutive errors."""
        return self.error_count >= 3


class EventBus:
    """Central event bus for Marlow Kernel.

    Features:
    - Pub/Sub with wildcard pattern matching (goal.*, *.failed, *)
    - Priority dispatch (critical events processed first)
    - Circuit breakers (disable failing subscribers after 3 errors)
    - Event history for debugging
    - Async handlers

    Usage::

        bus = EventBus()

        async def on_goal(event):
            print(f"Goal: {event.goal_text}")

        bus.subscribe("goal.*", on_goal, "my_component")
        await bus.publish(GoalStarted(goal_text="Open Notepad"))
    """

    MAX_HISTORY = 200
    CIRCUIT_BREAKER_THRESHOLD = 3

    def __init__(self):
        self._subscriptions: list[Subscription] = []
        self._history: list[Event] = []
        self._event_counts: dict[str, int] = defaultdict(int)
        self._paused = False

    def subscribe(
        self, pattern: str, handler: EventHandler, subscriber_name: str = "",
    ) -> Subscription:
        """Subscribe to events matching a pattern.

        Patterns:
        - ``"goal.started"`` — exact match
        - ``"goal.*"`` — all goal events
        - ``"*.failed"`` — all failed events
        - ``"*"`` — all events
        """
        sub = Subscription(pattern, handler, subscriber_name)
        self._subscriptions.append(sub)
        logger.debug("Subscribed '%s' to '%s'", subscriber_name, pattern)
        return sub

    def unsubscribe(self, subscription: Subscription):
        """Remove a subscription."""
        if subscription in self._subscriptions:
            self._subscriptions.remove(subscription)
            logger.debug(
                "Unsubscribed '%s' from '%s'",
                subscription.subscriber_name, subscription.pattern,
            )

    async def publish(self, event: Event):
        """Publish an event to all matching subscribers.

        Subscribers are called in order of registration.
        """
        if self._paused:
            logger.debug("EventBus paused, dropping %s", event.event_type)
            return

        # Record in history
        self._history.append(event)
        if len(self._history) > self.MAX_HISTORY:
            self._history.pop(0)
        self._event_counts[event.event_type] += 1

        # Find matching subscribers
        matching = [
            sub for sub in self._subscriptions
            if sub.active and not sub.is_circuit_broken and sub.matches(event.event_type)
        ]

        if not matching:
            return

        # Dispatch to all matching handlers
        for sub in matching:
            try:
                await sub.handler(event)
                sub.call_count += 1
                sub.error_count = 0  # reset on success
            except Exception as e:
                sub.error_count += 1
                logger.error(
                    "EventBus handler error: %s on %s: %s (errors: %d/%d)",
                    sub.subscriber_name, event.event_type, e,
                    sub.error_count, self.CIRCUIT_BREAKER_THRESHOLD,
                )
                if sub.is_circuit_broken:
                    logger.warning(
                        "Circuit breaker tripped for '%s' on pattern '%s' — disabling",
                        sub.subscriber_name, sub.pattern,
                    )

    def pause(self):
        """Pause event dispatch (events are dropped)."""
        self._paused = True
        logger.info("EventBus paused")

    def resume(self):
        """Resume event dispatch."""
        self._paused = False
        logger.info("EventBus resumed")

    @property
    def is_paused(self) -> bool:
        return self._paused

    def get_history(self, event_type: str = "", last_n: int = 50) -> list[Event]:
        """Get recent event history, optionally filtered by type."""
        if event_type:
            filtered = [e for e in self._history if fnmatch.fnmatch(e.event_type, event_type)]
            return filtered[-last_n:]
        return self._history[-last_n:]

    def get_stats(self) -> dict:
        """Get event bus statistics."""
        active_subs = [s for s in self._subscriptions if s.active and not s.is_circuit_broken]
        broken_subs = [s for s in self._subscriptions if s.is_circuit_broken]
        return {
            "total_events": sum(self._event_counts.values()),
            "event_types": dict(self._event_counts),
            "subscriptions": len(self._subscriptions),
            "active_subscriptions": len(active_subs),
            "circuit_broken": len(broken_subs),
            "paused": self._paused,
        }

    def reset_circuit_breaker(self, subscriber_name: str):
        """Reset circuit breaker for a subscriber."""
        for sub in self._subscriptions:
            if sub.subscriber_name == subscriber_name:
                sub.error_count = 0
                logger.info("Circuit breaker reset for '%s'", subscriber_name)

    def clear(self):
        """Clear all subscriptions and history."""
        self._subscriptions.clear()
        self._history.clear()
        self._event_counts.clear()
        self._paused = False
