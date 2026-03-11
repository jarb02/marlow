"""PatternDetector — Detects repetitive user behavior from action logs.

Analyzes historical action_logs to find:
- Temporal patterns: recurring actions at consistent times/days
- Sequential patterns: action pairs that frequently follow each other
- Contextual patterns: correlations between active app and executed actions

Patterns feed into ProactiveEngine for suggestion/auto-execution.

/ Detector de patrones — analiza logs para encontrar rutinas del usuario.
"""

import asyncio
import hashlib
import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("marlow.kernel.pattern_detector")


# ── Data types ───────────────────────────────────────────────


@dataclass
class Pattern:
    """A detected behavioral pattern."""
    id: str                        # deterministic hash
    type: str                      # "temporal", "sequential", "contextual"
    tool_name: str                 # primary tool involved
    params: dict = field(default_factory=dict)  # common params
    confidence: float = 0.0        # 0.0–1.0
    occurrences: int = 0
    last_seen: str = ""            # ISO timestamp
    # Temporal-specific
    schedule_days: list[int] = field(default_factory=list)  # 0=Mon..6=Sun
    schedule_hour: int = -1
    schedule_minute: int = -1
    # Sequential-specific
    trigger_tool: str = ""         # action A that precedes this
    trigger_params: dict = field(default_factory=dict)
    gap_seconds: float = 0.0      # average gap between A and B
    # Contextual-specific
    context_app: str = ""          # app that was active when this ran
    # Feedback
    approvals: int = 0
    rejections: int = 0
    consecutive_rejections: int = 0
    active: bool = True

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "tool_name": self.tool_name,
            "params": self.params,
            "confidence": self.confidence,
            "occurrences": self.occurrences,
            "last_seen": self.last_seen,
            "schedule_days": self.schedule_days,
            "schedule_hour": self.schedule_hour,
            "schedule_minute": self.schedule_minute,
            "trigger_tool": self.trigger_tool,
            "trigger_params": self.trigger_params,
            "gap_seconds": self.gap_seconds,
            "context_app": self.context_app,
            "approvals": self.approvals,
            "rejections": self.rejections,
            "active": self.active,
        }


def _pattern_id(type_: str, tool_name: str, extra: str = "") -> str:
    """Generate deterministic pattern ID."""
    key = f"{type_}:{tool_name}:{extra}"
    return hashlib.sha256(key.encode()).hexdigest()[:12]


# ── Constants ────────────────────────────────────────────────

DEFAULT_SCAN_INTERVAL = 1800  # 30 minutes
LOOKBACK_DAYS = 7
MIN_OCCURRENCES_TEMPORAL = 3
TEMPORAL_CONFIDENCE_THRESHOLD = 0.80
SEQUENTIAL_CONFIDENCE_THRESHOLD = 0.70
CONTEXTUAL_CONFIDENCE_THRESHOLD = 0.70
TIME_WINDOW_MINUTES = 15  # ±15 min for temporal match
DUE_WINDOW_MINUTES = 10   # ±10 min for "due now"
SEQUENTIAL_GAP_SECONDS = 300  # 5 minutes between A → B


class PatternDetector:
    """Analyzes action logs to detect behavioral patterns.

    Runs as a periodic background task. Stores detected patterns
    in MemorySystem for persistence across restarts.
    """

    def __init__(
        self,
        log_repo: Any = None,
        memory: Any = None,
        scan_interval: float = DEFAULT_SCAN_INTERVAL,
        confidence_threshold: float = 0.9,
    ):
        self._log_repo = log_repo
        self._memory = memory
        self._scan_interval = scan_interval
        self._confidence_threshold = confidence_threshold

        # In-memory pattern cache (loaded from memory on start)
        self._patterns: dict[str, Pattern] = {}
        self._last_scan: Optional[float] = None

        # Lifecycle
        self._task: Optional[asyncio.Task] = None
        self._stopping = False

    # ── Public API ───────────────────────────────────────────

    def get_active_patterns(self) -> list[Pattern]:
        """Return patterns above confidence threshold."""
        return [
            p for p in self._patterns.values()
            if p.active and p.confidence >= self._confidence_threshold
        ]

    def get_due_patterns(self, now: Optional[datetime] = None) -> list[Pattern]:
        """Return temporal patterns due for execution now (±10 min)."""
        if now is None:
            now = datetime.now()

        due = []
        for p in self._patterns.values():
            if not p.active or p.type != "temporal":
                continue
            if p.confidence < self._confidence_threshold:
                continue
            if now.weekday() not in p.schedule_days:
                continue

            # Check time window
            scheduled_min = p.schedule_hour * 60 + p.schedule_minute
            current_min = now.hour * 60 + now.minute
            if abs(current_min - scheduled_min) <= DUE_WINDOW_MINUTES:
                due.append(p)

        return due

    def record_feedback(self, pattern_id: str, approved: bool):
        """Record user feedback on a pattern suggestion."""
        p = self._patterns.get(pattern_id)
        if not p:
            return

        if approved:
            p.approvals += 1
            p.consecutive_rejections = 0
            p.confidence = min(1.0, p.confidence + 0.02)
        else:
            p.rejections += 1
            p.consecutive_rejections += 1
            p.confidence = max(0.0, p.confidence - 0.05)

            if p.consecutive_rejections >= 3:
                p.confidence = 0.0
                p.active = False
                logger.info(
                    "Pattern %s deactivated after 3 consecutive rejections",
                    pattern_id,
                )

    async def run(self):
        """Background task: periodic pattern analysis."""
        logger.info("PatternDetector starting (interval=%ds)", self._scan_interval)

        # Load persisted patterns from memory
        await self._load_patterns()

        while not self._stopping:
            try:
                await self._scan()
                self._last_scan = time.time()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("PatternDetector scan error: %s", e)

            try:
                await asyncio.sleep(self._scan_interval)
            except asyncio.CancelledError:
                break

        logger.info("PatternDetector stopped")

    def stop(self):
        """Signal shutdown."""
        self._stopping = True
        if self._task and not self._task.done():
            self._task.cancel()

    # ── Pattern analysis ─────────────────────────────────────

    async def _scan(self):
        """Run full pattern analysis on recent logs."""
        if not self._log_repo:
            return

        logs = await self._fetch_logs()
        if len(logs) < 10:
            logger.debug("Not enough logs for pattern analysis (%d)", len(logs))
            return

        # Detect each pattern type
        temporal = self._detect_temporal(logs)
        sequential = self._detect_sequential(logs)
        contextual = self._detect_contextual(logs)

        # Merge new patterns (update confidence if existing, add if new)
        new_count = 0
        for p in temporal + sequential + contextual:
            if p.id in self._patterns:
                existing = self._patterns[p.id]
                # Update stats but preserve feedback
                existing.occurrences = p.occurrences
                existing.last_seen = p.last_seen
                # Blend confidence: 70% new data, 30% old (preserves feedback)
                existing.confidence = 0.7 * p.confidence + 0.3 * existing.confidence
            else:
                self._patterns[p.id] = p
                new_count += 1

        if new_count:
            logger.info("Detected %d new patterns (%d total)", new_count, len(self._patterns))

        # Persist to memory
        await self._save_patterns()

    async def _fetch_logs(self) -> list[dict]:
        """Fetch action logs for the last LOOKBACK_DAYS days."""
        try:
            # LogRepository.get_recent() is the available method
            # Fetch enough to cover ~7 days (assuming ~100 actions/day)
            return await self._log_repo.get_recent(n=1000)
        except Exception as e:
            logger.debug("Failed to fetch logs: %s", e)
            return []

    def _detect_temporal(self, logs: list[dict]) -> list[Pattern]:
        """Find actions that occur regularly at specific times."""
        patterns = []

        # Group by (tool_name, app_name)
        groups: dict[tuple, list[dict]] = defaultdict(list)
        for log in logs:
            if not log.get("success"):
                continue
            key = (log["tool_name"], log.get("app_name", ""))
            groups[key].append(log)

        for (tool, app), entries in groups.items():
            if len(entries) < MIN_OCCURRENCES_TEMPORAL:
                continue

            # Parse timestamps, group by hour (across all weekdays)
            hour_slots: dict[int, list] = defaultdict(list)
            days_seen: set[str] = set()
            for e in entries:
                try:
                    ts = datetime.fromisoformat(
                        e["timestamp"].replace("Z", "+00:00")
                    )
                    hour_slots[ts.hour].append(ts)
                    days_seen.add(ts.strftime("%Y-%m-%d"))
                except (ValueError, KeyError):
                    continue

            total_days = len(days_seen) or 1

            for hour, timestamps in hour_slots.items():
                if len(timestamps) < MIN_OCCURRENCES_TEMPORAL:
                    continue

                # How many unique days had this action at this hour?
                unique_days = len({ts.strftime("%Y-%m-%d") for ts in timestamps})
                # What fraction of days in the lookback had this?
                confidence = min(1.0, unique_days / max(1, total_days))

                if confidence < TEMPORAL_CONFIDENCE_THRESHOLD * 0.8:
                    continue  # not regular enough

                avg_minute = int(
                    sum(ts.minute for ts in timestamps) / len(timestamps)
                )

                # Collect which weekdays this pattern appears on
                weekdays = sorted({ts.weekday() for ts in timestamps})

                # Build params from most common parameters
                common_params = self._most_common_params(
                    [e for e in entries
                     if self._parse_hour(e) == hour]
                )

                extra = f"{app}:{hour}"
                pid = _pattern_id("temporal", tool, extra)

                patterns.append(Pattern(
                    id=pid,
                    type="temporal",
                    tool_name=tool,
                    params=common_params,
                    confidence=confidence,
                    occurrences=len(timestamps),
                    last_seen=max(ts.isoformat() for ts in timestamps),
                    schedule_days=weekdays,
                    schedule_hour=hour,
                    schedule_minute=avg_minute,
                ))

        return patterns

    def _detect_sequential(self, logs: list[dict]) -> list[Pattern]:
        """Find action pairs (A → B) that frequently co-occur."""
        patterns = []

        # Sort by timestamp ascending
        sorted_logs = sorted(logs, key=lambda x: x.get("timestamp", ""))
        if len(sorted_logs) < 2:
            return patterns

        # Count pairs (A → B within 5 min)
        pair_counts: dict[tuple, list[float]] = defaultdict(list)  # gaps
        tool_counts: dict[str, int] = defaultdict(int)

        for i in range(len(sorted_logs) - 1):
            a = sorted_logs[i]
            b = sorted_logs[i + 1]
            if not a.get("success") or not b.get("success"):
                continue

            tool_counts[a["tool_name"]] += 1

            try:
                ts_a = datetime.fromisoformat(a["timestamp"].replace("Z", "+00:00"))
                ts_b = datetime.fromisoformat(b["timestamp"].replace("Z", "+00:00"))
                gap = (ts_b - ts_a).total_seconds()
            except (ValueError, KeyError):
                continue

            if 0 < gap <= SEQUENTIAL_GAP_SECONDS:
                key = (a["tool_name"], b["tool_name"])
                pair_counts[key].append(gap)

        for (tool_a, tool_b), gaps in pair_counts.items():
            if len(gaps) < 3:
                continue
            count_a = tool_counts.get(tool_a, 1)
            confidence = min(1.0, len(gaps) / max(1, count_a))

            if confidence < SEQUENTIAL_CONFIDENCE_THRESHOLD * 0.8:
                continue

            avg_gap = sum(gaps) / len(gaps)
            pid = _pattern_id("sequential", tool_b, f"after:{tool_a}")

            patterns.append(Pattern(
                id=pid,
                type="sequential",
                tool_name=tool_b,
                confidence=confidence,
                occurrences=len(gaps),
                last_seen=sorted_logs[-1].get("timestamp", ""),
                trigger_tool=tool_a,
                gap_seconds=round(avg_gap, 1),
            ))

        return patterns

    def _detect_contextual(self, logs: list[dict]) -> list[Pattern]:
        """Find correlations between active app and executed actions."""
        patterns = []

        # Group by (app_name, tool_name)
        context_counts: dict[tuple, int] = defaultdict(int)
        tool_counts: dict[str, int] = defaultdict(int)

        for log in logs:
            if not log.get("success"):
                continue
            app = log.get("app_name", "")
            tool = log["tool_name"]
            if app:
                context_counts[(app, tool)] += 1
            tool_counts[tool] += 1

        for (app, tool), count in context_counts.items():
            if count < 3:
                continue
            total_tool = tool_counts.get(tool, 1)
            # What fraction of this tool's usage was with this app?
            confidence = min(1.0, count / max(1, total_tool))

            if confidence < CONTEXTUAL_CONFIDENCE_THRESHOLD * 0.8:
                continue

            pid = _pattern_id("contextual", tool, f"app:{app}")

            patterns.append(Pattern(
                id=pid,
                type="contextual",
                tool_name=tool,
                confidence=confidence,
                occurrences=count,
                last_seen="",
                context_app=app,
            ))

        return patterns

    # ── Helpers ───────────────────────────────────────────────

    @staticmethod
    def _parse_hour(entry: dict) -> int:
        try:
            ts = datetime.fromisoformat(
                entry["timestamp"].replace("Z", "+00:00")
            )
            return ts.hour
        except (ValueError, KeyError):
            return -1

    @staticmethod
    def _most_common_params(entries: list[dict]) -> dict:
        """Extract the most common parameter values."""
        if not entries:
            return {}
        param_counts: dict[str, dict] = defaultdict(lambda: defaultdict(int))
        for e in entries:
            params = e.get("parameters", {})
            if isinstance(params, str):
                try:
                    params = json.loads(params)
                except (json.JSONDecodeError, TypeError):
                    continue
            for k, v in params.items():
                if isinstance(v, (str, int, float, bool)):
                    param_counts[k][str(v)] += 1

        result = {}
        for k, values in param_counts.items():
            if values:
                best = max(values, key=values.get)
                # Only include if >50% agreement
                total = sum(values.values())
                if values[best] / total > 0.5:
                    result[k] = best
        return result

    # ── Persistence ──────────────────────────────────────────

    async def _load_patterns(self):
        """Load persisted patterns from MemorySystem."""
        if not self._memory:
            return
        try:
            memories = await self._memory.recall(
                tier="long", category="pattern", limit=100,
            )
            for mem in memories:
                data = mem.content if hasattr(mem, "content") else mem
                if isinstance(data, dict) and "id" in data:
                    p = Pattern(**{
                        k: v for k, v in data.items()
                        if k in Pattern.__dataclass_fields__
                    })
                    self._patterns[p.id] = p

            if self._patterns:
                logger.info("Loaded %d patterns from memory", len(self._patterns))
        except Exception as e:
            logger.debug("Failed to load patterns: %s", e)

    async def _save_patterns(self):
        """Persist active patterns to MemorySystem."""
        if not self._memory:
            return
        try:
            for p in self._patterns.values():
                if not p.active:
                    continue
                await self._memory.remember_long(
                    content=p.to_dict(),
                    category="pattern",
                    tags=[p.type, f"tool:{p.tool_name}"],
                    relevance=p.confidence,
                )
        except Exception as e:
            logger.debug("Failed to save patterns: %s", e)
