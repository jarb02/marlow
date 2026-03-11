"""
Marlow Error Journal — Self-Improve Level 1

Maintains a persistent diary of method failures and successes per tool+app
combination. When a silent method (invoke, SetValue, UIA) fails on a specific
app, the journal remembers and tools can skip straight to the method that works.

Storage: SQLite (state.db method_journal table) with JSON fallback.
Max: 500 entries, evicts oldest low-value entries first.

/ Diario persistente de errores y soluciones por combinacion tool+app.
/ Cuando un metodo silencioso falla en una app, el journal lo recuerda
/ y las herramientas pueden saltar directo al metodo que funciona.
"""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from marlow.core.config import CONFIG_DIR

logger = logging.getLogger("marlow.core.error_journal")

JOURNAL_FILE = CONFIG_DIR / "memory" / "error_journal.json"

_MAX_ENTRIES = 500


class ErrorJournal:
    """
    Persistent error/success journal for method selection optimization.
    Uses SQLite when initialized via init_sqlite(), falls back to JSON.

    / Diario persistente de errores/exitos para optimizar seleccion de metodo.
    """

    def __init__(self, db_conn: Optional[sqlite3.Connection] = None):
        self._cache: Optional[list[dict]] = None
        self._conn = db_conn

    # ── Persistence ────────────────────────────────────────────

    def _load(self) -> list[dict]:
        """Load journal from SQLite or JSON, using cache if available."""
        if self._cache is not None:
            return self._cache

        if self._conn:
            return self._load_sqlite()

        return self._load_json()

    def _load_sqlite(self) -> list[dict]:
        """Load all entries from method_journal table."""
        try:
            cursor = self._conn.execute(
                """SELECT tool, app, window, method_failed, method_worked,
                          error_message, params, success_count, failure_count,
                          last_seen
                   FROM method_journal ORDER BY last_seen DESC"""
            )
            entries = []
            for row in cursor:
                entries.append({
                    "tool": row[0],
                    "app": row[1],
                    "window": row[2] or "unknown",
                    "method_failed": row[3],
                    "method_worked": row[4],
                    "error_message": row[5] or "",
                    "params": json.loads(row[6]) if row[6] else None,
                    "success_count": row[7],
                    "failure_count": row[8],
                    "timestamp": row[9],
                })
            self._cache = entries
            return self._cache
        except Exception as e:
            logger.warning("Failed to load from SQLite: %s", e)
            return self._load_json()

    def _load_json(self) -> list[dict]:
        """Load journal from JSON file (fallback)."""
        if JOURNAL_FILE.exists():
            try:
                data = json.loads(JOURNAL_FILE.read_text(encoding="utf-8"))
                self._cache = data if isinstance(data, list) else []
                return self._cache
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load error journal JSON: %s", e)
        self._cache = []
        return self._cache

    def _save(self, entries: list[dict]) -> None:
        """Save journal to SQLite or JSON and update cache."""
        self._cache = entries

        if self._conn:
            return  # SQLite writes happen in record_* methods

        # JSON fallback
        JOURNAL_FILE.parent.mkdir(parents=True, exist_ok=True)
        JOURNAL_FILE.write_text(
            json.dumps(entries, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ── Eviction ───────────────────────────────────────────────

    def _evict(self, entries: list[dict]) -> list[dict]:
        """
        Trim to max entries. Keeps high success_count entries,
        evicts oldest low-value ones first.

        / Recorta al maximo de entradas. Mantiene las de alto success_count,
        / elimina las mas viejas y de menor valor primero.
        """
        if len(entries) <= _MAX_ENTRIES:
            return entries

        entries.sort(
            key=lambda e: (e.get("success_count", 0), e.get("timestamp", "")),
            reverse=True,
        )
        return entries[:_MAX_ENTRIES]

    def _evict_sqlite(self) -> None:
        """Evict oldest low-value entries from SQLite if over limit."""
        if not self._conn:
            return
        try:
            cursor = self._conn.execute("SELECT COUNT(*) FROM method_journal")
            count = cursor.fetchone()[0]
            if count <= _MAX_ENTRIES:
                return
            # Delete entries with lowest success_count, oldest first
            self._conn.execute(
                """DELETE FROM method_journal WHERE id IN (
                       SELECT id FROM method_journal
                       ORDER BY success_count ASC, last_seen ASC
                       LIMIT ?
                   )""",
                (count - _MAX_ENTRIES,),
            )
            self._conn.commit()
        except Exception as e:
            logger.debug("Eviction error: %s", e)

    # ── Normalization ──────────────────────────────────────────

    def _normalize_window(self, window: Optional[str]) -> str:
        """Normalize window identifier to app name for matching."""
        if not window:
            return "unknown"
        w = window.strip()
        if " - " in w:
            w = w.rsplit(" - ", 1)[-1]
        return w.lower().strip()

    # ── Recording ──────────────────────────────────────────────

    def record_failure(
        self,
        tool: str,
        window: Optional[str],
        method: str,
        error: str,
        params: Optional[dict] = None,
    ) -> None:
        """
        Record that a method failed for a tool+app combination.

        / Registra que un metodo fallo para una combinacion tool+app.
        """
        app = self._normalize_window(window)
        now = datetime.now().isoformat()

        if self._conn:
            try:
                filtered_params = (
                    json.dumps({
                        k: v for k, v in (params or {}).items()
                        if k in ("element_name", "window_title", "target")
                    })
                    if params else None
                )
                self._conn.execute(
                    """INSERT INTO method_journal
                           (tool, app, window, method_failed, error_message,
                            params, failure_count, first_seen, last_seen)
                       VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                       ON CONFLICT(tool, app, method_failed) DO UPDATE SET
                           error_message = excluded.error_message,
                           failure_count = failure_count + 1,
                           last_seen = excluded.last_seen""",
                    (tool, app, window or "unknown", method, error,
                     filtered_params, now, now),
                )
                self._conn.commit()
                self._cache = None  # invalidate
                self._evict_sqlite()
                return
            except Exception as e:
                logger.debug("SQLite record_failure error: %s", e)

        # JSON fallback
        entries = self._load()
        for entry in entries:
            if (entry["tool"] == tool
                    and entry["app"] == app
                    and entry["method_failed"] == method):
                entry["error_message"] = error
                entry["timestamp"] = now
                entry["failure_count"] = entry.get("failure_count", 1) + 1
                self._save(entries)
                return

        entry = {
            "tool": tool, "app": app,
            "window": window or "unknown",
            "method_failed": method, "method_worked": None,
            "error_message": error,
            "params": {k: v for k, v in (params or {}).items()
                       if k in ("element_name", "window_title", "target")} or None,
            "timestamp": now, "success_count": 0, "failure_count": 1,
        }
        entries.append(entry)
        entries = self._evict(entries)
        self._save(entries)

    def record_success(
        self,
        tool: str,
        window: Optional[str],
        method: str,
    ) -> None:
        """
        Record that a method worked as a fallback for a tool+app.

        / Registra que un metodo funciono como alternativa para tool+app.
        """
        app = self._normalize_window(window)
        now = datetime.now().isoformat()

        if self._conn:
            try:
                # Find the most recent failure for this tool+app
                # where the failed method is different from the working one
                cursor = self._conn.execute(
                    """SELECT id FROM method_journal
                       WHERE tool = ? AND app = ? AND method_failed != ?
                         AND (method_worked IS NULL OR method_worked = ?)
                       ORDER BY last_seen DESC LIMIT 1""",
                    (tool, app, method, method),
                )
                row = cursor.fetchone()
                if row:
                    self._conn.execute(
                        """UPDATE method_journal
                           SET method_worked = ?,
                               success_count = success_count + 1,
                               last_seen = ?
                           WHERE id = ?""",
                        (method, now, row[0]),
                    )
                    self._conn.commit()
                    self._cache = None
                return
            except Exception as e:
                logger.debug("SQLite record_success error: %s", e)

        # JSON fallback
        entries = self._load()
        for entry in reversed(entries):
            if (entry["tool"] == tool
                    and entry["app"] == app
                    and entry.get("method_failed")
                    and entry.get("method_failed") != method):
                entry["method_worked"] = method
                entry["success_count"] = entry.get("success_count", 0) + 1
                entry["timestamp"] = now
                self._save(entries)
                return

    # ── Querying ───────────────────────────────────────────────

    def get_best_method(
        self,
        tool: str,
        window: Optional[str],
    ) -> Optional[str]:
        """
        Query the journal for the best method for a tool+app combination.

        / Consulta el journal por el mejor metodo para una combinacion tool+app.
        """
        app = self._normalize_window(window)

        if self._conn:
            try:
                cursor = self._conn.execute(
                    """SELECT method_worked FROM method_journal
                       WHERE tool = ? AND app = ?
                         AND method_worked IS NOT NULL
                         AND success_count > 0
                       ORDER BY success_count DESC
                       LIMIT 1""",
                    (tool, app),
                )
                row = cursor.fetchone()
                return row[0] if row else None
            except Exception as e:
                logger.debug("SQLite get_best_method error: %s", e)

        # JSON fallback
        entries = self._load()
        best: Optional[dict] = None
        for entry in entries:
            if (entry["tool"] == tool
                    and entry["app"] == app
                    and entry.get("method_worked")
                    and entry.get("success_count", 0) > 0):
                if best is None or entry["success_count"] > best["success_count"]:
                    best = entry
        return best["method_worked"] if best else None

    def get_known_issues(
        self,
        window: Optional[str] = None,
    ) -> list[dict]:
        """
        List known issues, optionally filtered by app/window.

        / Lista problemas conocidos, opcionalmente filtrados por app/ventana.
        """
        if self._conn:
            try:
                if window:
                    app = self._normalize_window(window)
                    cursor = self._conn.execute(
                        """SELECT tool, app, method_failed, method_worked,
                                  error_message, success_count, failure_count,
                                  last_seen
                           FROM method_journal WHERE app = ?
                           ORDER BY last_seen DESC""",
                        (app,),
                    )
                else:
                    cursor = self._conn.execute(
                        """SELECT tool, app, method_failed, method_worked,
                                  error_message, success_count, failure_count,
                                  last_seen
                           FROM method_journal ORDER BY last_seen DESC"""
                    )
                return [
                    {
                        "tool": r[0], "app": r[1],
                        "method_failed": r[2], "method_worked": r[3],
                        "error_message": r[4] or "",
                        "success_count": r[5], "failure_count": r[6],
                        "timestamp": r[7],
                    }
                    for r in cursor
                ]
            except Exception as e:
                logger.debug("SQLite get_known_issues error: %s", e)

        # JSON fallback
        entries = self._load()
        if window:
            app = self._normalize_window(window)
            entries = [e for e in entries if e["app"] == app]
        return [
            {
                "tool": e["tool"], "app": e["app"],
                "method_failed": e["method_failed"],
                "method_worked": e.get("method_worked"),
                "error_message": e.get("error_message", ""),
                "success_count": e.get("success_count", 0),
                "failure_count": e.get("failure_count", 1),
                "timestamp": e.get("timestamp"),
            }
            for e in entries
        ]

    def import_json_entries(self, entries: list[dict]) -> int:
        """Import entries from the old JSON format into SQLite.

        Returns count of entries imported.
        """
        if not self._conn:
            return 0
        count = 0
        for e in entries:
            try:
                self._conn.execute(
                    """INSERT INTO method_journal
                           (tool, app, window, method_failed, method_worked,
                            error_message, params, success_count, failure_count,
                            first_seen, last_seen)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(tool, app, method_failed) DO UPDATE SET
                           method_worked = COALESCE(excluded.method_worked, method_worked),
                           success_count = MAX(success_count, excluded.success_count),
                           failure_count = MAX(failure_count, excluded.failure_count),
                           last_seen = MAX(last_seen, excluded.last_seen)""",
                    (
                        e.get("tool", ""),
                        e.get("app", "unknown"),
                        e.get("window", "unknown"),
                        e.get("method_failed", ""),
                        e.get("method_worked"),
                        e.get("error_message"),
                        json.dumps(e.get("params")) if e.get("params") else None,
                        e.get("success_count", 0),
                        e.get("failure_count", 1),
                        e.get("timestamp", datetime.now().isoformat()),
                        e.get("timestamp", datetime.now().isoformat()),
                    ),
                )
                count += 1
            except Exception as exc:
                logger.debug("Import entry error: %s", exc)
        self._conn.commit()
        self._cache = None
        return count


# Module-level singleton
_journal = ErrorJournal()


def init_sqlite(db_path) -> None:
    """Switch the error journal singleton to SQLite backend.

    Called by the daemon during startup after DatabaseManager creates schema.
    """
    global _journal
    try:
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        # Ensure table exists (defensive — DatabaseManager also creates it)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS method_journal (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                tool            TEXT NOT NULL,
                app             TEXT NOT NULL,
                window          TEXT,
                method_failed   TEXT NOT NULL,
                method_worked   TEXT,
                error_message   TEXT,
                params          TEXT,
                success_count   INTEGER DEFAULT 0,
                failure_count   INTEGER DEFAULT 1,
                first_seen      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                last_seen       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                UNIQUE(tool, app, method_failed)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_method_journal_lookup
                ON method_journal(tool, app)
        """)
        conn.commit()
        _journal = ErrorJournal(db_conn=conn)
        logger.info("Error journal switched to SQLite: %s", db_path)
    except Exception as e:
        logger.warning("Failed to init SQLite for error journal: %s", e)


# ─────────────────────────────────────────────────────────────
# MCP Tools
# ─────────────────────────────────────────────────────────────

async def get_error_journal(window: Optional[str] = None) -> dict:
    """
    Show the error journal, optionally filtered by app/window.

    / Muestra el diario de errores, opcionalmente filtrado por app/ventana.
    """
    try:
        issues = _journal.get_known_issues(window)
        return {
            "success": True,
            "entries": issues,
            "total": len(issues),
            "filter": window or "all",
        }
    except Exception as e:
        logger.error("get_error_journal error: %s", e)
        return {"error": str(e)}


async def clear_error_journal(window: Optional[str] = None) -> dict:
    """
    Clear journal entries for an app, or all entries if no window specified.

    / Limpia entradas del journal para una app, o todas si no se especifica ventana.
    """
    try:
        if _journal._conn:
            # SQLite path
            if window:
                app = _journal._normalize_window(window)
                cursor = _journal._conn.execute(
                    "SELECT COUNT(*) FROM method_journal WHERE app = ?", (app,),
                )
                before = cursor.fetchone()[0]
                _journal._conn.execute(
                    "DELETE FROM method_journal WHERE app = ?", (app,),
                )
                _journal._conn.commit()
                cursor = _journal._conn.execute(
                    "SELECT COUNT(*) FROM method_journal",
                )
                remaining = cursor.fetchone()[0]
                _journal._cache = None
                return {
                    "success": True,
                    "cleared": before,
                    "remaining": remaining,
                    "filter": window,
                }
            else:
                _journal._conn.execute("DELETE FROM method_journal")
                _journal._conn.commit()
                _journal._cache = None
                return {"success": True, "cleared": "all", "remaining": 0}

        # JSON fallback
        if window:
            app = _journal._normalize_window(window)
            entries = _journal._load()
            before = len(entries)
            entries = [e for e in entries if e["app"] != app]
            _journal._save(entries)
            return {
                "success": True,
                "cleared": before - len(entries),
                "remaining": len(entries),
                "filter": window,
            }
        else:
            _journal._save([])
            return {"success": True, "cleared": "all", "remaining": 0}
    except Exception as e:
        logger.error("clear_error_journal error: %s", e)
        return {"error": str(e)}
