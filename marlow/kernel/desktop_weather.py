"""DesktopWeather — Ring buffer trend tracking for desktop state.

Classifies the desktop into 4 climate levels based on dialog rate,
error rate, window changes, and active window count:

    ESTABLE   — calm, safe to execute
    OCUPADO   — busy, many windows/focus changes
    INESTABLE — unstable, dialogs appearing, apps not responding
    TORMENTA  — storm, crashes, rapid errors, multiple dialogs

Based on Game AI World State patterns (RDR2, BotW).

/ Tendencias del escritorio con ring buffers y 4 niveles de clima.
"""

import logging
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger("marlow.kernel.desktop_weather")


class Climate(Enum):
    """Desktop climate classification."""
    ESTABLE = "estable"       # calm: 0 dialogs, few windows, low CPU
    OCUPADO = "ocupado"       # busy: many windows, frequent focus changes
    INESTABLE = "inestable"   # unstable: dialogs appearing, apps not responding
    TORMENTA = "tormenta"     # storm: crashes, rapid errors, multiple dialogs


@dataclass(frozen=True)
class WeatherReport:
    """Current desktop weather snapshot."""
    climate: Climate
    dialog_rate: float        # dialogs per minute
    error_rate: float         # errors per minute
    window_change_rate: float # window changes per minute
    active_windows: int
    confidence: float         # 0-1, how confident in classification

    @property
    def is_safe_to_execute(self) -> bool:
        """Is it safe to execute actions in this climate?"""
        return self.climate in (Climate.ESTABLE, Climate.OCUPADO)

    @property
    def should_pause(self) -> bool:
        """Should Marlow pause and wait?"""
        return self.climate == Climate.TORMENTA


class DesktopWeather:
    """Tracks desktop state trends using ring buffers.

    Classifies into 4 climate levels for decision making.
    Feeds into PreActionScorer and planner for context-aware decisions.

    Based on Game AI World State patterns (Red Dead Redemption 2, BotW).
    """

    BUFFER_SECONDS = 60  # track last 60 seconds

    # Thresholds for classification
    DIALOG_RATE_INESTABLE = 2.0    # 2+ dialogs/min = inestable
    DIALOG_RATE_TORMENTA = 4.0     # 4+ dialogs/min = tormenta
    ERROR_RATE_INESTABLE = 2.0     # 2+ errors/min = inestable
    ERROR_RATE_TORMENTA = 3.0      # 3+ errors/min = tormenta
    WINDOW_CHANGE_OCUPADO = 6.0    # 6+ changes/min = ocupado
    WINDOW_CHANGE_INESTABLE = 15.0 # 15+ changes/min = inestable
    ACTIVE_WINDOWS_OCUPADO = 5     # 5+ windows = ocupado

    def __init__(self, buffer_seconds: int = 60):
        self._buffer_seconds = buffer_seconds
        self._dialog_events: deque[float] = deque()    # timestamps
        self._error_events: deque[float] = deque()     # timestamps
        self._window_events: deque[float] = deque()    # timestamps
        self._active_window_count: int = 0
        self._last_report: Optional[WeatherReport] = None

    def record_dialog(self):
        """Record that a dialog appeared."""
        self._dialog_events.append(time.time())
        self._trim_buffers()

    def record_error(self):
        """Record that an error occurred."""
        self._error_events.append(time.time())
        self._trim_buffers()

    def record_window_change(self):
        """Record a window appeared/disappeared/focus change."""
        self._window_events.append(time.time())
        self._trim_buffers()

    def update_window_count(self, count: int):
        """Update the number of active windows."""
        self._active_window_count = max(0, count)

    def _trim_buffers(self):
        """Remove events older than buffer_seconds."""
        cutoff = time.time() - self._buffer_seconds
        while self._dialog_events and self._dialog_events[0] < cutoff:
            self._dialog_events.popleft()
        while self._error_events and self._error_events[0] < cutoff:
            self._error_events.popleft()
        while self._window_events and self._window_events[0] < cutoff:
            self._window_events.popleft()

    def _rate(self, events: deque) -> float:
        """Calculate events per minute from buffer."""
        if not events:
            return 0.0
        elapsed = time.time() - events[0] if len(events) > 1 else self._buffer_seconds
        elapsed = max(1.0, elapsed)  # avoid division by zero
        return len(events) * 60.0 / elapsed

    def get_report(self) -> WeatherReport:
        """Analyze current desktop state and return a weather report."""
        self._trim_buffers()

        dialog_rate = self._rate(self._dialog_events)
        error_rate = self._rate(self._error_events)
        window_rate = self._rate(self._window_events)
        windows = self._active_window_count

        # Classify climate (worst wins)
        climate = Climate.ESTABLE
        confidence = 0.9

        # Check for TORMENTA first (most severe)
        if (dialog_rate >= self.DIALOG_RATE_TORMENTA
                or error_rate >= self.ERROR_RATE_TORMENTA):
            climate = Climate.TORMENTA
            confidence = min(
                1.0,
                max(dialog_rate / self.DIALOG_RATE_TORMENTA,
                    error_rate / self.ERROR_RATE_TORMENTA) * 0.5 + 0.5,
            )

        # INESTABLE
        elif (dialog_rate >= self.DIALOG_RATE_INESTABLE
              or error_rate >= self.ERROR_RATE_INESTABLE
              or window_rate >= self.WINDOW_CHANGE_INESTABLE):
            climate = Climate.INESTABLE
            confidence = 0.75

        # OCUPADO
        elif (window_rate >= self.WINDOW_CHANGE_OCUPADO
              or windows >= self.ACTIVE_WINDOWS_OCUPADO):
            climate = Climate.OCUPADO
            confidence = 0.7

        report = WeatherReport(
            climate=climate,
            dialog_rate=round(dialog_rate, 1),
            error_rate=round(error_rate, 1),
            window_change_rate=round(window_rate, 1),
            active_windows=windows,
            confidence=round(confidence, 2),
        )

        # Log climate changes
        if self._last_report and self._last_report.climate != climate:
            logger.info(
                "Desktop climate changed: %s -> %s",
                self._last_report.climate.value, climate.value,
            )

        self._last_report = report
        return report

    def format_for_planner(self) -> str:
        """Format weather for LLM planner context."""
        report = self.get_report()
        return (
            f"Desktop climate: {report.climate.value} "
            f"(dialogs: {report.dialog_rate}/min, errors: {report.error_rate}/min, "
            f"window changes: {report.window_change_rate}/min, "
            f"active windows: {report.active_windows})"
        )
