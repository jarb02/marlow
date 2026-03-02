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
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from .tool_wrapper import wrap_tool_call, wrap_tool_call_async
from .types import ToolResult

logger = logging.getLogger("marlow.executor")


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
        """Execute a tool with proper async handling and timeout."""
        func = self._tools.get(tool_name)
        if not func:
            return ToolResult.fail(
                f"Unknown tool: {tool_name}", tool_name=tool_name,
            )

        try:
            if inspect.iscoroutinefunction(func):
                # Async tool
                result = await asyncio.wait_for(
                    wrap_tool_call_async(tool_name, func, **params),
                    timeout=self._timeout,
                )
            else:
                # Sync tool — run in thread pool
                loop = asyncio.get_running_loop()
                result = await asyncio.wait_for(
                    loop.run_in_executor(
                        self._executor,
                        lambda: wrap_tool_call(tool_name, func, **params),
                    ),
                    timeout=self._timeout,
                )
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
