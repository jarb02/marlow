"""Tests for Phase 2: Activate Read Paths.

Tests reliability gate, error_journal integration, context builder
app knowledge + memory sections, and memory recall for planning.
"""

import asyncio
import time

import pytest

from marlow.kernel.execution_pipeline import ExecutionPipeline, PipelineResult
from marlow.kernel.scoring.pre_scorer import PreActionScorer
from marlow.kernel.scoring.reliability import ReliabilityTracker
from marlow.kernel.context_builder import ContextBuilder


def _run(coro):
    return asyncio.run(coro)


def _make_tool_map():
    return {
        "list_windows": lambda **kw: {"success": True, "windows": []},
        "open_application": lambda **kw: {"success": True, "pid": 123},
        "type_text": lambda **kw: {"success": True},
        "click": lambda **kw: {"success": True},
        "run_command": lambda **kw: {"success": True, "output": "ok"},
        "failing_tool": lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")),
    }


# ══════════════════════════════════════════════════════════════
# Task 2.1: Reliability gate
# ══════════════════════════════════════════════════════════════


class TestReliabilityGate:
    """PreActionScorer reliability-based blocking for proactive origin."""

    def test_proactive_blocked_when_low_reliability(self):
        """Proactive origin should be blocked when reliability < 0.3."""
        tracker = ReliabilityTracker()
        # Record many failures to drive EMA below 0.3
        for _ in range(10):
            tracker.record("click", 0.0)

        scorer = PreActionScorer(reliability_tracker=tracker)
        pipeline = ExecutionPipeline(
            tool_map=_make_tool_map(),
            pre_scorer=scorer,
        )
        result = _run(pipeline.execute("click", {"x": 0, "y": 0}, origin="proactive"))
        assert not result.success
        assert "low reliability" in result.error

    def test_user_origin_allowed_with_low_reliability(self):
        """User origin should be allowed even with low reliability (just warns)."""
        tracker = ReliabilityTracker()
        for _ in range(10):
            tracker.record("click", 0.0)

        scorer = PreActionScorer(reliability_tracker=tracker)
        pipeline = ExecutionPipeline(
            tool_map=_make_tool_map(),
            pre_scorer=scorer,
        )
        result = _run(pipeline.execute("click", {"x": 0, "y": 0}, origin="gemini"))
        assert result.success

    def test_proactive_allowed_when_high_reliability(self):
        """Proactive origin should be allowed when reliability is good."""
        tracker = ReliabilityTracker()
        for _ in range(10):
            tracker.record("click", 1.0)

        scorer = PreActionScorer(reliability_tracker=tracker)
        pipeline = ExecutionPipeline(
            tool_map=_make_tool_map(),
            pre_scorer=scorer,
        )
        result = _run(pipeline.execute("click", {"x": 0, "y": 0}, origin="proactive"))
        assert result.success

    def test_unknown_tool_neutral_reliability_allows_proactive(self):
        """Unknown tools default to 0.5 reliability — should allow proactive."""
        scorer = PreActionScorer()
        pipeline = ExecutionPipeline(
            tool_map=_make_tool_map(),
            pre_scorer=scorer,
        )
        result = _run(pipeline.execute("list_windows", {}, origin="proactive"))
        assert result.success

    def test_reliability_tracker_connected_to_pre_scorer(self):
        """Verify PreActionScorer.score() calls ReliabilityTracker.get_reliability()."""
        tracker = ReliabilityTracker()
        for _ in range(5):
            tracker.record("click", 0.9)
        scorer = PreActionScorer(reliability_tracker=tracker)

        score = scorer.score("click", {}, app_name="")
        assert score.reliability > 0.6


# ══════════════════════════════════════════════════════════════
# Task 2.2: ContextBuilder app knowledge
# ══════════════════════════════════════════════════════════════


class TestContextBuilderAppKnowledge:
    """App knowledge section in context builder."""

    def test_no_knowledge_produces_no_section(self):
        """Without app_knowledge, no app context section."""
        cb = ContextBuilder()
        ctx = cb.build()
        assert "App context" not in ctx

    def test_cached_knowledge_produces_section(self):
        """Cached app knowledge should produce an app context section."""
        cb = ContextBuilder()
        cb._app_knowledge_cache = {
            "app_name": "Firefox",
            "reliability": 0.85,
            "known_elements": {"url_bar": {"type": "edit"}, "search": {"type": "edit"}},
            "error_solutions": [
                {"tool": "timeout", "solution": "increase wait time"},
            ],
        }
        ctx = cb.build()
        assert "App context (Firefox)" in ctx
        assert "Reliability: 0.85" in ctx
        assert "url_bar" in ctx
        assert "timeout" in ctx

    def test_empty_cache_no_section(self):
        """Empty cache should not produce a section."""
        cb = ContextBuilder()
        cb._app_knowledge_cache = {}
        ctx = cb.build()
        assert "App context" not in ctx

    def test_knowledge_cache_only_reliability(self):
        """Cache with only reliability should still show."""
        cb = ContextBuilder()
        cb._app_knowledge_cache = {
            "app_name": "Terminal",
            "reliability": 0.72,
            "known_elements": {},
            "error_solutions": [],
        }
        ctx = cb.build()
        assert "Reliability: 0.72" in ctx

    def test_update_cache_async(self):
        """update_app_knowledge_cache should populate cache."""
        calls = []

        class FakeKnowledge:
            async def get_reliability(self, app):
                calls.append("reliability")
                return 0.9
            async def get_known_elements(self, app):
                calls.append("elements")
                return {"btn_ok": {"type": "button"}}
            async def get_app_info(self, app):
                calls.append("info")
                return None
            async def get_error_solution(self, app, tool, err_type):
                return None

        cb = ContextBuilder(app_knowledge=FakeKnowledge())
        _run(cb.update_app_knowledge_cache("TestApp"))
        assert cb._app_knowledge_cache["app_name"] == "TestApp"
        assert cb._app_knowledge_cache["reliability"] == 0.9
        assert "btn_ok" in cb._app_knowledge_cache["known_elements"]

    def test_cache_reuses_for_same_app(self):
        """Calling update_cache for same app should reuse existing cache."""
        call_count = {"n": 0}

        class FakeKnowledge:
            async def get_reliability(self, app):
                call_count["n"] += 1
                return 0.8
            async def get_known_elements(self, app):
                return {}
            async def get_app_info(self, app):
                return None
            async def get_error_solution(self, app, tool, err_type):
                return None

        cb = ContextBuilder(app_knowledge=FakeKnowledge())
        _run(cb.update_app_knowledge_cache("AppX"))
        _run(cb.update_app_knowledge_cache("AppX"))  # should reuse
        assert call_count["n"] == 1  # only called once


# ══════════════════════════════════════════════════════════════
# Task 2.3: ContextBuilder memory section
# ══════════════════════════════════════════════════════════════


class TestContextBuilderMemory:
    """Recent actions section from MemorySystem."""

    def test_no_memory_produces_no_section(self):
        cb = ContextBuilder()
        ctx = cb.build()
        assert "Recent actions" not in ctx

    def test_memory_with_actions_produces_section(self):
        class FakeEntry:
            def __init__(self, content):
                self.content = content
                self.category = "action"
                self.timestamp = time.monotonic()

        class FakeMemory:
            def get_recent_actions(self, n=5):
                return [
                    FakeEntry({"tool": "click", "success": True, "duration_ms": 50}),
                    FakeEntry({"tool": "type_text", "success": False, "duration_ms": 120, "error": "element not found"}),
                ]

        cb = ContextBuilder(memory=FakeMemory())
        ctx = cb.build()
        assert "Recent actions" in ctx
        assert "click: OK (50ms)" in ctx
        assert "type_text: FAIL (120ms)" in ctx
        assert "element not found" in ctx

    def test_empty_memory_no_section(self):
        class FakeMemory:
            def get_recent_actions(self, n=5):
                return []

        cb = ContextBuilder(memory=FakeMemory())
        ctx = cb.build()
        assert "Recent actions" not in ctx


# ══════════════════════════════════════════════════════════════
# Task 2.4: ErrorJournal in pipeline
# ══════════════════════════════════════════════════════════════


class TestErrorJournalPipeline:
    """ErrorJournal integration in ExecutionPipeline."""

    def test_failure_records_in_journal(self):
        recorded = []

        class FakeJournal:
            def record_failure(self, tool, window, method, error, params=None):
                recorded.append({
                    "tool": tool, "window": window,
                    "method": method, "error": error,
                })
            def get_best_method(self, tool, window):
                return None

        pipeline = ExecutionPipeline(
            tool_map=_make_tool_map(),
            error_journal=FakeJournal(),
        )
        result = _run(pipeline.execute("failing_tool", {"app_name": "test_app"}))
        assert not result.success
        assert len(recorded) == 1
        assert recorded[0]["tool"] == "failing_tool"
        assert "boom" in recorded[0]["error"]

    def test_success_does_not_record_failure(self):
        recorded = []

        class FakeJournal:
            def record_failure(self, **kw):
                recorded.append(kw)
            def get_best_method(self, tool, window):
                return None

        pipeline = ExecutionPipeline(
            tool_map=_make_tool_map(),
            error_journal=FakeJournal(),
        )
        result = _run(pipeline.execute("list_windows", {}))
        assert result.success
        assert len(recorded) == 0

    def test_best_method_queried_pre_execution(self):
        queried = []

        class FakeJournal:
            def get_best_method(self, tool, window):
                queried.append(tool)
                return "alternative_method"
            def record_failure(self, **kw):
                pass

        pipeline = ExecutionPipeline(
            tool_map=_make_tool_map(),
            error_journal=FakeJournal(),
        )
        _run(pipeline.execute("click", {"window_title": "Notepad"}, origin="gemini"))
        assert "click" in queried

    def test_journal_crash_does_not_break_pipeline(self):
        class CrashingJournal:
            def get_best_method(self, tool, window):
                raise RuntimeError("journal on fire")
            def record_failure(self, **kw):
                raise RuntimeError("journal on fire")

        pipeline = ExecutionPipeline(
            tool_map=_make_tool_map(),
            error_journal=CrashingJournal(),
        )
        # Should still succeed despite journal crash
        result = _run(pipeline.execute("list_windows", {}))
        assert result.success
        # Should still return error for failing tool despite journal crash
        result = _run(pipeline.execute("failing_tool", {}))
        assert not result.success

    def test_no_journal_no_error(self):
        """Pipeline without error_journal should work fine."""
        pipeline = ExecutionPipeline(tool_map=_make_tool_map())
        result = _run(pipeline.execute("list_windows", {}))
        assert result.success
