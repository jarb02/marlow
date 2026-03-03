"""SmartExecutor — 4-layer tool execution with escalation.

Layer 1: Cached/heuristic (no LLM needed)
Layer 2: Plan persistence (follow the plan)
Layer 3: Direct tool call (standard execution)
Layer 4: LLM-assisted recovery (Tier 3+)

For T2.4, only Layers 2-3 are active.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from .constants import TOOL_RISK_MAP
from .tool_wrapper import wrap_tool_call, wrap_tool_call_async
from .types import ToolResult

logger = logging.getLogger("marlow.executor")


def _raw_to_result(
    tool_name: str, raw, duration_ms: float,
) -> ToolResult:
    """Normalize a raw tool return value into a ToolResult."""
    risk = TOOL_RISK_MAP.get(tool_name, "dangerous")
    if isinstance(raw, dict) and raw.get("error"):
        return ToolResult(
            success=False,
            data=raw,
            error=str(raw["error"]),
            duration_ms=duration_ms,
            tool_name=tool_name,
            risk_level=risk,
        )
    if raw is None:
        logger.warning("Tool '%s' returned None — treating as success", tool_name)
    return ToolResult(
        success=True,
        data=raw,
        duration_ms=duration_ms,
        tool_name=tool_name,
        risk_level=risk,
    )


class SmartExecutor:
    """Executes tool calls with proper async/sync handling.

    Handles:
    - Async tools (native coroutines)
    - Sync tools (run in thread pool to not block event loop)
    - Timeout per tool (default 30s)
    - Error wrapping to ToolResult

    Parameters
    ----------
    * **tool_registry** (dict or None):
        Mapping of tool_name -> callable.
    * **default_timeout** (float):
        Timeout in seconds for each tool call.
    * **max_workers** (int):
        Thread pool size for sync tool execution.
    """

    def __init__(
        self,
        tool_registry: dict[str, Callable] = None,
        default_timeout: float = 30.0,
        max_workers: int = 4,
    ):
        self._tools = tool_registry or {}
        self._timeout = default_timeout
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="marlow-tool",
        )

    def register_tool(self, name: str, func: Callable):
        """Register a tool function."""
        self._tools[name] = func

    def register_tools(self, tools: dict[str, Callable]):
        """Register multiple tools."""
        self._tools.update(tools)

    async def execute(self, tool_name: str, params: dict) -> ToolResult:
        """Execute a tool with proper async handling and timeout.

        Handles three cases:
        1. Native ``async def`` — detected by ``iscoroutinefunction``.
        2. Sync wrapper returning a coroutine (lambda around async) —
           detected by calling ``func()`` then checking ``iscoroutine``.
        3. Truly sync function — result already computed after call.
        """
        func = self._tools.get(tool_name)
        if not func:
            return ToolResult.fail(
                f"Unknown tool: {tool_name}", tool_name=tool_name,
            )

        try:
            if inspect.iscoroutinefunction(func):
                # Case 1: native async
                result = await asyncio.wait_for(
                    wrap_tool_call_async(tool_name, func, **params),
                    timeout=self._timeout,
                )
            else:
                # Case 2 or 3: call it and inspect the return value.
                start = time.perf_counter()
                maybe_coro = func(**params)

                if inspect.iscoroutine(maybe_coro):
                    # Case 2: lambda wrapping async — await the coroutine
                    raw = await asyncio.wait_for(
                        maybe_coro, timeout=self._timeout,
                    )
                else:
                    # Case 3: truly sync — already have the result
                    raw = maybe_coro

                duration_ms = (time.perf_counter() - start) * 1000
                result = _raw_to_result(tool_name, raw, duration_ms)

            return result

        except asyncio.TimeoutError:
            return ToolResult.fail(
                f"Tool timed out after {self._timeout}s",
                tool_name=tool_name,
            )
        except Exception as e:
            return ToolResult.fail(
                f"Executor error: {type(e).__name__}: {e}",
                tool_name=tool_name,
            )

    @property
    def available_tools(self) -> list[str]:
        """List registered tool names."""
        return list(self._tools.keys())

    def shutdown(self):
        """Shut down the thread pool."""
        self._executor.shutdown(wait=False)
