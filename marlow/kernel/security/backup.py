"""FileBackupManager — automatic backup before any file modification.

Backup dir: ~/.marlow/backups/
Retention: 24 hours, max 100 backups.
"""

from __future__ import annotations

import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Optional


class FileBackupManager:
    """Automatic backup before any file modification.

    Parameters
    ----------
    * **backup_dir** (str or None):
        Directory for backups. Defaults to ``~/.marlow/backups``.
    * **max_age_hours** (float):
        Maximum age before a backup is eligible for cleanup.
    * **max_count** (int):
        Maximum number of backups to retain.
    """

    def __init__(
        self,
        backup_dir: str | None = None,
        max_age_hours: float = 24.0,
        max_count: int = 100,
    ):
        self._backup_dir = Path(
            backup_dir or os.path.expanduser("~/.marlow/backups"),
        )
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        self._max_age_hours = max_age_hours
        self._max_count = max_count

    @property
    def backup_dir(self) -> Path:
        """Return the backup directory path."""
        return self._backup_dir

    def backup_before_modify(self, file_path: str) -> Optional[str]:
        """Create backup of a file before modification.

        Returns the backup path, or None if the source file doesn't exist.
        """
        src = Path(file_path)
        if not src.exists() or not src.is_file():
            return None

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{src.stem}_{timestamp}{src.suffix}.bak"
        backup_path = self._backup_dir / backup_name

        shutil.copy2(str(src), str(backup_path))
        return str(backup_path)

    def restore(self, backup_path: str, original_path: str) -> bool:
        """Restore a file from backup.

        Returns True if the restore succeeded.
        """
        src = Path(backup_path)
        if not src.exists() or not src.is_file():
            return False

        dst = Path(original_path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dst))
        return True

    def cleanup_old_backups(
        self,
        max_age_hours: float | None = None,
        max_count: int | None = None,
    ) -> int:
        """Remove old backups. Returns the number of files deleted."""
        age_limit = max_age_hours if max_age_hours is not None else self._max_age_hours
        count_limit = max_count if max_count is not None else self._max_count

        backups = sorted(
            self._backup_dir.glob("*.bak"),
            key=lambda p: p.stat().st_mtime,
        )

        deleted = 0
        now = time.time()
        cutoff = now - (age_limit * 3600)

        # Remove old files
        for bak in backups:
            if bak.stat().st_mtime < cutoff:
                bak.unlink()
                deleted += 1

        # Re-read after age cleanup
        backups = sorted(
            self._backup_dir.glob("*.bak"),
            key=lambda p: p.stat().st_mtime,
        )

        # Remove excess beyond max_count (oldest first)
        while len(backups) > count_limit:
            backups[0].unlink()
            backups.pop(0)
            deleted += 1

        return deleted

    def list_backups(self) -> list[dict]:
        """List available backups with metadata."""
        backups = sorted(
            self._backup_dir.glob("*.bak"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        result = []
        for bak in backups:
            stat = bak.stat()
            result.append({
                "path": str(bak),
                "name": bak.name,
                "size_bytes": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
        return result
