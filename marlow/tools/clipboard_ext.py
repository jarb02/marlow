"""
Marlow Clipboard History Tool

Monitors the system clipboard and maintains a history of the last N entries.
Supports listing, searching, and clearing the history.

/ Monitorea el clipboard del sistema y mantiene un historial.
"""

import time
import logging
import threading
from datetime import datetime
from typing import Optional

logger = logging.getLogger("marlow.tools.clipboard_ext")

_history: list[dict] = []
_monitor_active = False
_monitor_thread: Optional[threading.Thread] = None
_max_history = 100


def _read_clipboard() -> str:
    """Read clipboard content via Win32 API."""
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        try:
            data = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
            return data or ""
        except TypeError:
            return ""
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        return ""


def _monitor_clipboard():
    """Background thread that watches for clipboard changes."""
    global _monitor_active
    last_content = _read_clipboard()

    while _monitor_active:
        try:
            current = _read_clipboard()
            if current and current != last_content:
                _history.append({
                    "content": current[:500],
                    "timestamp": datetime.now().isoformat(),
                    "length": len(current),
                })

                while len(_history) > _max_history:
                    _history.pop(0)

                last_content = current
        except Exception:
            pass

        time.sleep(1)


async def clipboard_history(
    action: str = "list",
    search: Optional[str] = None,
    limit: int = 20,
) -> dict:
    """
    Manage clipboard history.

    Args:
        action: What to do:
            - "start": Begin monitoring clipboard changes.
            - "stop": Stop monitoring.
            - "list": Show recent clipboard entries (default).
            - "search": Search history for text (requires search param).
            - "clear": Delete all history entries.
        search: Text to search for (only with action="search").
        limit: Max entries to return (default: 20).

    Returns:
        Dictionary with history entries or status info.

    / Gestiona el historial de clipboard.
    """
    global _monitor_active, _monitor_thread

    if action == "start":
        if _monitor_active:
            return {"success": True, "status": "already_running", "entries": len(_history)}
        _monitor_active = True
        _monitor_thread = threading.Thread(target=_monitor_clipboard, daemon=True)
        _monitor_thread.start()
        return {"success": True, "status": "started", "checking_interval_seconds": 1}

    elif action == "stop":
        if not _monitor_active:
            return {"success": True, "status": "already_stopped"}
        _monitor_active = False
        return {"success": True, "status": "stopped", "entries_captured": len(_history)}

    elif action == "clear":
        count = len(_history)
        _history.clear()
        return {"success": True, "status": "cleared", "entries_removed": count}

    elif action == "search":
        if not search:
            return {"error": "Provide 'search' parameter when action='search'"}
        matches = [h for h in _history if search.lower() in h["content"].lower()]
        return {
            "success": True,
            "matches": matches[-limit:],
            "total_matches": len(matches),
            "query": search,
        }

    else:  # "list"
        return {
            "success": True,
            "entries": _history[-limit:],
            "total_entries": len(_history),
            "monitoring_active": _monitor_active,
        }
