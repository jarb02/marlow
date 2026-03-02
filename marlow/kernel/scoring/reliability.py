"""EMA-based reliability tracking per tool+app combination.

Tracks an exponential moving average (alpha=0.3) of composite scores
per tool+app pair. Detects trends (improving/stable/degrading) and
provides formatted strings for the LLM planner prompt.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ReliabilityRecord:
    """Reliability data for a tool+app pair."""

    ema_score: float = 0.5  # Exponential moving average
    sample_count: int = 0
    recent_scores: list[float] = field(default_factory=list)  # Last N raw scores
    last_updated: float = 0.0  # monotonic
    trend: str = "stable"  # "improving" | "stable" | "degrading"


class ReliabilityTracker:
    """Tracks reliability per tool+app using EMA.

    Parameters
    ----------
    * **alpha** (float): EMA smoothing factor. 0.3 means ~77% weight
      on last 5 observations.
    * **min_samples** (int): Minimum samples before score is reliable.
    * **max_recent** (int): Maximum recent scores to keep.
    * **degradation_threshold** (float): Minimum diff to detect trend.
    """

    def __init__(
        self,
        alpha: float = 0.3,
        min_samples: int = 3,
        max_recent: int = 50,
        degradation_threshold: float = 0.15,
    ):
        self._alpha = alpha
        self._min_samples = min_samples
        self._max_recent = max_recent
        self._degradation_threshold = degradation_threshold
        self._records: dict[str, ReliabilityRecord] = {}

    def _key(self, tool_name: str, app_name: str = "") -> str:
        """Build lookup key."""
        return f"{tool_name}:{app_name}" if app_name else tool_name

    def record(self, tool_name: str, score: float, app_name: str = "") -> None:
        """Record a new score observation."""
        key = self._key(tool_name, app_name)
        rec = self._records.get(key)

        if rec is None:
            rec = ReliabilityRecord(
                ema_score=score,
                sample_count=1,
                recent_scores=[score],
                last_updated=time.monotonic(),
            )
            self._records[key] = rec
            return

        # Update EMA
        rec.ema_score = self._alpha * score + (1 - self._alpha) * rec.ema_score
        rec.sample_count += 1
        rec.recent_scores.append(score)
        if len(rec.recent_scores) > self._max_recent:
            rec.recent_scores = rec.recent_scores[-self._max_recent:]
        rec.last_updated = time.monotonic()

        # Update trend
        rec.trend = self._detect_trend(rec)

    def get_reliability(self, tool_name: str, app_name: str = "") -> float:
        """Get current reliability score. Returns 0.5 if unknown."""
        key = self._key(tool_name, app_name)
        rec = self._records.get(key)
        if rec is None or rec.sample_count < self._min_samples:
            return 0.5  # Unknown — neutral
        return rec.ema_score

    def is_reliable(
        self, tool_name: str, app_name: str = "", threshold: float = 0.6,
    ) -> bool:
        """Is this tool+app combination reliable enough?"""
        return self.get_reliability(tool_name, app_name) >= threshold

    def is_degrading(self, tool_name: str, app_name: str = "") -> bool:
        """Is reliability trending down?"""
        key = self._key(tool_name, app_name)
        rec = self._records.get(key)
        return rec.trend == "degrading" if rec else False

    def _detect_trend(self, rec: ReliabilityRecord) -> str:
        """Compare recent window vs older window."""
        scores = rec.recent_scores
        if len(scores) < 10:
            return "stable"

        window = 5
        recent_avg = sum(scores[-window:]) / window
        older_avg = sum(scores[-2 * window:-window]) / window

        diff = recent_avg - older_avg
        if diff < -self._degradation_threshold:
            return "degrading"
        elif diff > self._degradation_threshold:
            return "improving"
        return "stable"

    def get_report(
        self, tool_name: str, app_name: str = "",
    ) -> Optional[ReliabilityRecord]:
        """Get full reliability record."""
        return self._records.get(self._key(tool_name, app_name))

    def get_all_degrading(self) -> list[tuple[str, float]]:
        """Get all tool+app pairs that are degrading."""
        return [
            (key, rec.ema_score)
            for key, rec in self._records.items()
            if rec.trend == "degrading"
        ]

    def format_for_planner(
        self, tool_name: str, app_name: str = "",
    ) -> str:
        """Format reliability info for LLM planner prompt."""
        key = self._key(tool_name, app_name)
        rec = self._records.get(key)
        if not rec or rec.sample_count < self._min_samples:
            return f"{tool_name}(): unknown (no data)"

        trend_str = (
            "\u2191" if rec.trend == "improving"
            else "\u2193" if rec.trend == "degrading"
            else "\u2192"
        )
        return f"{tool_name}(): {rec.ema_score:.2f} {trend_str}"
