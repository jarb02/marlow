"""Kernel constants — risk classification for every MCP tool.

Each tool is mapped to a risk level that determines:
- safe: read-only, no side effects, can execute freely
- moderate: low-risk side effects, reversible, runs with basic checks
- dangerous: significant side effects, needs safety engine approval
- critical: irreversible or privacy-sensitive, requires explicit confirmation
"""

from __future__ import annotations

# Risk levels (ordered by severity)
RISK_SAFE = "safe"
RISK_MODERATE = "moderate"
RISK_DANGEROUS = "dangerous"
RISK_CRITICAL = "critical"

RISK_LEVELS = (RISK_SAFE, RISK_MODERATE, RISK_DANGEROUS, RISK_CRITICAL)

# ── Tool Risk Map ──
# Maps every registered MCP tool name to its risk level.
# New tools MUST be added here before registration.

TOOL_RISK_MAP: dict[str, str] = {
    # ─── Safe: read-only, no side effects ───
    "take_screenshot":          RISK_SAFE,
    "get_ui_tree":              RISK_SAFE,
    "list_windows":             RISK_SAFE,
    "ocr_region":               RISK_SAFE,
    "list_ocr_languages":       RISK_SAFE,
    "get_annotated_screenshot": RISK_SAFE,
    "smart_find":               RISK_SAFE,
    "find_elements":            RISK_SAFE,
    "cascade_find":             RISK_SAFE,
    "detect_app_framework":     RISK_SAFE,
    "get_dialog_info":          RISK_SAFE,
    "get_ui_events":            RISK_SAFE,
    "get_error_journal":        RISK_SAFE,
    "clear_error_journal":      RISK_SAFE,
    "get_suggestions":          RISK_SAFE,
    "accept_suggestion":        RISK_SAFE,
    "dismiss_suggestion":       RISK_SAFE,
    "get_agent_screen_state":   RISK_SAFE,
    "visual_diff":              RISK_SAFE,
    "visual_diff_compare":      RISK_SAFE,
    "wait_for_element":         RISK_SAFE,
    "wait_for_text":            RISK_SAFE,
    "wait_for_window":          RISK_SAFE,
    "wait_for_idle":            RISK_SAFE,
    "memory_recall":            RISK_SAFE,
    "memory_list":              RISK_SAFE,
    "system_info":              RISK_SAFE,
    "list_watchers":            RISK_SAFE,
    "list_scheduled_tasks":     RISK_SAFE,
    "get_task_history":         RISK_SAFE,
    "workflow_list":            RISK_SAFE,
    "get_voice_hotkey_status":  RISK_SAFE,
    "run_diagnostics":          RISK_SAFE,
    "clipboard_history":        RISK_SAFE,
    "extensions_list":          RISK_SAFE,
    "extensions_audit":         RISK_SAFE,
    # CDP read-only
    "cdp_discover":             RISK_SAFE,
    "cdp_list_connections":     RISK_SAFE,
    "cdp_screenshot":           RISK_SAFE,
    "cdp_get_dom":              RISK_SAFE,
    "cdp_get_knowledge_base":   RISK_SAFE,

    # ─── Moderate: low-risk side effects, reversible ───
    "click":                    RISK_MODERATE,
    "type_text":                RISK_MODERATE,
    "press_key":                RISK_MODERATE,
    "hotkey":                   RISK_MODERATE,
    "som_click":                RISK_MODERATE,
    "focus_window":             RISK_MODERATE,
    "restore_user_focus":       RISK_MODERATE,
    "open_application":         RISK_MODERATE,
    "scrape_url":               RISK_MODERATE,
    "toggle_voice_overlay":     RISK_MODERATE,
    "start_ui_monitor":         RISK_MODERATE,
    "stop_ui_monitor":          RISK_MODERATE,
    "set_agent_screen_only":    RISK_MODERATE,
    "setup_background_mode":    RISK_MODERATE,
    "move_to_agent_screen":     RISK_MODERATE,
    "move_to_user_screen":      RISK_MODERATE,
    "download_whisper_model":   RISK_MODERATE,
    # CDP connections
    "cdp_connect":              RISK_MODERATE,
    "cdp_disconnect":           RISK_MODERATE,
    "cdp_send":                 RISK_MODERATE,
    "cdp_ensure":               RISK_MODERATE,

    # ─── Dangerous: significant side effects ───
    "manage_window":            RISK_DANGEROUS,
    "clipboard":                RISK_DANGEROUS,
    "memory_save":              RISK_DANGEROUS,
    "memory_delete":            RISK_DANGEROUS,
    "workflow_record":          RISK_DANGEROUS,
    "workflow_stop":            RISK_DANGEROUS,
    "workflow_run":             RISK_DANGEROUS,
    "workflow_delete":          RISK_DANGEROUS,
    "schedule_task":            RISK_DANGEROUS,
    "remove_task":              RISK_DANGEROUS,
    "watch_folder":             RISK_DANGEROUS,
    "unwatch_folder":           RISK_DANGEROUS,
    "get_watch_events":         RISK_DANGEROUS,
    "speak":                    RISK_DANGEROUS,
    "speak_and_listen":         RISK_DANGEROUS,
    "handle_dialog":            RISK_DANGEROUS,
    "transcribe_audio":         RISK_DANGEROUS,
    "listen_for_command":       RISK_DANGEROUS,
    # CDP write actions
    "cdp_click":                RISK_DANGEROUS,
    "cdp_type_text":            RISK_DANGEROUS,
    "cdp_key_combo":            RISK_DANGEROUS,
    "cdp_click_selector":       RISK_DANGEROUS,
    "cdp_evaluate":             RISK_DANGEROUS,
    "cdp_restart_confirmed":    RISK_DANGEROUS,

    # ─── Critical: irreversible or privacy-sensitive ───
    "run_command":              RISK_CRITICAL,
    "run_app_script":           RISK_CRITICAL,
    "capture_mic_audio":        RISK_CRITICAL,
    "capture_system_audio":     RISK_CRITICAL,
    "kill_switch":              RISK_CRITICAL,
    "extensions_install":       RISK_CRITICAL,
    "extensions_uninstall":     RISK_CRITICAL,
}


def get_risk_level(tool_name: str) -> str:
    """Return the risk level for a tool. Defaults to dangerous if unknown."""
    return TOOL_RISK_MAP.get(tool_name, RISK_DANGEROUS)
