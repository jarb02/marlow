"""
Marlow Extension Sandbox

Enforces declared permissions for extensions. An extension can only
use capabilities it declared in its manifest.

/ Sandbox que hace cumplir los permisos declarados en el manifiesto.
"""

import logging
from typing import Optional

logger = logging.getLogger("marlow.extensions.sandbox")


class ExtensionSandbox:
    """
    Enforces extension permissions at runtime.

    Each extension declares what it needs in its manifest.
    This sandbox verifies every action against those declarations.
    """

    def __init__(self, permissions: dict):
        self.permissions = permissions

    def check_com(self, prog_id: str) -> bool:
        """Check if the extension is allowed to use a specific COM ProgID."""
        allowed = self.permissions.get("com_automation", [])
        if not allowed:
            return False
        return prog_id in allowed

    def check_filesystem(self, operation: str) -> bool:
        """Check if the extension is allowed a filesystem operation (read/write)."""
        allowed = self.permissions.get("file_system", [])
        if not allowed:
            return False
        return operation in allowed

    def check_network(self) -> bool:
        """Check if the extension is allowed network access."""
        return bool(self.permissions.get("network", False))

    def check_shell(self) -> bool:
        """Check if the extension is allowed to run shell commands."""
        return bool(self.permissions.get("shell_commands", False))

    def enforce(self, action: str, detail: Optional[str] = None) -> tuple[bool, str]:
        """
        Enforce a permission check.

        Args:
            action: Type of action (com, filesystem_read, filesystem_write, network, shell).
            detail: Additional context (e.g., ProgID, file path).

        Returns:
            Tuple of (allowed: bool, reason: str).
        """
        if action == "com":
            if self.check_com(detail or ""):
                return True, "allowed"
            return False, f"COM access to '{detail}' not declared in manifest"

        elif action in ("filesystem_read", "filesystem_write"):
            op = action.split("_")[1]
            if self.check_filesystem(op):
                return True, "allowed"
            return False, f"Filesystem {op} not declared in manifest"

        elif action == "network":
            if self.check_network():
                return True, "allowed"
            return False, "Network access not declared in manifest"

        elif action == "shell":
            if self.check_shell():
                return True, "allowed"
            return False, "Shell command access not declared in manifest"

        else:
            return False, f"Unknown action type: {action}"

    def get_summary(self) -> dict:
        """Return a summary of what this sandbox allows."""
        return {
            "com_automation": self.permissions.get("com_automation", []),
            "file_system": self.permissions.get("file_system", []),
            "network": bool(self.permissions.get("network", False)),
            "shell_commands": bool(self.permissions.get("shell_commands", False)),
        }
