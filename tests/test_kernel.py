"""Tests for marlow.kernel.kernel and marlow.kernel.executor."""

import asyncio

import pytest

from marlow.kernel.config import KernelConfig
from marlow.kernel.executor import SmartExecutor
from marlow.kernel.kernel import (
    GoalContext,
    KernelState,
    MarlowKernel,
    PlanStep,
)
from marlow.kernel.scoring.scorer import ActionScorer, StepVerdict
from marlow.kernel.security.manager import SecurityManager
from marlow.kernel.types import ToolResult
from marlow.kernel.world_state import WindowInfo, WorldStateCapture, WorldStateSnapshot


# ── Helpers ──


def _make_snapshot(cycle=1, **kwargs):
    """Build a minimal WorldStateSnapshot for testing."""
    defaults = {
        "cycle_number": cycle,
        "timestamp_mono": 100.0 + cycle,
        "timestamp_utc": "2026-03-02T12:00:00.000Z",
    }
    defaults.update(kwargs)
    return WorldStateSnapshot(**defaults)


class FakeWorldCapture:
    """Deterministic world capture for testing — no Win32 calls."""

    def __init__(self):
        self._cycle = 0
        self._windows = (
            WindowInfo(hwnd=1, title="Notepad", process_name="notepad.exe"),
        )

    def capture(self, active_goal_id=None, active_step_index=0):
        self._cycle += 1
        return WorldStateSnapshot(
            cycle_number=self._cycle,
            timestamp_mono=100.0 + self._cycle * 0.1,
            timestamp_utc="2026-03-02T12:00:00.000Z",
            active_window=self._windows[0] if self._windows else None,
            open_windows=self._windows,
            screen_width=1920,
            screen_height=1080,
            active_goal_id=active_goal_id,
            active_step_index=active_step_index,
        )


class VaryingWorldCapture:
    """World capture that changes state each cycle (avoids no-progress guard)."""

    def __init__(self):
        self._cycle = 0

    def capture(self, active_goal_id=None, active_step_index=0):
        self._cycle += 1
        # Different window title each cycle → different fingerprint
        win = WindowInfo(
            hwnd=1,
            title=f"App - Document {self._cycle}",
            process_name="app.exe",
        )
        return WorldStateSnapshot(
            cycle_number=self._cycle,
            timestamp_mono=100.0 + self._cycle * 0.1,
            timestamp_utc="2026-03-02T12:00:00.000Z",
            active_window=win,
            open_windows=(win,),
            clipboard_hash=f"clip_{self._cycle}",
            screen_width=1920,
            screen_height=1080,
            active_goal_id=active_goal_id,
            active_step_index=active_step_index,
        )


async def _mock_executor(tool_name: str, params: dict) -> ToolResult:
    """Mock tool executor for kernel tests."""
    if tool_name == "fail_tool":
        return ToolResult.fail("simulated error", tool_name=tool_name)
    if tool_name == "slow_tool":
        await asyncio.sleep(0.05)
        return ToolResult.ok({"status": "done"}, tool_name=tool_name)
    # Default: success for click, type_text, take_screenshot, etc.
    return ToolResult.ok({"status": "ok"}, tool_name=tool_name)


# ── Fixtures ──


@pytest.fixture
def config():
    """Fast config for tests — short timeouts, fast loop."""
    return KernelConfig(
        max_iterations=25,
        loop_frequency_idle=0.05,  # 50ms idle poll
        loop_frequency_executing=0.001,  # 1ms between steps
    )


@pytest.fixture
def kernel(config):
    """MarlowKernel with mock executor and fake world capture."""
    return MarlowKernel(
        tool_executor=_mock_executor,
        config=config,
        world_capture=FakeWorldCapture(),
    )


# ── TestKernelLifecycle ──


class TestKernelLifecycle:
    """Kernel start/stop/state tests."""

    @pytest.mark.asyncio
    async def test_initial_state(self, kernel):
        """Kernel starts in IDLE state before start() is called."""
        assert kernel.state == KernelState.IDLE

    @pytest.mark.asyncio
    async def test_start_and_shutdown(self, kernel):
        """start() -> IDLE, shutdown() -> SHUTDOWN."""
        await kernel.start()
        assert kernel.state == KernelState.IDLE
        assert kernel._running is True

        await kernel.shutdown()
        assert kernel.state == KernelState.SHUTDOWN
        assert kernel._running is False

    @pytest.mark.asyncio
    async def test_start_idempotent(self, kernel):
        """Calling start() twice does not create a second loop task."""
        await kernel.start()
        task1 = kernel._loop_task
        await kernel.start()
        task2 = kernel._loop_task
        assert task1 is task2
        await kernel.shutdown()


# ── TestGoalExecution ──


class TestGoalExecution:
    """Plan submission and execution tests."""

    @pytest.mark.asyncio
    async def test_simple_plan_executes(self, kernel):
        """2-step plan (click, type_text) completes successfully."""
        results = []

        async def on_goal_complete(result):
            results.append(result)

        kernel._on_goal_complete = on_goal_complete

        await kernel.start()
        await kernel.submit_goal("Test goal", plan=[
            {"tool_name": "click", "params": {"x": 100, "y": 200}},
            {"tool_name": "type_text", "params": {"text": "hello"}},
        ])

        # Wait for goal to complete
        for _ in range(200):
            await asyncio.sleep(0.02)
            if results:
                break

        await kernel.shutdown()

        assert len(results) == 1
        assert results[0]["success"] is True
        assert results[0]["steps_completed"] == 2
        assert results[0]["steps_total"] == 2

    @pytest.mark.asyncio
    async def test_failing_step_retries(self, kernel):
        """fail_tool retries max_retries times then moves on."""
        results = []

        async def on_goal_complete(result):
            results.append(result)

        kernel._on_goal_complete = on_goal_complete

        await kernel.start()
        await kernel.submit_goal("Retry test", plan=[
            {"tool_name": "fail_tool", "params": {}, "max_retries": 2},
            {"tool_name": "click", "params": {}},
        ])

        for _ in range(200):
            await asyncio.sleep(0.02)
            if results:
                break

        await kernel.shutdown()

        assert len(results) == 1
        # Goal completes (both steps attempted)
        assert results[0]["steps_completed"] == 2

    @pytest.mark.asyncio
    async def test_failing_step_recovers(self, kernel):
        """fail_tool triggers recovery, then continues to next step."""
        results = []

        async def on_goal_complete(result):
            results.append(result)

        kernel._on_goal_complete = on_goal_complete

        await kernel.start()
        await kernel.submit_goal("Recovery test", plan=[
            {"tool_name": "fail_tool", "params": {}, "max_retries": 0},
            {"tool_name": "click", "params": {}},
        ])

        for _ in range(200):
            await asyncio.sleep(0.02)
            if results:
                break

        await kernel.shutdown()

        assert len(results) == 1
        # Recovery skips to next step; both steps in plan
        assert results[0]["steps_total"] == 2

    @pytest.mark.asyncio
    async def test_plan_with_all_failures(self, kernel):
        """All steps fail — goal result has success=False."""
        results = []

        async def on_goal_complete(result):
            results.append(result)

        kernel._on_goal_complete = on_goal_complete

        await kernel.start()
        await kernel.submit_goal("All fail", plan=[
            {"tool_name": "fail_tool", "params": {}, "max_retries": 0},
            {"tool_name": "fail_tool", "params": {}, "max_retries": 0},
        ])

        for _ in range(200):
            await asyncio.sleep(0.02)
            if results:
                break

        await kernel.shutdown()

        assert len(results) == 1
        assert results[0]["success"] is False

    @pytest.mark.asyncio
    async def test_empty_plan(self, kernel):
        """Submit goal with empty plan — evaluates immediately."""
        results = []

        async def on_goal_complete(result):
            results.append(result)

        kernel._on_goal_complete = on_goal_complete

        await kernel.start()
        await kernel.submit_goal("Empty", plan=[])

        for _ in range(100):
            await asyncio.sleep(0.02)
            if results:
                break

        await kernel.shutdown()

        assert len(results) == 1
        assert results[0]["steps_total"] == 0
        assert results[0]["avg_score"] == 0.0

    @pytest.mark.asyncio
    async def test_multiple_goals_queue(self, kernel):
        """Submit 2 goals — first completes, then second starts."""
        results = []

        async def on_goal_complete(result):
            results.append(result)

        kernel._on_goal_complete = on_goal_complete

        await kernel.start()
        await kernel.submit_goal("Goal 1", plan=[
            {"tool_name": "click", "params": {}},
        ])
        await kernel.submit_goal("Goal 2", plan=[
            {"tool_name": "type_text", "params": {"text": "hi"}},
        ])

        for _ in range(300):
            await asyncio.sleep(0.02)
            if len(results) >= 2:
                break

        await kernel.shutdown()

        assert len(results) == 2
        assert results[0]["title"] == "Goal 1"
        assert results[1]["title"] == "Goal 2"


# ── TestSecurityIntegration ──


class TestSecurityIntegration:
    """Security checks are enforced inside the kernel loop."""

    @pytest.mark.asyncio
    async def test_blocked_command_skipped(self):
        """run_command with blocked command gets skipped by security."""
        results = []

        async def on_goal_complete(result):
            results.append(result)

        kernel = MarlowKernel(
            tool_executor=_mock_executor,
            config=KernelConfig(
                loop_frequency_idle=0.05,
                loop_frequency_executing=0.001,
            ),
            world_capture=FakeWorldCapture(),
        )
        kernel._on_goal_complete = on_goal_complete

        await kernel.start()
        # "format C:" is blocked by HardcodedInvariants
        await kernel.submit_goal("Blocked cmd", plan=[
            {
                "tool_name": "run_command",
                "params": {"command": "format C:"},
            },
            {"tool_name": "click", "params": {}},
        ])

        for _ in range(200):
            await asyncio.sleep(0.02)
            if results:
                break

        await kernel.shutdown()

        assert len(results) == 1
        # First step was blocked, second succeeded
        assert results[0]["steps_completed"] == 2
        assert any("Security" in e for e in results[0]["errors"])

    @pytest.mark.asyncio
    async def test_rate_limit_blocks(self):
        """Rate limiter triggers after 30 rapid actions."""
        actions_executed = []

        async def counting_executor(tool_name, params):
            actions_executed.append(tool_name)
            return ToolResult.ok(tool_name=tool_name)

        kernel = MarlowKernel(
            tool_executor=counting_executor,
            config=KernelConfig(
                max_iterations=40,
                loop_frequency_idle=0.05,
                loop_frequency_executing=0.001,
            ),
            # Varying capture so no-progress guard doesn't fire first
            world_capture=VaryingWorldCapture(),
        )

        results = []

        async def on_goal_complete(result):
            results.append(result)

        kernel._on_goal_complete = on_goal_complete

        # 35-step plan — rate limiter should kick in at 30
        plan = [{"tool_name": "click", "params": {"x": i}} for i in range(35)]

        await kernel.start()
        await kernel.submit_goal("Rate limit test", plan=plan)

        for _ in range(500):
            await asyncio.sleep(0.02)
            if results:
                break

        await kernel.shutdown()

        assert len(results) == 1
        # Some steps were blocked by rate limiter or security
        assert any("Security" in e or "Rate" in str(e) or "rate" in str(e).lower()
                    for e in results[0]["errors"])


# ── TestLoopGuardIntegration ──


class TestLoopGuardIntegration:
    """LoopGuard prevents infinite loops."""

    @pytest.mark.asyncio
    async def test_repetition_stops_loop(self):
        """Same action 5x with same params triggers repetition guard."""
        results = []

        async def on_goal_complete(result):
            results.append(result)

        kernel = MarlowKernel(
            tool_executor=_mock_executor,
            config=KernelConfig(
                max_iterations=25,
                loop_frequency_idle=0.05,
                loop_frequency_executing=0.001,
            ),
            world_capture=FakeWorldCapture(),
        )
        kernel._on_goal_complete = on_goal_complete

        # 5 identical steps — repetition guard triggers at 3
        plan = [{"tool_name": "click", "params": {"x": 100, "y": 200}}] * 5

        await kernel.start()
        await kernel.submit_goal("Repetition test", plan=plan)

        for _ in range(200):
            await asyncio.sleep(0.02)
            if results:
                break

        await kernel.shutdown()

        assert len(results) == 1
        # Should have errors from loop guard
        assert any("repeated" in e.lower() or "loop" in e.lower()
                    for e in results[0]["errors"])

    @pytest.mark.asyncio
    async def test_max_iterations(self):
        """Plan with 30 steps triggers max_iterations guard at 25."""
        results = []

        async def on_goal_complete(result):
            results.append(result)

        kernel = MarlowKernel(
            tool_executor=_mock_executor,
            config=KernelConfig(
                max_iterations=25,
                loop_frequency_idle=0.05,
                loop_frequency_executing=0.001,
            ),
            # Varying capture so no-progress doesn't fire before max_iterations
            world_capture=VaryingWorldCapture(),
        )
        kernel._on_goal_complete = on_goal_complete

        # 30 different steps — each unique to avoid repetition guard
        plan = [{"tool_name": f"tool_{i}", "params": {"step": i}}
                for i in range(30)]

        await kernel.start()
        await kernel.submit_goal("Max iterations test", plan=plan)

        for _ in range(500):
            await asyncio.sleep(0.02)
            if results:
                break

        await kernel.shutdown()

        assert len(results) == 1
        # Loop guard triggers at 25 → goes to EVALUATING
        assert results[0]["steps_completed"] < 30
        assert any("iteration" in e.lower() or "max" in e.lower()
                    for e in results[0]["errors"])


# ── TestSmartExecutor ──


class TestSmartExecutor:
    """SmartExecutor tool dispatch tests."""

    @pytest.mark.asyncio
    async def test_execute_async_tool(self):
        """Async tool executes and returns ToolResult."""
        async def my_tool(**kwargs):
            return {"result": "ok"}

        executor = SmartExecutor(tool_registry={"my_tool": my_tool})
        result = await executor.execute("my_tool", {})
        assert result.success is True
        executor.shutdown()

    @pytest.mark.asyncio
    async def test_execute_sync_tool(self):
        """Sync tool runs in thread pool and returns ToolResult."""
        def sync_tool(**kwargs):
            return {"computed": 42}

        executor = SmartExecutor(tool_registry={"sync_tool": sync_tool})
        result = await executor.execute("sync_tool", {})
        assert result.success is True
        executor.shutdown()

    @pytest.mark.asyncio
    async def test_execute_unknown_tool(self):
        """Unknown tool returns ToolResult.fail."""
        executor = SmartExecutor()
        result = await executor.execute("nonexistent", {})
        assert result.success is False
        assert "Unknown tool" in result.error
        executor.shutdown()

    @pytest.mark.asyncio
    async def test_execute_timeout(self):
        """Slow tool exceeds timeout and returns failure."""
        async def slow(**kwargs):
            await asyncio.sleep(10)
            return {"never": "reached"}

        executor = SmartExecutor(
            tool_registry={"slow": slow},
            default_timeout=0.1,
        )
        result = await executor.execute("slow", {})
        assert result.success is False
        assert "timed out" in result.error
        executor.shutdown()

    @pytest.mark.asyncio
    async def test_register_and_execute(self):
        """Register a tool dynamically, then execute it."""
        executor = SmartExecutor()
        assert "dynamic" not in executor.available_tools

        async def dynamic(**kwargs):
            return {"registered": True}

        executor.register_tool("dynamic", dynamic)
        assert "dynamic" in executor.available_tools

        result = await executor.execute("dynamic", {})
        assert result.success is True
        executor.shutdown()


# ── TestCallbacks ──


class TestCallbacks:
    """Callback mechanism tests."""

    @pytest.mark.asyncio
    async def test_on_state_change_fires(self, kernel):
        """State change callback fires on transitions."""
        transitions = []

        async def on_change(old, new):
            transitions.append((old, new))

        kernel._on_state_change = on_change

        await kernel.start()
        await kernel.submit_goal("Callback test", plan=[
            {"tool_name": "click", "params": {}},
        ])

        for _ in range(200):
            await asyncio.sleep(0.02)
            if kernel.state == KernelState.IDLE and len(transitions) >= 2:
                break

        await kernel.shutdown()

        # Should have seen IDLE->EXECUTING and EXECUTING->EVALUATING etc.
        state_values = [(t[0].value, t[1].value) for t in transitions]
        # At minimum, there was a transition to EXECUTING
        assert any(new == "executing" for _, new in state_values)

    @pytest.mark.asyncio
    async def test_on_action_complete_fires(self, kernel):
        """Action complete callback fires after each tool execution."""
        actions = []

        async def on_action(step, result, score, verdict):
            actions.append({
                "tool": step.tool_name,
                "success": result.success,
                "score": score.composite,
                "verdict": verdict,
            })

        kernel._on_action_complete = on_action

        await kernel.start()
        await kernel.submit_goal("Action CB", plan=[
            {"tool_name": "click", "params": {}},
            {"tool_name": "type_text", "params": {"text": "hi"}},
        ])

        for _ in range(200):
            await asyncio.sleep(0.02)
            if len(actions) >= 2:
                break

        await kernel.shutdown()

        assert len(actions) >= 2
        assert actions[0]["tool"] == "click"
        assert actions[0]["success"] is True
        assert isinstance(actions[0]["score"], float)
        assert isinstance(actions[0]["verdict"], str)

    @pytest.mark.asyncio
    async def test_on_goal_complete_fires(self, kernel):
        """Goal complete callback fires with result dict."""
        results = []

        async def on_complete(result):
            results.append(result)

        kernel._on_goal_complete = on_complete

        await kernel.start()
        await kernel.submit_goal("Goal CB", plan=[
            {"tool_name": "click", "params": {}},
        ])

        for _ in range(200):
            await asyncio.sleep(0.02)
            if results:
                break

        await kernel.shutdown()

        assert len(results) == 1
        r = results[0]
        assert "goal_id" in r
        assert "success" in r
        assert "avg_score" in r
        assert isinstance(r["avg_score"], float)


# ── TestPauseResume ──


class TestPauseResume:
    """Pause and resume mechanism."""

    @pytest.mark.asyncio
    async def test_pause_and_resume(self, kernel):
        """Kernel pauses execution and resumes when told."""
        await kernel.start()

        # Submit a multi-step goal
        await kernel.submit_goal("Pause test", plan=[
            {"tool_name": "click", "params": {}},
            {"tool_name": "type_text", "params": {"text": "hello"}},
            {"tool_name": "click", "params": {}},
        ])

        # Let it start executing
        for _ in range(50):
            await asyncio.sleep(0.01)
            if kernel.state == KernelState.EXECUTING:
                break

        # Pause
        await kernel.pause()
        assert kernel.state == KernelState.PAUSED

        # Wait a bit — should stay paused
        await asyncio.sleep(0.1)
        assert kernel.state == KernelState.PAUSED

        # Resume
        results = []
        kernel._on_goal_complete = lambda r: results.append(r)
        # Need to make it awaitable
        async def goal_cb(r):
            results.append(r)
        kernel._on_goal_complete = goal_cb

        await kernel.resume()
        assert kernel.state != KernelState.PAUSED

        # Wait for completion
        for _ in range(200):
            await asyncio.sleep(0.02)
            if results:
                break

        await kernel.shutdown()


# ── TestStats ──


class TestStats:
    """Kernel statistics tracking."""

    @pytest.mark.asyncio
    async def test_stats_update(self, kernel):
        """Stats reflect correct cycle_count and total_actions."""
        results = []

        async def on_complete(r):
            results.append(r)

        kernel._on_goal_complete = on_complete

        # Check initial stats
        s = kernel.stats
        assert s["state"] == "idle"
        assert s["cycle_count"] == 0
        assert s["total_actions"] == 0
        assert s["current_goal"] is None

        await kernel.start()
        await kernel.submit_goal("Stats test", plan=[
            {"tool_name": "click", "params": {}},
            {"tool_name": "type_text", "params": {"text": "a"}},
        ])

        for _ in range(200):
            await asyncio.sleep(0.02)
            if results:
                break

        await kernel.shutdown()

        s = kernel.stats
        assert s["cycle_count"] >= 2
        assert s["total_actions"] >= 2
