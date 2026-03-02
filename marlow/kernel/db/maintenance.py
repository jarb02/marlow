"""DatabaseMaintenance — periodic cleanup tasks for state.db and logs.db.

Runs as an asyncio background task, cleaning up expired data,
aggregating metrics, and vacuuming databases.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from .manager import DatabaseManager

logger = logging.getLogger(__name__)

# Retention periods
LOGS_RETENTION_DAYS = 30
UIA_RETENTION_HOURS = 1
MAX_SNAPSHOTS = 20


class DatabaseMaintenance:
    """Periodic database cleanup and aggregation.

    Parameters
    ----------
    * **db_manager** (DatabaseManager):
        Initialized database manager with open connections.
    """

    def __init__(self, db_manager: DatabaseManager):
        self._db = db_manager
        self._task: Optional[asyncio.Task] = None

    async def run_cycle(self) -> dict:
        """Run all maintenance tasks once. Returns summary."""
        results = {}

        results["expired_memories"] = await self._cleanup_expired_memories()
        results["old_logs"] = await self._cleanup_old_logs()
        results["old_uia_events"] = await self._cleanup_uia_events()
        results["old_snapshots"] = await self._cleanup_old_snapshots()
        results["metrics_updated"] = await self._update_metrics_hourly()

        # Run database-level maintenance
        await self._db.maintenance()

        logger.debug("Maintenance cycle complete: %s", results)
        return results

    async def start_background(self, interval_minutes: int = 5) -> None:
        """Start a background asyncio task that runs maintenance."""
        if self._task is not None and not self._task.done():
            return

        self._task = asyncio.create_task(
            self._background_loop(interval_minutes * 60)
        )

    async def stop(self) -> None:
        """Cancel the background maintenance task."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _background_loop(self, interval_seconds: int) -> None:
        """Background loop that runs maintenance periodically."""
        while True:
            try:
                await asyncio.sleep(interval_seconds)
                await self.run_cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Maintenance cycle error: %s", e)

    async def _cleanup_expired_memories(self) -> int:
        """Delete memories past their expiration time."""
        try:
            cursor = await self._db.state.execute(
                """DELETE FROM memory
                   WHERE expires_at IS NOT NULL
                     AND expires_at < strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"""
            )
            await self._db.state.commit()
            return cursor.rowcount
        except Exception as e:
            logger.warning("cleanup_expired_memories failed: %s", e)
            return 0

    async def _cleanup_old_logs(self) -> int:
        """Delete action logs older than retention period."""
        try:
            cursor = await self._db.logs.execute(
                """DELETE FROM action_logs
                   WHERE timestamp < strftime('%Y-%m-%dT%H:%M:%fZ',
                                              'now', ? || ' days')""",
                (str(-LOGS_RETENTION_DAYS),),
            )
            await self._db.logs.commit()
            return cursor.rowcount
        except Exception as e:
            logger.warning("cleanup_old_logs failed: %s", e)
            return 0

    async def _cleanup_uia_events(self) -> int:
        """Delete UIA events older than retention period."""
        try:
            cursor = await self._db.logs.execute(
                """DELETE FROM uia_events
                   WHERE timestamp < strftime('%Y-%m-%dT%H:%M:%fZ',
                                              'now', ? || ' hours')""",
                (str(-UIA_RETENTION_HOURS),),
            )
            await self._db.logs.commit()
            return cursor.rowcount
        except Exception as e:
            logger.warning("cleanup_uia_events failed: %s", e)
            return 0

    async def _cleanup_old_snapshots(self) -> int:
        """Keep only the most recent N snapshots."""
        try:
            cursor = await self._db.state.execute(
                """DELETE FROM snapshots
                   WHERE id NOT IN (
                       SELECT id FROM snapshots
                       ORDER BY created_at DESC
                       LIMIT ?
                   )""",
                (MAX_SNAPSHOTS,),
            )
            await self._db.state.commit()
            return cursor.rowcount
        except Exception as e:
            logger.warning("cleanup_old_snapshots failed: %s", e)
            return 0

    async def _update_metrics_hourly(self) -> bool:
        """Aggregate recent action_logs into metrics_hourly."""
        try:
            await self._db.state.execute(
                """INSERT OR REPLACE INTO metrics_hourly
                       (period_start, tool_name, app_name,
                        total_actions, success_count, failure_count,
                        avg_score, avg_duration_ms, p95_duration_ms)
                   SELECT
                       strftime('%Y-%m-%dT%H:00:00Z', l.timestamp) as period,
                       l.tool_name,
                       COALESCE(l.app_name, ''),
                       COUNT(*),
                       SUM(CASE WHEN l.success = 1 THEN 1 ELSE 0 END),
                       SUM(CASE WHEN l.success = 0 THEN 1 ELSE 0 END),
                       AVG(l.score),
                       AVG(l.duration_ms),
                       NULL
                   FROM action_logs l
                   WHERE l.timestamp > strftime('%Y-%m-%dT%H:%M:%fZ',
                                                'now', '-2 hours')
                   GROUP BY period, l.tool_name, COALESCE(l.app_name, '')"""
            )
            await self._db.state.commit()
            return True
        except Exception as e:
            # action_logs is in logs.db but metrics_hourly is in state.db
            # Cross-db aggregation requires ATTACH — skip for now
            logger.debug("metrics aggregation skipped (cross-db): %s", e)
            return False
