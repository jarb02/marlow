"""HardcodedInvariants — security rules that CANNOT be bypassed.

These are checked BEFORE every action execution. The Kernel cannot
modify them. Only a source-code change can alter these rules.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class InvariantResult:
    """Result of an invariant check."""

    allowed: bool
    violated_rule: str = ""
    severity: str = "none"  # none | high | critical


class HardcodedInvariants:
    """Security invariants that are hardcoded and cannot be overridden.

    Checked BEFORE every action execution. The Kernel cannot modify
    these — only a code change can.
    """

    # Paths that Marlow can NEVER write to or delete from
    PROTECTED_PATHS: list[str] = [
        r"C:\Windows",
        r"C:\Program Files",
        r"C:\Program Files (x86)",
        r"C:\ProgramData",
        os.path.join(os.environ.get("APPDATA", ""), "Microsoft"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft"),
    ]

    # Commands that are ALWAYS blocked
    BLOCKED_COMMANDS: list[str] = [
        r"(?i)format\s+[a-z]:",
        r"(?i)del\s+/[sf]",
        r"(?i)rmdir\s+/[sq]",
        r"(?i)rd\s+/[sq]",
        r"(?i)reg\s+delete",
        r"(?i)reg\s+add",
        r"(?i)net\s+user",
        r"(?i)net\s+localgroup",
        r"(?i)netsh\s+advfirewall",
        r"(?i)sc\s+(delete|create|config)",
        r"(?i)bcdedit",
        r"(?i)diskpart",
        r"(?i)cipher\s+/w",
        r"(?i)schtasks\s+/(create|delete)",
        r"(?i)wmic\s+.*(delete|call)",
        r"(?i)powershell.*-enc",
        r"(?i)iex\s*\(",
        r"(?i)invoke-expression",
        r"(?i)invoke-webrequest.*\|\s*(iex|invoke-expression)",
        r"(?i)set-executionpolicy",
        r"(?i)rm\s+-r(f)?\s+(/|\\|[A-Z]:)",
    ]

    # Whitelisted domains (only these can be accessed by scripts)
    WHITELISTED_DOMAINS: list[str] = [
        "api.anthropic.com",
        "api.openai.com",
        "localhost",
        "127.0.0.1",
    ]

    def check_file_path(self, path: str) -> InvariantResult:
        """Check if a file path is allowed for write/delete."""
        normalized = os.path.normpath(os.path.abspath(path))
        for protected in self.PROTECTED_PATHS:
            if protected and normalized.lower().startswith(protected.lower()):
                return InvariantResult(
                    allowed=False,
                    violated_rule=f"Protected path: {protected}",
                    severity="critical",
                )
        return InvariantResult(allowed=True)

    def check_command(self, command: str) -> InvariantResult:
        """Check if a shell command is allowed."""
        for pattern in self.BLOCKED_COMMANDS:
            if re.search(pattern, command):
                return InvariantResult(
                    allowed=False,
                    violated_rule=f"Blocked command pattern: {pattern}",
                    severity="critical",
                )
        return InvariantResult(allowed=True)

    def check_url(self, url: str) -> InvariantResult:
        """Check if a URL domain is in the whitelist."""
        parsed = urlparse(url)
        domain = parsed.hostname or ""
        if domain in self.WHITELISTED_DOMAINS:
            return InvariantResult(allowed=True)
        return InvariantResult(
            allowed=False,
            violated_rule=f"Domain not whitelisted: {domain}",
            severity="high",
        )
