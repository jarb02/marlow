"""Tests for marlow.kernel.blackboard — Centralized Key-Value Store."""

import asyncio
import time

import pytest
from marlow.kernel.blackboard import Blackboard, BlackboardEntry


# ------------------------------------------------------------------
# Basic set / get / has / delete
# ------------------------------------------------------------------

class TestBasicOperations:
    def setup_method(self):
        self.bb = Blackboard()

    def test_set_and_get(self):
        self.bb.set("goal.current", "Open Notepad")
        assert self.bb.get("goal.current") == "Open Notepad"

    def test_get_default_missing(self):
        assert self.bb.get("nonexistent") is None
        assert self.bb.get("nonexistent", "fallback") == "fallback"

    def test_get_entry_with_metadata(self):
        self.bb.set("world.app", "Notepad", source="tracker")
        entry = self.bb.get_entry("world.app")
        assert isinstance(entry, BlackboardEntry)
        assert entry.value == "Notepad"
        assert entry.source == "tracker"
        assert entry.timestamp > 0

    def test_has_key_exists(self):
        self.bb.set("x", 1)
        assert self.bb.has("x") is True

    def test_has_key_missing(self):
        assert self.bb.has("nope") is False

    def test_delete_existing(self):
        self.bb.set("x", 1)
        assert self.bb.delete("x") is True
        assert self.bb.has("x") is False

    def test_delete_missing(self):
        assert self.bb.delete("nope") is False

    def test_set_returns_old_value(self):
        self.bb.set("x", "old")
        old = self.bb.set("x", "new")
        assert old == "old"
        assert self.bb.get("x") == "new"

    def test_set_returns_none_first_time(self):
        old = self.bb.set("x", "first")
        assert old is None


# ------------------------------------------------------------------
# TTL
# ------------------------------------------------------------------

class TestTTL:
    def test_ttl_not_expired(self):
        bb = Blackboard()
        bb.set("temp", "value", ttl=60)
        assert bb.get("temp") == "value"
        assert bb.has("temp") is True

    def test_ttl_expired(self):
        bb = Blackboard()
        bb.set("temp", "value", ttl=0.01)
        time.sleep(0.03)
        assert bb.get("temp") is None
        assert bb.has("temp") is False

    def test_ttl_zero_means_forever(self):
        entry = BlackboardEntry(key="k", value="v", timestamp=0.0, ttl=0.0)
        assert entry.is_expired is False


# ------------------------------------------------------------------
# Namespace
# ------------------------------------------------------------------

class TestNamespace:
    def setup_method(self):
        self.bb = Blackboard()
        self.bb.set("world.app", "Notepad")
        self.bb.set("world.window_count", 5)
        self.bb.set("goal.current", "Open Notepad")

    def test_get_namespace(self):
        world = self.bb.get_namespace("world")
        assert world == {"app": "Notepad", "window_count": 5}

    def test_get_namespace_empty(self):
        assert self.bb.get_namespace("config") == {}

    def test_clear_namespace(self):
        self.bb.clear("world")
        assert self.bb.get("world.app") is None
        assert self.bb.get("goal.current") == "Open Notepad"


# ------------------------------------------------------------------
# Snapshot / restore
# ------------------------------------------------------------------

class TestSnapshotRestore:
    def test_snapshot(self):
        bb = Blackboard()
        bb.set("a", 1)
        bb.set("b", 2)
        snap = bb.snapshot()
        assert snap == {"a": 1, "b": 2}

    def test_restore(self):
        bb = Blackboard()
        bb.restore({"x": 10, "y": 20}, source="test")
        assert bb.get("x") == 10
        assert bb.get("y") == 20
        entry = bb.get_entry("x")
        assert entry.source == "test"


# ------------------------------------------------------------------
# clear / size / keys
# ------------------------------------------------------------------

class TestUtilities:
    def test_clear_all(self):
        bb = Blackboard()
        bb.set("a", 1)
        bb.set("b", 2)
        bb.clear()
        assert bb.size == 0

    def test_size(self):
        bb = Blackboard()
        bb.set("a", 1)
        bb.set("b", 2)
        assert bb.size == 2

    def test_keys(self):
        bb = Blackboard()
        bb.set("x", 1)
        bb.set("y", 2)
        assert sorted(bb.keys()) == ["x", "y"]


# ------------------------------------------------------------------
# format_for_planner
# ------------------------------------------------------------------

class TestFormatForPlanner:
    def test_format_for_planner(self):
        bb = Blackboard()
        bb.set("goal.current", "Open Notepad")
        bb.set("world.app", "Desktop")
        output = bb.format_for_planner()
        assert "[goal]" in output
        assert "current: Open Notepad" in output
        assert "[world]" in output

    def test_format_for_planner_empty(self):
        bb = Blackboard()
        assert bb.format_for_planner() == "Blackboard empty."


# ------------------------------------------------------------------
# Pattern matching
# ------------------------------------------------------------------

class TestPatternMatches:
    def test_pattern_matches_exact(self):
        assert Blackboard._pattern_matches("goal.current", "goal.current") is True
        assert Blackboard._pattern_matches("goal.current", "goal.other") is False

    def test_pattern_matches_namespace(self):
        assert Blackboard._pattern_matches("world.", "world.app") is True
        assert Blackboard._pattern_matches("world.", "goal.current") is False

    def test_pattern_matches_wildcard(self):
        assert Blackboard._pattern_matches("*", "anything.here") is True
        assert Blackboard._pattern_matches("*", "x") is True


# ------------------------------------------------------------------
# Async listeners
# ------------------------------------------------------------------

class TestAsyncListeners:
    def test_async_set_notifies_listener(self):
        bb = Blackboard()
        notifications = []

        async def on_change(key, old, new):
            notifications.append((key, old, new))

        bb.on_change("goal.current", on_change)

        async def run():
            await bb.set_async("goal.current", "Open Notepad", source="test")
            await bb.set_async("goal.current", "Close Notepad", source="test")

        asyncio.run(run())
        assert len(notifications) == 2
        assert notifications[0] == ("goal.current", None, "Open Notepad")
        assert notifications[1] == ("goal.current", "Open Notepad", "Close Notepad")

    def test_async_wildcard_listener(self):
        bb = Blackboard()
        notifications = []

        async def on_any(key, old, new):
            notifications.append(key)

        bb.on_change("*", on_any)

        async def run():
            await bb.set_async("a", 1)
            await bb.set_async("b", 2)

        asyncio.run(run())
        assert notifications == ["a", "b"]


# ------------------------------------------------------------------
# History
# ------------------------------------------------------------------

class TestHistory:
    def test_max_history(self):
        bb = Blackboard()
        for i in range(250):
            bb.set(f"key_{i}", i)
        assert len(bb._history) <= 200
