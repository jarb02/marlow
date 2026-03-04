"""Window State Tracker — detects window changes between execution steps."""

from dataclasses import dataclass, field
from typing import Optional
import time
import logging

logger = logging.getLogger("marlow.kernel.window_tracker")


@dataclass(frozen=True)
class WindowSnapshot:
    title: str
    hwnd: int  # window handle
    pid: int
    rect: tuple[int, int, int, int]  # left, top, right, bottom
    is_active: bool
    timestamp: float


@dataclass
class WindowChange:
    change_type: str  # "appeared", "disappeared", "title_changed", "focus_lost", "focus_gained", "moved", "not_responding"
    window_title: str
    old_value: str = ""
    new_value: str = ""
    timestamp: float = 0.0


class WindowTracker:
    def __init__(self, max_history: int = 50):
        self._snapshots: list[list[WindowSnapshot]] = []
        self._max_history = max_history
        self._expected_app: str = ""

    def set_expected_app(self, app_name: str):
        """Set which app we expect to be active for the current task."""
        self._expected_app = app_name

    def record_snapshot(self, windows: list[dict]):
        """
        Record a snapshot of all current windows.
        windows = list of dicts from list_windows tool, each with:
            title, hwnd, pid, rect (dict with left/top/right/bottom), is_active (bool)
        """
        snap = []
        now = time.time()
        for w in windows:
            rect = w.get("rect", {})
            snap.append(WindowSnapshot(
                title=w.get("title", ""),
                hwnd=w.get("hwnd", 0),
                pid=w.get("pid", 0),
                rect=(rect.get("left", 0), rect.get("top", 0), rect.get("right", 0), rect.get("bottom", 0)),
                is_active=w.get("is_active", False),
                timestamp=now,
            ))
        self._snapshots.append(snap)
        if len(self._snapshots) > self._max_history:
            self._snapshots.pop(0)

    def detect_changes(self) -> list[WindowChange]:
        """
        Compare last two snapshots and return list of changes.
        Call this after recording a new snapshot.
        """
        if len(self._snapshots) < 2:
            return []

        prev = {w.hwnd: w for w in self._snapshots[-2]}
        curr = {w.hwnd: w for w in self._snapshots[-1]}
        now = time.time()
        changes = []

        # New windows (appeared)
        for hwnd, w in curr.items():
            if hwnd not in prev and w.title.strip():
                changes.append(WindowChange("appeared", w.title, timestamp=now))

        # Gone windows (disappeared)
        for hwnd, w in prev.items():
            if hwnd not in curr and w.title.strip():
                changes.append(WindowChange("disappeared", w.title, timestamp=now))

        # Title changes
        for hwnd in set(prev) & set(curr):
            if prev[hwnd].title != curr[hwnd].title and curr[hwnd].title.strip():
                changes.append(WindowChange(
                    "title_changed", curr[hwnd].title,
                    old_value=prev[hwnd].title, new_value=curr[hwnd].title,
                    timestamp=now,
                ))

        # Focus changes
        prev_active = [w for w in prev.values() if w.is_active]
        curr_active = [w for w in curr.values() if w.is_active]
        if prev_active and curr_active:
            if prev_active[0].hwnd != curr_active[0].hwnd:
                changes.append(WindowChange(
                    "focus_lost", prev_active[0].title, timestamp=now,
                ))
                changes.append(WindowChange(
                    "focus_gained", curr_active[0].title, timestamp=now,
                ))

        return changes

    def is_expected_app_active(self) -> bool:
        """Check if the expected app is currently the active window."""
        if not self._expected_app or not self._snapshots:
            return True  # no expectation = assume OK
        current = self._snapshots[-1]
        active = [w for w in current if w.is_active]
        if not active:
            return False
        stem = self._expected_app.lower().replace(".exe", "")
        return stem in active[0].title.lower()

    def get_active_window_title(self) -> str:
        """Get the title of the currently active window."""
        if not self._snapshots:
            return ""
        active = [w for w in self._snapshots[-1] if w.is_active]
        return active[0].title if active else ""

    def window_appeared_since_last(self, title_contains: str) -> bool:
        """Check if a window containing the given text appeared since last snapshot."""
        changes = self.detect_changes()
        return any(
            c.change_type == "appeared" and title_contains.lower() in c.window_title.lower()
            for c in changes
        )
