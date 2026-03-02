"""SecurityManager — central security orchestrator.

Every action passes through here before execution. Checks are
applied in order: rate limits, permissions, invariants, backup.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .backup import FileBackupManager
from .invariants import HardcodedInvariants
from .permissions import ToolPermissions
from .rate_limiter import RateLimiter
from .sanitizer import ContentSanitizer, SanitizeResult


@dataclass(frozen=True)
class SecurityDecision:
    """Final decision on whether an action may proceed."""

    allowed: bool
    needs_confirmation: bool = False
    reasons: tuple[str, ...] = ()
    severity: str = "none"  # none | medium | high | critical


# Tools that modify files on disk
_FILE_MODIFYING_TOOLS: frozenset[str] = frozenset({
    "run_command", "run_app_script", "type_text", "press_key", "hotkey",
    "clipboard",
})


class SecurityManager:
    """Central security orchestrator.

    Every action passes through here before execution.

    Parameters
    ----------
    * **autonomous_mode** (bool):
        Whether the Kernel is operating autonomously.
    * **backup_dir** (str or None):
        Override for backup directory.
    """

    def __init__(
        self,
        autonomous_mode: bool = False,
        backup_dir: str | None = None,
    ):
        self.sanitizer = ContentSanitizer()
        self.invariants = HardcodedInvariants()
        self.permissions = ToolPermissions(autonomous_mode=autonomous_mode)
        self.rate_limiter = RateLimiter()
        self.backup = FileBackupManager(backup_dir=backup_dir)

    def check_action(
        self,
        tool_name: str,
        params: dict,
        goal_id: str = "",
    ) -> SecurityDecision:
        """Full security check before executing an action.

        Checks in order:
        1. Rate limits
        2. Tool permissions
        3. Invariants (path check for file ops, command check for run_command)
        4. Auto-backup if file modification

        Parameters
        ----------
        * **tool_name** (str): MCP tool name.
        * **params** (dict): Tool parameters.
        * **goal_id** (str): Current goal ID (for per-goal limits).
        """
        reasons: list[str] = []
        needs_confirmation = False

        # 1. Rate limits
        action_check = self.rate_limiter.check_action()
        if not action_check.allowed:
            return SecurityDecision(
                allowed=False,
                reasons=(f"Rate limit: {action_check.limit_name}",),
                severity="high",
            )

        # 2. Permissions
        perm = self.permissions.check_permission(tool_name, params)
        if not perm.allowed:
            return SecurityDecision(
                allowed=False,
                reasons=(perm.reason,),
                severity="critical",
            )
        if perm.needs_confirmation:
            needs_confirmation = True
            reasons.append(perm.reason)

        # 3. Invariant checks based on tool type
        if tool_name == "run_command":
            cmd = params.get("command", "")
            cmd_check = self.invariants.check_command(cmd)
            if not cmd_check.allowed:
                return SecurityDecision(
                    allowed=False,
                    reasons=(cmd_check.violated_rule,),
                    severity="critical",
                )

        # Command rate limit for shell/script tools
        if tool_name in ("run_command", "run_app_script"):
            cmd_rate = self.rate_limiter.check_command()
            if not cmd_rate.allowed:
                return SecurityDecision(
                    allowed=False,
                    reasons=("Command rate limit exceeded",),
                    severity="high",
                )

        # File path checks
        file_path = (
            params.get("path")
            or params.get("file_path")
            or params.get("target")
        )
        if file_path and tool_name in ("run_command", "run_app_script"):
            path_check = self.invariants.check_file_path(file_path)
            if not path_check.allowed:
                return SecurityDecision(
                    allowed=False,
                    reasons=(path_check.violated_rule,),
                    severity="critical",
                )

        # 4. Auto-backup for file modifications
        if file_path and tool_name in _FILE_MODIFYING_TOOLS:
            self.backup.backup_before_modify(file_path)
            if goal_id:
                file_rate = self.rate_limiter.check_file_modification(goal_id)
                if not file_rate.allowed:
                    needs_confirmation = True
                    reasons.append(
                        f"File modification limit: "
                        f"{file_rate.current}/{file_rate.maximum}",
                    )

        # Record the action
        self.rate_limiter.record_action()
        if tool_name in ("run_command", "run_app_script"):
            self.rate_limiter.record_command()

        return SecurityDecision(
            allowed=True,
            needs_confirmation=needs_confirmation,
            reasons=tuple(reasons),
            severity="medium" if needs_confirmation else "none",
        )

    def sanitize_content(
        self, content: str, source: str,
    ) -> SanitizeResult:
        """Sanitize external content before LLM."""
        return self.sanitizer.sanitize(content, source)

    def wrap_content_for_llm(self, content: str, source: str) -> str:
        """Sanitize and wrap with spotlighting delimiters."""
        result = self.sanitizer.sanitize(content, source)
        return self.sanitizer.wrap_as_untrusted(result.sanitized_text, source)
