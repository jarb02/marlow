"""Tests for Phase 5: Proactive Security Guardrails.

Tests ApprovalQueue, RollbackExecutor, ProactiveEngine shadow-first,
proactivity kill switch, and audit trail.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marlow.kernel.approval_queue import (
    ApprovalQueue,
    ApprovalResult,
    ApprovalStatus,
    PendingApproval,
)
from marlow.kernel.rollback import (
    ProactiveAction,
    RollbackExecutor,
    ROLLBACK_WINDOW_SECONDS,
)


def _run(coro):
    return asyncio.run(coro)


# ══════════════════════════════════════════════════════════════
# 5.1: ApprovalQueue
# ══════════════════════════════════════════════════════════════


class TestApprovalQueue:
    """Approval lifecycle: submit → approve/reject/timeout."""

    def test_approve_executes_action(self):
        pipeline = AsyncMock()
        pipeline.execute = AsyncMock(return_value=MagicMock(success=True, error=""))
        detector = MagicMock()
        queue = ApprovalQueue(pipeline=pipeline, pattern_detector=detector, default_timeout=5.0)

        async def _test():
            # Submit in background
            task = asyncio.create_task(
                queue.submit("open_application", {"application": "firefox"},
                             pattern_id="p1", trust_level=2, confidence=0.92)
            )
            await asyncio.sleep(0.05)  # let submit start

            # Approve
            pending = queue.get_pending()
            assert len(pending) == 1
            approval_id = pending[0]["id"]
            assert queue.approve(approval_id)

            result = await task
            assert result.approved
            assert result.status == ApprovalStatus.APPROVED

            # Pipeline was called
            pipeline.execute.assert_called_once()
            # Feedback recorded
            detector.record_feedback.assert_called_with("p1", approved=True)

        _run(_test())

    def test_reject_does_not_execute(self):
        pipeline = AsyncMock()
        detector = MagicMock()
        queue = ApprovalQueue(pipeline=pipeline, pattern_detector=detector, default_timeout=5.0)

        async def _test():
            task = asyncio.create_task(
                queue.submit("click", {"x": 100, "y": 200},
                             pattern_id="p2", trust_level=2)
            )
            await asyncio.sleep(0.05)

            pending = queue.get_pending()
            approval_id = pending[0]["id"]
            assert queue.reject(approval_id)

            result = await task
            assert not result.approved
            assert result.status == ApprovalStatus.REJECTED

            pipeline.execute.assert_not_called()
            detector.record_feedback.assert_called_with("p2", approved=False)

        _run(_test())

    def test_timeout_cancels_action(self):
        pipeline = AsyncMock()
        queue = ApprovalQueue(pipeline=pipeline, default_timeout=0.1)

        async def _test():
            result = await queue.submit(
                "type_text", {"text": "hello"},
                pattern_id="p3", trust_level=2, timeout=0.1,
            )
            assert not result.approved
            assert result.status == ApprovalStatus.EXPIRED
            pipeline.execute.assert_not_called()

        _run(_test())

    def test_cancel_all_clears_queue(self):
        queue = ApprovalQueue(default_timeout=60.0)

        async def _test():
            task = asyncio.create_task(
                queue.submit("click", {}, pattern_id="p4", trust_level=2)
            )
            await asyncio.sleep(0.05)
            assert len(queue.get_pending()) == 1

            queue.cancel_all()
            result = await task
            assert result.status == ApprovalStatus.CANCELLED
            assert not result.approved

        _run(_test())

    def test_approve_nonexistent_returns_false(self):
        queue = ApprovalQueue()
        assert queue.approve("nonexistent") is False

    def test_reject_nonexistent_returns_false(self):
        queue = ApprovalQueue()
        assert queue.reject("nonexistent") is False

    def test_get_pending_excludes_expired(self):
        queue = ApprovalQueue()

        async def _test():
            task = asyncio.create_task(
                queue.submit("click", {}, pattern_id="p5", trust_level=2, timeout=0.05)
            )
            await asyncio.sleep(0.1)
            pending = queue.get_pending()
            assert len(pending) == 0
            await task

        _run(_test())

    def test_ws_broadcast_called(self):
        broadcast = MagicMock()
        queue = ApprovalQueue(ws_broadcast=broadcast, default_timeout=0.1)

        async def _test():
            await queue.submit("click", {}, pattern_id="p6", trust_level=2,
                               description="Test action", timeout=0.1)
            broadcast.assert_called_once()
            msg = broadcast.call_args[0][0]
            assert msg["type"] == "approval_request"
            assert "id" in msg

        _run(_test())

    def test_no_pipeline_returns_error(self):
        queue = ApprovalQueue(pipeline=None, default_timeout=5.0)

        async def _test():
            task = asyncio.create_task(
                queue.submit("click", {}, pattern_id="p7", trust_level=2)
            )
            await asyncio.sleep(0.05)
            pending = queue.get_pending()
            queue.approve(pending[0]["id"])
            result = await task
            assert result.approved  # it was approved
            assert result.error  # but has error about no pipeline

        _run(_test())


# ══════════════════════════════════════════════════════════════
# 5.2: Proactivity Kill Switch (DesktopObserver)
# ══════════════════════════════════════════════════════════════


class TestProactivityToggle:
    """DesktopObserver handles ProactivityToggle events."""

    def test_toggle_event_dispatched(self):
        from marlow.kernel.desktop_observer import DesktopObserver

        bus = AsyncMock()
        bus.publish = AsyncMock()
        obs = DesktopObserver(event_bus=bus)

        _run(obs._dispatch_event({"event": "ProactivityToggle"}))
        bus.publish.assert_called()
        evt = bus.publish.call_args[0][0]
        assert evt.event_type == "system.proactivity_toggle"


# ══════════════════════════════════════════════════════════════
# 5.3: Shadow First Scope
# ══════════════════════════════════════════════════════════════


class TestShadowFirst:
    """Proactive AUTO actions use launch_in_shadow instead of open_application."""

    def test_open_application_rewritten_to_shadow(self):
        from marlow.kernel.proactive_engine import ProactiveEngine
        from marlow.kernel.pattern_detector import Pattern

        pipeline = AsyncMock()
        pipeline.execute = AsyncMock(return_value=MagicMock(success=True, error=""))
        detector = MagicMock()
        engine = ProactiveEngine(
            pattern_detector=detector,
            pipeline=pipeline,
        )

        pattern = Pattern(
            id="a", type="temporal", tool_name="open_application",
            confidence=0.99, params={"application": "firefox"},
        )

        with patch.object(engine, '_notify'):
            _run(engine._execute_auto(pattern))

        # Should call launch_in_shadow, not open_application
        call_args = pipeline.execute.call_args
        assert call_args[0][0] == "launch_in_shadow"
        assert call_args[0][1] == {"command": "firefox"}

    def test_non_launch_not_rewritten(self):
        from marlow.kernel.proactive_engine import ProactiveEngine
        from marlow.kernel.pattern_detector import Pattern

        pipeline = AsyncMock()
        pipeline.execute = AsyncMock(return_value=MagicMock(success=True, error=""))
        detector = MagicMock()
        engine = ProactiveEngine(
            pattern_detector=detector,
            pipeline=pipeline,
        )

        pattern = Pattern(
            id="a", type="temporal", tool_name="focus_window",
            confidence=0.99, params={"window_title": "Firefox"},
        )

        with patch.object(engine, '_notify'):
            _run(engine._execute_auto(pattern))

        call_args = pipeline.execute.call_args
        assert call_args[0][0] == "focus_window"


# ══════════════════════════════════════════════════════════════
# 5.4: RollbackExecutor
# ══════════════════════════════════════════════════════════════


class TestRollbackExecutor:
    """Rollback of proactive actions."""

    def test_record_and_rollback_launch(self):
        from marlow.kernel.desktop_observer import WindowInfo

        pipeline = AsyncMock()
        pipeline.execute = AsyncMock(return_value=MagicMock(success=True))
        observer = MagicMock()

        # Pre-action: 1 window
        pre_state = MagicMock()
        pre_state.windows = {1: WindowInfo(id=1, title="Terminal", app_id="foot")}
        pre_state.focused_window = pre_state.windows[1]

        # Post-action: 2 windows (new one appeared)
        post_state = MagicMock()
        post_state.windows = {
            1: WindowInfo(id=1, title="Terminal", app_id="foot"),
            2: WindowInfo(id=2, title="Firefox", app_id="firefox"),
        }
        observer.get_state = MagicMock(side_effect=[pre_state, post_state])

        executor = RollbackExecutor(pipeline=pipeline, desktop_observer=observer)
        executor.record_action("launch_in_shadow", {"command": "firefox"})

        result = _run(executor.rollback_last())
        assert result is True
        # Should have called close_window for window 2
        pipeline.execute.assert_called()
        call = pipeline.execute.call_args
        assert call[0][0] == "close_window"
        assert call[0][1]["window_id"] == 2

    def test_rollback_move_to_user(self):
        pipeline = AsyncMock()
        pipeline.execute = AsyncMock(return_value=MagicMock(success=True))
        executor = RollbackExecutor(pipeline=pipeline)
        executor.record_action("move_to_user", {"window_id": 42})

        result = _run(executor.rollback_last())
        assert result is True
        call = pipeline.execute.call_args
        assert call[0][0] == "move_to_shadow"
        assert call[0][1]["window_id"] == 42

    def test_rollback_focus_restores_previous(self):
        from marlow.kernel.desktop_observer import WindowInfo

        pipeline = AsyncMock()
        pipeline.execute = AsyncMock(return_value=MagicMock(success=True))
        observer = MagicMock()
        state = MagicMock()
        state.windows = {1: WindowInfo(id=1, title="A", app_id="a")}
        state.focused_window = state.windows[1]
        observer.get_state = MagicMock(return_value=state)

        executor = RollbackExecutor(pipeline=pipeline, desktop_observer=observer)
        executor.record_action("focus_window", {"window_id": 5})

        result = _run(executor.rollback_last())
        assert result is True
        call = pipeline.execute.call_args
        assert call[0][0] == "focus_window"
        assert call[0][1]["window_id"] == 1  # restored pre-focus

    def test_non_reversible_action(self):
        executor = RollbackExecutor()
        executor._journal.append(ProactiveAction(
            tool_name="type_text",
            params={"text": "hello"},
            timestamp=time.time(),
        ))
        result = _run(executor.rollback_last())
        assert result is False

    def test_rollback_too_old(self):
        executor = RollbackExecutor()
        executor._journal.append(ProactiveAction(
            tool_name="launch_in_shadow",
            params={"command": "firefox"},
            timestamp=time.time() - 60,  # 1 min ago
        ))
        result = _run(executor.rollback_last())
        assert result is False

    def test_already_rolled_back(self):
        executor = RollbackExecutor()
        executor._journal.append(ProactiveAction(
            tool_name="launch_in_shadow",
            params={"command": "firefox"},
            timestamp=time.time(),
            rolled_back=True,
        ))
        result = _run(executor.rollback_last())
        assert result is False

    def test_empty_journal(self):
        executor = RollbackExecutor()
        result = _run(executor.rollback_last())
        assert result is False

    def test_rollback_all_since(self):
        pipeline = AsyncMock()
        pipeline.execute = AsyncMock(return_value=MagicMock(success=True))
        executor = RollbackExecutor(pipeline=pipeline)

        now = time.time()
        executor._journal.append(ProactiveAction(
            tool_name="move_to_user", params={"window_id": 1}, timestamp=now - 5,
        ))
        executor._journal.append(ProactiveAction(
            tool_name="move_to_user", params={"window_id": 2}, timestamp=now - 2,
        ))

        count = _run(executor.rollback_all_since(now - 10))
        assert count == 2

    def test_tormenta_auto_rollback(self):
        pipeline = AsyncMock()
        pipeline.execute = AsyncMock(return_value=MagicMock(success=True))
        executor = RollbackExecutor(pipeline=pipeline)

        executor._journal.append(ProactiveAction(
            tool_name="move_to_user", params={"window_id": 3},
            timestamp=time.time(),
        ))

        event = MagicMock()
        event.data = {"climate": "TORMENTA"}
        _run(executor.on_weather_event(event))

        assert pipeline.execute.called
        assert executor._journal[0].rolled_back


# ══════════════════════════════════════════════════════════════
# 5.5: ProactiveEngine integration
# ══════════════════════════════════════════════════════════════


class TestProactiveEngineIntegration:
    """ProactiveEngine uses ApprovalQueue for SUGGEST and RollbackExecutor."""

    def test_suggest_uses_approval_queue(self):
        from marlow.kernel.proactive_engine import ProactiveEngine
        from marlow.kernel.pattern_detector import Pattern

        queue = AsyncMock()
        queue.submit = AsyncMock(return_value=ApprovalResult(
            approved=True, status=ApprovalStatus.APPROVED, approval_id="x",
        ))

        engine = ProactiveEngine()
        engine._approval_queue = queue

        pattern = Pattern(
            id="a", type="temporal", tool_name="type_text",
            confidence=0.92, params={"text": "hello"},
        )

        _run(engine._execute_suggest(pattern))
        queue.submit.assert_called_once()
        call_kwargs = queue.submit.call_args
        assert call_kwargs[1]["tool_name"] == "type_text" or call_kwargs[0][0] == "type_text"

    def test_kill_switch_pauses_and_cancels(self):
        from marlow.kernel.proactive_engine import ProactiveEngine

        queue = MagicMock()
        queue.cancel_all = MagicMock()
        engine = ProactiveEngine()
        engine._approval_queue = queue

        engine.toggle()
        assert engine.is_paused()
        queue.cancel_all.assert_called_once()

    def test_kill_switch_resumes(self):
        from marlow.kernel.proactive_engine import ProactiveEngine

        engine = ProactiveEngine()
        engine._paused = True
        engine.toggle()
        assert not engine.is_paused()


# ══════════════════════════════════════════════════════════════
# 5.6: Fault tolerance
# ══════════════════════════════════════════════════════════════


class TestPhase5FaultTolerance:
    """Resilience to errors across Phase 5 components."""

    def test_approval_queue_pipeline_crash(self):
        pipeline = AsyncMock()
        pipeline.execute = AsyncMock(side_effect=RuntimeError("boom"))
        queue = ApprovalQueue(pipeline=pipeline, default_timeout=5.0)

        async def _test():
            task = asyncio.create_task(
                queue.submit("click", {}, pattern_id="p", trust_level=2)
            )
            await asyncio.sleep(0.05)
            pending = queue.get_pending()
            queue.approve(pending[0]["id"])
            result = await task
            assert result.approved  # was approved
            assert "boom" in result.error  # but execution failed

        _run(_test())

    def test_rollback_pipeline_crash(self):
        pipeline = AsyncMock()
        pipeline.execute = AsyncMock(side_effect=RuntimeError("fire"))
        executor = RollbackExecutor(pipeline=pipeline)
        executor._journal.append(ProactiveAction(
            tool_name="move_to_user", params={"window_id": 1},
            timestamp=time.time(),
        ))
        result = _run(executor.rollback_last())
        assert result is False  # didn't crash, just returned False

    def test_rollback_no_observer(self):
        executor = RollbackExecutor(pipeline=AsyncMock())
        executor._journal.append(ProactiveAction(
            tool_name="launch_in_shadow", params={"command": "ff"},
            timestamp=time.time(),
        ))
        # No observer means can't find new windows, should not crash
        result = _run(executor.rollback_last())
        assert result is False
