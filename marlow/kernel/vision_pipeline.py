"""VisionPipeline — proactive monitor that decides how much attention to pay.

Manages vision checks after tool execution, cascading from cheap to expensive
sensors based on context. Connects UIA, OCR, and screenshot to SensorFusion.

Levels:
  PASSIVE (0): only react to events (UIA window open/close)
  LIGHT (1): check active window title between steps
  ACTIVE (2): window snapshot + dialog check after each action
  FULL (3): screenshot + OCR verification after each action

Source: Vision Research Part 2 — "UIA gives structure, OCR gives text, CV gives context"

/ Monitor proactivo con niveles de atencion al desktop.
"""

import logging
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional

from .sensor_fusion import SensorFusion, SensorTier

logger = logging.getLogger("marlow.kernel.vision_pipeline")


class MonitorLevel(IntEnum):
    """How much attention to pay to the desktop."""

    PASSIVE = 0    # Minimal: only react to events
    LIGHT = 1      # Light: check active window title between steps
    ACTIVE = 2     # Active: window snapshot + dialog check after each action
    FULL = 3       # Full: screenshot + OCR verification after each action


@dataclass(frozen=True)
class VisionCheck:
    """Result of a vision pipeline check."""

    level: MonitorLevel
    duration_ms: float
    elements_found: int
    active_window: str = ""
    dialog_detected: bool = False
    text_verified: str = ""
    needs_escalation: bool = False
    next_sensor: Optional[SensorTier] = None


class VisionPipeline:
    """Manages the vision check pipeline for Marlow.

    Decides which sensors to use based on context:
    - After opening an app: ACTIVE (verify it opened)
    - After typing text: LIGHT (check window title)
    - After clicking: ACTIVE (verify something happened)
    - After saving: FULL (OCR verify success message)
    - During idle: PASSIVE (just events)
    """

    # Tools that need specific verification levels
    TOOL_MONITOR_LEVELS: dict[str, MonitorLevel] = {
        # Active: verify something happened
        "open_application": MonitorLevel.ACTIVE,
        "manage_window": MonitorLevel.ACTIVE,
        "click": MonitorLevel.ACTIVE,
        "som_click": MonitorLevel.ACTIVE,
        "handle_dialog": MonitorLevel.ACTIVE,

        # Light: just check we're still in the right place
        "type_text": MonitorLevel.LIGHT,
        "press_key": MonitorLevel.LIGHT,
        "hotkey": MonitorLevel.LIGHT,
        "focus_window": MonitorLevel.LIGHT,

        # Passive: read-only, no verification needed
        "take_screenshot": MonitorLevel.PASSIVE,
        "list_windows": MonitorLevel.PASSIVE,
        "get_ui_tree": MonitorLevel.PASSIVE,
        "ocr_region": MonitorLevel.PASSIVE,
        "get_dialog_info": MonitorLevel.PASSIVE,
        "system_info": MonitorLevel.PASSIVE,
        "get_annotated_screenshot": MonitorLevel.PASSIVE,
        "find_elements": MonitorLevel.PASSIVE,
        "smart_find": MonitorLevel.PASSIVE,
    }

    def __init__(self):
        self._fusion = SensorFusion()
        self._check_history: list[VisionCheck] = []
        self._max_history = 100

    @property
    def fusion(self) -> SensorFusion:
        """Access the underlying sensor fusion engine."""
        return self._fusion

    def get_monitor_level(self, tool_name: str) -> MonitorLevel:
        """Determine monitoring level for a tool."""
        return self.TOOL_MONITOR_LEVELS.get(tool_name, MonitorLevel.LIGHT)

    def should_check(self, tool_name: str) -> bool:
        """Should we run a vision check after this tool?"""
        return self.get_monitor_level(tool_name) >= MonitorLevel.LIGHT

    def should_screenshot(self, tool_name: str) -> bool:
        """Should we take a screenshot after this tool?"""
        return self.get_monitor_level(tool_name) >= MonitorLevel.FULL

    def should_ocr(self, tool_name: str) -> bool:
        """Should we run OCR after this tool?"""
        return self.get_monitor_level(tool_name) >= MonitorLevel.FULL

    def record_check(self, check: VisionCheck):
        """Record a vision check result."""
        self._check_history.append(check)
        if len(self._check_history) > self._max_history:
            self._check_history.pop(0)

    def create_check(
        self,
        tool_name: str,
        duration_ms: float = 0.0,
        active_window: str = "",
        dialog_detected: bool = False,
        elements_found: int = 0,
        text_verified: str = "",
    ) -> VisionCheck:
        """Create and record a vision check."""
        level = self.get_monitor_level(tool_name)

        needs_escalation = bool(dialog_detected)

        check = VisionCheck(
            level=level,
            duration_ms=duration_ms,
            elements_found=elements_found,
            active_window=active_window,
            dialog_detected=dialog_detected,
            text_verified=text_verified,
            needs_escalation=needs_escalation,
            next_sensor=None,
        )

        self.record_check(check)
        return check

    def get_recent_checks(self, n: int = 10) -> list[VisionCheck]:
        """Get the N most recent vision checks."""
        return self._check_history[-n:]

    def get_dialog_rate(self, seconds: int = 60) -> float:
        """How many dialogs detected per minute in recent checks."""
        recent = [c for c in self._check_history if c.dialog_detected]
        if not recent:
            return 0.0
        return len(recent) * 60.0 / max(seconds, 1)

    def reset(self):
        """Reset fusion and history for a new task."""
        self._fusion.clear()
        self._check_history.clear()

    @property
    def stats(self) -> dict:
        checks = len(self._check_history)
        dialogs = sum(1 for c in self._check_history if c.dialog_detected)
        escalations = sum(1 for c in self._check_history if c.needs_escalation)
        return {
            "total_checks": checks,
            "dialogs_detected": dialogs,
            "escalations": escalations,
            "fusion": self._fusion.stats,
        }
