"""ToolPermissions — risk-based permission checks for tool execution.

Uses TOOL_RISK_MAP from kernel.constants to decide whether a tool
can execute, and whether user confirmation is required.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PermissionResult:
    """Result of a permission check."""

    allowed: bool
    needs_confirmation: bool = False
    reason: str = ""


class ToolPermissions:
    """Decides whether a tool can execute based on risk level and context.

    Parameters
    ----------
    * **autonomous_mode** (bool):
        When True, dangerous tools require confirmation unless the
        command is whitelisted. When False (default), dangerous tools
        execute without confirmation (the existing safety engine
        handles confirmation in non-autonomous mode).
    """

    def __init__(self, autonomous_mode: bool = False):
        self.autonomous_mode = autonomous_mode
        # Commands whitelisted for autonomous execution (read-only / harmless)
        self.whitelisted_commands: set[str] = {
            "dir", "ls", "cd", "type", "cat", "echo", "cls", "clear",
            "whoami", "hostname", "ipconfig", "ping", "nslookup",
            "tasklist", "systeminfo",
        }

    def check_permission(
        self, tool_name: str, params: dict | None = None,
    ) -> PermissionResult:
        """Check if a tool call is permitted.

        Parameters
        ----------
        * **tool_name** (str): Name of the MCP tool.
        * **params** (dict or None): Tool parameters (used for command checks).

        Returns
        -------
        PermissionResult with allowed, needs_confirmation, and reason.
        """
        from ..constants import TOOL_RISK_MAP

        risk = TOOL_RISK_MAP.get(tool_name, "moderate")

        if risk == "safe":
            return PermissionResult(allowed=True, needs_confirmation=False)

        if risk == "moderate":
            return PermissionResult(
                allowed=True, needs_confirmation=False,
                reason="Logged and rate-limited",
            )

        if risk == "dangerous":
            # In autonomous mode, dangerous tools need confirmation
            # EXCEPT whitelisted commands via run_command
            if self.autonomous_mode and tool_name == "run_command":
                cmd = (params or {}).get("command", "")
                base_cmd = cmd.strip().split()[0].lower() if cmd.strip() else ""
                if base_cmd in self.whitelisted_commands:
                    return PermissionResult(
                        allowed=True, needs_confirmation=False,
                        reason=f"Whitelisted command: {base_cmd}",
                    )

            if self.autonomous_mode:
                return PermissionResult(
                    allowed=True, needs_confirmation=True,
                    reason=f"Dangerous tool in autonomous mode: {tool_name}",
                )
            return PermissionResult(allowed=True, needs_confirmation=False)

        if risk == "critical":
            # ALWAYS needs confirmation, no exceptions
            return PermissionResult(
                allowed=True, needs_confirmation=True,
                reason=f"Critical tool always requires confirmation: {tool_name}",
            )

        return PermissionResult(allowed=True, needs_confirmation=False)
