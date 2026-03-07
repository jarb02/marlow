"""HardcodedInvariants — Linux security rules that CANNOT be bypassed.

These are checked BEFORE every action execution. The Kernel cannot
modify them. Only a source-code change can alter these rules.

/ Invariantes de seguridad Linux — reglas que NO se pueden saltar.
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
        "/usr",
        "/bin",
        "/sbin",
        "/boot",
        "/lib",
        "/lib64",
        "/opt",
        "/snap",
        "/etc/shadow",
        "/etc/passwd",
        "/etc/sudoers",
        "/etc/ssh",
        "/etc/pam.d",
        "/etc/security",
        "/proc",
        "/sys",
        "/dev",
        "/run",
        "/var/log",
        os.path.expanduser("~/.ssh"),
        os.path.expanduser("~/.gnupg"),
        os.path.expanduser("~/.config/systemd"),
    ]

    # Commands that are ALWAYS blocked
    BLOCKED_COMMANDS: list[str] = [
        r"(?i)rm\s+-r(f)?\s+/",
        r"(?i)rm\s+-rf\s+~",
        r"(?i)rm\s+-rf\s+\*",
        r"(?i)mkfs\.",
        r"(?i)dd\s+if=.*of=/dev/",
        r"(?i)fdisk\s+/dev/",
        r"(?i)parted\s+/dev/",
        r"(?i)chmod\s+777\s+/",
        r"(?i)chown\s+-R\s+.*\s+/",
        r"(?i)visudo",
        r"(?i)passwd\s+root",
        r"(?i)usermod\s+-.*-G\s+(sudo|wheel|root)",
        r"(?i)useradd\s+.*-G\s+(sudo|wheel|root)",
        r"(?i)userdel",
        r"(?i)groupdel",
        r"(?i)iptables\s+(-F|-X|-Z|--flush)",
        r"(?i)ufw\s+(disable|reset)",
        r"(?i)systemctl\s+(disable|mask)\s+(firewalld|ufw|sshd|apparmor)",
        r"(?i)shutdown",
        r"(?i)reboot",
        r"(?i)init\s+[06]",
        r"(?i)kill\s+-9\s+1\b",
        r"(?i)pkill\s+-9\s+(systemd|init)",
        r"(?i)curl.*\|\s*(bash|sh|zsh)",
        r"(?i)wget.*\|\s*(bash|sh|zsh)",
        r"(?i)python[23]?\s+-c\s+.*exec\s*\(",
        r"(?i)eval\s+.*\$\(",
        r"(?i)crontab\s+-r",
        r"(?i):>\s*/etc/",
        r"(?i)echo\s+.*>\s*/etc/(passwd|shadow|sudoers)",
        r"(?i)mv\s+/etc/(passwd|shadow|sudoers)",
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
            if protected and normalized.startswith(protected):
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
