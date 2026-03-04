"""PlanGranularityAdapter — adjusts verification based on per-app reliability.

High reliability apps (>0.9): minimal verification, fast execution.
Normal apps (0.5-0.9): standard verification.
Low reliability apps (0.3-0.5): cautious, extra verification + OCR.
Very low reliability (<0.3): paranoid, verify everything + fallbacks.

Based on Game AI "AI Director" adaptive difficulty pattern.

/ Granularidad adaptativa: apps confiables ejecutan rapido, apps fragiles verifican mas.
"""

import logging
from dataclasses import dataclass
from typing import Optional

from .scoring.reliability import ReliabilityTracker

logger = logging.getLogger("marlow.kernel.plan_granularity")


class GranularityLevel:
    """Constants for plan granularity levels."""

    MINIMAL = "minimal"       # high reliability: skip most verification
    STANDARD = "standard"     # normal: default behavior
    CAUTIOUS = "cautious"     # low reliability: add verification steps
    PARANOID = "paranoid"     # very low reliability: verify everything


@dataclass(frozen=True)
class GranularityConfig:
    """Configuration for a specific granularity level."""

    level: str
    verify_after_action: bool       # take screenshot after each action
    verify_with_ocr: bool           # OCR check after critical actions
    add_wait_after_action: float    # extra wait after each action (seconds)
    max_retries: int                # retries before replan
    add_fallback_steps: bool        # inject fallback alternatives

    @property
    def is_cautious(self) -> bool:
        return self.level in (GranularityLevel.CAUTIOUS, GranularityLevel.PARANOID)


# Predefined configs per level
GRANULARITY_CONFIGS: dict[str, GranularityConfig] = {
    GranularityLevel.MINIMAL: GranularityConfig(
        level=GranularityLevel.MINIMAL,
        verify_after_action=False,
        verify_with_ocr=False,
        add_wait_after_action=0.0,
        max_retries=1,
        add_fallback_steps=False,
    ),
    GranularityLevel.STANDARD: GranularityConfig(
        level=GranularityLevel.STANDARD,
        verify_after_action=True,
        verify_with_ocr=False,
        add_wait_after_action=0.3,
        max_retries=2,
        add_fallback_steps=False,
    ),
    GranularityLevel.CAUTIOUS: GranularityConfig(
        level=GranularityLevel.CAUTIOUS,
        verify_after_action=True,
        verify_with_ocr=True,
        add_wait_after_action=0.5,
        max_retries=3,
        add_fallback_steps=True,
    ),
    GranularityLevel.PARANOID: GranularityConfig(
        level=GranularityLevel.PARANOID,
        verify_after_action=True,
        verify_with_ocr=True,
        add_wait_after_action=1.0,
        max_retries=4,
        add_fallback_steps=True,
    ),
}


class PlanGranularityAdapter:
    """Adapts plan execution granularity based on per-app reliability.

    Usage::

        adapter = PlanGranularityAdapter(reliability_tracker)
        config = adapter.get_config("notepad")

        if config.verify_after_action:
            # take screenshot
        if config.verify_with_ocr:
            # run OCR check
        await asyncio.sleep(config.add_wait_after_action)
    """

    # Reliability thresholds
    THRESHOLD_MINIMAL = 0.9    # above = minimal verification
    THRESHOLD_STANDARD = 0.5   # above = standard
    THRESHOLD_CAUTIOUS = 0.3   # above = cautious
    # below 0.3 = paranoid

    def __init__(self, reliability_tracker: Optional[ReliabilityTracker] = None):
        self._reliability = reliability_tracker or ReliabilityTracker()
        self._overrides: dict[str, str] = {}  # app -> forced level

    def get_level(self, app_name: str, tool_name: str = "") -> str:
        """Determine granularity level for an app+tool combination."""
        key = app_name.lower().strip()

        # Check manual overrides first
        if key in self._overrides:
            return self._overrides[key]

        # Get reliability score
        reliability = (
            self._reliability.get_reliability(tool_name, key)
            if tool_name else None
        )

        if reliability is None:
            return GranularityLevel.STANDARD

        if reliability >= self.THRESHOLD_MINIMAL:
            return GranularityLevel.MINIMAL
        elif reliability >= self.THRESHOLD_STANDARD:
            return GranularityLevel.STANDARD
        elif reliability >= self.THRESHOLD_CAUTIOUS:
            return GranularityLevel.CAUTIOUS
        else:
            return GranularityLevel.PARANOID

    def get_config(self, app_name: str, tool_name: str = "") -> GranularityConfig:
        """Get full granularity config for an app."""
        level = self.get_level(app_name, tool_name)
        return GRANULARITY_CONFIGS[level]

    def set_override(self, app_name: str, level: str):
        """Force a specific granularity level for an app."""
        if level in GRANULARITY_CONFIGS:
            self._overrides[app_name.lower().strip()] = level
            logger.info("Granularity override: %s -> %s", app_name, level)

    def clear_override(self, app_name: str):
        """Remove a granularity override."""
        key = app_name.lower().strip()
        self._overrides.pop(key, None)

    def format_summary(self) -> str:
        """Format current granularity settings for debugging."""
        lines = ["Plan granularity settings:"]
        if self._overrides:
            for app, level in self._overrides.items():
                lines.append(f"  {app}: {level} (override)")
        else:
            lines.append("  No overrides (all adaptive)")
        return "\n".join(lines)
