"""DatabaseManager — manages aiosqlite connections to state.db and logs.db.

Handles connection lifecycle, PRAGMA configuration, schema creation,
and version migration. Provides both async (for kernel) and sync
(for daemon threads like UIAEventWriter) access.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Optional

import aiosqlite

from .schema import LOGS_SCHEMA, SCHEMA_VERSION, STATE_SCHEMA

logger = logging.getLogger(__name__)

# PRAGMAs applied on every connection open
_RUNTIME_PRAGMAS = [
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous = NORMAL",
    "PRAGMA foreign_keys = ON",
    "PRAGMA cache_size = -32000",
    "PRAGMA temp_store = MEMORY",
    "PRAGMA mmap_size = 268435456",
    "PRAGMA busy_timeout = 5000",
    "PRAGMA wal_autocheckpoint = 1000",
]

# PRAGMAs applied only when creating a new database
_INIT_PRAGMAS = [
    "PRAGMA page_size = 4096",
    "PRAGMA auto_vacuum = INCREMENTAL",
]


class DatabaseManager:
    """Manages SQLite connections to state.db and logs.db.

    Parameters
    ----------
    * **data_dir** (str or Path or None):
        Directory for database files. Defaults to ~/.marlow.
    """

    def __init__(self, data_dir: str | Path | None = None):
        if data_dir is None:
            data_dir = Path.home() / ".marlow"
        self._data_dir = Path(data_dir)
        self._state_path = self._data_dir / "state.db"
        self._logs_path = self._data_dir / "logs.db"
        self._state: Optional[aiosqlite.Connection] = None
        self._logs: Optional[aiosqlite.Connection] = None
        self._initialized = False

    @property
    def state(self) -> aiosqlite.Connection:
        """Async connection to state.db."""
        if self._state is None:
            raise RuntimeError("DatabaseManager not initialized. Call initialize() first.")
        return self._state

    @property
    def logs(self) -> aiosqlite.Connection:
        """Async connection to logs.db."""
        if self._logs is None:
            raise RuntimeError("DatabaseManager not initialized. Call initialize() first.")
        return self._logs

    @property
    def is_initialized(self) -> bool:
        """Return True if both connections are open."""
        return self._initialized

    async def initialize(self) -> None:
        """Open connections, apply PRAGMAs, and create schema.

        Safe to call multiple times (idempotent).
        """
        if self._initialized:
            return

        self._data_dir.mkdir(parents=True, exist_ok=True)

        # Open connections
        self._state = await aiosqlite.connect(str(self._state_path))
        self._logs = await aiosqlite.connect(str(self._logs_path))

        # Apply init PRAGMAs if databases are new
        await self._apply_init_pragmas(self._state, self._state_path)
        await self._apply_init_pragmas(self._logs, self._logs_path)

        # Apply runtime PRAGMAs
        await self._apply_runtime_pragmas(self._state)
        await self._apply_runtime_pragmas(self._logs)

        # Create schema
        await self._state.executescript(STATE_SCHEMA)
        await self._logs.executescript(LOGS_SCHEMA)

        # Record schema version if not present
        await self._ensure_schema_version(self._state, "state")
        await self._ensure_schema_version(self._logs, "logs")

        self._initialized = True
        logger.info(
            "Database initialized: state=%s, logs=%s",
            self._state_path, self._logs_path,
        )

    async def close(self) -> None:
        """Optimize and close both connections."""
        for conn, name in [(self._state, "state"), (self._logs, "logs")]:
            if conn is not None:
                try:
                    await conn.execute("PRAGMA optimize")
                except Exception:
                    pass
                try:
                    await conn.close()
                except Exception as e:
                    logger.warning("Error closing %s.db: %s", name, e)

        self._state = None
        self._logs = None
        self._initialized = False
        logger.info("Database connections closed")

    async def maintenance(self) -> None:
        """Run incremental vacuum and optimize on both databases."""
        for conn, name in [(self._state, "state"), (self._logs, "logs")]:
            if conn is not None:
                try:
                    await conn.execute("PRAGMA incremental_vacuum")
                    await conn.execute("PRAGMA optimize")
                except Exception as e:
                    logger.warning("Maintenance failed on %s.db: %s", name, e)

    def get_sync_logs_connection(self) -> sqlite3.Connection:
        """Open a synchronous connection to logs.db for daemon threads.

        The caller is responsible for closing this connection.
        """
        conn = sqlite3.connect(str(self._logs_path))
        for pragma in _RUNTIME_PRAGMAS:
            conn.execute(pragma)
        return conn

    async def _apply_init_pragmas(
        self, conn: aiosqlite.Connection, db_path: Path
    ) -> None:
        """Apply one-time PRAGMAs for new databases."""
        # Check if database file is new (just created, no tables)
        cursor = await conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table'"
        )
        row = await cursor.fetchone()
        if row[0] == 0:
            for pragma in _INIT_PRAGMAS:
                await conn.execute(pragma)

    async def _apply_runtime_pragmas(self, conn: aiosqlite.Connection) -> None:
        """Apply PRAGMAs on every connection open."""
        for pragma in _RUNTIME_PRAGMAS:
            await conn.execute(pragma)

    async def _ensure_schema_version(
        self, conn: aiosqlite.Connection, db_name: str
    ) -> None:
        """Insert schema version if not already present."""
        cursor = await conn.execute(
            "SELECT MAX(version) FROM schema_version"
        )
        row = await cursor.fetchone()
        current = row[0] if row and row[0] is not None else 0

        if current < SCHEMA_VERSION:
            await conn.execute(
                "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                (SCHEMA_VERSION, "Initial schema for {}".format(db_name)),
            )
            await conn.commit()
