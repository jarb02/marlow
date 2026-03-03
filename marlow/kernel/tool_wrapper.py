"""Wrapper that converts existing tool results to ToolResult.

Used by the kernel to normalize the inconsistent return types from
96+ MCP tools (strings, dicts, lists, exceptions) into a uniform
ToolResult for scoring and audit.

Does NOT modify any tool implementation — it wraps at the call site.
"""

import inspect
import time
from typing import Any, Callable

from .constants import TOOL_RISK_MAP
from .types import ToolResult


def wrap_tool_call(tool_name: str, func: Callable, *args, **kwargs) -> ToolResult:
    """Execute a tool and convert its result to ToolResult.

    Handles existing return patterns:
    - str: success with string data
    - dict with "error" key: failure
    - dict without "error": success
    - list: success with list data
    - Exception: failure with traceback info

    Parameters
    ----------
    * **tool_name** (str):
        Registered MCP tool name (used for risk lookup).
    * **func** (Callable):
        The tool function to execute (sync).
    * **args**:
        Positional arguments passed to the tool.
    * **kwargs**:
        Keyword arguments passed to the tool.

    Returns
    -------
    ToolResult
        Normalized result with timing, risk level, and success/error.
    """
    risk = TOOL_RISK_MAP.get(tool_name, "dangerous")
    start = time.perf_counter()

    try:
        raw = func(*args, **kwargs)
        duration_ms = (time.perf_counter() - start) * 1000

        # Safety net: if a lambda wrapping an async function slips
        # through to the sync path, close the coroutine to prevent
        # "coroutine was never awaited" warnings and return an error.
        if inspect.iscoroutine(raw):
            raw.close()
            return ToolResult(
                success=False,
                error=(
                    f"Tool '{tool_name}' returned a coroutine in sync "
                    f"context — use SmartExecutor.execute() instead"
                ),
                duration_ms=duration_ms,
                tool_name=tool_name,
                risk_level=risk,
            )

        if isinstance(raw, dict) and raw.get("error"):
            return ToolResult(
                success=False,
                data=raw,
                error=str(raw["error"]),
                duration_ms=duration_ms,
                tool_name=tool_name,
                risk_level=risk,
            )

        return ToolResult(
            success=True,
            data=raw,
            duration_ms=duration_ms,
            tool_name=tool_name,
            risk_level=risk,
        )

    except Exception as e:
        duration_ms = (time.perf_counter() - start) * 1000
        return ToolResult(
            success=False,
            error="{}: {}".format(type(e).__name__, e),
            duration_ms=duration_ms,
            tool_name=tool_name,
            risk_level=risk,
        )


async def wrap_tool_call_async(
    tool_name: str, func: Callable, *args, **kwargs
) -> ToolResult:
    """Execute an async tool and convert its result to ToolResult.

    Same normalization logic as ``wrap_tool_call`` but awaits the
    tool function.

    Parameters
    ----------
    * **tool_name** (str):
        Registered MCP tool name (used for risk lookup).
    * **func** (Callable):
        The async tool function to execute.
    * **args**:
        Positional arguments passed to the tool.
    * **kwargs**:
        Keyword arguments passed to the tool.

    Returns
    -------
    ToolResult
        Normalized result with timing, risk level, and success/error.
    """
    risk = TOOL_RISK_MAP.get(tool_name, "dangerous")
    start = time.perf_counter()

    try:
        raw = await func(*args, **kwargs)
        duration_ms = (time.perf_counter() - start) * 1000

        if isinstance(raw, dict) and raw.get("error"):
            return ToolResult(
                success=False,
                data=raw,
                error=str(raw["error"]),
                duration_ms=duration_ms,
                tool_name=tool_name,
                risk_level=risk,
            )

        return ToolResult(
            success=True,
            data=raw,
            duration_ms=duration_ms,
            tool_name=tool_name,
            risk_level=risk,
        )

    except Exception as e:
        duration_ms = (time.perf_counter() - start) * 1000
        return ToolResult(
            success=False,
            error="{}: {}".format(type(e).__name__, e),
            duration_ms=duration_ms,
            tool_name=tool_name,
            risk_level=risk,
        )
