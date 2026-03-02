"""UIAEventWriter — buffered synchronous writer for UIA events.

Designed for the UIA event daemon thread which runs in STA COM mode
and cannot use async I/O. Events are buffered in a thread-safe deque
and flushed to logs.db periodically by a background flush thread.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class UIAEvent:
    """Single UIA event to be written to logs.db."""

    event_type: str
    window_title: str = ""
    process_name: str = ""
    element_name: str = ""
    details: str = "{}"
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            )


class UIAEventWriter:
    """Buffered sync writer for UIA events in daemon threads.

    Parameters
    ----------
    * **db_path** (str or Path):
        Path to logs.db.
    * **flush_interval** (float):
        Seconds between automatic flushes. Default 0.5.
    * **max_buffer** (int):
        Maximum buffered events before forced flush. Default 200.
    """

    def __init__(
        self,
        db_path: str | Path,
        flush_interval: float = 0.5,
        max_buffer: int = 200,
    ):
        self._db_path = str(db_path)
        self._flush_interval = flush_interval
        self._max_buffer = max_buffer
        self._buffer: deque[UIAEvent] = deque()
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._flush_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._running = False

    @property
    def is_running(self) -> bool:
        """Return True if the writer is active."""
        return self._running

    @property
    def buffer_size(self) -> int:
        """Current number of buffered events."""
        return len(self._buffer)

    def start(self) -> None:
        """Open database connection and start flush thread."""
        if self._running:
            return

        self._stop_event.clear()
        self._ready_event.clear()
        self._flush_thread = threading.Thread(
            target=self._flush_loop,
            name="uia-event-writer",
            daemon=True,
        )
        self._flush_thread.start()
        self._ready_event.wait(timeout=5.0)
        self._running = True

    def stop(self) -> None:
        """Flush remaining events and close connection."""
        if not self._running:
            return

        self._stop_event.set()

        if self._flush_thread is not None and self._flush_thread.is_alive():
            self._flush_thread.join(timeout=3.0)

        # Final flush
        self._flush_now()

        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

        self._running = False

    def push(self, event: UIAEvent) -> None:
        """Add an event to the buffer (thread-safe).

        If the buffer exceeds max_buffer, triggers an immediate flush.
        """
        with self._lock:
            self._buffer.append(event)

        if len(self._buffer) >= self._max_buffer:
            self._flush_now()

    def _flush_loop(self) -> None:
        """Background thread that opens the DB connection and flushes periodically."""
        self._conn = sqlite3.connect(
            self._db_path, check_same_thread=False
        )
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._ready_event.set()

        while not self._stop_event.is_set():
            self._stop_event.wait(self._flush_interval)
            if self._buffer:
                self._flush_now()

    def _flush_now(self) -> None:
        """Write all buffered events to the database."""
        with self._lock:
            if not self._buffer:
                return
            batch = list(self._buffer)
            self._buffer.clear()

        if self._conn is None:
            return

        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._conn.executemany(
                """INSERT INTO uia_events
                       (event_type, window_title, process_name,
                        element_name, details, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (
                        e.event_type,
                        e.window_title,
                        e.process_name,
                        e.element_name,
                        e.details,
                        e.timestamp,
                    )
                    for e in batch
                ],
            )
            self._conn.commit()
        except Exception as e:
            logger.warning("UIA event flush failed (%d events): %s", len(batch), e)
            try:
                self._conn.rollback()
            except Exception:
                pass
