"""Repository classes for accessing state.db and logs.db tables.

Each repository wraps a single aiosqlite connection and provides
typed CRUD operations for its domain. DTOs are mutable dataclasses
for easy construction and modification before persistence.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import aiosqlite


# ─── DTOs ───


@dataclass
class SystemStateEntry:
    """Key-value state entry."""

    key: str
    value: Any
    value_type: str = "string"
    category: str = "general"


@dataclass
class Memory:
    """Tiered memory entry."""

    id: str
    tier: str  # 'mid' or 'long'
    category: str
    content: dict
    relevance: float = 1.0
    access_count: int = 0
    tags: list[str] = field(default_factory=list)
    expires_at: str | None = None
    created_at: str | None = None
    last_accessed: str | None = None

    @staticmethod
    def new(tier: str, category: str, content: dict, **kwargs) -> Memory:
        """Create a new Memory with a generated ID."""
        return Memory(
            id=uuid.uuid4().hex[:16],
            tier=tier,
            category=category,
            content=content,
            **kwargs,
        )


@dataclass
class AppKnowledge:
    """Knowledge about a specific application."""

    app_name: str
    display_name: str | None = None
    framework: str | None = None
    preferred_input: str = "uia"
    reliability: float = 0.5
    total_actions: int = 0
    success_actions: int = 0
    known_elements: dict = field(default_factory=dict)
    known_dialogs: list = field(default_factory=list)
    quirks: dict = field(default_factory=dict)
    cdp_port: int | None = None


@dataclass
class Goal:
    """Goal tracking entry."""

    id: str
    title: str
    status: str = "pending"
    parent_id: str | None = None
    description: str | None = None
    priority: int = 5
    plan: str | None = None
    result: str | None = None
    error: str | None = None


@dataclass
class ActionLog:
    """Single action log entry."""

    tool_name: str
    action_type: str
    goal_id: str | None = None
    app_name: str | None = None
    parameters: dict = field(default_factory=dict)
    state_before: str | None = None
    state_after: str | None = None
    result: str | None = None
    success: bool = True
    score: float | None = None
    duration_ms: int | None = None
    error_message: str | None = None
    decision_reason: str | None = None


@dataclass
class ErrorPattern:
    """Error pattern with optional solution."""

    app_name: str
    tool_name: str
    error_type: str
    error_message: str | None = None
    solution: str | None = None
    solution_worked: bool = False


# ─── Repositories ───


def _now() -> str:
    """UTC ISO timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class StateRepository:
    """CRUD for the system_state key-value table."""

    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn

    async def get(self, key: str) -> str | None:
        """Get a raw string value by key."""
        cursor = await self._conn.execute(
            "SELECT value FROM system_state WHERE key = ?", (key,)
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def get_typed(self, key: str) -> Any:
        """Get a value with automatic type conversion."""
        cursor = await self._conn.execute(
            "SELECT value, value_type FROM system_state WHERE key = ?", (key,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None

        raw, vtype = row
        if raw is None:
            return None
        if vtype == "int":
            return int(raw)
        if vtype == "float":
            return float(raw)
        if vtype == "bool":
            return raw.lower() in ("true", "1", "yes")
        if vtype == "json":
            return json.loads(raw)
        return raw  # string

    async def set(self, key: str, value: Any, category: str = "general") -> None:
        """Upsert a key-value pair with automatic type detection."""
        vtype, raw = self._serialize(value)
        await self._conn.execute(
            """INSERT INTO system_state (key, value, value_type, updated_at, category)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                   value = excluded.value,
                   value_type = excluded.value_type,
                   updated_at = excluded.updated_at,
                   category = excluded.category""",
            (key, raw, vtype, _now(), category),
        )
        await self._conn.commit()

    async def get_category(self, category: str) -> dict[str, Any]:
        """Get all entries in a category as {key: typed_value}."""
        cursor = await self._conn.execute(
            "SELECT key, value, value_type FROM system_state WHERE category = ?",
            (category,),
        )
        result = {}
        async for row in cursor:
            key, raw, vtype = row
            if vtype == "int":
                result[key] = int(raw)
            elif vtype == "float":
                result[key] = float(raw)
            elif vtype == "bool":
                result[key] = raw.lower() in ("true", "1", "yes")
            elif vtype == "json":
                result[key] = json.loads(raw)
            else:
                result[key] = raw
        return result

    async def save_bulk(
        self, entries: list[SystemStateEntry], category: str = "general"
    ) -> None:
        """Atomically upsert multiple entries."""
        now = _now()
        rows = []
        for e in entries:
            vtype, raw = self._serialize(e.value)
            rows.append((e.key, raw, vtype, now, category))
        await self._conn.executemany(
            """INSERT INTO system_state (key, value, value_type, updated_at, category)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                   value = excluded.value,
                   value_type = excluded.value_type,
                   updated_at = excluded.updated_at,
                   category = excluded.category""",
            rows,
        )
        await self._conn.commit()

    @staticmethod
    def _serialize(value: Any) -> tuple[str, str]:
        """Detect type and serialize to (type_name, string)."""
        if isinstance(value, bool):
            return "bool", str(value).lower()
        if isinstance(value, int):
            return "int", str(value)
        if isinstance(value, float):
            return "float", str(value)
        if isinstance(value, (dict, list)):
            return "json", json.dumps(value, ensure_ascii=False)
        return "string", str(value)


class MemoryRepository:
    """CRUD for the tiered memory table."""

    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn

    async def store(self, memory: Memory) -> None:
        """Insert or replace a memory entry."""
        now = _now()
        await self._conn.execute(
            """INSERT INTO memory
                   (id, tier, category, content, relevance, access_count,
                    created_at, expires_at, last_accessed, tags)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   content = excluded.content,
                   relevance = excluded.relevance,
                   expires_at = excluded.expires_at,
                   tags = excluded.tags""",
            (
                memory.id,
                memory.tier,
                memory.category,
                json.dumps(memory.content, ensure_ascii=False),
                memory.relevance,
                memory.access_count,
                memory.created_at or now,
                memory.expires_at,
                memory.last_accessed,
                json.dumps(memory.tags, ensure_ascii=False),
            ),
        )
        await self._conn.commit()

    async def recall(
        self,
        tier: str,
        category: str | None = None,
        limit: int = 20,
    ) -> list[Memory]:
        """Retrieve memories ordered by relevance DESC.

        Bumps access_count on returned entries.
        """
        if category:
            cursor = await self._conn.execute(
                """SELECT id, tier, category, content, relevance, access_count,
                          created_at, expires_at, last_accessed, tags
                   FROM memory
                   WHERE tier = ? AND category = ?
                   ORDER BY relevance DESC
                   LIMIT ?""",
                (tier, category, limit),
            )
        else:
            cursor = await self._conn.execute(
                """SELECT id, tier, category, content, relevance, access_count,
                          created_at, expires_at, last_accessed, tags
                   FROM memory
                   WHERE tier = ?
                   ORDER BY relevance DESC
                   LIMIT ?""",
                (tier, limit),
            )

        rows = await cursor.fetchall()
        memories = []
        ids = []
        for row in rows:
            ids.append(row[0])
            memories.append(Memory(
                id=row[0],
                tier=row[1],
                category=row[2],
                content=json.loads(row[3]),
                relevance=row[4],
                access_count=row[5],
                created_at=row[6],
                expires_at=row[7],
                last_accessed=row[8],
                tags=json.loads(row[9]) if row[9] else [],
            ))

        # Bump access counts
        if ids:
            now = _now()
            placeholders = ",".join("?" for _ in ids)
            await self._conn.execute(
                """UPDATE memory
                   SET access_count = access_count + 1, last_accessed = ?
                   WHERE id IN ({})""".format(placeholders),
                [now] + ids,
            )
            await self._conn.commit()

        return memories

    async def cleanup_expired(self) -> int:
        """Delete expired memories. Returns count deleted."""
        now = _now()
        cursor = await self._conn.execute(
            "DELETE FROM memory WHERE expires_at IS NOT NULL AND expires_at < ?",
            (now,),
        )
        await self._conn.commit()
        return cursor.rowcount

    async def decay_relevance(self, factor: float = 0.95) -> None:
        """Decay relevance of mid-term memories (long-term is stable)."""
        await self._conn.execute(
            "UPDATE memory SET relevance = relevance * ? WHERE tier = 'mid'",
            (factor,),
        )
        await self._conn.commit()
    async def get_by_id(self, memory_id: str) -> Optional[Memory]:
        """Get a single memory entry by ID."""
        cursor = await self._conn.execute(
            """SELECT id, tier, category, content, relevance, access_count,
                      created_at, expires_at, last_accessed, tags
               FROM memory WHERE id = ?""",
            (memory_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return Memory(
            id=row[0], tier=row[1], category=row[2],
            content=json.loads(row[3]),
            relevance=row[4], access_count=row[5],
            created_at=row[6], expires_at=row[7],
            last_accessed=row[8],
            tags=json.loads(row[9]) if row[9] else [],
        )

    async def delete_by_id(self, memory_id: str) -> bool:
        """Delete a memory entry by ID. Returns True if deleted."""
        cursor = await self._conn.execute(
            "DELETE FROM memory WHERE id = ?", (memory_id,),
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def list_ids_by_category(
        self, category: str, tier: str = "long",
    ) -> list[str]:
        """List memory IDs in a category."""
        cursor = await self._conn.execute(
            "SELECT id FROM memory WHERE tier = ? AND category = ? ORDER BY id",
            (tier, category),
        )
        return [row[0] for row in await cursor.fetchall()]

    async def list_categories(self, tier: str = "long") -> dict[str, int]:
        """List categories with entry counts."""
        cursor = await self._conn.execute(
            "SELECT category, COUNT(*) FROM memory WHERE tier = ? GROUP BY category",
            (tier,),
        )
        return {row[0]: row[1] for row in await cursor.fetchall()}




class KnowledgeRepository:
    """CRUD for app_knowledge and error_patterns tables."""

    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn

    async def get_app(self, app_name: str) -> AppKnowledge | None:
        """Get knowledge about an app."""
        cursor = await self._conn.execute(
            """SELECT app_name, display_name, framework, preferred_input,
                      reliability, total_actions, success_actions,
                      known_elements, known_dialogs, quirks, cdp_port
               FROM app_knowledge WHERE app_name = ?""",
            (app_name,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return AppKnowledge(
            app_name=row[0],
            display_name=row[1],
            framework=row[2],
            preferred_input=row[3],
            reliability=row[4],
            total_actions=row[5],
            success_actions=row[6],
            known_elements=json.loads(row[7]) if row[7] else {},
            known_dialogs=json.loads(row[8]) if row[8] else [],
            quirks=json.loads(row[9]) if row[9] else {},
            cdp_port=row[10],
        )

    async def upsert_app(self, app: AppKnowledge) -> None:
        """Insert or update app knowledge."""
        now = _now()
        await self._conn.execute(
            """INSERT INTO app_knowledge
                   (app_name, display_name, framework, preferred_input,
                    reliability, total_actions, success_actions,
                    known_elements, known_dialogs, quirks, cdp_port,
                    first_seen, last_seen, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(app_name) DO UPDATE SET
                   display_name = excluded.display_name,
                   framework = excluded.framework,
                   preferred_input = excluded.preferred_input,
                   reliability = excluded.reliability,
                   total_actions = excluded.total_actions,
                   success_actions = excluded.success_actions,
                   known_elements = excluded.known_elements,
                   known_dialogs = excluded.known_dialogs,
                   quirks = excluded.quirks,
                   cdp_port = excluded.cdp_port,
                   last_seen = excluded.last_seen,
                   updated_at = excluded.updated_at""",
            (
                app.app_name,
                app.display_name,
                app.framework,
                app.preferred_input,
                app.reliability,
                app.total_actions,
                app.success_actions,
                json.dumps(app.known_elements, ensure_ascii=False),
                json.dumps(app.known_dialogs, ensure_ascii=False),
                json.dumps(app.quirks, ensure_ascii=False),
                app.cdp_port,
                now, now, now,
            ),
        )
        await self._conn.commit()

    async def record_action_result(
        self, app_name: str, success: bool
    ) -> None:
        """Increment action counters and update reliability."""
        if success:
            await self._conn.execute(
                """UPDATE app_knowledge
                   SET total_actions = total_actions + 1,
                       success_actions = success_actions + 1,
                       reliability = CAST(success_actions + 1 AS REAL)
                                     / (total_actions + 1),
                       last_seen = ?,
                       updated_at = ?
                   WHERE app_name = ?""",
                (_now(), _now(), app_name),
            )
        else:
            await self._conn.execute(
                """UPDATE app_knowledge
                   SET total_actions = total_actions + 1,
                       reliability = CAST(success_actions AS REAL)
                                     / (total_actions + 1),
                       last_seen = ?,
                       updated_at = ?
                   WHERE app_name = ?""",
                (_now(), _now(), app_name),
            )
        await self._conn.commit()

    async def get_error_solution(
        self, app_name: str, tool_name: str, error_type: str
    ) -> str | None:
        """Get the most recent working solution for an error pattern."""
        cursor = await self._conn.execute(
            """SELECT solution FROM error_patterns
               WHERE app_name = ? AND tool_name = ? AND error_type = ?
                 AND solution_worked = 1
               ORDER BY last_seen DESC
               LIMIT 1""",
            (app_name, tool_name, error_type),
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def record_error(self, error: ErrorPattern) -> None:
        """Record an error occurrence, incrementing count if exists."""
        now = _now()
        # Try to find existing pattern
        cursor = await self._conn.execute(
            """SELECT id, occurrence_count FROM error_patterns
               WHERE app_name = ? AND tool_name = ? AND error_type = ?
                 AND solution IS ?
               LIMIT 1""",
            (error.app_name, error.tool_name, error.error_type, error.solution),
        )
        row = await cursor.fetchone()

        if row:
            await self._conn.execute(
                """UPDATE error_patterns
                   SET occurrence_count = occurrence_count + 1,
                       last_seen = ?,
                       solution_worked = ?
                   WHERE id = ?""",
                (now, int(error.solution_worked), row[0]),
            )
        else:
            await self._conn.execute(
                """INSERT INTO error_patterns
                       (app_name, tool_name, error_type, error_message,
                        solution, solution_worked, first_seen, last_seen)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    error.app_name,
                    error.tool_name,
                    error.error_type,
                    error.error_message,
                    error.solution,
                    int(error.solution_worked),
                    now, now,
                ),
            )
        await self._conn.commit()


class LogRepository:
    """CRUD for the action_logs table in logs.db."""

    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn

    async def log_action(self, log: ActionLog) -> int:
        """Insert an action log entry. Returns the row id."""
        cursor = await self._conn.execute(
            """INSERT INTO action_logs
                   (goal_id, tool_name, app_name, action_type, parameters,
                    state_before, state_after, result, success, score,
                    duration_ms, error_message, decision_reason, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                log.goal_id,
                log.tool_name,
                log.app_name,
                log.action_type,
                json.dumps(log.parameters, ensure_ascii=False),
                log.state_before,
                log.state_after,
                log.result,
                int(log.success),
                log.score,
                log.duration_ms,
                log.error_message,
                log.decision_reason,
                _now(),
            ),
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def get_recent(self, n: int = 50) -> list[dict]:
        """Get the N most recent log entries."""
        cursor = await self._conn.execute(
            """SELECT id, goal_id, tool_name, app_name, action_type,
                      parameters, success, score, duration_ms,
                      error_message, timestamp
               FROM action_logs
               ORDER BY timestamp DESC
               LIMIT ?""",
            (n,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "goal_id": r[1],
                "tool_name": r[2],
                "app_name": r[3],
                "action_type": r[4],
                "parameters": json.loads(r[5]) if r[5] else {},
                "success": bool(r[6]),
                "score": r[7],
                "duration_ms": r[8],
                "error_message": r[9],
                "timestamp": r[10],
            }
            for r in rows
        ]

    async def get_by_goal(self, goal_id: str) -> list[dict]:
        """Get all log entries for a specific goal."""
        cursor = await self._conn.execute(
            """SELECT id, tool_name, app_name, action_type, success,
                      score, duration_ms, error_message, timestamp
               FROM action_logs
               WHERE goal_id = ?
               ORDER BY timestamp DESC""",
            (goal_id,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "tool_name": r[1],
                "app_name": r[2],
                "action_type": r[3],
                "success": bool(r[4]),
                "score": r[5],
                "duration_ms": r[6],
                "error_message": r[7],
                "timestamp": r[8],
            }
            for r in rows
        ]

    async def get_tool_stats(
        self, tool_name: str, hours: int = 1
    ) -> dict:
        """Get aggregated stats for a tool over the last N hours."""
        cursor = await self._conn.execute(
            """SELECT
                   COUNT(*) as total,
                   SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successes,
                   AVG(score) as avg_score,
                   AVG(duration_ms) as avg_duration
               FROM action_logs
               WHERE tool_name = ?
                 AND timestamp > strftime('%Y-%m-%dT%H:%M:%fZ',
                                          'now', ? || ' hours')""",
            (tool_name, str(-hours)),
        )
        row = await cursor.fetchone()
        total = row[0] or 0
        successes = row[1] or 0
        return {
            "tool_name": tool_name,
            "hours": hours,
            "total": total,
            "successes": successes,
            "failures": total - successes,
            "success_rate": successes / total if total > 0 else 0.0,
            "avg_score": row[2],
            "avg_duration_ms": row[3],
        }

    # ── EventBus handlers ──

    async def on_action_event(self, event) -> None:
        """EventBus handler for action.* events.

        Logs ActionStarting, ActionCompleted, ActionFailed to action_logs.
        """
        from marlow.kernel.events import (
            ActionStarting, ActionCompleted, ActionFailed,
        )
        try:
            if isinstance(event, ActionStarting):
                await self.log_action(ActionLog(
                    tool_name=event.tool_name,
                    action_type="starting",
                    goal_id=event.correlation_id or None,
                ))
            elif isinstance(event, ActionCompleted):
                await self.log_action(ActionLog(
                    tool_name=event.tool_name,
                    action_type="completed",
                    success=event.success,
                    duration_ms=round(event.duration_ms) if event.duration_ms else None,
                    goal_id=event.correlation_id or None,
                ))
            elif isinstance(event, ActionFailed):
                await self.log_action(ActionLog(
                    tool_name=event.tool_name,
                    action_type="failed",
                    success=False,
                    error_message=event.error[:500] if event.error else None,
                    goal_id=event.correlation_id or None,
                ))
        except Exception:
            pass  # circuit breaker handles repeated failures

    async def on_goal_event(self, event) -> None:
        """EventBus handler for goal.* events.

        Logs GoalStarted, GoalCompleted, GoalFailed to action_logs.
        """
        from marlow.kernel.events import (
            GoalStarted, GoalCompleted, GoalFailed,
        )
        try:
            if isinstance(event, GoalStarted):
                await self.log_action(ActionLog(
                    tool_name="goal_engine",
                    action_type="goal_started",
                    result=event.goal_text[:200] if event.goal_text else None,
                    goal_id=event.correlation_id or None,
                ))
            elif isinstance(event, GoalCompleted):
                await self.log_action(ActionLog(
                    tool_name="goal_engine",
                    action_type="goal_completed",
                    success=event.success,
                    result=event.goal_text[:200] if event.goal_text else None,
                    goal_id=event.correlation_id or None,
                ))
            elif isinstance(event, GoalFailed):
                await self.log_action(ActionLog(
                    tool_name="goal_engine",
                    action_type="goal_failed",
                    success=False,
                    error_message=event.error[:500] if event.error else None,
                    result=event.goal_text[:200] if event.goal_text else None,
                    goal_id=event.correlation_id or None,
                ))
        except Exception:
            pass
