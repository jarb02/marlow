"""
Marlow Error Journal — Self-Improve Level 1

Maintains a persistent diary of method failures and successes per tool+app
combination. When a silent method (invoke, SetValue, UIA) fails on a specific
app, the journal remembers and tools can skip straight to the method that works.

Storage: ~/.marlow/memory/error_journal.json
Max: 500 entries, evicts oldest low-value entries first.

/ Diario persistente de errores y soluciones por combinacion tool+app.
/ Cuando un metodo silencioso falla en una app, el journal lo recuerda
/ y las herramientas pueden saltar directo al metodo que funciona.
"""

import json
import logging
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

    / Diario persistente de errores/exitos para optimizar seleccion de metodo.
    """

    def __init__(self):
        self._cache: Optional[list[dict]] = None

    # ── Persistence ────────────────────────────────────────────

    def _load(self) -> list[dict]:
        """Load journal from disk, using cache if available."""
        if self._cache is not None:
            return self._cache
        if JOURNAL_FILE.exists():
            try:
                data = json.loads(JOURNAL_FILE.read_text(encoding="utf-8"))
                self._cache = data if isinstance(data, list) else []
                return self._cache
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to load error journal: {e}")
        self._cache = []
        return self._cache

    def _save(self, entries: list[dict]) -> None:
        """Save journal to disk and update cache."""
        JOURNAL_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._cache = entries
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

        # Sort: high success_count first, then by recency
        entries.sort(
            key=lambda e: (e.get("success_count", 0), e.get("timestamp", "")),
            reverse=True,
        )
        return entries[:_MAX_ENTRIES]

    # ── Normalization ──────────────────────────────────────────

    def _normalize_window(self, window: Optional[str]) -> str:
        """Normalize window identifier to app name for matching."""
        if not window:
            return "unknown"
        # Extract app name: take first word or before " - "
        w = window.strip()
        if " - " in w:
            # e.g. "Document - Notepad" -> "Notepad"
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
        entries = self._load()
        app = self._normalize_window(window)

        # Check if we already have this exact failure
        for entry in entries:
            if (entry["tool"] == tool
                    and entry["app"] == app
                    and entry["method_failed"] == method):
                # Update existing entry
                entry["error_message"] = error
                entry["timestamp"] = datetime.now().isoformat()
                entry["failure_count"] = entry.get("failure_count", 1) + 1
                self._save(entries)
                return

        # New entry
        entry = {
            "tool": tool,
            "app": app,
            "window": window or "unknown",
            "method_failed": method,
            "method_worked": None,
            "error_message": error,
            "params": {k: v for k, v in (params or {}).items()
                       if k in ("element_name", "window_title", "target")} or None,
            "timestamp": datetime.now().isoformat(),
            "success_count": 0,
            "failure_count": 1,
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
        Links the successful method to the most recent failure entry.

        / Registra que un metodo funciono como alternativa para tool+app.
        """
        entries = self._load()
        app = self._normalize_window(window)

        # Find matching failure entry for this tool+app without a solution yet
        for entry in reversed(entries):
            if (entry["tool"] == tool
                    and entry["app"] == app
                    and entry.get("method_failed")
                    and entry.get("method_failed") != method):
                entry["method_worked"] = method
                entry["success_count"] = entry.get("success_count", 0) + 1
                entry["timestamp"] = datetime.now().isoformat()
                self._save(entries)
                return

        # No matching failure — still worth recording as general knowledge
        # (app works with this method)

    # ── Querying ───────────────────────────────────────────────

    def get_best_method(
        self,
        tool: str,
        window: Optional[str],
    ) -> Optional[str]:
        """
        Query the journal for the best method for a tool+app combination.
        Returns the method_worked from the entry with highest success_count,
        or None if no data.

        / Consulta el journal por el mejor metodo para una combinacion tool+app.
        """
        entries = self._load()
        app = self._normalize_window(window)

        best: Optional[dict] = None
        for entry in entries:
            if (entry["tool"] == tool
                    and entry["app"] == app
                    and entry.get("method_worked")
                    and entry.get("success_count", 0) > 0):
                if best is None or entry["success_count"] > best["success_count"]:
                    best = entry

        if best:
            return best["method_worked"]
        return None

    def get_known_issues(
        self,
        window: Optional[str] = None,
    ) -> list[dict]:
        """
        List known issues, optionally filtered by app/window.

        / Lista problemas conocidos, opcionalmente filtrados por app/ventana.
        """
        entries = self._load()

        if window:
            app = self._normalize_window(window)
            entries = [e for e in entries if e["app"] == app]

        # Return summary without raw params
        return [
            {
                "tool": e["tool"],
                "app": e["app"],
                "method_failed": e["method_failed"],
                "method_worked": e.get("method_worked"),
                "error_message": e.get("error_message", ""),
                "success_count": e.get("success_count", 0),
                "failure_count": e.get("failure_count", 1),
                "timestamp": e.get("timestamp"),
            }
            for e in entries
        ]


# Module-level singleton
_journal = ErrorJournal()


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
        logger.error(f"get_error_journal error: {e}")
        return {"error": str(e)}


async def clear_error_journal(window: Optional[str] = None) -> dict:
    """
    Clear journal entries for an app, or all entries if no window specified.

    / Limpia entradas del journal para una app, o todas si no se especifica ventana.
    """
    try:
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
            return {
                "success": True,
                "cleared": "all",
                "remaining": 0,
            }
    except Exception as e:
        logger.error(f"clear_error_journal error: {e}")
        return {"error": str(e)}
