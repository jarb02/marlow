"""Tests for marlow.kernel.memory (MemorySystem) and marlow.kernel.knowledge (AppKnowledgeManager)."""

import asyncio
import time

import pytest

from marlow.kernel.db.manager import DatabaseManager
from marlow.kernel.db.repositories import (
    KnowledgeRepository,
    MemoryRepository,
)
from marlow.kernel.knowledge import AppKnowledgeManager
from marlow.kernel.memory import MemorySystem


def _run(coro):
    return asyncio.run(coro)


async def _make_db(tmp_path):
    db = DatabaseManager(data_dir=tmp_path)
    await db.initialize()
    return db


# ── MemorySystem ──


class TestMemorySystem:
    """Tests for the three-tier memory system."""

    def test_short_term_remember_and_recall(self, tmp_path):
        """Short-term entries should be returned newest-first."""
        async def run():
            db = await _make_db(tmp_path)
            try:
                ms = MemorySystem(MemoryRepository(db.state))
                ms.remember_short({"action": "click"}, category="action")
                ms.remember_short({"action": "type"}, category="action")
                ms.remember_short({"action": "screenshot"}, category="observation")

                recent = ms.get_recent_actions(10)
                assert len(recent) == 3
                assert recent[0].content == {"action": "screenshot"}
                assert recent[2].content == {"action": "click"}
            finally:
                await db.close()
        _run(run())

    def test_short_term_max_size(self, tmp_path):
        """Short-term buffer should cap at 50 entries."""
        async def run():
            db = await _make_db(tmp_path)
            try:
                ms = MemorySystem(MemoryRepository(db.state))
                for i in range(60):
                    ms.remember_short({"i": i}, category="test")

                assert ms.short_term_count == 50
                recent = ms.get_recent_actions(50)
                # Oldest kept should be i=10 (0-9 evicted)
                assert recent[-1].content == {"i": 10}
            finally:
                await db.close()
        _run(run())

    def test_short_term_filter_by_goal(self, tmp_path):
        """get_short_term_for_goal should filter correctly."""
        async def run():
            db = await _make_db(tmp_path)
            try:
                ms = MemorySystem(MemoryRepository(db.state))
                ms.remember_short({"a": 1}, category="action", goal_id="g1")
                ms.remember_short({"b": 2}, category="action", goal_id="g2")
                ms.remember_short({"c": 3}, category="action", goal_id="g1")

                g1 = ms.get_short_term_for_goal("g1")
                assert len(g1) == 2
                assert all(e.goal_id == "g1" for e in g1)
            finally:
                await db.close()
        _run(run())

    def test_mid_term_store_and_recall(self, tmp_path):
        """Mid-term memories should persist and be retrievable."""
        async def run():
            db = await _make_db(tmp_path)
            try:
                ms = MemorySystem(MemoryRepository(db.state))
                await ms.remember_mid(
                    {"notepad": "uses SetValue"},
                    category="apps",
                    tags=["notepad", "input"],
                )

                results = await ms.recall(tier="mid", category="apps")
                assert len(results) == 1
                assert results[0].content == {"notepad": "uses SetValue"}
                assert "notepad" in results[0].tags
            finally:
                await db.close()
        _run(run())

    def test_mid_term_expires(self, tmp_path):
        """Mid-term memories with tiny TTL should be cleaned up."""
        async def run():
            db = await _make_db(tmp_path)
            try:
                ms = MemorySystem(MemoryRepository(db.state))
                # TTL of ~3.6ms — effectively expired immediately
                await ms.remember_mid(
                    {"temp": True},
                    category="test",
                    ttl_hours=0.000001,
                )
                # Small sleep to ensure expiry
                await asyncio.sleep(0.01)

                deleted = await ms.cleanup()
                assert deleted == 1

                results = await ms.recall(tier="mid", category="test")
                assert len(results) == 0
            finally:
                await db.close()
        _run(run())

    def test_long_term_never_expires(self, tmp_path):
        """Long-term memories should survive cleanup."""
        async def run():
            db = await _make_db(tmp_path)
            try:
                ms = MemorySystem(MemoryRepository(db.state))
                await ms.remember_long(
                    {"permanent": True},
                    category="core",
                    tags=["important"],
                )

                deleted = await ms.cleanup()
                assert deleted == 0

                results = await ms.recall(tier="long")
                assert len(results) == 1
                assert results[0].content == {"permanent": True}
            finally:
                await db.close()
        _run(run())

    def test_recall_by_tags(self, tmp_path):
        """Tag-based search should find matching memories."""
        async def run():
            db = await _make_db(tmp_path)
            try:
                ms = MemorySystem(MemoryRepository(db.state))
                await ms.remember_mid(
                    {"a": 1}, category="apps", tags=["notepad", "win32"],
                )
                await ms.remember_mid(
                    {"b": 2}, category="apps", tags=["chrome", "electron"],
                )
                await ms.remember_long(
                    {"c": 3}, category="apps", tags=["notepad", "keyboard"],
                )

                found = await ms.recall_by_tags(["notepad"])
                assert len(found) == 2
                contents = [m.content for m in found]
                assert {"a": 1} in contents
                assert {"c": 3} in contents
            finally:
                await db.close()
        _run(run())

    def test_persist_short_term(self, tmp_path):
        """Short-term entries should be saved to mid-term on persist."""
        async def run():
            db = await _make_db(tmp_path)
            try:
                ms = MemorySystem(MemoryRepository(db.state))
                ms.remember_short({"x": 1}, category="action")
                ms.remember_short({"y": 2}, category="observation")

                count = await ms.persist_short_term()
                assert count == 2

                # Verify they're in mid-term
                results = await ms.recall(tier="mid")
                assert len(results) == 2
                categories = {r.category for r in results}
                assert "recovered_action" in categories
                assert "recovered_observation" in categories
            finally:
                await db.close()
        _run(run())

    def test_decay_relevance(self, tmp_path):
        """Mid-term relevance should decrease after decay."""
        async def run():
            db = await _make_db(tmp_path)
            try:
                ms = MemorySystem(MemoryRepository(db.state))
                await ms.remember_mid(
                    {"data": 1}, category="test", ttl_hours=24,
                )

                # Decay multiple times
                await ms.cleanup()
                await ms.cleanup()
                await ms.cleanup()

                results = await ms.recall(tier="mid", category="test")
                assert len(results) == 1
                # 1.0 * 0.95^3 ≈ 0.857
                assert results[0].relevance < 0.9
            finally:
                await db.close()
        _run(run())

    def test_clear_short_term(self, tmp_path):
        """clear_short_term should empty the buffer."""
        async def run():
            db = await _make_db(tmp_path)
            try:
                ms = MemorySystem(MemoryRepository(db.state))
                ms.remember_short({"a": 1}, category="test")
                ms.remember_short({"b": 2}, category="test")
                assert ms.short_term_count == 2

                ms.clear_short_term()
                assert ms.short_term_count == 0
            finally:
                await db.close()
        _run(run())


# ── AppKnowledgeManager ──


class TestAppKnowledgeManager:
    """Tests for the app knowledge business logic."""

    def test_record_action_creates_app(self, tmp_path):
        """Recording an action for an unknown app should create it."""
        async def run():
            db = await _make_db(tmp_path)
            try:
                km = AppKnowledgeManager(KnowledgeRepository(db.state))
                await km.record_action("new_app.exe", success=True)

                app = await km.get_app_info("new_app.exe")
                assert app is not None
                assert app.total_actions == 1
                assert app.success_actions == 1
            finally:
                await db.close()
        _run(run())

    def test_record_action_updates_reliability(self, tmp_path):
        """Reliability should increase with successful actions."""
        async def run():
            db = await _make_db(tmp_path)
            try:
                km = AppKnowledgeManager(KnowledgeRepository(db.state))
                await km.record_action("app.exe", success=True)
                await km.record_action("app.exe", success=True)
                await km.record_action("app.exe", success=True)

                rel = await km.get_reliability("app.exe")
                assert rel > 0.5  # started at 0.5 default, should be higher
            finally:
                await db.close()
        _run(run())

    def test_get_preferred_input_default(self, tmp_path):
        """Unknown app should return 'uia' as default input method."""
        async def run():
            db = await _make_db(tmp_path)
            try:
                km = AppKnowledgeManager(KnowledgeRepository(db.state))
                method = await km.get_preferred_input("unknown.exe")
                assert method == "uia"
            finally:
                await db.close()
        _run(run())

    def test_should_use_cdp_electron(self, tmp_path):
        """Electron app with CDP port should return True."""
        async def run():
            db = await _make_db(tmp_path)
            try:
                km = AppKnowledgeManager(KnowledgeRepository(db.state))
                await km.record_action(
                    "vscode.exe", success=True,
                    framework="electron", cdp_port=9222,
                )

                should, port = await km.should_use_cdp("vscode.exe")
                assert should is True
                assert port == 9222
            finally:
                await db.close()
        _run(run())

    def test_should_use_cdp_normal_app(self, tmp_path):
        """Non-electron app should return False."""
        async def run():
            db = await _make_db(tmp_path)
            try:
                km = AppKnowledgeManager(KnowledgeRepository(db.state))
                await km.record_action(
                    "notepad.exe", success=True, framework="win32",
                )

                should, port = await km.should_use_cdp("notepad.exe")
                assert should is False
                assert port is None
            finally:
                await db.close()
        _run(run())

    def test_learn_element(self, tmp_path):
        """Learning an element should persist and be retrievable."""
        async def run():
            db = await _make_db(tmp_path)
            try:
                km = AppKnowledgeManager(KnowledgeRepository(db.state))
                await km.learn_element("notepad.exe", "SaveButton", {
                    "automation_id": "SaveBtn",
                    "control_type": "Button",
                    "method": "invoke",
                })

                elements = await km.get_known_elements("notepad.exe")
                assert "SaveButton" in elements
                assert elements["SaveButton"]["method"] == "invoke"
            finally:
                await db.close()
        _run(run())

    def test_learn_dialog_no_duplicates(self, tmp_path):
        """Learning a dialog with same title should replace, not duplicate."""
        async def run():
            db = await _make_db(tmp_path)
            try:
                km = AppKnowledgeManager(KnowledgeRepository(db.state))
                await km.learn_dialog("notepad.exe", {
                    "title": "Save Changes?",
                    "action": "dismiss",
                })
                await km.learn_dialog("notepad.exe", {
                    "title": "Save Changes?",
                    "action": "click_save",
                })

                app = await km.get_app_info("notepad.exe")
                assert len(app.known_dialogs) == 1
                assert app.known_dialogs[0]["action"] == "click_save"
            finally:
                await db.close()
        _run(run())

    def test_record_error_and_get_solution(self, tmp_path):
        """Recording a working solution should be retrievable."""
        async def run():
            db = await _make_db(tmp_path)
            try:
                km = AppKnowledgeManager(KnowledgeRepository(db.state))
                await km.record_error(
                    app_name="app.exe",
                    tool_name="click",
                    error_type="ElementNotFound",
                    solution="use_ocr_fallback",
                    worked=True,
                )

                solution = await km.get_error_solution(
                    "app.exe", "click", "ElementNotFound"
                )
                assert solution == "use_ocr_fallback"
            finally:
                await db.close()
        _run(run())

    def test_import_from_error_journal(self, tmp_path):
        """Bulk import from error journal should create entries."""
        async def run():
            db = await _make_db(tmp_path)
            try:
                km = AppKnowledgeManager(KnowledgeRepository(db.state))
                journal = [
                    {
                        "app_name": "notepad.exe",
                        "tool_name": "type_text",
                        "error_type": "SetValueFailed",
                        "solution": "use_keyboard",
                        "worked": True,
                    },
                    {
                        "app_name": "chrome.exe",
                        "tool_name": "click",
                        "error_type": "Timeout",
                        "solution": "use_cdp",
                        "worked": True,
                    },
                ]
                count = await km.import_from_error_journal(journal)
                assert count == 2

                s1 = await km.get_error_solution(
                    "notepad.exe", "type_text", "SetValueFailed"
                )
                assert s1 == "use_keyboard"

                s2 = await km.get_error_solution(
                    "chrome.exe", "click", "Timeout"
                )
                assert s2 == "use_cdp"
            finally:
                await db.close()
        _run(run())

    def test_import_from_cdp_knowledge(self, tmp_path):
        """Bulk import from CDP knowledge should set framework and port."""
        async def run():
            db = await _make_db(tmp_path)
            try:
                km = AppKnowledgeManager(KnowledgeRepository(db.state))
                cdp_data = {
                    "vscode.exe": {
                        "framework": "electron",
                        "port": 9222,
                    },
                    "slack.exe": {
                        "framework": "electron",
                        "port": 9223,
                    },
                }
                count = await km.import_from_cdp_knowledge(cdp_data)
                assert count == 2

                should, port = await km.should_use_cdp("vscode.exe")
                assert should is True
                assert port == 9222

                app = await km.get_app_info("slack.exe")
                assert app.preferred_input == "cdp"
                assert app.framework == "electron"
            finally:
                await db.close()
        _run(run())
