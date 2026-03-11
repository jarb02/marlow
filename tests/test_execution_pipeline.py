"""Tests for ExecutionPipeline and SecurityGate."""

import asyncio
import time

import pytest

from marlow.kernel.execution_pipeline import (
    ExecutionPipeline,
    PipelineResult,
    _INPUT_TOOLS,
    _READ_ONLY_TOOLS,
)
from marlow.kernel.security.gate import (
    SecurityGate,
    SecurityResult,
    TRUST_OBSERVE,
    TRUST_LAUNCH,
    TRUST_INTERACT,
    TRUST_COMMAND,
    TRUST_DESTRUCTIVE,
    get_trust_level,
)


# ── Helpers ──


def _make_tool_map():
    """Minimal tool map for testing."""
    return {
        "list_windows": lambda **kw: {"success": True, "windows": []},
        "take_screenshot": lambda **kw: {"success": True, "path": "/tmp/s.png"},
        "open_application": lambda **kw: {"success": True, "pid": 123},
        "type_text": lambda **kw: {"success": True},
        "click": lambda **kw: {"success": True},
        "run_command": lambda **kw: {"success": True, "output": "ok"},
        "launch_in_shadow": lambda **kw: {"success": False, "error": "no compositor"},
        "focus_window": lambda **kw: {"success": True},
        "failing_tool": lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")),
    }


def _run(coro):
    """Run async in sync test (Python 3.14 compatible)."""
    return asyncio.run(coro)


# ══════════════════════════════════════════════════════════════
# SecurityGate Tests
# ══════════════════════════════════════════════════════════════


class TestTrustLevels:
    """Trust level classification for tools."""

    def test_read_only_tools_are_observe(self):
        assert get_trust_level("list_windows") == TRUST_OBSERVE
        assert get_trust_level("take_screenshot") == TRUST_OBSERVE
        assert get_trust_level("system_info") == TRUST_OBSERVE
        assert get_trust_level("get_ui_tree") == TRUST_OBSERVE

    def test_launch_tools(self):
        assert get_trust_level("open_application") == TRUST_LAUNCH
        assert get_trust_level("focus_window") == TRUST_LAUNCH
        assert get_trust_level("launch_in_shadow") == TRUST_LAUNCH
        assert get_trust_level("memory_save") == TRUST_LAUNCH

    def test_interact_tools(self):
        assert get_trust_level("type_text") == TRUST_INTERACT
        assert get_trust_level("click") == TRUST_INTERACT
        assert get_trust_level("press_key") == TRUST_INTERACT
        assert get_trust_level("manage_window") == TRUST_INTERACT

    def test_command_tools(self):
        assert get_trust_level("run_command") == TRUST_COMMAND
        assert get_trust_level("speak") == TRUST_COMMAND
        assert get_trust_level("workflow_run") == TRUST_COMMAND

    def test_unknown_defaults_to_interact(self):
        assert get_trust_level("nonexistent_tool_xyz") == TRUST_INTERACT


class TestSecurityGateOrigins:
    """Origin-based permission checks."""

    def test_gemini_allows_command_tier(self):
        gate = SecurityGate()
        result = _run(gate.check("run_command", {"command": "ls"}, origin="gemini"))
        assert result.allowed

    def test_claude_allows_command_tier(self):
        gate = SecurityGate()
        result = _run(gate.check("run_command", {"command": "ls"}, origin="claude"))
        assert result.allowed

    def test_proactive_allows_observe(self):
        gate = SecurityGate()
        result = _run(gate.check("list_windows", {}, origin="proactive"))
        assert result.allowed

    def test_proactive_allows_launch(self):
        gate = SecurityGate()
        result = _run(gate.check("open_application", {"app_name": "firefox"}, origin="proactive"))
        assert result.allowed

    def test_proactive_suggests_interact(self):
        gate = SecurityGate()
        result = _run(gate.check("type_text", {"text": "hello"}, origin="proactive"))
        assert not result.allowed
        assert result.suggestion_only is True

    def test_proactive_blocks_command(self):
        gate = SecurityGate()
        result = _run(gate.check("run_command", {"command": "ls"}, origin="proactive"))
        assert not result.allowed
        assert result.suggestion_only is False

    def test_goal_engine_allows_all(self):
        gate = SecurityGate()
        result = _run(gate.check("run_command", {"command": "ls"}, origin="goal_engine"))
        assert result.allowed


class TestSecurityGateInjection:
    """Prompt injection detection in params."""

    def test_clean_params_pass(self):
        gate = SecurityGate()
        result = _run(gate.check("type_text", {"text": "Hello world"}, origin="gemini"))
        assert result.allowed

    def test_short_params_skip_scan(self):
        gate = SecurityGate()
        result = _run(gate.check("type_text", {"text": "hi"}, origin="gemini"))
        assert result.allowed

    def test_gate_with_no_subsystems(self):
        gate = SecurityGate()
        result = _run(gate.check("list_windows", {}, origin="user"))
        assert result.allowed
        assert result.trust_level == TRUST_OBSERVE


class TestSecurityResult:
    """SecurityResult dataclass."""

    def test_default_sanitized_params(self):
        r = SecurityResult(allowed=True)
        assert r.sanitized_params == {}

    def test_custom_sanitized_params(self):
        r = SecurityResult(allowed=True, sanitized_params={"a": 1})
        assert r.sanitized_params == {"a": 1}


# ══════════════════════════════════════════════════════════════
# ExecutionPipeline Tests
# ══════════════════════════════════════════════════════════════


class TestPipelineBasicExecution:
    """Basic tool execution through the pipeline."""

    def test_simple_read_only_tool(self):
        pipeline = ExecutionPipeline(tool_map=_make_tool_map())
        result = _run(pipeline.execute("list_windows", {}))
        assert result.success
        assert isinstance(result.data, dict)
        assert result.tool_name == "list_windows"

    def test_unknown_tool_returns_error(self):
        pipeline = ExecutionPipeline(tool_map=_make_tool_map())
        result = _run(pipeline.execute("nonexistent_tool", {}))
        assert not result.success
        assert "Unknown tool" in result.error

    def test_tool_exception_returns_error(self):
        pipeline = ExecutionPipeline(tool_map=_make_tool_map())
        result = _run(pipeline.execute("failing_tool", {}))
        assert not result.success
        assert "boom" in result.error

    def test_origin_is_preserved(self):
        pipeline = ExecutionPipeline(tool_map=_make_tool_map())
        result = _run(pipeline.execute("list_windows", {}, origin="gemini"))
        assert result.origin == "gemini"

    def test_duration_is_positive(self):
        pipeline = ExecutionPipeline(tool_map=_make_tool_map())
        result = _run(pipeline.execute("list_windows", {}))
        assert result.duration_ms > 0

    def test_to_dict_includes_success(self):
        pipeline = ExecutionPipeline(tool_map=_make_tool_map())
        result = _run(pipeline.execute("list_windows", {}))
        d = result.to_dict()
        assert d["success"] is True
        assert "duration_ms" in d


class TestPipelineShadowFallback:
    """launch_in_shadow -> open_application fallback."""

    def test_shadow_fallback_to_open_application(self):
        pipeline = ExecutionPipeline(tool_map=_make_tool_map())
        result = _run(pipeline.execute(
            "launch_in_shadow", {"command": "firefox"}, origin="gemini",
        ))
        assert result.success
        assert result.data.get("note") == "Opened visibly (shadow mode unavailable)"

    def test_shadow_fallback_when_tool_missing(self):
        tools = _make_tool_map()
        del tools["launch_in_shadow"]  # not registered, but open_application is
        pipeline = ExecutionPipeline(tool_map=tools)
        result = _run(pipeline.execute(
            "launch_in_shadow", {"command": "firefox"}, origin="gemini",
        ))
        assert result.success

    def test_shadow_no_fallback_if_open_missing(self):
        tools = {"launch_in_shadow": lambda **kw: {"success": False}}
        pipeline = ExecutionPipeline(tool_map=tools)
        result = _run(pipeline.execute(
            "launch_in_shadow", {"command": "firefox"}, origin="gemini",
        ))
        assert not result.success


class TestPipelineSecurityGate:
    """SecurityGate integration with pipeline."""

    def test_blocked_tool_returns_error(self):
        gate = SecurityGate()
        pipeline = ExecutionPipeline(
            tool_map=_make_tool_map(),
            security_gate=gate,
        )
        result = _run(pipeline.execute(
            "run_command", {"command": "ls"}, origin="proactive",
        ))
        assert not result.success
        assert "blocked" in result.error.lower() or "Proactive" in result.error

    def test_allowed_tool_passes_gate(self):
        gate = SecurityGate()
        pipeline = ExecutionPipeline(
            tool_map=_make_tool_map(),
            security_gate=gate,
        )
        result = _run(pipeline.execute("list_windows", {}, origin="proactive"))
        assert result.success


class TestPipelineSubsystems:
    """Optional subsystem integration."""

    def test_blackboard_receives_state(self):
        state = {}

        class FakeBlackboard:
            def set(self, key, value, source=""):
                state[key] = value

        pipeline = ExecutionPipeline(
            tool_map=_make_tool_map(),
            blackboard=FakeBlackboard(),
        )
        _run(pipeline.execute("open_application", {"app_name": "firefox"}))
        assert state.get("world.active_tool") == "open_application"
        assert state.get("world.active_app") == "firefox"

    def test_weather_pause_on_tormenta(self):
        paused = {"count": 0}

        class FakeWeather:
            class FakeReport:
                should_pause = True
                recommended_delay = 0.01  # tiny for tests
            def get_report(self):
                paused["count"] += 1
                return self.FakeReport()
            def record_error(self):
                pass
            def record_window_change(self):
                pass

        pipeline = ExecutionPipeline(
            tool_map=_make_tool_map(),
            desktop_weather=FakeWeather(),
        )
        _run(pipeline.execute("open_application", {"app_name": "firefox"}))
        assert paused["count"] >= 1

    def test_memory_records_success(self):
        recorded = []

        class FakeMemory:
            def remember_short(self, data, category="", tool_name=""):
                recorded.append(data)

        pipeline = ExecutionPipeline(
            tool_map=_make_tool_map(),
            memory=FakeMemory(),
        )
        _run(pipeline.execute("list_windows", {}))
        assert len(recorded) == 1
        assert recorded[0]["tool"] == "list_windows"
        assert recorded[0]["success"] is True

    def test_memory_records_failure(self):
        recorded = []

        class FakeMemory:
            def remember_short(self, data, category="", tool_name=""):
                recorded.append(data)

        pipeline = ExecutionPipeline(
            tool_map=_make_tool_map(),
            memory=FakeMemory(),
        )
        _run(pipeline.execute("failing_tool", {}))
        assert len(recorded) == 1
        assert recorded[0]["success"] is False

    def test_focus_handler_called_for_input_tools(self):
        focused = {"called": False}

        async def fake_focus(tool_name, params):
            focused["called"] = True

        pipeline = ExecutionPipeline(
            tool_map=_make_tool_map(),
            focus_handler=fake_focus,
        )
        _run(pipeline.execute("type_text", {"text": "hello"}))
        assert focused["called"] is True

    def test_focus_handler_not_called_for_read_tools(self):
        focused = {"called": False}

        async def fake_focus(tool_name, params):
            focused["called"] = True

        pipeline = ExecutionPipeline(
            tool_map=_make_tool_map(),
            focus_handler=fake_focus,
        )
        _run(pipeline.execute("list_windows", {}))
        assert focused["called"] is False

    def test_snapshot_called_for_non_readonly(self):
        snaps = {"count": 0}

        async def fake_snapshot():
            snaps["count"] += 1

        pipeline = ExecutionPipeline(
            tool_map=_make_tool_map(),
            snapshot_handler=fake_snapshot,
        )
        _run(pipeline.execute("open_application", {"app_name": "firefox"}))
        # before + after = 2 snapshots
        assert snaps["count"] == 2

    def test_snapshot_not_called_for_readonly(self):
        snaps = {"count": 0}

        async def fake_snapshot():
            snaps["count"] += 1

        pipeline = ExecutionPipeline(
            tool_map=_make_tool_map(),
            snapshot_handler=fake_snapshot,
        )
        _run(pipeline.execute("list_windows", {}))
        assert snaps["count"] == 0

    def test_subsystem_crash_does_not_break_pipeline(self):
        """A crashing blackboard should not prevent tool execution."""
        class CrashingBlackboard:
            def set(self, *a, **kw):
                raise RuntimeError("blackboard on fire")

        pipeline = ExecutionPipeline(
            tool_map=_make_tool_map(),
            blackboard=CrashingBlackboard(),
        )
        result = _run(pipeline.execute("list_windows", {}))
        assert result.success  # pipeline continues despite crash


class TestPipelineInterrupts:
    """InterruptManager integration."""

    def test_interrupt_detected_on_window_change(self):
        class FakeTracker:
            class FakeChange:
                change_type = "window_appeared"
                window_title = "Error"
            def detect_changes(self):
                return [self.FakeChange()]

        class FakeInterruptMgr:
            class FakeInterrupt:
                priority = 1
                source = "dialog"
            def classify_event(self, event_type="", title="", message=""):
                return self.FakeInterrupt()
            def should_interrupt(self, interrupt):
                return True

        pipeline = ExecutionPipeline(
            tool_map=_make_tool_map(),
            window_tracker=FakeTracker(),
            interrupt_manager=FakeInterruptMgr(),
        )
        result = _run(pipeline.execute("open_application", {"app_name": "firefox"}))
        assert result.success  # tool itself succeeded
        assert result.interrupted is True
        assert result.interrupt_priority == 1

    def test_no_interrupt_on_readonly(self):
        class FakeTracker:
            def detect_changes(self):
                raise RuntimeError("should not be called")

        pipeline = ExecutionPipeline(
            tool_map=_make_tool_map(),
            window_tracker=FakeTracker(),
        )
        result = _run(pipeline.execute("list_windows", {}))
        assert result.success
        assert result.interrupted is False


class TestPipelineResult:
    """PipelineResult to_dict conversion."""

    def test_to_dict_with_interrupt(self):
        r = PipelineResult(
            success=True,
            data={"windows": []},
            tool_name="list_windows",
            origin="gemini",
            interrupted=True,
            interrupt_priority=1,
        )
        d = r.to_dict()
        assert d["interrupted"] is True
        assert d["interrupt_priority"] == 1

    def test_to_dict_without_interrupt(self):
        r = PipelineResult(success=True, data={"ok": True}, tool_name="x", origin="y")
        d = r.to_dict()
        assert "interrupted" not in d

    def test_to_dict_with_error(self):
        r = PipelineResult(success=False, error="fail", tool_name="x", origin="y")
        d = r.to_dict()
        assert d["error"] == "fail"
        assert d["success"] is False
