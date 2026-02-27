"""
Tests for Marlow SafetyEngine — the core security gate.

Every action in Marlow passes through SafetyEngine.approve_action().
If safety says no, the action does not happen. Period.
"""

import asyncio
import time

import pytest

from marlow.core.config import MarlowConfig
from marlow.core.safety import SafetyEngine


@pytest.fixture
def safety():
    """Fresh SafetyEngine with default config."""
    config = MarlowConfig()
    return SafetyEngine(config)


@pytest.fixture
def autonomous_safety():
    """SafetyEngine with confirmation_mode='autonomous' (no confirmation prompts)."""
    config = MarlowConfig()
    config.security.confirmation_mode = "autonomous"
    return SafetyEngine(config)


# ─────────────────────────────────────────────────────────────
# Kill Switch
# ─────────────────────────────────────────────────────────────

class TestKillSwitch:
    """Kill switch stops ALL automation immediately."""

    def test_not_killed_by_default(self, safety):
        assert safety.is_killed is False

    def test_trigger_kill(self, safety):
        safety._trigger_kill()
        assert safety.is_killed is True

    def test_reset_after_kill(self, safety):
        safety._trigger_kill()
        assert safety.is_killed is True

        safety.reset_kill_switch()
        assert safety.is_killed is False

    @pytest.mark.asyncio
    async def test_kill_blocks_all_actions(self, safety):
        safety._trigger_kill()

        approved, reason = await safety.approve_action("click", "click", {})
        assert approved is False
        assert "Kill switch" in reason

    @pytest.mark.asyncio
    async def test_action_works_after_reset(self, autonomous_safety):
        autonomous_safety._trigger_kill()
        autonomous_safety.reset_kill_switch()

        approved, _ = await autonomous_safety.approve_action("click", "click", {})
        assert approved is True

    def test_kill_is_thread_safe(self, safety):
        """Kill switch uses a lock for thread safety."""
        import threading

        results = []

        def trigger():
            safety._trigger_kill()
            results.append(safety.is_killed)

        threads = [threading.Thread(target=trigger) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(results)
        assert safety.is_killed is True


# ─────────────────────────────────────────────────────────────
# Blocked Apps
# ─────────────────────────────────────────────────────────────

class TestBlockedApps:
    """Marlow never interacts with banking, password managers, or security apps."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("app", [
        "PayPal", "Chase Bank", "1Password", "Bitwarden", "Authenticator",
    ])
    async def test_blocked_app_by_window_title(self, autonomous_safety, app):
        approved, reason = await autonomous_safety.approve_action(
            "click", "click", {"window_title": app}
        )
        assert approved is False
        assert "Blocked" in reason

    @pytest.mark.asyncio
    async def test_blocked_app_case_insensitive(self, autonomous_safety):
        approved, _ = await autonomous_safety.approve_action(
            "click", "click", {"window_title": "PAYPAL CHECKOUT"}
        )
        assert approved is False

    @pytest.mark.asyncio
    async def test_safe_app_allowed(self, autonomous_safety):
        approved, _ = await autonomous_safety.approve_action(
            "click", "click", {"window_title": "Notepad"}
        )
        assert approved is True

    @pytest.mark.asyncio
    async def test_blocked_app_by_app_name(self, autonomous_safety):
        approved, _ = await autonomous_safety.approve_action(
            "open_application", "open_application", {"app_name": "LastPass"}
        )
        assert approved is False


# ─────────────────────────────────────────────────────────────
# Blocked Commands
# ─────────────────────────────────────────────────────────────

class TestBlockedCommands:
    """Destructive shell commands are always blocked."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("cmd", [
        "format C:",
        "del /f /s *",
        "rm -rf /",
        "shutdown /s /t 0",
        "reg delete HKLM\\SOFTWARE",
        "rmdir /s /q C:\\",
    ])
    async def test_destructive_command_blocked(self, autonomous_safety, cmd):
        approved, reason = await autonomous_safety.approve_action(
            "run_command", "run_command", {"command": cmd}
        )
        assert approved is False
        assert "Blocked" in reason

    @pytest.mark.asyncio
    @pytest.mark.parametrize("cmd", [
        "powershell -encodedcommand ZQBjAGgAbw==",
        "powershell -enc ZQBjAGgAbw==",
        "Invoke-WebRequest http://evil.com",
        "Set-ExecutionPolicy Unrestricted",
    ])
    async def test_powershell_abuse_blocked(self, autonomous_safety, cmd):
        approved, _ = await autonomous_safety.approve_action(
            "run_command", "run_command", {"command": cmd}
        )
        assert approved is False

    @pytest.mark.asyncio
    async def test_safe_command_allowed(self, autonomous_safety):
        approved, _ = await autonomous_safety.approve_action(
            "run_command", "run_command", {"command": "echo Hello"}
        )
        assert approved is True

    @pytest.mark.asyncio
    async def test_non_command_tool_ignores_command_check(self, autonomous_safety):
        """Tools that aren't run_command shouldn't be blocked by command patterns."""
        approved, _ = await autonomous_safety.approve_action(
            "click", "click", {"element_name": "Format"}
        )
        assert approved is True


# ─────────────────────────────────────────────────────────────
# Rate Limiter
# ─────────────────────────────────────────────────────────────

class TestRateLimiter:
    """Max actions per minute prevents runaway automation."""

    @pytest.mark.asyncio
    async def test_under_limit_allowed(self, autonomous_safety):
        for _ in range(5):
            approved, _ = await autonomous_safety.approve_action("click", "click", {})
            assert approved is True

    @pytest.mark.asyncio
    async def test_over_limit_blocked(self):
        config = MarlowConfig()
        config.security.confirmation_mode = "autonomous"
        config.security.max_actions_per_minute = 5
        safety = SafetyEngine(config)

        for _ in range(5):
            approved, _ = await safety.approve_action("click", "click", {})
            assert approved is True

        approved, reason = await safety.approve_action("click", "click", {})
        assert approved is False
        assert "Rate limit" in reason

    @pytest.mark.asyncio
    async def test_rate_limit_resets_after_window(self):
        config = MarlowConfig()
        config.security.confirmation_mode = "autonomous"
        config.security.max_actions_per_minute = 3
        safety = SafetyEngine(config)

        # Fill up the rate limiter
        for _ in range(3):
            await safety.approve_action("click", "click", {})

        # Manually expire all timestamps (simulate 61 seconds passing)
        safety._action_timestamps = [time.time() - 61 for _ in safety._action_timestamps]

        approved, _ = await safety.approve_action("click", "click", {})
        assert approved is True


# ─────────────────────────────────────────────────────────────
# Sensitive Action Detection
# ─────────────────────────────────────────────────────────────

class TestSensitiveActions:
    """In 'sensitive' mode, certain tools require confirmation."""

    @pytest.fixture
    def sensitive_safety(self):
        config = MarlowConfig()
        config.security.confirmation_mode = "sensitive"
        return SafetyEngine(config)

    def test_run_command_is_sensitive(self, sensitive_safety):
        assert sensitive_safety._is_sensitive_action("run_command", "run_command", {})

    def test_type_text_is_sensitive(self, sensitive_safety):
        assert sensitive_safety._is_sensitive_action("type_text", "type_text", {})

    def test_clipboard_is_sensitive(self, sensitive_safety):
        assert sensitive_safety._is_sensitive_action("clipboard", "clipboard", {})

    def test_list_windows_is_not_sensitive(self, sensitive_safety):
        assert not sensitive_safety._is_sensitive_action("list_windows", "list_windows", {})

    def test_get_ui_tree_is_not_sensitive(self, sensitive_safety):
        assert not sensitive_safety._is_sensitive_action("get_ui_tree", "get_ui_tree", {})


# ─────────────────────────────────────────────────────────────
# Action Log
# ─────────────────────────────────────────────────────────────

class TestActionLog:
    """All actions are logged for audit trail."""

    @pytest.mark.asyncio
    async def test_approved_action_is_logged(self, autonomous_safety):
        await autonomous_safety.approve_action("click", "click", {"x": 100})

        log = autonomous_safety.get_action_log()
        assert len(log) >= 1
        assert log[-1]["tool"] == "click"
        assert log[-1]["approved"] is True

    @pytest.mark.asyncio
    async def test_blocked_action_is_logged(self, autonomous_safety):
        await autonomous_safety.approve_action(
            "click", "click", {"window_title": "PayPal"}
        )

        log = autonomous_safety.get_action_log()
        assert len(log) >= 1
        assert log[-1]["approved"] is False
        assert log[-1]["result"] == "blocked"

    @pytest.mark.asyncio
    async def test_killed_action_is_logged(self, autonomous_safety):
        autonomous_safety._trigger_kill()
        await autonomous_safety.approve_action("click", "click", {})

        log = autonomous_safety.get_action_log()
        assert any(entry["result"] == "killed" for entry in log)

    def test_get_status(self, safety):
        status = safety.get_status()
        assert "kill_switch_active" in status
        assert "confirmation_mode" in status
        assert "actions_this_minute" in status
        assert "max_actions_per_minute" in status
        assert "blocked_apps_count" in status
        assert "blocked_commands_count" in status
        assert "total_actions_logged" in status

    @pytest.mark.asyncio
    async def test_log_limits_to_last_n(self, autonomous_safety):
        for i in range(10):
            await autonomous_safety.approve_action("click", "click", {"i": i})

        log = autonomous_safety.get_action_log(last_n=3)
        assert len(log) == 3


# ─────────────────────────────────────────────────────────────
# Priority Order
# ─────────────────────────────────────────────────────────────

class TestApprovalPriority:
    """Kill switch > blocked app > blocked command > rate limit."""

    @pytest.mark.asyncio
    async def test_kill_switch_beats_everything(self, autonomous_safety):
        """Even a safe command is blocked when kill switch is active."""
        autonomous_safety._trigger_kill()
        approved, reason = await autonomous_safety.approve_action(
            "run_command", "run_command", {"command": "echo hi"}
        )
        assert approved is False
        assert "Kill switch" in reason

    @pytest.mark.asyncio
    async def test_blocked_app_checked_before_command(self, autonomous_safety):
        """A blocked app with a safe command is still blocked."""
        approved, reason = await autonomous_safety.approve_action(
            "run_command", "run_command",
            {"command": "echo hi", "window_title": "PayPal"}
        )
        assert approved is False
        assert "Blocked" in reason
