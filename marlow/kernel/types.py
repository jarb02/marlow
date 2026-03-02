"""Kernel data types — canonical result type for all tool executions.

Every MCP tool call will eventually return a ToolResult instead of a raw
dict. This is the foundation for scoring, retry logic, and audit trails.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolResult:
    """Immutable result of a single tool execution.

    Parameters
    ----------
    * **success** (bool):
        Whether the tool completed its intended action.
    * **data** (Any):
        Arbitrary payload returned by the tool.
    * **error** (str or None):
        Error message if the tool failed. None on success.
    * **duration_ms** (float):
        Wall-clock execution time in milliseconds.
    * **tool_name** (str):
        Name of the tool that produced this result.
    * **risk_level** (str):
        One of: safe, moderate, dangerous, critical.
    * **side_effects** (tuple of str):
        Human-readable descriptions of side effects performed
        (e.g. "wrote file X", "clicked button Y").
    """

    success: bool
    data: Any = None
    error: str | None = None
    duration_ms: float = 0.0
    tool_name: str = ""
    risk_level: str = "safe"
    side_effects: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        """Convert to a plain dict for MCP response serialization."""
        result = {"success": self.success}
        if self.data is not None:
            result["data"] = self.data
        if self.error is not None:
            result["error"] = self.error
        if self.duration_ms > 0:
            result["duration_ms"] = round(self.duration_ms, 2)
        if self.tool_name:
            result["tool_name"] = self.tool_name
        if self.risk_level != "safe":
            result["risk_level"] = self.risk_level
        if self.side_effects:
            result["side_effects"] = list(self.side_effects)
        return result

    @staticmethod
    def ok(data: Any = None, **kwargs) -> ToolResult:
        """Convenience constructor for a successful result."""
        return ToolResult(success=True, data=data, **kwargs)

    @staticmethod
    def fail(error: str, **kwargs) -> ToolResult:
        """Convenience constructor for a failed result."""
        return ToolResult(success=False, error=error, **kwargs)
