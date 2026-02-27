"""
Marlow Folder Watcher

Monitors folders for file system changes (created, modified, deleted, moved).
Uses watchdog for cross-platform event-driven monitoring.

/ Monitoreo de carpetas con watchdog para detectar cambios en archivos.
"""

import hashlib
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

logger = logging.getLogger("marlow.tools.watcher")

# Module-level state
_watchers: dict[str, dict] = {}
_events: list[dict] = []
_max_events = 500
_event_lock = threading.Lock()


class MarlowEventHandler(FileSystemEventHandler):
    """Captures filesystem events and stores them in the event log."""

    def __init__(self, watch_id: str, event_types: list[str]):
        self.watch_id = watch_id
        self.event_types = event_types

    def on_created(self, event):
        if "created" in self.event_types and not event.is_directory:
            self._record("created", event.src_path)

    def on_modified(self, event):
        if "modified" in self.event_types and not event.is_directory:
            self._record("modified", event.src_path)

    def on_deleted(self, event):
        if "deleted" in self.event_types and not event.is_directory:
            self._record("deleted", event.src_path)

    def on_moved(self, event):
        if "moved" in self.event_types and not event.is_directory:
            self._record("moved", event.src_path, event.dest_path)

    def _record(self, event_type: str, src_path: str, dest_path: str = None):
        with _event_lock:
            entry = {
                "watch_id": self.watch_id,
                "event": event_type,
                "path": src_path,
                "filename": Path(src_path).name,
                "timestamp": datetime.now().isoformat(),
            }
            if dest_path:
                entry["dest_path"] = dest_path
                entry["dest_filename"] = Path(dest_path).name

            _events.append(entry)

            # Enforce max events limit
            while len(_events) > _max_events:
                _events.pop(0)


async def watch_folder(
    path: str,
    events: list[str] = None,
    recursive: bool = False,
) -> dict:
    """
    Start monitoring a folder for file changes.

    Args:
        path: Folder path to monitor.
        events: Event types to watch (created, modified, deleted, moved).
        recursive: Whether to watch subdirectories.

    Returns:
        Dict with watch_id on success, or error.
    """
    if events is None:
        events = ["created", "modified", "deleted", "moved"]

    folder = Path(path)
    if not folder.exists():
        return {"error": f"Folder not found: {path}"}
    if not folder.is_dir():
        return {"error": f"Not a folder: {path}"}

    # Generate unique watch_id
    watch_id = hashlib.md5(f"{path}{time.time()}".encode()).hexdigest()[:8]

    # Create and start observer
    handler = MarlowEventHandler(watch_id, events)
    observer = Observer()
    observer.schedule(handler, str(folder), recursive=recursive)
    observer.start()

    _watchers[watch_id] = {
        "path": str(folder),
        "events": events,
        "recursive": recursive,
        "observer": observer,
        "started": datetime.now().isoformat(),
    }

    logger.info(f"Started watching folder: {folder} (id={watch_id})")

    return {
        "success": True,
        "watch_id": watch_id,
        "path": str(folder),
        "events": events,
        "recursive": recursive,
    }


async def unwatch_folder(watch_id: str) -> dict:
    """
    Stop monitoring a folder.

    Args:
        watch_id: The watch_id returned by watch_folder.

    Returns:
        Dict with success or error.
    """
    if watch_id not in _watchers:
        return {"error": f"Watcher '{watch_id}' not found"}

    watcher = _watchers[watch_id]
    watcher["observer"].stop()
    watcher["observer"].join(timeout=5)

    path = watcher["path"]
    del _watchers[watch_id]

    logger.info(f"Stopped watching folder: {path} (id={watch_id})")

    return {
        "success": True,
        "watch_id": watch_id,
        "path": path,
        "action": "stopped",
    }


async def get_watch_events(
    watch_id: str = None,
    limit: int = 50,
    since: str = None,
) -> dict:
    """
    Get detected filesystem events.

    Args:
        watch_id: Filter events to a specific watcher.
        limit: Maximum events to return.
        since: ISO timestamp — only return events after this time.

    Returns:
        Dict with events list and metadata.
    """
    with _event_lock:
        filtered = _events.copy()

    if watch_id:
        filtered = [e for e in filtered if e["watch_id"] == watch_id]

    if since:
        filtered = [e for e in filtered if e["timestamp"] > since]

    return {
        "events": filtered[-limit:],
        "total": len(filtered),
        "watchers_active": len(_watchers),
    }


async def list_watchers() -> dict:
    """
    List all active folder watchers.

    Returns:
        Dict with watchers list and count.
    """
    watchers = []
    for wid, w in _watchers.items():
        watchers.append({
            "watch_id": wid,
            "path": w["path"],
            "events": w["events"],
            "recursive": w["recursive"],
            "started": w["started"],
        })

    return {
        "watchers": watchers,
        "count": len(watchers),
    }
