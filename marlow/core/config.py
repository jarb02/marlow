"""
Marlow Configuration Manager

Handles loading, saving, and validating configuration.
Default: maximum security (confirmation mode ON, everything locked down).
"""

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Optional
from pathlib import Path


# Default config location: ~/.marlow/config.json
CONFIG_DIR = Path.home() / ".marlow"
CONFIG_FILE = CONFIG_DIR / "config.json"
LOG_DIR = CONFIG_DIR / "logs"


@dataclass
class SecurityConfig:
    """Security settings — secure by default."""

    # Confirmation mode: "all" | "sensitive" | "autonomous"
    # DEFAULT: "all" — every action needs user approval
    # (OpenClaw didn't have this. We do.)
    confirmation_mode: str = "all"

    # Kill switch hotkey
    kill_switch_hotkey: str = "ctrl+shift+escape"
    kill_switch_enabled: bool = True

    # Apps that Marlow will NEVER touch
    blocked_apps: list[str] = field(default_factory=lambda: [
        # Banking & Finance
        "chase", "bankofamerica", "wellsfargo", "citi", "capital one",
        "paypal", "venmo", "zelle", "cashapp", "coinbase", "robinhood",
        # Password Managers
        "1password", "lastpass", "bitwarden", "keepass", "dashlane",
        # Security & Auth
        "authenticator", "authy", "yubikey",
        # System Security
        "windows security", "defender", "firewall",
    ])

    # Shell commands that are ALWAYS blocked
    blocked_commands: list[str] = field(default_factory=lambda: [
        "format", "del /f", "del /s", "rmdir /s", "rm -rf",
        "shutdown", "restart", "reg delete", "bcdedit",
        "cipher /w", "diskpart", "sfc", "dism",
        "net user", "net localgroup", "netsh",
        "powershell -encodedcommand", "powershell -enc",
        "invoke-webrequest", "invoke-restmethod",
        "set-executionpolicy", "new-service",
    ])

    # Max actions per minute (rate limiter)
    max_actions_per_minute: int = 30

    # Sensitive data patterns to redact (regex)
    # These are detected and replaced with [REDACTED] before sending to AI
    sensitive_patterns: dict[str, str] = field(default_factory=lambda: {
        "credit_card": r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b",
        "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
        "email": r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Z|a-z]{2,}\b",
        "phone_us": r"\b(\+1[\s\-]?)?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{4}\b",
        "password_field": r"(?i)(password|passwd|pwd|secret|token|api[_\-]?key)",
    })

    # Encryption for screenshots/logs
    encrypt_logs: bool = True

    # Log retention in days
    log_retention_days: int = 30


@dataclass
class AutomationConfig:
    """Automation behavior settings."""

    # Default pywinauto backend: "uia" (modern) or "win32" (legacy)
    default_backend: str = "uia"

    # Screenshot format and quality
    screenshot_format: str = "png"
    screenshot_quality: int = 85

    # Timeout for UI operations (seconds)
    ui_timeout: float = 10.0

    # Whether to use silent methods first (background-friendly)
    prefer_silent_methods: bool = True

    # Mouse movement speed (0 = instant, higher = slower)
    mouse_speed: float = 0.0

    # Agent screen only: auto-move windows to agent monitor
    # When True, open_application and manage_window auto-redirect to agent screen
    agent_screen_only: bool = True


@dataclass
class MarlowConfig:
    """Root configuration for Marlow."""

    security: SecurityConfig = field(default_factory=SecurityConfig)
    automation: AutomationConfig = field(default_factory=AutomationConfig)

    # Language for messages: "en" | "es" | "auto"
    language: str = "auto"

    # Telemetry — always False, not configurable
    # This exists only to make our stance explicit
    _telemetry: bool = field(default=False, init=False, repr=False)

    def save(self, path: Optional[Path] = None):
        """Save configuration to JSON file."""
        config_path = path or CONFIG_FILE
        config_path.parent.mkdir(parents=True, exist_ok=True)

        data = asdict(self)
        # Remove private fields
        data.pop("_telemetry", None)

        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "MarlowConfig":
        """Load configuration from JSON file. Creates default if not found."""
        config_path = path or CONFIG_FILE

        if not config_path.exists():
            config = cls()
            config.save(config_path)
            return config

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            security = SecurityConfig(**data.get("security", {}))
            automation = AutomationConfig(**data.get("automation", {}))

            return cls(
                security=security,
                automation=automation,
                language=data.get("language", "auto"),
            )
        except (json.JSONDecodeError, TypeError, KeyError):
            # Corrupted config — use defaults
            config = cls()
            config.save(config_path)
            return config


def ensure_dirs():
    """Create necessary directories."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
