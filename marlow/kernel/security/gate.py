"""SecurityGate — unified pre-execution security check.

Integrates all security layers into a single check() call:
1. SecurityManager (rate limits, permissions, invariants, backups)
2. ContentSanitizer (prompt injection detection in params)
3. Trust levels per origin (user vs proactive)

Every tool execution passes through here before running.

/ Puerta de seguridad unificada para el pipeline de ejecucion.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("marlow.kernel.security.gate")


# ─────────────────────────────────────────────────────────────
# Trust Level Classification
# ─────────────────────────────────────────────────────────────

TRUST_OBSERVE = 0     # Read-only: list, screenshot, system_info, get_*
TRUST_LAUNCH = 1      # Launch/focus/memory: open_application, focus_window
TRUST_INTERACT = 2    # Input: type_text, click, press_key, manage_window
TRUST_COMMAND = 3     # Shell/voice: run_command, workflow_run, speak
TRUST_DESTRUCTIVE = 4 # Future: irreversible bulk operations

# Map every known tool to a trust level.
# Tools not listed default to TRUST_INTERACT (safe middle ground).
TOOL_TRUST_LEVELS: dict[str, int] = {
    # ── Tier 0: OBSERVE (read-only, no side effects) ──
    "list_windows": TRUST_OBSERVE,
    "take_screenshot": TRUST_OBSERVE,
    "system_info": TRUST_OBSERVE,
    "get_ui_tree": TRUST_OBSERVE,
    "find_elements": TRUST_OBSERVE,
    "get_element_properties": TRUST_OBSERVE,
    "get_text": TRUST_OBSERVE,
    "get_shadow_windows": TRUST_OBSERVE,
    "get_voice_hotkey_status": TRUST_OBSERVE,
    "ocr_region": TRUST_OBSERVE,
    "list_ocr_languages": TRUST_OBSERVE,
    "get_annotated_screenshot": TRUST_OBSERVE,
    "smart_find": TRUST_OBSERVE,
    "cascade_find": TRUST_OBSERVE,
    "detect_app_framework": TRUST_OBSERVE,
    "get_dialog_info": TRUST_OBSERVE,
    "get_ui_events": TRUST_OBSERVE,
    "get_error_journal": TRUST_OBSERVE,
    "clear_error_journal": TRUST_OBSERVE,
    "get_suggestions": TRUST_OBSERVE,
    "accept_suggestion": TRUST_OBSERVE,
    "dismiss_suggestion": TRUST_OBSERVE,
    "get_agent_screen_state": TRUST_OBSERVE,
    "visual_diff": TRUST_OBSERVE,
    "visual_diff_compare": TRUST_OBSERVE,
    "wait_for_element": TRUST_OBSERVE,
    "wait_for_text": TRUST_OBSERVE,
    "wait_for_window": TRUST_OBSERVE,
    "wait_for_idle": TRUST_OBSERVE,
    "memory_recall": TRUST_OBSERVE,
    "memory_list": TRUST_OBSERVE,
    "list_watchers": TRUST_OBSERVE,
    "list_scheduled_tasks": TRUST_OBSERVE,
    "get_task_history": TRUST_OBSERVE,
    "workflow_list": TRUST_OBSERVE,
    "run_diagnostics": TRUST_OBSERVE,
    "clipboard_history": TRUST_OBSERVE,
    "extensions_list": TRUST_OBSERVE,
    "extensions_audit": TRUST_OBSERVE,
    "cdp_screenshot": TRUST_OBSERVE,
    "cdp_get_dom": TRUST_OBSERVE,
    "demo_status": TRUST_OBSERVE,
    "demo_list": TRUST_OBSERVE,

    # ── Tier 1: LAUNCH (low-risk side effects) ──
    "open_application": TRUST_LAUNCH,
    "focus_window": TRUST_LAUNCH,
    "launch_in_shadow": TRUST_LAUNCH,
    "scrape_url": TRUST_LAUNCH,
    "memory_save": TRUST_LAUNCH,
    "memory_delete": TRUST_LAUNCH,
    "restore_user_focus": TRUST_LAUNCH,
    "toggle_voice_overlay": TRUST_LAUNCH,
    "start_ui_monitor": TRUST_LAUNCH,
    "stop_ui_monitor": TRUST_LAUNCH,
    "set_agent_screen_only": TRUST_LAUNCH,
    "setup_background_mode": TRUST_LAUNCH,
    "move_to_agent_screen": TRUST_LAUNCH,
    "move_to_user_screen": TRUST_LAUNCH,
    "download_whisper_model": TRUST_LAUNCH,

    # ── Tier 2: INTERACT (input / mutation) ──
    "type_text": TRUST_INTERACT,
    "click": TRUST_INTERACT,
    "press_key": TRUST_INTERACT,
    "hotkey": TRUST_INTERACT,
    "move_mouse": TRUST_INTERACT,
    "som_click": TRUST_INTERACT,
    "manage_window": TRUST_INTERACT,
    "move_to_user": TRUST_INTERACT,
    "move_to_shadow": TRUST_INTERACT,
    "do_action": TRUST_INTERACT,
    "clipboard": TRUST_INTERACT,
    "handle_dialog": TRUST_INTERACT,
    "cdp_send": TRUST_COMMAND,
    "watch_folder": TRUST_INTERACT,
    "unwatch_folder": TRUST_INTERACT,
    "get_watch_events": TRUST_INTERACT,
    "schedule_task": TRUST_INTERACT,
    "remove_task": TRUST_INTERACT,
    "workflow_record": TRUST_INTERACT,
    "workflow_stop": TRUST_INTERACT,
    "workflow_delete": TRUST_INTERACT,
    "demo_start": TRUST_INTERACT,
    "demo_stop": TRUST_INTERACT,
    "demo_replay": TRUST_INTERACT,
    "transcribe_audio": TRUST_INTERACT,
    "listen_for_command": TRUST_INTERACT,

    # ── Tier 3: COMMAND (shell, voice, risky CDP) ──
    "run_command": TRUST_COMMAND,
    "workflow_run": TRUST_COMMAND,
    "speak": TRUST_COMMAND,
    "speak_and_listen": TRUST_COMMAND,
    "run_app_script": TRUST_COMMAND,
    "cdp_evaluate": TRUST_COMMAND,
    "capture_mic_audio": TRUST_COMMAND,
    "capture_system_audio": TRUST_COMMAND,
    "kill_switch": TRUST_COMMAND,
    "extensions_install": TRUST_COMMAND,
    "extensions_uninstall": TRUST_COMMAND,
}

# Maximum trust level allowed per origin without extra confirmation.
# "user" origins (gemini, claude, goal_engine) can do everything.
# "proactive" is restricted: auto-approve 0-1, suggest 2, block 3+.
_ORIGIN_MAX_AUTO: dict[str, int] = {
    "gemini": TRUST_COMMAND,
    "claude": TRUST_COMMAND,
    "goal_engine": TRUST_COMMAND,
    "user": TRUST_COMMAND,
    "proactive": TRUST_LAUNCH,
}

_ORIGIN_MAX_SUGGEST: dict[str, int] = {
    "proactive": TRUST_INTERACT,
}


def get_trust_level(tool_name: str) -> int:
    """Return trust level for a tool. Defaults to TRUST_INTERACT."""
    return TOOL_TRUST_LEVELS.get(tool_name, TRUST_INTERACT)


# ─────────────────────────────────────────────────────────────
# SecurityResult
# ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SecurityResult:
    """Result of a security gate check."""
    allowed: bool
    reason: str = ""
    trust_level: int = 0
    sanitized_params: dict = None  # type: ignore[assignment]
    needs_confirmation: bool = False
    suggestion_only: bool = False   # proactive Tier 2: suggest, don't execute

    def __post_init__(self):
        if self.sanitized_params is None:
            object.__setattr__(self, "sanitized_params", {})


# ─────────────────────────────────────────────────────────────
# SecurityGate
# ─────────────────────────────────────────────────────────────

class SecurityGate:
    """Unified pre-execution security gate.

    Integrates:
    - SecurityManager (rate limits, permissions, invariants, backups)
    - ContentSanitizer (prompt injection detection in params)
    - Trust levels per origin

    All subsystems are Optional — if None, that check is skipped.
    Only SecurityManager blocking and trust-level blocking are hard stops.
    """

    def __init__(
        self,
        security_manager=None,
    ):
        self._security_manager = security_manager
        # Lazy-init injection scanner (zero deps, always available)
        self._content_sanitizer = None
        try:
            from .sanitizer import ContentSanitizer
            self._content_sanitizer = ContentSanitizer()
        except Exception:
            pass

    async def check(
        self,
        tool_name: str,
        params: dict,
        origin: str = "user",
        goal_id: str = "",
    ) -> SecurityResult:
        """Run all security checks before tool execution.

        Returns SecurityResult with allowed=True if all checks pass.
        """
        trust = get_trust_level(tool_name)
        sanitized = dict(params)

        # ── 1. Trust level vs origin ──
        max_auto = _ORIGIN_MAX_AUTO.get(origin, TRUST_LAUNCH)
        max_suggest = _ORIGIN_MAX_SUGGEST.get(origin, -1)

        if trust > max_auto:
            if trust <= max_suggest:
                return SecurityResult(
                    allowed=False,
                    reason=(
                        f"Proactive origin: {tool_name} (trust={trust}) "
                        f"exceeds auto-approve limit ({max_auto}), suggesting only"
                    ),
                    trust_level=trust,
                    sanitized_params=sanitized,
                    suggestion_only=True,
                )
            if origin == "proactive":
                return SecurityResult(
                    allowed=False,
                    reason=(
                        f"Proactive origin blocked: {tool_name} (trust={trust}) "
                        f"exceeds max allowed ({max_suggest})"
                    ),
                    trust_level=trust,
                    sanitized_params=sanitized,
                )

        # ── 2. SecurityManager (rate limits, permissions, invariants) ──
        if self._security_manager:
            try:
                decision = self._security_manager.check_action(
                    tool_name, params, goal_id=goal_id,
                )
                if not decision.allowed:
                    reason = "; ".join(decision.reasons) if decision.reasons else "Blocked by security manager"
                    return SecurityResult(
                        allowed=False,
                        reason=reason,
                        trust_level=trust,
                        sanitized_params=sanitized,
                    )
                if decision.needs_confirmation:
                    if origin == "proactive":
                        reason = "; ".join(decision.reasons) if decision.reasons else "Requires confirmation"
                        return SecurityResult(
                            allowed=False,
                            reason=f"Blocked for proactive: {tool_name} needs confirmation ({reason})",
                            trust_level=trust,
                            sanitized_params=sanitized,
                            needs_confirmation=True,
                        )
                    logger.warning(
                        "Security: %s needs confirmation, proceeding (origin=%s): %s",
                        tool_name, origin, decision.reasons,
                    )
            except Exception as e:
                logger.warning("SecurityManager error (non-blocking): %s", e)

        # ── 3. Injection scan on string params ──
        if self._content_sanitizer:
            try:
                for key, value in params.items():
                    if not isinstance(value, str) or len(value) < 10:
                        continue
                    scan = self._content_sanitizer.sanitize(
                        value, source=f"param:{key}",
                    )
                    if not scan.is_safe and scan.threat_level == "high":
                        logger.warning(
                            "Injection detected in param '%s' of %s: %s",
                            key, tool_name,
                            [t.pattern_name for t in scan.threats_found],
                        )
                        sanitized[key] = scan.sanitized_text
            except Exception as e:
                logger.warning("Injection scan error (non-blocking): %s", e)

        return SecurityResult(
            allowed=True,
            trust_level=trust,
            sanitized_params=sanitized,
        )
