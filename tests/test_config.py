"""
Tests for Marlow configuration — verifies secure defaults.

Every new Marlow installation must start locked-down:
- confirmation_mode = "all"
- kill switch enabled
- blocked apps include banking/password managers
- blocked commands include destructive shell commands
- telemetry always False
"""

import json
import tempfile
from pathlib import Path

import pytest

from marlow.core.config import MarlowConfig, SecurityConfig, AutomationConfig, ensure_dirs


# ─────────────────────────────────────────────────────────────
# Secure Defaults
# ─────────────────────────────────────────────────────────────

class TestSecureDefaults:
    """New users get maximum security out of the box."""

    def test_confirmation_mode_defaults_to_all(self):
        config = MarlowConfig()
        assert config.security.confirmation_mode == "all"

    def test_kill_switch_enabled_by_default(self):
        config = MarlowConfig()
        assert config.security.kill_switch_enabled is True

    def test_kill_switch_hotkey(self):
        config = MarlowConfig()
        assert config.security.kill_switch_hotkey == "ctrl+shift+escape"

    def test_telemetry_always_false(self):
        config = MarlowConfig()
        assert config._telemetry is False

    def test_encrypt_logs_enabled(self):
        config = MarlowConfig()
        assert config.security.encrypt_logs is True

    def test_rate_limit_default(self):
        config = MarlowConfig()
        assert config.security.max_actions_per_minute == 30

    def test_prefer_silent_methods(self):
        config = MarlowConfig()
        assert config.automation.prefer_silent_methods is True

    def test_default_backend_is_uia(self):
        config = MarlowConfig()
        assert config.automation.default_backend == "uia"


# ─────────────────────────────────────────────────────────────
# Blocked Apps
# ─────────────────────────────────────────────────────────────

class TestBlockedApps:
    """Banking, password managers, and security apps are blocked."""

    @pytest.fixture
    def blocked_apps(self):
        return SecurityConfig().blocked_apps

    def test_blocked_apps_not_empty(self, blocked_apps):
        assert len(blocked_apps) > 0

    @pytest.mark.parametrize("app", [
        "chase", "bankofamerica", "wellsfargo", "paypal", "venmo",
    ])
    def test_banking_apps_blocked(self, blocked_apps, app):
        assert app in blocked_apps, f"{app} should be blocked"

    @pytest.mark.parametrize("app", [
        "1password", "lastpass", "bitwarden", "keepass", "dashlane",
    ])
    def test_password_managers_blocked(self, blocked_apps, app):
        assert app in blocked_apps, f"{app} should be blocked"

    @pytest.mark.parametrize("app", [
        "authenticator", "authy",
    ])
    def test_auth_apps_blocked(self, blocked_apps, app):
        assert app in blocked_apps, f"{app} should be blocked"

    def test_windows_security_blocked(self, blocked_apps):
        assert "windows security" in blocked_apps


# ─────────────────────────────────────────────────────────────
# Blocked Commands
# ─────────────────────────────────────────────────────────────

class TestBlockedCommands:
    """Destructive shell commands are blocked."""

    @pytest.fixture
    def blocked_commands(self):
        return SecurityConfig().blocked_commands

    def test_blocked_commands_not_empty(self, blocked_commands):
        assert len(blocked_commands) > 0

    @pytest.mark.parametrize("cmd", [
        "format", "del /f", "del /s", "rmdir /s", "rm -rf",
        "shutdown", "reg delete",
    ])
    def test_destructive_commands_blocked(self, blocked_commands, cmd):
        assert cmd in blocked_commands, f"'{cmd}' should be blocked"

    @pytest.mark.parametrize("cmd", [
        "powershell -encodedcommand", "powershell -enc",
        "invoke-webrequest", "invoke-restmethod",
        "set-executionpolicy",
    ])
    def test_powershell_abuse_blocked(self, blocked_commands, cmd):
        assert cmd in blocked_commands, f"'{cmd}' should be blocked"

    @pytest.mark.parametrize("cmd", [
        "net user", "net localgroup", "netsh",
    ])
    def test_network_commands_blocked(self, blocked_commands, cmd):
        assert cmd in blocked_commands, f"'{cmd}' should be blocked"


# ─────────────────────────────────────────────────────────────
# Config Persistence
# ─────────────────────────────────────────────────────────────

class TestConfigPersistence:
    """Config saves and loads correctly."""

    def test_save_and_load_roundtrip(self, tmp_path):
        config_file = tmp_path / "config.json"
        original = MarlowConfig()
        original.save(config_file)

        loaded = MarlowConfig.load(config_file)

        assert loaded.security.confirmation_mode == original.security.confirmation_mode
        assert loaded.security.kill_switch_enabled == original.security.kill_switch_enabled
        assert loaded.security.max_actions_per_minute == original.security.max_actions_per_minute
        assert loaded.automation.default_backend == original.automation.default_backend
        assert loaded.language == original.language

    def test_load_creates_default_if_missing(self, tmp_path):
        config_file = tmp_path / "nonexistent.json"
        assert not config_file.exists()

        config = MarlowConfig.load(config_file)

        assert config_file.exists()
        assert config.security.confirmation_mode == "all"

    def test_load_handles_corrupted_file(self, tmp_path):
        config_file = tmp_path / "bad.json"
        config_file.write_text("not valid json {{{")

        config = MarlowConfig.load(config_file)

        # Should fall back to defaults
        assert config.security.confirmation_mode == "all"
        assert config.security.kill_switch_enabled is True

    def test_telemetry_not_saved_to_disk(self, tmp_path):
        config_file = tmp_path / "config.json"
        MarlowConfig().save(config_file)

        data = json.loads(config_file.read_text())
        assert "_telemetry" not in data

    def test_saved_json_is_valid(self, tmp_path):
        config_file = tmp_path / "config.json"
        MarlowConfig().save(config_file)

        data = json.loads(config_file.read_text())
        assert "security" in data
        assert "automation" in data

    def test_blocked_apps_persist(self, tmp_path):
        config_file = tmp_path / "config.json"
        MarlowConfig().save(config_file)

        loaded = MarlowConfig.load(config_file)
        assert "paypal" in loaded.security.blocked_apps
        assert "1password" in loaded.security.blocked_apps
