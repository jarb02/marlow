"""Tests for marlow.kernel.tool_wrapper."""

import asyncio

import pytest

from marlow.kernel.tool_wrapper import wrap_tool_call, wrap_tool_call_async
from marlow.kernel.types import ToolResult


# ── Sync wrapper tests ──


class TestWrapToolCallSync:
    """Tests for the synchronous wrap_tool_call."""

    def test_string_result(self):
        """Tool returning a string -> success with string data."""
        def tool():
            return "hello world"

        result = wrap_tool_call("system_info", tool)
        assert result.success is True
        assert result.data == "hello world"
        assert result.error is None
        assert result.tool_name == "system_info"
        assert result.risk_level == "safe"

    def test_dict_result(self):
        """Tool returning a normal dict -> success."""
        def tool():
            return {"window": "Notepad", "pid": 1234}

        result = wrap_tool_call("list_windows", tool)
        assert result.success is True
        assert result.data == {"window": "Notepad", "pid": 1234}
        assert result.error is None

    def test_dict_with_error(self):
        """Tool returning dict with 'error' key -> failure."""
        def tool():
            return {"error": "Window not found", "details": "no match"}

        result = wrap_tool_call("focus_window", tool)
        assert result.success is False
        assert result.error == "Window not found"
        assert result.data == {"error": "Window not found", "details": "no match"}

    def test_list_result(self):
        """Tool returning a list -> success with list data."""
        def tool():
            return [{"name": "Notepad"}, {"name": "Chrome"}]

        result = wrap_tool_call("list_windows", tool)
        assert result.success is True
        assert isinstance(result.data, list)
        assert len(result.data) == 2

    def test_none_result(self):
        """Tool returning None -> success with None data."""
        def tool():
            return None

        result = wrap_tool_call("kill_switch", tool)
        assert result.success is True
        assert result.data is None

    def test_exception(self):
        """Tool raising exception -> failure with error message."""
        def tool():
            raise TypeError("expected str, got int")

        result = wrap_tool_call("click", tool)
        assert result.success is False
        assert result.data is None
        assert "TypeError" in result.error
        assert "expected str, got int" in result.error

    def test_duration_measured(self):
        """Duration should be positive for any call."""
        def tool():
            return "ok"

        result = wrap_tool_call("system_info", tool)
        assert result.duration_ms > 0

    def test_duration_on_exception(self):
        """Duration should be measured even when tool raises."""
        def tool():
            raise RuntimeError("boom")

        result = wrap_tool_call("click", tool)
        assert result.duration_ms > 0

    def test_risk_from_map(self):
        """Risk level should come from TOOL_RISK_MAP."""
        def tool():
            return {}

        # safe tool
        r1 = wrap_tool_call("take_screenshot", tool)
        assert r1.risk_level == "safe"

        # moderate tool
        r2 = wrap_tool_call("click", tool)
        assert r2.risk_level == "moderate"

        # dangerous tool
        r3 = wrap_tool_call("manage_window", tool)
        assert r3.risk_level == "dangerous"

        # critical tool
        r4 = wrap_tool_call("run_command", tool)
        assert r4.risk_level == "critical"

    def test_unknown_tool_defaults_dangerous(self):
        """Unknown tool names should default to 'dangerous'."""
        def tool():
            return "ok"

        result = wrap_tool_call("nonexistent_tool_xyz", tool)
        assert result.risk_level == "dangerous"

    def test_args_forwarded(self):
        """Positional and keyword args should reach the tool."""
        def tool(x, y, mode="default"):
            return {"x": x, "y": y, "mode": mode}

        result = wrap_tool_call("click", tool, 100, 200, mode="silent")
        assert result.success is True
        assert result.data == {"x": 100, "y": 200, "mode": "silent"}

    def test_result_is_frozen(self):
        """Returned ToolResult should be immutable."""
        def tool():
            return "ok"

        result = wrap_tool_call("system_info", tool)
        with pytest.raises(AttributeError):
            result.success = False

    def test_dict_error_falsy_not_failure(self):
        """Dict with error=None or error='' should be success."""
        def tool_none():
            return {"error": None, "data": "ok"}

        def tool_empty():
            return {"error": "", "data": "ok"}

        r1 = wrap_tool_call("click", tool_none)
        assert r1.success is True

        r2 = wrap_tool_call("click", tool_empty)
        assert r2.success is True


# ── Async wrapper tests ──


class TestWrapToolCallAsync:
    """Tests for the async wrap_tool_call_async."""

    def _run(self, coro):
        """Run a coroutine in a fresh event loop."""
        return asyncio.run(coro)

    def test_async_success(self):
        """Async tool returning dict -> success."""
        async def tool():
            return {"status": "ok"}

        result = self._run(wrap_tool_call_async("take_screenshot", tool))
        assert result.success is True
        assert result.data == {"status": "ok"}
        assert result.risk_level == "safe"

    def test_async_error_dict(self):
        """Async tool returning error dict -> failure."""
        async def tool():
            return {"error": "timeout"}

        result = self._run(wrap_tool_call_async("click", tool))
        assert result.success is False
        assert result.error == "timeout"

    def test_async_exception(self):
        """Async tool raising exception -> failure."""
        async def tool():
            raise ConnectionError("lost connection")

        result = self._run(wrap_tool_call_async("cdp_send", tool))
        assert result.success is False
        assert "ConnectionError" in result.error
        assert "lost connection" in result.error

    def test_async_duration(self):
        """Async wrapper should measure duration."""
        async def tool():
            return "fast"

        result = self._run(wrap_tool_call_async("system_info", tool))
        assert result.duration_ms > 0

    def test_async_args_forwarded(self):
        """Args should be forwarded to async tool."""
        async def tool(target, button="left"):
            return {"target": target, "button": button}

        result = self._run(
            wrap_tool_call_async("click", tool, "Save", button="right")
        )
        assert result.success is True
        assert result.data == {"target": "Save", "button": "right"}

    def test_async_risk_from_map(self):
        """Risk level should come from TOOL_RISK_MAP for async too."""
        async def tool():
            return {}

        r = self._run(wrap_tool_call_async("run_command", tool))
        assert r.risk_level == "critical"
