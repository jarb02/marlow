"""Tests for marlow.kernel.db — DatabaseManager, repositories, maintenance, uia_writer."""

import asyncio
import json
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from marlow.kernel.db.manager import DatabaseManager
from marlow.kernel.db.repositories import (
    ActionLog,
    AppKnowledge,
    ErrorPattern,
    LogRepository,
    KnowledgeRepository,
    Memory,
    MemoryRepository,
    StateRepository,
    SystemStateEntry,
)
from marlow.kernel.db.maintenance import DatabaseMaintenance
from marlow.kernel.db.uia_writer import UIAEvent, UIAEventWriter


# ── Helpers ──


def _run(coro):
    """Run an async coroutine."""
    return asyncio.run(coro)


async def _make_db(tmp_path) -> DatabaseManager:
    """Create and initialize a DatabaseManager in a temp dir."""
    db = DatabaseManager(data_dir=tmp_path)
    await db.initialize()
    return db


# ── DatabaseManager ──


class TestDatabaseManager:
    """Tests for DatabaseManager lifecycle and configuration."""

    def test_initialize_creates_both_dbs(self, tmp_path):
        """initialize() should create state.db and logs.db."""
        async def run():
            db = await _make_db(tmp_path)
            try:
                assert (tmp_path / "state.db").exists()
                assert (tmp_path / "logs.db").exists()
            finally:
                await db.close()
        _run(run())

    def test_pragmas_applied(self, tmp_path):
        """WAL journal mode should be active after init."""
        async def run():
            db = await _make_db(tmp_path)
            try:
                cursor = await db.state.execute("PRAGMA journal_mode")
                row = await cursor.fetchone()
                assert row[0] == "wal"

                cursor = await db.logs.execute("PRAGMA journal_mode")
                row = await cursor.fetchone()
                assert row[0] == "wal"

                cursor = await db.state.execute("PRAGMA foreign_keys")
                row = await cursor.fetchone()
                assert row[0] >= 1  # schema version may increment
            finally:
                await db.close()
        _run(run())

    def test_close_works(self, tmp_path):
        """close() should release connections without error."""
        async def run():
            db = await _make_db(tmp_path)
            await db.close()
            assert db.is_initialized is False
        _run(run())

    def test_double_initialize_idempotent(self, tmp_path):
        """Calling initialize() twice should not raise."""
        async def run():
            db = await _make_db(tmp_path)
            try:
                await db.initialize()  # second call
                assert db.is_initialized is True
            finally:
                await db.close()
        _run(run())

    def test_schema_version_recorded(self, tmp_path):
        """Schema version should be inserted after init."""
        async def run():
            db = await _make_db(tmp_path)
            try:
                cursor = await db.state.execute(
                    "SELECT MAX(version) FROM schema_version"
                )
                row = await cursor.fetchone()
                assert row[0] >= 1  # schema version may increment
            finally:
                await db.close()
        _run(run())

    def test_state_property_before_init_raises(self, tmp_path):
        """Accessing .state before initialize() should raise."""
        db = DatabaseManager(data_dir=tmp_path)
        with pytest.raises(RuntimeError, match="not initialized"):
            _ = db.state

    def test_get_sync_logs_connection(self, tmp_path):
        """get_sync_logs_connection should return a working sqlite3 conn."""
        async def run():
            db = await _make_db(tmp_path)
            try:
                conn = db.get_sync_logs_connection()
                cursor = conn.execute(
                    "SELECT count(*) FROM sqlite_master WHERE type='table'"
                )
                assert cursor.fetchone()[0] > 0
                conn.close()
            finally:
                await db.close()
        _run(run())


# ── StateRepository ──


class TestStateRepository:
    """Tests for key-value state operations."""

    def test_set_get_string(self, tmp_path):
        async def run():
            db = await _make_db(tmp_path)
            try:
                repo = StateRepository(db.state)
                await repo.set("name", "Marlow")
                assert await repo.get("name") == "Marlow"
            finally:
                await db.close()
        _run(run())

    def test_set_get_int(self, tmp_path):
        async def run():
            db = await _make_db(tmp_path)
            try:
                repo = StateRepository(db.state)
                await repo.set("count", 42)
                assert await repo.get_typed("count") == 42
            finally:
                await db.close()
        _run(run())

    def test_set_get_float(self, tmp_path):
        async def run():
            db = await _make_db(tmp_path)
            try:
                repo = StateRepository(db.state)
                await repo.set("score", 0.95)
                val = await repo.get_typed("score")
                assert abs(val - 0.95) < 0.001
            finally:
                await db.close()
        _run(run())

    def test_set_get_bool(self, tmp_path):
        async def run():
            db = await _make_db(tmp_path)
            try:
                repo = StateRepository(db.state)
                await repo.set("enabled", True)
                assert await repo.get_typed("enabled") is True

                await repo.set("disabled", False)
                assert await repo.get_typed("disabled") is False
            finally:
                await db.close()
        _run(run())

    def test_set_get_json(self, tmp_path):
        async def run():
            db = await _make_db(tmp_path)
            try:
                repo = StateRepository(db.state)
                data = {"tools": ["click", "type"], "version": 2}
                await repo.set("config", data)
                result = await repo.get_typed("config")
                assert result == data
            finally:
                await db.close()
        _run(run())

    def test_get_nonexistent_returns_none(self, tmp_path):
        async def run():
            db = await _make_db(tmp_path)
            try:
                repo = StateRepository(db.state)
                assert await repo.get("nope") is None
                assert await repo.get_typed("nope") is None
            finally:
                await db.close()
        _run(run())

    def test_get_category(self, tmp_path):
        async def run():
            db = await _make_db(tmp_path)
            try:
                repo = StateRepository(db.state)
                await repo.set("a", "1", category="test")
                await repo.set("b", "2", category="test")
                await repo.set("c", "3", category="other")

                cat = await repo.get_category("test")
                assert len(cat) == 2
                assert cat["a"] == "1"
                assert cat["b"] == "2"
            finally:
                await db.close()
        _run(run())

    def test_save_bulk_atomic(self, tmp_path):
        async def run():
            db = await _make_db(tmp_path)
            try:
                repo = StateRepository(db.state)
                entries = [
                    SystemStateEntry("x", 10),
                    SystemStateEntry("y", 20),
                    SystemStateEntry("z", 30),
                ]
                await repo.save_bulk(entries, category="bulk")

                assert await repo.get_typed("x") == 10
                assert await repo.get_typed("y") == 20
                assert await repo.get_typed("z") == 30
            finally:
                await db.close()
        _run(run())

    def test_upsert_overwrites(self, tmp_path):
        async def run():
            db = await _make_db(tmp_path)
            try:
                repo = StateRepository(db.state)
                await repo.set("key", "old")
                await repo.set("key", "new")
                assert await repo.get("key") == "new"
            finally:
                await db.close()
        _run(run())


# ── MemoryRepository ──


class TestMemoryRepository:
    """Tests for tiered memory operations."""

    def test_store_and_recall(self, tmp_path):
        async def run():
            db = await _make_db(tmp_path)
            try:
                repo = MemoryRepository(db.state)
                mem = Memory.new("mid", "general", {"fact": "test"}, relevance=0.9)
                await repo.store(mem)

                results = await repo.recall("mid")
                assert len(results) == 1
                assert results[0].content == {"fact": "test"}
            finally:
                await db.close()
        _run(run())

    def test_recall_filters_by_tier(self, tmp_path):
        async def run():
            db = await _make_db(tmp_path)
            try:
                repo = MemoryRepository(db.state)
                await repo.store(Memory.new("mid", "general", {"a": 1}))
                await repo.store(Memory.new("long", "general", {"b": 2}))

                mid = await repo.recall("mid")
                assert len(mid) == 1
                assert mid[0].content == {"a": 1}

                long = await repo.recall("long")
                assert len(long) == 1
                assert long[0].content == {"b": 2}
            finally:
                await db.close()
        _run(run())

    def test_recall_filters_by_category(self, tmp_path):
        async def run():
            db = await _make_db(tmp_path)
            try:
                repo = MemoryRepository(db.state)
                await repo.store(Memory.new("mid", "apps", {"a": 1}))
                await repo.store(Memory.new("mid", "user", {"b": 2}))

                results = await repo.recall("mid", category="apps")
                assert len(results) == 1
                assert results[0].content == {"a": 1}
            finally:
                await db.close()
        _run(run())

    def test_recall_orders_by_relevance(self, tmp_path):
        async def run():
            db = await _make_db(tmp_path)
            try:
                repo = MemoryRepository(db.state)
                await repo.store(Memory.new("mid", "g", {"low": 1}, relevance=0.3))
                await repo.store(Memory.new("mid", "g", {"high": 2}, relevance=0.9))
                await repo.store(Memory.new("mid", "g", {"med": 3}, relevance=0.6))

                results = await repo.recall("mid")
                relevances = [r.relevance for r in results]
                assert relevances == sorted(relevances, reverse=True)
            finally:
                await db.close()
        _run(run())

    def test_recall_bumps_access_count(self, tmp_path):
        async def run():
            db = await _make_db(tmp_path)
            try:
                repo = MemoryRepository(db.state)
                mem = Memory.new("mid", "g", {"x": 1})
                await repo.store(mem)

                await repo.recall("mid")  # access 1
                await repo.recall("mid")  # access 2

                # Check raw DB value
                cursor = await db.state.execute(
                    "SELECT access_count FROM memory WHERE id = ?", (mem.id,)
                )
                row = await cursor.fetchone()
                assert row[0] == 2
            finally:
                await db.close()
        _run(run())

    def test_cleanup_expired(self, tmp_path):
        async def run():
            db = await _make_db(tmp_path)
            try:
                repo = MemoryRepository(db.state)
                # Expired
                past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
                    "%Y-%m-%dT%H:%M:%S.%fZ"
                )
                await repo.store(Memory.new(
                    "mid", "g", {"old": 1}, expires_at=past
                ))
                # Not expired
                future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime(
                    "%Y-%m-%dT%H:%M:%S.%fZ"
                )
                await repo.store(Memory.new(
                    "mid", "g", {"new": 1}, expires_at=future
                ))
                # No expiry
                await repo.store(Memory.new("mid", "g", {"perm": 1}))

                deleted = await repo.cleanup_expired()
                assert deleted == 1

                remaining = await repo.recall("mid", limit=100)
                assert len(remaining) == 2
            finally:
                await db.close()
        _run(run())

    def test_decay_relevance_mid_only(self, tmp_path):
        async def run():
            db = await _make_db(tmp_path)
            try:
                repo = MemoryRepository(db.state)
                mid = Memory.new("mid", "g", {"a": 1}, relevance=1.0)
                long = Memory.new("long", "g", {"b": 1}, relevance=1.0)
                await repo.store(mid)
                await repo.store(long)

                await repo.decay_relevance(0.5)

                cursor = await db.state.execute(
                    "SELECT relevance FROM memory WHERE id = ?", (mid.id,)
                )
                mid_rel = (await cursor.fetchone())[0]
                assert abs(mid_rel - 0.5) < 0.001

                cursor = await db.state.execute(
                    "SELECT relevance FROM memory WHERE id = ?", (long.id,)
                )
                long_rel = (await cursor.fetchone())[0]
                assert abs(long_rel - 1.0) < 0.001
            finally:
                await db.close()
        _run(run())


# ── KnowledgeRepository ──


class TestKnowledgeRepository:
    """Tests for app knowledge and error patterns."""

    def test_upsert_and_get_app(self, tmp_path):
        async def run():
            db = await _make_db(tmp_path)
            try:
                repo = KnowledgeRepository(db.state)
                app = AppKnowledge(
                    app_name="notepad.exe",
                    display_name="Notepad",
                    framework="win32",
                )
                await repo.upsert_app(app)

                result = await repo.get_app("notepad.exe")
                assert result is not None
                assert result.display_name == "Notepad"
                assert result.framework == "win32"
                assert result.reliability == 0.5
            finally:
                await db.close()
        _run(run())

    def test_get_app_nonexistent(self, tmp_path):
        async def run():
            db = await _make_db(tmp_path)
            try:
                repo = KnowledgeRepository(db.state)
                assert await repo.get_app("nope.exe") is None
            finally:
                await db.close()
        _run(run())

    def test_record_action_result(self, tmp_path):
        async def run():
            db = await _make_db(tmp_path)
            try:
                repo = KnowledgeRepository(db.state)
                await repo.upsert_app(AppKnowledge(app_name="test.exe"))

                await repo.record_action_result("test.exe", success=True)
                await repo.record_action_result("test.exe", success=True)
                await repo.record_action_result("test.exe", success=False)

                app = await repo.get_app("test.exe")
                assert app.total_actions == 3
                assert app.success_actions == 2
            finally:
                await db.close()
        _run(run())

    def test_record_error_and_get_solution(self, tmp_path):
        async def run():
            db = await _make_db(tmp_path)
            try:
                repo = KnowledgeRepository(db.state)

                # Record a failed solution
                await repo.record_error(ErrorPattern(
                    app_name="app.exe",
                    tool_name="click",
                    error_type="ElementNotFound",
                    solution="use_ocr",
                    solution_worked=False,
                ))

                # Record a working solution
                await repo.record_error(ErrorPattern(
                    app_name="app.exe",
                    tool_name="click",
                    error_type="ElementNotFound",
                    solution="use_coordinates",
                    solution_worked=True,
                ))

                solution = await repo.get_error_solution(
                    "app.exe", "click", "ElementNotFound"
                )
                assert solution == "use_coordinates"
            finally:
                await db.close()
        _run(run())

    def test_get_error_solution_none(self, tmp_path):
        async def run():
            db = await _make_db(tmp_path)
            try:
                repo = KnowledgeRepository(db.state)
                result = await repo.get_error_solution("x", "y", "z")
                assert result is None
            finally:
                await db.close()
        _run(run())


# ── LogRepository ──


class TestLogRepository:
    """Tests for action log operations."""

    def test_log_and_get_recent(self, tmp_path):
        async def run():
            db = await _make_db(tmp_path)
            try:
                repo = LogRepository(db.logs)
                row_id = await repo.log_action(ActionLog(
                    tool_name="click",
                    action_type="ui_interaction",
                    app_name="notepad.exe",
                    success=True,
                    duration_ms=42,
                ))
                assert row_id > 0

                recent = await repo.get_recent(10)
                assert len(recent) == 1
                assert recent[0]["tool_name"] == "click"
                assert recent[0]["success"] is True
            finally:
                await db.close()
        _run(run())

    def test_get_by_goal(self, tmp_path):
        async def run():
            db = await _make_db(tmp_path)
            try:
                repo = LogRepository(db.logs)

                await repo.log_action(ActionLog(
                    tool_name="click", action_type="ui",
                    goal_id="goal-1",
                ))
                await repo.log_action(ActionLog(
                    tool_name="type_text", action_type="ui",
                    goal_id="goal-1",
                ))
                await repo.log_action(ActionLog(
                    tool_name="screenshot", action_type="read",
                    goal_id="goal-2",
                ))

                goal1_logs = await repo.get_by_goal("goal-1")
                assert len(goal1_logs) == 2
                assert all(l["tool_name"] in ("click", "type_text") for l in goal1_logs)
            finally:
                await db.close()
        _run(run())

    def test_get_tool_stats(self, tmp_path):
        async def run():
            db = await _make_db(tmp_path)
            try:
                repo = LogRepository(db.logs)

                for i in range(5):
                    await repo.log_action(ActionLog(
                        tool_name="click",
                        action_type="ui",
                        success=(i < 3),  # 3 success, 2 fail
                        score=0.8 if i < 3 else 0.2,
                        duration_ms=100 + i * 10,
                    ))

                stats = await repo.get_tool_stats("click", hours=1)
                assert stats["total"] == 5
                assert stats["successes"] == 3
                assert stats["failures"] == 2
                assert stats["success_rate"] == pytest.approx(0.6)
                assert stats["avg_score"] is not None
            finally:
                await db.close()
        _run(run())

    def test_get_tool_stats_empty(self, tmp_path):
        async def run():
            db = await _make_db(tmp_path)
            try:
                repo = LogRepository(db.logs)
                stats = await repo.get_tool_stats("nonexistent")
                assert stats["total"] == 0
                assert stats["success_rate"] == 0.0
            finally:
                await db.close()
        _run(run())


# ── UIAEventWriter ──


class TestUIAEventWriter:
    """Tests for the buffered sync UIA event writer."""

    def test_push_and_flush(self, tmp_path):
        """Events should be flushed to the database."""
        # Create logs.db with schema first
        async def setup():
            db = await _make_db(tmp_path)
            await db.close()

        _run(setup())

        writer = UIAEventWriter(
            db_path=tmp_path / "logs.db",
            flush_interval=0.1,
            max_buffer=100,
        )
        writer.start()

        try:
            writer.push(UIAEvent(
                event_type="window_opened",
                window_title="Notepad",
                process_name="notepad.exe",
            ))
            writer.push(UIAEvent(
                event_type="focus_changed",
                element_name="Edit",
            ))

            # Wait for flush
            time.sleep(0.5)
        finally:
            writer.stop()

        # Verify in DB
        conn = sqlite3.connect(str(tmp_path / "logs.db"))
        cursor = conn.execute("SELECT count(*) FROM uia_events")
        count = cursor.fetchone()[0]
        conn.close()
        assert count == 2

    def test_buffer_overflow_triggers_flush(self, tmp_path):
        """Exceeding max_buffer should trigger immediate flush."""
        async def setup():
            db = await _make_db(tmp_path)
            await db.close()

        _run(setup())

        writer = UIAEventWriter(
            db_path=tmp_path / "logs.db",
            flush_interval=60,  # long interval
            max_buffer=5,
        )
        writer.start()

        try:
            for i in range(10):
                writer.push(UIAEvent(
                    event_type="focus_changed",
                    element_name="item_{}".format(i),
                ))
            time.sleep(0.3)
        finally:
            writer.stop()

        conn = sqlite3.connect(str(tmp_path / "logs.db"))
        cursor = conn.execute("SELECT count(*) FROM uia_events")
        count = cursor.fetchone()[0]
        conn.close()
        assert count == 10

    def test_stop_flushes_remaining(self, tmp_path):
        """stop() should flush any remaining buffered events."""
        async def setup():
            db = await _make_db(tmp_path)
            await db.close()

        _run(setup())

        writer = UIAEventWriter(
            db_path=tmp_path / "logs.db",
            flush_interval=60,  # won't auto-flush
            max_buffer=1000,
        )
        writer.start()

        writer.push(UIAEvent(event_type="test", window_title="x"))
        writer.stop()  # should flush the one event

        conn = sqlite3.connect(str(tmp_path / "logs.db"))
        cursor = conn.execute("SELECT count(*) FROM uia_events")
        count = cursor.fetchone()[0]
        conn.close()
        assert count == 1


# ── Maintenance ──


class TestMaintenance:
    """Tests for periodic cleanup tasks."""

    def test_cleanup_old_logs(self, tmp_path):
        """Old logs should be deleted, recent ones kept."""
        async def run():
            db = await _make_db(tmp_path)
            try:
                # Insert an old log (45 days ago)
                old_ts = (
                    datetime.now(timezone.utc) - timedelta(days=45)
                ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                await db.logs.execute(
                    """INSERT INTO action_logs
                           (tool_name, action_type, timestamp)
                       VALUES (?, ?, ?)""",
                    ("click", "ui", old_ts),
                )

                # Insert a recent log
                await db.logs.execute(
                    """INSERT INTO action_logs
                           (tool_name, action_type)
                       VALUES (?, ?)""",
                    ("click", "ui"),
                )
                await db.logs.commit()

                maint = DatabaseMaintenance(db)
                results = await maint.run_cycle()

                # Old log should be deleted
                cursor = await db.logs.execute(
                    "SELECT count(*) FROM action_logs"
                )
                count = (await cursor.fetchone())[0]
                assert count == 1
                assert results["old_logs"] == 1
            finally:
                await db.close()
        _run(run())

    def test_cleanup_keeps_recent(self, tmp_path):
        """Recent logs should not be deleted."""
        async def run():
            db = await _make_db(tmp_path)
            try:
                for i in range(5):
                    await db.logs.execute(
                        """INSERT INTO action_logs
                               (tool_name, action_type)
                           VALUES (?, ?)""",
                        ("tool_{}".format(i), "ui"),
                    )
                await db.logs.commit()

                maint = DatabaseMaintenance(db)
                results = await maint.run_cycle()

                cursor = await db.logs.execute(
                    "SELECT count(*) FROM action_logs"
                )
                count = (await cursor.fetchone())[0]
                assert count == 5
                assert results["old_logs"] == 0
            finally:
                await db.close()
        _run(run())

    def test_cleanup_snapshots_keeps_last_20(self, tmp_path):
        """Only the 20 most recent snapshots should be kept."""
        async def run():
            db = await _make_db(tmp_path)
            try:
                for i in range(30):
                    await db.state.execute(
                        """INSERT INTO snapshots (reason, state_json, size_bytes)
                           VALUES (?, ?, ?)""",
                        ("test_{}".format(i), "{}", 100),
                    )
                await db.state.commit()

                maint = DatabaseMaintenance(db)
                results = await maint.run_cycle()

                cursor = await db.state.execute(
                    "SELECT count(*) FROM snapshots"
                )
                count = (await cursor.fetchone())[0]
                assert count == 20
                assert results["old_snapshots"] == 10
            finally:
                await db.close()
        _run(run())

    def test_cleanup_expired_memories(self, tmp_path):
        """Expired memories should be deleted."""
        async def run():
            db = await _make_db(tmp_path)
            try:
                repo = MemoryRepository(db.state)
                past = (
                    datetime.now(timezone.utc) - timedelta(hours=2)
                ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                await repo.store(Memory.new(
                    "mid", "g", {"old": 1}, expires_at=past
                ))
                await repo.store(Memory.new("mid", "g", {"perm": 1}))

                maint = DatabaseMaintenance(db)
                results = await maint.run_cycle()

                assert results["expired_memories"] == 1

                remaining = await repo.recall("mid", limit=100)
                assert len(remaining) == 1
                assert remaining[0].content == {"perm": 1}
            finally:
                await db.close()
        _run(run())
