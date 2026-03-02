"""Immutable snapshot of the desktop state at a point in time.

Created once per Decision Loop cycle. Never mutated. The Kernel
reasons about this snapshot, never about live state.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass(frozen=True)
class WindowInfo:
    """Info about a single window."""

    hwnd: int
    title: str
    process_name: str
    class_name: str = ""
    is_visible: bool = True
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)  # left, top, right, bottom


@dataclass(frozen=True)
class WorldStateSnapshot:
    """Immutable snapshot of the desktop at a specific moment.

    Created at the start of each Decision Loop cycle.
    The Kernel reasons about this snapshot, never about live state.
    """

    # Timing
    cycle_number: int
    timestamp_mono: float  # time.monotonic()
    timestamp_utc: str  # ISO format for logging

    # Active window
    active_window: Optional[WindowInfo] = None

    # All visible windows
    open_windows: tuple[WindowInfo, ...] = ()

    # Focused element (if detectable)
    focused_element: Optional[str] = None

    # Clipboard hash (not content — privacy)
    clipboard_hash: Optional[str] = None

    # Screen info
    screen_width: int = 0
    screen_height: int = 0

    # Running goals context
    active_goal_id: Optional[str] = None
    active_step_index: int = 0

    def has_window(self, title_contains: str) -> bool:
        """Check if any open window title contains the given text."""
        title_lower = title_contains.lower()
        return any(title_lower in w.title.lower() for w in self.open_windows)

    def get_window(self, title_contains: str) -> Optional[WindowInfo]:
        """Find first window matching title."""
        title_lower = title_contains.lower()
        for w in self.open_windows:
            if title_lower in w.title.lower():
                return w
        return None

    @property
    def active_window_title(self) -> str:
        """Title of the active window, or empty string."""
        return self.active_window.title if self.active_window else ""

    @property
    def active_process(self) -> str:
        """Process name of the active window, or empty string."""
        return self.active_window.process_name if self.active_window else ""

    @property
    def window_count(self) -> int:
        """Number of open windows."""
        return len(self.open_windows)

    def fingerprint(self) -> str:
        """Hash of key state for change detection."""
        state_str = (
            f"{self.active_window_title}|{self.window_count}|"
            f"{self.clipboard_hash}|{self.focused_element}"
        )
        return hashlib.md5(state_str.encode()).hexdigest()[:12]

    def diff(self, other: WorldStateSnapshot) -> dict:
        """Compare two snapshots, return what changed."""
        changes: dict = {}
        if self.active_window != other.active_window:
            changes["active_window"] = {
                "from": other.active_window_title,
                "to": self.active_window_title,
            }
        if self.window_count != other.window_count:
            changes["window_count"] = {
                "from": other.window_count,
                "to": self.window_count,
            }
        if self.clipboard_hash != other.clipboard_hash:
            changes["clipboard_changed"] = True
        if self.focused_element != other.focused_element:
            changes["focused_element"] = {
                "from": other.focused_element,
                "to": self.focused_element,
            }
        return changes


class WorldStateCapture:
    """Captures the current desktop state into an immutable snapshot.

    Uses ctypes Win32 API when available, falls back to stubs for
    testing on non-Windows platforms.
    """

    def __init__(self):
        self._cycle_counter = 0

    def capture(
        self,
        active_goal_id: str | None = None,
        active_step_index: int = 0,
    ) -> WorldStateSnapshot:
        """Capture current desktop state (synchronous)."""
        self._cycle_counter += 1

        windows = self._get_windows()
        active = self._get_active_window(windows)

        return WorldStateSnapshot(
            cycle_number=self._cycle_counter,
            timestamp_mono=time.monotonic(),
            timestamp_utc=datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%f",
            )[:-3] + "Z",
            active_window=active,
            open_windows=tuple(windows),
            focused_element=self._get_focused_element(),
            clipboard_hash=self._get_clipboard_hash(),
            screen_width=self._get_screen_width(),
            screen_height=self._get_screen_height(),
            active_goal_id=active_goal_id,
            active_step_index=active_step_index,
        )

    # ── Private helpers ──

    def _get_windows(self) -> list[WindowInfo]:
        """Get visible windows via EnumWindows. Empty list on non-Windows."""
        try:
            import ctypes
            from ctypes import wintypes

            windows: list[WindowInfo] = []

            def enum_callback(hwnd, _):
                if ctypes.windll.user32.IsWindowVisible(hwnd):
                    length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buf = ctypes.create_unicode_buffer(length + 1)
                        ctypes.windll.user32.GetWindowTextW(
                            hwnd, buf, length + 1,
                        )
                        title = buf.value
                        if title.strip():
                            pid = wintypes.DWORD()
                            ctypes.windll.user32.GetWindowThreadProcessId(
                                hwnd, ctypes.byref(pid),
                            )
                            proc_name = self._get_process_name(pid.value)
                            windows.append(WindowInfo(
                                hwnd=hwnd,
                                title=title,
                                process_name=proc_name,
                            ))
                return True

            WNDENUMPROC = ctypes.WINFUNCTYPE(
                ctypes.c_bool, wintypes.HWND, wintypes.LPARAM,
            )
            ctypes.windll.user32.EnumWindows(
                WNDENUMPROC(enum_callback), 0,
            )
            return windows
        except (ImportError, AttributeError, OSError):
            return []

    def _get_active_window(
        self, windows: list[WindowInfo],
    ) -> Optional[WindowInfo]:
        """Get the foreground window from the enumerated list."""
        try:
            import ctypes

            hwnd = ctypes.windll.user32.GetForegroundWindow()
            for w in windows:
                if w.hwnd == hwnd:
                    return w
        except (ImportError, AttributeError, OSError):
            pass
        return windows[0] if windows else None

    def _get_focused_element(self) -> Optional[str]:
        """Placeholder — populated by UIA in future tiers."""
        return None

    def _get_clipboard_hash(self) -> Optional[str]:
        """Hash of clipboard content (privacy-safe)."""
        try:
            import ctypes
            from ctypes import wintypes

            # Set proper types for 64-bit handle compatibility
            ctypes.windll.user32.GetClipboardData.restype = ctypes.c_void_p
            ctypes.windll.kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
            ctypes.windll.kernel32.GlobalLock.restype = ctypes.c_void_p
            ctypes.windll.kernel32.GlobalSize.argtypes = [ctypes.c_void_p]
            ctypes.windll.kernel32.GlobalSize.restype = ctypes.c_size_t
            ctypes.windll.kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]

            if not ctypes.windll.user32.OpenClipboard(0):
                return None
            try:
                handle = ctypes.windll.user32.GetClipboardData(13)  # CF_UNICODETEXT
                if not handle:
                    return None
                ptr = ctypes.windll.kernel32.GlobalLock(handle)
                if not ptr:
                    return None
                try:
                    size = ctypes.windll.kernel32.GlobalSize(handle)
                    if size > 0:
                        # Read up to 1KB for hashing (enough for change detection)
                        data = ctypes.string_at(ptr, min(size, 1024))
                        return hashlib.md5(data).hexdigest()[:12]
                finally:
                    ctypes.windll.kernel32.GlobalUnlock(handle)
            finally:
                ctypes.windll.user32.CloseClipboard()
        except Exception:
            pass
        return None

    def _get_screen_width(self) -> int:
        """Primary monitor width."""
        try:
            import ctypes

            return ctypes.windll.user32.GetSystemMetrics(0)
        except (ImportError, AttributeError, OSError):
            return 1920

    def _get_screen_height(self) -> int:
        """Primary monitor height."""
        try:
            import ctypes

            return ctypes.windll.user32.GetSystemMetrics(1)
        except (ImportError, AttributeError, OSError):
            return 1080

    def _get_process_name(self, pid: int) -> str:
        """Get process name from PID via Win32 API."""
        try:
            import ctypes

            PROCESS_QUERY_INFORMATION = 0x0400
            PROCESS_VM_READ = 0x0010
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid,
            )
            if handle:
                try:
                    buf = ctypes.create_unicode_buffer(260)
                    ctypes.windll.psapi.GetModuleBaseNameW(
                        handle, None, buf, 260,
                    )
                    return buf.value or f"pid_{pid}"
                finally:
                    ctypes.windll.kernel32.CloseHandle(handle)
        except (ImportError, AttributeError, OSError):
            pass
        return f"pid_{pid}"
