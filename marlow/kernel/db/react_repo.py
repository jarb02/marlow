"""ReactSessionRepo — SQLite persistence for reactive goal sessions.

Stores multi-step goal execution state: plan, completed steps,
observations, key facts, and errors. Uses sync sqlite3 (same pattern
as marlow.tools.memory) for compatibility with mixed sync/async callers.

/ Persistencia SQLite para sesiones de ejecucion reactiva.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timedelta

logger = logging.getLogger("marlow.kernel.db.react_repo")

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS react_sessions (
    id TEXT PRIMARY KEY,
    goal_text TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'console',
    status TEXT NOT NULL DEFAULT 'active',
    plan TEXT DEFAULT '[]',
    current_step INTEGER DEFAULT 0,
    key_facts TEXT DEFAULT '[]',
    completed_steps TEXT DEFAULT '[]',
    errors TEXT DEFAULT '[]',
    observations TEXT DEFAULT '[]',
    iteration_count INTEGER DEFAULT 0,
    max_iterations INTEGER DEFAULT 8,
    created_at TEXT,
    updated_at TEXT,
    completed_at TEXT
)
"""

# Fields that store JSON arrays/objects
_JSON_FIELDS = frozenset({
    "plan", "key_facts", "completed_steps", "errors", "observations",
})


def _now() -> str:
    return datetime.now().isoformat()


def _row_to_dict(row: sqlite3.Row) -> dict:
    """Convert a sqlite3.Row to a dict, deserializing JSON fields."""
    d = dict(row)
    for field in _JSON_FIELDS:
        if field in d and isinstance(d[field], str):
            try:
                d[field] = json.loads(d[field])
            except (json.JSONDecodeError, TypeError):
                d[field] = []
    return d


class ReactSessionRepo:
    """CRUD for the react_sessions table.

    Uses sync sqlite3 with WAL mode and check_same_thread=False
    for safe access from thread pool executors.
    """

    def __init__(self, db_path: str):
        self._db_path = str(db_path)
        self._ensure_table()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def _ensure_table(self):
        try:
            conn = self._get_conn()
            conn.execute(_CREATE_TABLE)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error("Failed to create react_sessions table: %s", e)

    def create_session(self, goal_text: str, channel: str = "console") -> dict:
        """Create a new reactive session. Returns dict with all fields."""
        session_id = uuid.uuid4().hex[:12]
        now = _now()

        try:
            conn = self._get_conn()
            conn.execute(
                """INSERT INTO react_sessions
                       (id, goal_text, channel, status, plan, current_step,
                        key_facts, completed_steps, errors, observations,
                        iteration_count, max_iterations, created_at, updated_at)
                   VALUES (?, ?, ?, 'active', '[]', 0, '[]', '[]', '[]', '[]',
                           0, 8, ?, ?)""",
                (session_id, goal_text, channel, now, now),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error("create_session error: %s", e)
            return {
                "id": session_id, "goal_text": goal_text, "channel": channel,
                "status": "active", "plan": [], "current_step": 0,
                "key_facts": [], "completed_steps": [], "errors": [],
                "observations": [], "iteration_count": 0, "max_iterations": 8,
                "created_at": now, "updated_at": now, "completed_at": None,
            }

        return {
            "id": session_id, "goal_text": goal_text, "channel": channel,
            "status": "active", "plan": [], "current_step": 0,
            "key_facts": [], "completed_steps": [], "errors": [],
            "observations": [], "iteration_count": 0, "max_iterations": 8,
            "created_at": now, "updated_at": now, "completed_at": None,
        }

    def get_session(self, session_id: str) -> dict | None:
        """Get a session by ID. Returns dict or None."""
        try:
            conn = self._get_conn()
            cursor = conn.execute(
                "SELECT * FROM react_sessions WHERE id = ?", (session_id,),
            )
            row = cursor.fetchone()
            conn.close()
            if row is None:
                return None
            return _row_to_dict(row)
        except Exception as e:
            logger.error("get_session error: %s", e)
            return None

    def update_session(self, session_id: str, **fields) -> bool:
        """Update specific fields of a session. JSON fields are auto-serialized."""
        if not fields:
            return False

        fields["updated_at"] = _now()

        # Serialize JSON fields
        set_parts = []
        values = []
        for key, value in fields.items():
            set_parts.append(f"{key} = ?")
            if key in _JSON_FIELDS and not isinstance(value, str):
                values.append(json.dumps(value, ensure_ascii=False, default=str))
            else:
                values.append(value)

        values.append(session_id)
        sql = f"UPDATE react_sessions SET {', '.join(set_parts)} WHERE id = ?"

        try:
            conn = self._get_conn()
            conn.execute(sql, values)
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error("update_session error: %s", e)
            return False

    def complete_session(
        self, session_id: str, status: str, summary: str | None = None,
    ) -> bool:
        """Mark a session as completed/failed/aborted."""
        now = _now()
        try:
            conn = self._get_conn()
            conn.execute(
                "UPDATE react_sessions SET status = ?, completed_at = ?, updated_at = ? WHERE id = ?",
                (status, now, now, session_id),
            )
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error("complete_session error: %s", e)
            return False

    def get_active_sessions(self) -> list[dict]:
        """Get all sessions with status='active'. For recovery on restart."""
        try:
            conn = self._get_conn()
            cursor = conn.execute(
                "SELECT * FROM react_sessions WHERE status = 'active' ORDER BY created_at DESC",
            )
            rows = cursor.fetchall()
            conn.close()
            return [_row_to_dict(row) for row in rows]
        except Exception as e:
            logger.error("get_active_sessions error: %s", e)
            return []

    def cleanup_old_sessions(self, days: int = 7) -> int:
        """Delete completed/failed sessions older than N days. Returns count deleted."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        try:
            conn = self._get_conn()
            cursor = conn.execute(
                """DELETE FROM react_sessions
                   WHERE status IN ('completed', 'failed', 'aborted')
                     AND completed_at < ?""",
                (cutoff,),
            )
            count = cursor.rowcount
            conn.commit()
            conn.close()
            return count
        except Exception as e:
            logger.error("cleanup_old_sessions error: %s", e)
            return 0
