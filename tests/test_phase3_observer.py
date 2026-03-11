"""Tests for Phase 3: Continuous Observation.

Tests DesktopObserver model updates, event publishing, idle detection,
ContextBuilder integration, and fault tolerance.
"""

import asyncio
import time
from collections import deque
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from marlow.kernel.desktop_observer import (
    DesktopObserver,
    DesktopState,
    WindowInfo,
)
from marlow.kernel.events import Event, WindowChanged, FocusLost


def _run(coro):
    return asyncio.run(coro)


# ══════════════════════════════════════════════════════════════
# 3.1: DesktopObserver model updates
# ══════════════════════════════════════════════════════════════


class TestDesktopObserverModel:
    """Verify the in-memory desktop model updates correctly."""

    def test_initial_state_empty(self):
        obs = DesktopObserver()
        state = obs.get_state()
        assert state.windows == {}
        assert state.focused_window is None
        assert state.focus_history == []
        assert state.connected is False
        assert state.user_idle is False

    def test_window_created_updates_model(self):
        obs = DesktopObserver()
        event = {"event": "WindowCreated", "window_id": 42, "title": "foot", "app_id": "foot"}
        _run(obs._dispatch_event(event))

        state = obs.get_state()
        assert 42 in state.windows
        assert state.windows[42].title == "foot"
        assert state.windows[42].app_id == "foot"
        assert state.last_change is not None

    def test_window_destroyed_removes_from_model(self):
        obs = DesktopObserver()
        _run(obs._dispatch_event({"event": "WindowCreated", "window_id": 1, "title": "A", "app_id": "a"}))
        _run(obs._dispatch_event({"event": "WindowCreated", "window_id": 2, "title": "B", "app_id": "b"}))
        assert len(obs.get_state().windows) == 2

        _run(obs._dispatch_event({"event": "WindowDestroyed", "window_id": 1}))
        state = obs.get_state()
        assert 1 not in state.windows
        assert 2 in state.windows

    def test_window_focused_updates_focus(self):
        obs = DesktopObserver()
        _run(obs._dispatch_event({"event": "WindowCreated", "window_id": 10, "title": "Terminal", "app_id": "foot"}))
        _run(obs._dispatch_event({"event": "WindowFocused", "window_id": 10, "title": "Terminal"}))

        state = obs.get_state()
        assert state.focused_window is not None
        assert state.focused_window.id == 10
        assert len(state.focus_history) == 1

    def test_focus_history_accumulates(self):
        obs = DesktopObserver()
        for i in range(5):
            _run(obs._dispatch_event({"event": "WindowCreated", "window_id": i, "title": f"W{i}", "app_id": f"a{i}"}))
            _run(obs._dispatch_event({"event": "WindowFocused", "window_id": i, "title": f"W{i}"}))

        state = obs.get_state()
        assert len(state.focus_history) == 5
        assert state.focused_window.id == 4

    def test_focus_history_bounded(self):
        obs = DesktopObserver()
        for i in range(30):
            _run(obs._dispatch_event({"event": "WindowFocused", "window_id": i, "title": f"W{i}"}))
        assert len(obs._focus_history) == 20  # maxlen

    def test_destroyed_focused_clears_focus(self):
        obs = DesktopObserver()
        _run(obs._dispatch_event({"event": "WindowCreated", "window_id": 5, "title": "X", "app_id": "x"}))
        _run(obs._dispatch_event({"event": "WindowFocused", "window_id": 5, "title": "X"}))
        assert obs.get_state().focused_window is not None

        _run(obs._dispatch_event({"event": "WindowDestroyed", "window_id": 5}))
        assert obs.get_state().focused_window is None

    def test_moved_to_shadow_updates_space(self):
        obs = DesktopObserver()
        _run(obs._dispatch_event({"event": "WindowCreated", "window_id": 7, "title": "FF", "app_id": "firefox"}))
        assert obs._windows[7].space == "user"

        _run(obs._dispatch_event({"event": "WindowMovedToShadow", "window_id": 7}))
        assert obs._windows[7].space == "shadow"

    def test_moved_to_user_updates_space(self):
        obs = DesktopObserver()
        _run(obs._dispatch_event({"event": "WindowCreated", "window_id": 8, "title": "App", "app_id": "app"}))
        _run(obs._dispatch_event({"event": "WindowMovedToShadow", "window_id": 8}))
        _run(obs._dispatch_event({"event": "WindowMovedToUser", "window_id": 8}))
        assert obs._windows[8].space == "user"

    def test_focused_unknown_window_adds_it(self):
        """Focusing a window we don't know about should add it to model."""
        obs = DesktopObserver()
        _run(obs._dispatch_event({"event": "WindowFocused", "window_id": 99, "title": "Mystery"}))
        assert 99 in obs._windows
        assert obs._windows[99].title == "Mystery"

    def test_focus_updates_title(self):
        """If compositor sends new title in focus event, update it."""
        obs = DesktopObserver()
        _run(obs._dispatch_event({"event": "WindowCreated", "window_id": 3, "title": "Old", "app_id": "a"}))
        _run(obs._dispatch_event({"event": "WindowFocused", "window_id": 3, "title": "New Title"}))
        assert obs._windows[3].title == "New Title"


# ══════════════════════════════════════════════════════════════
# 3.1b: EventBus publishing
# ══════════════════════════════════════════════════════════════


class TestDesktopObserverEvents:
    """Verify events are published to EventBus."""

    def _make_observer_with_bus(self):
        bus = AsyncMock()
        bus.publish = AsyncMock()
        obs = DesktopObserver(event_bus=bus)
        return obs, bus

    def test_window_created_publishes_event(self):
        obs, bus = self._make_observer_with_bus()
        _run(obs._dispatch_event({"event": "WindowCreated", "window_id": 1, "title": "T", "app_id": "a"}))
        bus.publish.assert_called()
        evt = bus.publish.call_args[0][0]
        assert evt.event_type == "world.window_changed"
        assert isinstance(evt, WindowChanged)
        assert evt.change_type == "appeared"

    def test_window_destroyed_publishes_event(self):
        obs, bus = self._make_observer_with_bus()
        _run(obs._dispatch_event({"event": "WindowCreated", "window_id": 1, "title": "T", "app_id": "a"}))
        bus.publish.reset_mock()
        _run(obs._dispatch_event({"event": "WindowDestroyed", "window_id": 1}))
        bus.publish.assert_called()
        evt = bus.publish.call_args[0][0]
        assert evt.change_type == "disappeared"

    def test_focus_publishes_focus_changed(self):
        obs, bus = self._make_observer_with_bus()
        _run(obs._dispatch_event({"event": "WindowFocused", "window_id": 1, "title": "T"}))
        bus.publish.assert_called()
        evt = bus.publish.call_args[0][0]
        assert evt.event_type == "world.focus_changed"

    def test_shadow_move_publishes_event(self):
        obs, bus = self._make_observer_with_bus()
        _run(obs._dispatch_event({"event": "WindowCreated", "window_id": 1, "title": "T", "app_id": "a"}))
        bus.publish.reset_mock()
        _run(obs._dispatch_event({"event": "WindowMovedToShadow", "window_id": 1}))
        bus.publish.assert_called()
        evt = bus.publish.call_args[0][0]
        assert evt.event_type == "world.window_moved_shadow"

    def test_conflict_publishes_focus_lost(self):
        obs, bus = self._make_observer_with_bus()
        obs._windows[5] = WindowInfo(id=5, title="Victim", app_id="v")
        _run(obs._dispatch_event({"event": "ConflictDetected", "window_id": 5, "reason": "user_click"}))
        bus.publish.assert_called()
        evt = bus.publish.call_args[0][0]
        assert isinstance(evt, FocusLost)

    def test_no_bus_no_crash(self):
        """Observer without EventBus should still work."""
        obs = DesktopObserver(event_bus=None)
        _run(obs._dispatch_event({"event": "WindowCreated", "window_id": 1, "title": "T", "app_id": "a"}))
        assert 1 in obs._windows


# ══════════════════════════════════════════════════════════════
# 3.1c: DesktopWeather + WindowTracker feeds
# ══════════════════════════════════════════════════════════════


class TestDesktopObserverFeeds:
    """Verify subsystem feeds."""

    def test_weather_fed_on_window_created(self):
        weather = MagicMock()
        obs = DesktopObserver(desktop_weather=weather)
        _run(obs._dispatch_event({"event": "WindowCreated", "window_id": 1, "title": "T", "app_id": "a"}))
        weather.record_window_change.assert_called()
        weather.update_window_count.assert_called_with(1)

    def test_weather_fed_on_window_destroyed(self):
        weather = MagicMock()
        obs = DesktopObserver(desktop_weather=weather)
        obs._windows[1] = WindowInfo(id=1, title="T", app_id="a")
        _run(obs._dispatch_event({"event": "WindowDestroyed", "window_id": 1}))
        weather.record_window_change.assert_called()
        weather.update_window_count.assert_called_with(0)

    def test_tracker_fed_on_window_events(self):
        tracker = MagicMock()
        obs = DesktopObserver(window_tracker=tracker)
        _run(obs._dispatch_event({"event": "WindowCreated", "window_id": 1, "title": "T", "app_id": "a"}))
        tracker.record_snapshot.assert_called()
        snap = tracker.record_snapshot.call_args[0][0]
        assert len(snap) == 1
        assert snap[0]["title"] == "T"
        assert snap[0]["hwnd"] == 1

    def test_tracker_marks_focused_window_active(self):
        tracker = MagicMock()
        obs = DesktopObserver(window_tracker=tracker)
        _run(obs._dispatch_event({"event": "WindowCreated", "window_id": 1, "title": "A", "app_id": "a"}))
        _run(obs._dispatch_event({"event": "WindowCreated", "window_id": 2, "title": "B", "app_id": "b"}))
        _run(obs._dispatch_event({"event": "WindowFocused", "window_id": 2, "title": "B"}))

        snap = tracker.record_snapshot.call_args[0][0]
        active = [w for w in snap if w["is_active"]]
        assert len(active) == 1
        assert active[0]["hwnd"] == 2


# ══════════════════════════════════════════════════════════════
# 3.2: Idle detection
# ══════════════════════════════════════════════════════════════


class TestIdleDetection:
    """Verify user idle/active detection."""

    def test_activity_on_focus_change(self):
        obs = DesktopObserver()
        _run(obs._dispatch_event({"event": "WindowFocused", "window_id": 1, "title": "T"}))
        assert obs._last_user_activity is not None
        assert not obs._user_idle

    def test_activity_on_window_created(self):
        obs = DesktopObserver()
        _run(obs._dispatch_event({"event": "WindowCreated", "window_id": 1, "title": "T", "app_id": "a"}))
        assert obs._last_user_activity is not None

    def test_idle_after_threshold(self):
        bus = AsyncMock()
        bus.publish = AsyncMock()
        obs = DesktopObserver(event_bus=bus, idle_minutes=0.001)  # ~0.06s
        obs._last_user_activity = time.time() - 1.0  # 1s ago
        _run(obs._check_idle())
        assert obs._user_idle is True
        # Should have published system.user_idle
        bus.publish.assert_called()
        evt = bus.publish.call_args[0][0]
        assert evt.event_type == "system.user_idle"

    def test_active_after_idle(self):
        bus = AsyncMock()
        bus.publish = AsyncMock()
        obs = DesktopObserver(event_bus=bus, idle_minutes=0.001)
        obs._last_user_activity = time.time() - 1.0
        obs._user_idle = True

        # Must run inside event loop for ensure_future to work
        async def _trigger():
            obs._record_user_activity()
            # Let the scheduled task run
            await asyncio.sleep(0.01)

        _run(_trigger())
        assert not obs._user_idle

    def test_no_double_idle(self):
        """Should not re-publish idle if already idle."""
        bus = AsyncMock()
        bus.publish = AsyncMock()
        obs = DesktopObserver(event_bus=bus, idle_minutes=0.001)
        obs._last_user_activity = time.time() - 1.0
        _run(obs._check_idle())
        call_count_1 = bus.publish.call_count

        _run(obs._check_idle())
        assert bus.publish.call_count == call_count_1  # no new publish

    def test_no_idle_when_no_activity_recorded(self):
        obs = DesktopObserver(idle_minutes=0.001)
        _run(obs._check_idle())
        assert not obs._user_idle


# ══════════════════════════════════════════════════════════════
# 3.3: ContextBuilder integration
# ══════════════════════════════════════════════════════════════


class TestContextBuilderObserver:
    """Verify ContextBuilder reads from DesktopObserver model."""

    def test_observer_windows_in_context(self):
        from marlow.kernel.context_builder import ContextBuilder

        obs = DesktopObserver()
        obs._windows = {
            1: WindowInfo(id=1, title="Terminal — foot", app_id="foot"),
            2: WindowInfo(id=2, title="Firefox — Google", app_id="firefox"),
        }
        obs._focused_window = obs._windows[1]

        cb = ContextBuilder(desktop_observer=obs)
        ctx = cb.build()
        assert "foot" in ctx or "Terminal" in ctx
        assert "Firefox" in ctx or "firefox" in ctx

    def test_observer_fallback_to_platform(self):
        """Without observer, should still try platform.windows."""
        from marlow.kernel.context_builder import ContextBuilder

        class FakePlatform:
            class windows:
                @staticmethod
                def list_windows(include_minimized=False):
                    return []

        cb = ContextBuilder(platform=FakePlatform())
        ctx = cb.build()
        assert "no windows" in ctx.lower() or ctx  # should not crash

    def test_no_observer_no_platform_no_crash(self):
        from marlow.kernel.context_builder import ContextBuilder

        cb = ContextBuilder()
        ctx = cb.build()
        assert isinstance(ctx, str)


# ══════════════════════════════════════════════════════════════
# 3.4: Fault tolerance
# ══════════════════════════════════════════════════════════════


class TestFaultTolerance:
    """Observer resilience to errors."""

    def test_unknown_event_ignored(self):
        obs = DesktopObserver()
        _run(obs._dispatch_event({"event": "SomeFutureEvent", "data": 123}))
        # Should not crash, model unchanged
        assert obs.get_state().windows == {}

    def test_missing_fields_handled(self):
        obs = DesktopObserver()
        _run(obs._dispatch_event({"event": "WindowCreated"}))
        # Should create with defaults, not crash
        assert 0 in obs._windows
        assert obs._windows[0].title == ""

    def test_weather_crash_does_not_break_observer(self):
        weather = MagicMock()
        weather.record_window_change.side_effect = RuntimeError("boom")
        obs = DesktopObserver(desktop_weather=weather)
        # Should not raise
        _run(obs._dispatch_event({"event": "WindowCreated", "window_id": 1, "title": "T", "app_id": "a"}))
        assert 1 in obs._windows

    def test_tracker_crash_does_not_break_observer(self):
        tracker = MagicMock()
        tracker.record_snapshot.side_effect = RuntimeError("boom")
        obs = DesktopObserver(window_tracker=tracker)
        _run(obs._dispatch_event({"event": "WindowCreated", "window_id": 1, "title": "T", "app_id": "a"}))
        assert 1 in obs._windows

    def test_eventbus_crash_does_not_break_observer(self):
        bus = AsyncMock()
        bus.publish = AsyncMock(side_effect=RuntimeError("bus fire"))
        obs = DesktopObserver(event_bus=bus)
        _run(obs._dispatch_event({"event": "WindowCreated", "window_id": 1, "title": "T", "app_id": "a"}))
        assert 1 in obs._windows

    def test_stop_sets_stopping(self):
        obs = DesktopObserver()
        obs.stop()
        assert obs._stopping is True

    def test_get_state_always_returns_dataclass(self):
        obs = DesktopObserver()
        state = obs.get_state()
        assert isinstance(state, DesktopState)
