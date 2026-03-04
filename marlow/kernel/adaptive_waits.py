"""AdaptiveWaits — EMA per app for launch timing, replaces hardcoded sleeps.

Learns how long each app takes to become ready after launch using
Exponential Moving Average (alpha=0.3). Includes known baselines for
18 common apps and a 50% safety buffer clamped to [0.5, 15.0]s.

Based on Game AI "AI Director" adaptive timing pattern.

/ Tiempos de espera adaptativos por app con EMA.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("marlow.kernel.adaptive_waits")


@dataclass
class AppTiming:
    """EMA-tracked timing for a specific app."""
    app_name: str
    ema_seconds: float = 2.0     # current EMA estimate
    sample_count: int = 0
    min_observed: float = float("inf")
    max_observed: float = 0.0
    last_updated: float = 0.0

    @property
    def recommended_wait(self) -> float:
        """Recommended wait = EMA * 1.5 (50% buffer), clamped to [0.5, 15.0]."""
        wait = self.ema_seconds * 1.5
        return max(0.5, min(15.0, wait))

    @property
    def has_data(self) -> bool:
        return self.sample_count > 0


class AdaptiveWaits:
    """Learns how long each app takes to become ready after launch.

    Uses Exponential Moving Average (EMA, alpha=0.3) per app.
    Replaces hardcoded sleep(2.0) with data-driven waits.

    Usage::

        waits = AdaptiveWaits()

        # Before launching app:
        wait_time = waits.get_wait("notepad")  # returns recommended wait

        # After app is ready, record actual time:
        waits.record("notepad", actual_seconds=0.8)

        # Next time, get_wait returns updated estimate
    """

    DEFAULT_WAIT = 2.0  # seconds, used when no data available
    EMA_ALPHA = 0.3     # how fast to adapt (higher = more reactive)
    BUFFER_MULTIPLIER = 1.5  # safety margin over EMA
    MIN_WAIT = 0.5
    MAX_WAIT = 15.0

    # Known baseline waits for common apps (before any learning)
    KNOWN_BASELINES: dict[str, float] = {
        "notepad": 0.8,
        "calculator": 1.0,
        "paint": 1.2,
        "wordpad": 1.5,
        "explorer": 1.5,
        "cmd": 0.5,
        "powershell": 1.0,
        "terminal": 1.5,
        "code": 4.0,       # VS Code is slow
        "vscode": 4.0,
        "excel": 5.0,
        "word": 5.0,
        "outlook": 6.0,
        "teams": 8.0,
        "slack": 5.0,
        "chrome": 3.0,
        "firefox": 3.0,
        "edge": 2.5,
    }

    def __init__(self, alpha: float = 0.3):
        self._alpha = alpha
        self._timings: dict[str, AppTiming] = {}

    def get_wait(self, app_name: str) -> float:
        """Get recommended wait time for an app.

        Returns EMA * buffer if we have data, or known baseline, or default.
        """
        key = app_name.lower().strip()

        # Check if we have learned data
        if key in self._timings and self._timings[key].has_data:
            wait = self._timings[key].recommended_wait
            logger.debug(
                "AdaptiveWait for %s: %.1fs (EMA=%.1fs, n=%d)",
                key, wait, self._timings[key].ema_seconds,
                self._timings[key].sample_count,
            )
            return wait

        # Check known baselines
        for known_key, baseline in self.KNOWN_BASELINES.items():
            if known_key in key or key in known_key:
                wait = min(self.MAX_WAIT, baseline * self.BUFFER_MULTIPLIER)
                logger.debug("AdaptiveWait for %s: %.1fs (baseline)", key, wait)
                return wait

        # Default
        logger.debug("AdaptiveWait for %s: %.1fs (default)", key, self.DEFAULT_WAIT)
        return self.DEFAULT_WAIT

    def record(self, app_name: str, actual_seconds: float):
        """Record how long an app actually took to become ready.

        Updates EMA for future predictions.
        """
        key = app_name.lower().strip()
        actual_seconds = max(0.1, actual_seconds)  # clamp minimum

        if key not in self._timings:
            # First observation: use actual as initial EMA
            self._timings[key] = AppTiming(
                app_name=key,
                ema_seconds=actual_seconds,
                sample_count=1,
                min_observed=actual_seconds,
                max_observed=actual_seconds,
                last_updated=time.time(),
            )
        else:
            timing = self._timings[key]
            # EMA update: new = alpha * actual + (1-alpha) * old
            timing.ema_seconds = (
                self._alpha * actual_seconds
                + (1 - self._alpha) * timing.ema_seconds
            )
            timing.sample_count += 1
            timing.min_observed = min(timing.min_observed, actual_seconds)
            timing.max_observed = max(timing.max_observed, actual_seconds)
            timing.last_updated = time.time()

        logger.info(
            "AdaptiveWait recorded %s: %.1fs (EMA now %.1fs, n=%d)",
            key, actual_seconds, self._timings[key].ema_seconds,
            self._timings[key].sample_count,
        )

    def get_timing(self, app_name: str) -> Optional[AppTiming]:
        """Get full timing data for an app."""
        return self._timings.get(app_name.lower().strip())

    def get_all_timings(self) -> dict[str, AppTiming]:
        """Get all recorded timings."""
        return dict(self._timings)

    def format_for_planner(self) -> str:
        """Format timing data for LLM planner context."""
        if not self._timings:
            return "No app timing data collected yet."
        lines = []
        for key, t in sorted(self._timings.items()):
            if t.has_data:
                lines.append(
                    f"  {t.app_name}: ~{t.ema_seconds:.1f}s "
                    f"(wait: {t.recommended_wait:.1f}s, n={t.sample_count})"
                )
        if not lines:
            return "No app timing data collected yet."
        return "App launch timings:\n" + "\n".join(lines)
