"""Tests for marlow.kernel.goal_engine, plan_validator, success_checker, replan."""

import os
import tempfile

import pytest

from marlow.kernel.goal_engine import (
    GoalEngine,
    GoalResult,
    GoalState,
    Plan,
    PlanStep,
)
from marlow.kernel.plan_validator import PlanValidator, ValidationResult
from marlow.kernel.replan import COMMON_FAILURE_HANDLERS, ReplanDecision
from marlow.kernel.success_checker import SuccessChecker
from marlow.kernel.types import ToolResult
from marlow.kernel.world_state import WindowInfo, WorldStateSnapshot


# ── Helpers ──


def _make_step(
    tool_name="click",
    step_id=None,
    description="",
    risk="low",
    skippable=False,
    success_check=None,
    max_retries=2,
    **extra,
):
    return PlanStep(
        id=step_id or f"step_{tool_name}",
        tool_name=tool_name,
        params=extra.get("params", {}),
        description=description or f"Do {tool_name}",
        risk=risk,
        skippable=skippable,
        success_check=success_check,
        max_retries=max_retries,
    )


def _make_plan(steps=None, goal_text="Test goal", **kwargs):
    return Plan(
        goal_id="test_goal",
        goal_text=goal_text,
        steps=steps or [],
        **kwargs,
    )


async def _mock_tool_executor(tool_name: str, params: dict) -> ToolResult:
    if tool_name == "fail_tool":
        return ToolResult.fail("simulated error", tool_name=tool_name)
    if tool_name == "timeout_tool":
        return ToolResult.fail("timeout waiting for element", tool_name=tool_name)
    if tool_name == "not_found_tool":
        return ToolResult.fail("element not found in tree", tool_name=tool_name)
    if tool_name == "critical_fail":
        return ToolResult.fail("critical system error", tool_name=tool_name)
    return ToolResult.ok({"status": "ok"}, tool_name=tool_name)


# ── Fixtures ──


@pytest.fixture
def simple_plan():
    return _make_plan(steps=[
        _make_step("click", step_id="s1", description="Click button"),
        _make_step("type_text", step_id="s2", description="Type hello",
                   params={"text": "hello"}),
        _make_step("take_screenshot", step_id="s3", description="Screenshot"),
    ])


@pytest.fixture
def failing_plan():
    return _make_plan(steps=[
        _make_step("click", step_id="s1", description="Click button"),
        _make_step("fail_tool", step_id="s2", description="This will fail",
                   max_retries=0),
        _make_step("type_text", step_id="s3", description="Type something"),
    ])


@pytest.fixture
def engine():
    return GoalEngine(tool_executor=_mock_tool_executor)


# ── TestGoalEngine ──


class TestGoalEngine:
    """GoalEngine lifecycle tests."""

    @pytest.mark.asyncio
    async def test_execute_with_prebuilt_plan(self, engine, simple_plan):
        """Pre-built 3-step plan, all succeed -> COMPLETED."""
        result = await engine.execute_goal(
            "Test goal", pre_built_plan=simple_plan,
        )
        assert result.success is True
        assert result.steps_completed == 3
        assert result.steps_total == 3
        assert engine.state == GoalState.COMPLETED

    @pytest.mark.asyncio
    async def test_state_progression(self, engine, simple_plan):
        """Verify states go through expected progression."""
        await engine.execute_goal("Test", pre_built_plan=simple_plan)

        history = engine.state_history
        # Must visit VALIDATING_PLAN, EXECUTING, VERIFYING_STEP, COMPLETED
        state_values = [s.value for s in history]
        assert "validating_plan" in state_values
        assert "executing" in state_values
        assert "verifying_step" in state_values
        assert "completed" in state_values

    @pytest.mark.asyncio
    async def test_failing_step_retries(self):
        """Step fails with transient error, retries, then moves on."""
        plan = _make_plan(steps=[
            _make_step("timeout_tool", step_id="s1",
                       description="Will timeout", max_retries=2),
            _make_step("click", step_id="s2", description="Click"),
        ])
        engine = GoalEngine(tool_executor=_mock_tool_executor)
        result = await engine.execute_goal("Retry test", pre_built_plan=plan)

        # timeout_tool should have retried (transient "timeout" error)
        step = plan.steps[0]
        assert step.retries > 0

    @pytest.mark.asyncio
    async def test_failing_step_with_known_handler(self):
        """Error contains 'element not found' -> handle_known fires."""
        plan = _make_plan(steps=[
            _make_step("not_found_tool", step_id="s1",
                       description="Element missing", max_retries=2),
            _make_step("click", step_id="s2", description="Click"),
        ])
        engine = GoalEngine(tool_executor=_mock_tool_executor)
        result = await engine.execute_goal("Handler test", pre_built_plan=plan)

        # Step should have been retried after handler
        step = plan.steps[0]
        assert step.retries > 0

    @pytest.mark.asyncio
    async def test_skippable_step_skipped(self):
        """Low-risk skippable step fails -> skipped."""
        plan = _make_plan(steps=[
            _make_step("fail_tool", step_id="s1", description="Skippable",
                       skippable=True, risk="low", max_retries=0),
            _make_step("click", step_id="s2", description="Click"),
        ])
        engine = GoalEngine(tool_executor=_mock_tool_executor)
        result = await engine.execute_goal("Skip test", pre_built_plan=plan)

        assert plan.steps[0].status == "skipped"
        assert plan.steps[1].status == "completed"

    @pytest.mark.asyncio
    async def test_critical_step_aborts(self):
        """Critical risk step fails -> FAILED."""
        plan = _make_plan(steps=[
            _make_step("fail_tool", step_id="s1", description="Critical op",
                       risk="critical", max_retries=0),
            _make_step("click", step_id="s2", description="Never reached"),
        ])
        engine = GoalEngine(tool_executor=_mock_tool_executor)
        result = await engine.execute_goal("Abort test", pre_built_plan=plan)

        assert result.success is False
        assert engine.state == GoalState.FAILED
        assert any("Aborting" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_no_plan_generator_fails(self):
        """No LLM and no pre-built plan -> FAILED."""
        engine = GoalEngine(tool_executor=_mock_tool_executor)
        result = await engine.execute_goal("Do something")

        assert result.success is False
        assert engine.state == GoalState.FAILED
        assert any("No plan generator" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_empty_plan_invalid(self):
        """Plan with no steps -> validation fails."""
        plan = _make_plan(steps=[])
        engine = GoalEngine(tool_executor=_mock_tool_executor)
        result = await engine.execute_goal("Empty", pre_built_plan=plan)

        assert result.success is False
        assert any("no steps" in e.lower() for e in result.errors)

    @pytest.mark.asyncio
    async def test_too_many_steps_invalid(self):
        """Plan with 25 steps -> validation fails."""
        steps = [
            _make_step("click", step_id=f"s{i}", description=f"Step {i}")
            for i in range(25)
        ]
        plan = _make_plan(steps=steps)
        engine = GoalEngine(tool_executor=_mock_tool_executor)
        result = await engine.execute_goal("Too many", pre_built_plan=plan)

        assert result.success is False
        assert any("max" in e.lower() for e in result.errors)

    @pytest.mark.asyncio
    async def test_progress_callback_fires(self):
        """Progress callback fires with (step_num, total, description)."""
        progress = []

        async def on_progress(step_num, total, description):
            progress.append((step_num, total, description))

        plan = _make_plan(steps=[
            _make_step("click", step_id="s1", description="Click A"),
            _make_step("click", step_id="s2", description="Click B"),
        ])
        engine = GoalEngine(
            tool_executor=_mock_tool_executor,
            progress_callback=on_progress,
        )
        await engine.execute_goal("Progress test", pre_built_plan=plan)

        assert len(progress) == 2
        assert progress[0] == (1, 2, "Click A")
        assert progress[1] == (2, 2, "Click B")

    @pytest.mark.asyncio
    async def test_confirmation_flow_approved(self):
        """Plan requires confirmation, handler returns True -> executes."""
        plan = _make_plan(steps=[
            _make_step("click", step_id="s1", description="Click",
                       params={"command": "rm -rf /tmp/test"}),
        ])
        plan.requires_confirmation = True

        async def approve(p):
            return True

        engine = GoalEngine(
            tool_executor=_mock_tool_executor,
            confirmation_handler=approve,
        )
        result = await engine.execute_goal("Confirm test", pre_built_plan=plan)

        assert result.success is True

    @pytest.mark.asyncio
    async def test_confirmation_flow_denied(self):
        """Handler returns False -> CANCELLED."""
        plan = _make_plan(steps=[
            _make_step("click", step_id="s1", description="Click"),
        ])
        plan.requires_confirmation = True

        async def deny(p):
            return False

        engine = GoalEngine(
            tool_executor=_mock_tool_executor,
            confirmation_handler=deny,
        )
        result = await engine.execute_goal("Deny test", pre_built_plan=plan)

        assert result.success is False
        assert engine.state == GoalState.CANCELLED

    @pytest.mark.asyncio
    async def test_replan_on_failure(self):
        """Plan generator called again after failure, new steps replace remaining."""
        call_count = 0

        async def mock_generator(goal_text, context):
            nonlocal call_count
            call_count += 1
            if context.get("replan"):
                # Replanned: return a working step
                return _make_plan(steps=[
                    _make_step("click", step_id="replan_s1",
                               description="Replanned click"),
                ])
            return _make_plan(steps=[
                _make_step("fail_tool", step_id="s1",
                           description="Will fail", max_retries=0),
            ])

        engine = GoalEngine(
            plan_generator=mock_generator,
            tool_executor=_mock_tool_executor,
        )
        result = await engine.execute_goal("Replan test")

        assert call_count >= 2  # Original + replan
        assert result.replan_count >= 1

    @pytest.mark.asyncio
    async def test_max_replans_exhausted(self):
        """After 3 replans, stops replanning and moves on."""
        call_count = 0

        async def always_fail_generator(goal_text, context):
            nonlocal call_count
            call_count += 1
            return _make_plan(steps=[
                _make_step("fail_tool", step_id=f"s{call_count}",
                           description="Always fails", max_retries=0),
            ])

        engine = GoalEngine(
            plan_generator=always_fail_generator,
            tool_executor=_mock_tool_executor,
        )
        result = await engine.execute_goal("Exhaust replans")

        assert result.replan_count <= GoalEngine.MAX_REPLANS
        assert result.success is False


# ── TestPlanValidator ──


class TestPlanValidator:
    """PlanValidator structural and pattern checks."""

    def test_valid_plan(self):
        """3 normal steps -> is_valid=True."""
        tools = ["click", "type_text", "take_screenshot"]
        validator = PlanValidator(available_tools=tools)
        plan = _make_plan(steps=[
            _make_step("click", step_id="s1"),
            _make_step("type_text", step_id="s2"),
            _make_step("take_screenshot", step_id="s3"),
        ])
        result = validator.validate(plan)
        assert result.is_valid is True
        assert len(result.errors) == 0

    def test_missing_tool_name(self):
        """Step without tool_name -> error."""
        validator = PlanValidator()
        plan = _make_plan(steps=[
            PlanStep(id="s1", tool_name=""),
        ])
        result = validator.validate(plan)
        assert result.is_valid is False
        assert any("missing tool_name" in e for e in result.errors)

    def test_unknown_tool(self):
        """Tool not in available list -> error."""
        validator = PlanValidator(available_tools=["click", "type_text"])
        plan = _make_plan(steps=[
            _make_step("nonexistent_tool", step_id="s1"),
        ])
        result = validator.validate(plan)
        assert result.is_valid is False
        assert any("unknown tool" in e for e in result.errors)

    def test_duplicate_ids(self):
        """Two steps with same id -> error."""
        validator = PlanValidator()
        plan = _make_plan(steps=[
            _make_step("click", step_id="dup"),
            _make_step("type_text", step_id="dup"),
        ])
        result = validator.validate(plan)
        assert result.is_valid is False
        assert any("duplicate id" in e for e in result.errors)

    def test_dangerous_pattern_enforced(self):
        """Params contain 'rm -rf' -> step marked critical."""
        validator = PlanValidator()
        step = PlanStep(
            id="s1", tool_name="run_command",
            params={"command": "rm -rf /tmp/test"},
        )
        plan = _make_plan(steps=[step])
        result = validator.validate(plan)

        assert result.is_valid is True  # Valid but dangerous
        assert step.risk == "critical"
        assert step.requires_confirmation is True
        assert plan.requires_confirmation is True
        assert len(result.warnings) > 0

    def test_max_steps_exceeded(self):
        """25 steps exceeds max_steps=20 -> error."""
        validator = PlanValidator(max_steps=20)
        steps = [
            _make_step("click", step_id=f"s{i}") for i in range(25)
        ]
        plan = _make_plan(steps=steps)
        result = validator.validate(plan)
        assert result.is_valid is False
        assert any("max" in e.lower() for e in result.errors)

    def test_duration_warning(self):
        """High total duration -> warning (not error)."""
        validator = PlanValidator(max_duration_ms=10_000)
        steps = [
            PlanStep(
                id=f"s{i}", tool_name="click",
                estimated_duration_ms=5000.0,
            )
            for i in range(3)
        ]
        plan = _make_plan(steps=steps)
        result = validator.validate(plan)
        assert result.is_valid is True  # Warnings don't block
        assert len(result.warnings) > 0
        assert any("duration" in w.lower() for w in result.warnings)


# ── TestSuccessChecker ──


class TestSuccessChecker:
    """SuccessChecker programmatic check tests."""

    @pytest.mark.asyncio
    async def test_none_check_passes(self):
        """type 'none' -> True."""
        checker = SuccessChecker()
        assert await checker.check({"type": "none"}) is True

    @pytest.mark.asyncio
    async def test_window_exists_passes(self):
        """World has matching window -> True."""
        win = WindowInfo(hwnd=1, title="Notepad - file.txt",
                         process_name="notepad.exe")
        world = WorldStateSnapshot(
            cycle_number=1, timestamp_mono=100.0,
            timestamp_utc="2026-03-02T12:00:00.000Z",
            open_windows=(win,), active_window=win,
        )
        checker = SuccessChecker(world_state_getter=lambda: world)
        result = await checker.check({
            "type": "window_exists",
            "params": {"title_contains": "Notepad"},
        })
        assert result is True

    @pytest.mark.asyncio
    async def test_window_exists_fails(self):
        """World doesn't have matching window -> False."""
        win = WindowInfo(hwnd=1, title="Calculator",
                         process_name="calc.exe")
        world = WorldStateSnapshot(
            cycle_number=1, timestamp_mono=100.0,
            timestamp_utc="2026-03-02T12:00:00.000Z",
            open_windows=(win,), active_window=win,
        )
        checker = SuccessChecker(world_state_getter=lambda: world)
        result = await checker.check({
            "type": "window_exists",
            "params": {"title_contains": "Notepad"},
        })
        assert result is False

    @pytest.mark.asyncio
    async def test_file_exists(self):
        """Real temp file -> True, nonexistent -> False."""
        checker = SuccessChecker()

        # Existing file
        with tempfile.NamedTemporaryFile(delete=False) as f:
            path = f.name
        try:
            result = await checker.check({
                "type": "file_exists",
                "params": {"path": path},
            })
            assert result is True
        finally:
            os.unlink(path)

        # Non-existent file
        result = await checker.check({
            "type": "file_exists",
            "params": {"path": "/nonexistent/file.txt"},
        })
        assert result is False

    @pytest.mark.asyncio
    async def test_all_of(self):
        """Both pass -> True, one fails -> False."""
        checker = SuccessChecker()

        # Both pass
        result = await checker.check({
            "type": "all_of",
            "params": {
                "checks": [
                    {"type": "none"},
                    {"type": "none"},
                ],
            },
        })
        assert result is True

        # One fails
        result = await checker.check({
            "type": "all_of",
            "params": {
                "checks": [
                    {"type": "none"},
                    {"type": "file_exists", "params": {"path": "/nope"}},
                ],
            },
        })
        assert result is False

    @pytest.mark.asyncio
    async def test_any_of(self):
        """One passes -> True, none pass -> False."""
        checker = SuccessChecker()

        # One passes
        result = await checker.check({
            "type": "any_of",
            "params": {
                "checks": [
                    {"type": "file_exists", "params": {"path": "/nope"}},
                    {"type": "none"},
                ],
            },
        })
        assert result is True

        # None pass (both file_exists on non-existent paths)
        result = await checker.check({
            "type": "any_of",
            "params": {
                "checks": [
                    {"type": "file_exists", "params": {"path": "/nope1"}},
                    {"type": "file_exists", "params": {"path": "/nope2"}},
                ],
            },
        })
        assert result is False


# ── TestReplanDecision ──


class TestReplanDecision:
    """ReplanDecision failure handling logic."""

    def test_transient_retries(self):
        """Transient 'window not ready' error + retries left -> 'retry'."""
        step = _make_step("tool", max_retries=2)
        result = ReplanDecision.decide(
            step, "window not ready yet",
            retry_count=0, max_retries=2,
        )
        assert result == "retry"

    def test_known_handler(self):
        """'element not found' -> 'handle_known'."""
        step = _make_step("tool", max_retries=2)
        result = ReplanDecision.decide(
            step, "element not found in UIA tree",
            retry_count=0, max_retries=2,
        )
        assert result == "handle_known"

    def test_check_passed_skips(self):
        """Check passed despite error -> 'skip'."""
        step = _make_step("tool", max_retries=0)
        result = ReplanDecision.decide(
            step, "some unknown error",
            retry_count=0, max_retries=0,
            check_passed=True,
        )
        assert result == "skip"

    def test_critical_aborts(self):
        """Critical risk -> 'abort'."""
        step = _make_step("tool", risk="critical", max_retries=0)
        result = ReplanDecision.decide(
            step, "some error",
            retry_count=0, max_retries=0,
        )
        assert result == "abort"

    def test_default_replan(self):
        """Unknown error, non-critical, no retries -> 'replan'."""
        step = _make_step("tool", risk="medium", max_retries=0)
        result = ReplanDecision.decide(
            step, "completely unexpected error",
            retry_count=0, max_retries=0,
        )
        assert result == "replan"

    def test_get_handler(self):
        """Returns correct handler list for known errors."""
        handler = ReplanDecision.get_handler("element not found in tree")
        assert len(handler) > 0
        assert handler[0]["tool"] == "wait_for_idle"

        # Unknown error returns empty
        handler = ReplanDecision.get_handler("alien invasion")
        assert handler == []
