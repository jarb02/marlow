"""Immutable snapshot of the desktop state — Linux implementation.

Replaces ctypes.windll calls with the Marlow platform layer
(Sway IPC, wl-clipboard, AT-SPI2). Returns the same format as
the Windows original so the rest of the kernel is unaffected.

/ Estado del escritorio — implementacion Linux via platform layer.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("marlow.kernel.world_state_linux")


# Re-export the data classes unchanged — kernel code imports these.
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

    Uses the Marlow Linux platform layer (Sway IPC + wl-clipboard +
    AT-SPI2) instead of ctypes Win32 API.
    """

    def __init__(self):
        self._cycle_counter = 0
        self._platform = None

    def _get_platform(self):
        """Lazy-load the platform singleton."""
        if self._platform is None:
            try:
                from marlow.platform import get_platform
                self._platform = get_platform()
            except Exception as e:
                logger.warning("Platform layer not available: %s", e)
        return self._platform

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

    # -- Private helpers --

    def _get_windows(self) -> list[WindowInfo]:
        """Get visible windows via Sway IPC (platform.windows.list_windows)."""
        p = self._get_platform()
        if not p:
            return []
        try:
            win_list = p.windows.list_windows(include_minimized=False)
            windows: list[WindowInfo] = []
            for w in win_list:
                # Platform WindowInfo -> kernel WindowInfo
                # Map identifier (con_id string) to int for hwnd compat
                try:
                    hwnd = int(w.identifier) if w.identifier else 0
                except (ValueError, TypeError):
                    hwnd = hash(w.identifier) & 0x7FFFFFFF
                windows.append(WindowInfo(
                    hwnd=hwnd,
                    title=w.title or "",
                    process_name=w.app_name or f"pid_{w.pid}",
                    class_name=w.extra.get("app_id", "") if w.extra else "",
                    is_visible=w.is_visible,
                    rect=(w.x, w.y, w.x + w.width, w.y + w.height),
                ))
            return windows
        except Exception as e:
            logger.debug("Failed to enumerate windows: %s", e)
            return []

    def _get_active_window(
        self, windows: list[WindowInfo],
    ) -> Optional[WindowInfo]:
        """Get the focused window from the platform layer."""
        p = self._get_platform()
        if not p:
            return windows[0] if windows else None
        try:
            focused = p.windows.get_focused_window()
            if focused:
                # Match by identifier
                try:
                    focused_hwnd = int(focused.identifier) if focused.identifier else 0
                except (ValueError, TypeError):
                    focused_hwnd = hash(focused.identifier) & 0x7FFFFFFF
                for w in windows:
                    if w.hwnd == focused_hwnd:
                        return w
                # Not found in list — could be a new window
                return WindowInfo(
                    hwnd=focused_hwnd,
                    title=focused.title or "",
                    process_name=focused.app_name or "",
                )
        except Exception as e:
            logger.debug("Failed to get focused window: %s", e)
        return windows[0] if windows else None

    def _get_focused_element(self) -> Optional[str]:
        """Placeholder — could use AT-SPI2 focused element in future."""
        return None

    def _get_clipboard_hash(self) -> Optional[str]:
        """Hash of clipboard content via wl-paste (privacy-safe)."""
        p = self._get_platform()
        if not p or not p.clipboard:
            return None
        try:
            text = p.clipboard.get_clipboard()
            if text:
                # Hash up to 1KB for change detection
                data = text[:1024].encode("utf-8", errors="replace")
                return hashlib.md5(data).hexdigest()[:12]
        except Exception:
            pass
        return None

    def _get_screen_width(self) -> int:
        """Primary display width via Sway IPC."""
        p = self._get_platform()
        if not p:
            return 1920
        try:
            info = p.system.get_system_info()
            display = info.get("display", {})
            displays = display.get("displays", [])
            if displays:
                return displays[0].get("width", 1920)
        except Exception:
            pass
        return 1920

    def _get_screen_height(self) -> int:
        """Primary display height via Sway IPC."""
        p = self._get_platform()
        if not p:
            return 1080
        try:
            info = p.system.get_system_info()
            display = info.get("display", {})
            displays = display.get("displays", [])
            if displays:
                return displays[0].get("height", 1080)
        except Exception:
            pass
        return 1080
