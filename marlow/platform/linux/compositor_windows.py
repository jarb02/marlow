"""Linux WindowManager — Marlow Compositor IPC with Sway fallback.

Tries the Marlow compositor IPC socket first. If unavailable (compositor
not running, socket doesn't exist yet), falls back to Sway i3ipc.
Reconnects lazily — if the compositor starts after the daemon, the next
call will pick it up.

/ WindowManager Linux — IPC compositor con fallback a Sway.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from marlow.platform.base import WindowInfo, WindowManager

logger = logging.getLogger("marlow.platform.linux.compositor_windows")


def _socket_path() -> str:
    runtime_dir = os.environ.get(
        "XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"
    )
    return os.path.join(runtime_dir, "marlow-compositor.sock")


class CompositorWindowManager(WindowManager):
    """Window management: compositor IPC first, Sway i3ipc fallback."""

    def __init__(self):
        self._client = None
        self._connected = False
        self._sway_fallback = None

    def _try_connect(self) -> bool:
        """Try to connect to the compositor IPC. Non-fatal on failure."""
        sock = _socket_path()
        if not os.path.exists(sock):
            self._connected = False
            return False

        try:
            if self._client is None:
                from .compositor_client import MarlowCompositorClient
                self._client = MarlowCompositorClient()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(self._client.connect(sock))
                self._connected = True
                logger.info("Connected to Marlow compositor IPC")
                return True
            finally:
                loop.close()
        except Exception as e:
            logger.debug("Compositor IPC connect failed: %s", e)
            self._connected = False
            self._client = None
            return False

    def _run(self, coro):
        """Run an async coroutine synchronously with a fresh event loop."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        except Exception as e:
            logger.debug("IPC call failed: %s", e)
            self._connected = False
            self._client = None
            return None
        finally:
            loop.close()

    def _get_sway(self):
        """Lazy-init the Sway fallback."""
        if self._sway_fallback is None:
            try:
                from .windows import SwayWindowManager
                self._sway_fallback = SwayWindowManager()
            except Exception:
                pass
        return self._sway_fallback

    # ── WindowManager interface ──

    def list_windows(self, include_minimized: bool = True) -> list[WindowInfo]:
        # Try compositor IPC first
        if not self._connected:
            self._try_connect()

        if self._connected:
            try:
                windows = self._run(self._client.list_windows())
                if windows is not None:
                    logger.info("list_windows via compositor IPC: %d windows", len(windows))
                    return [self._to_window_info(w) for w in windows]
            except Exception as e:
                logger.debug("Compositor list_windows failed: %s", e)
                self._connected = False

        # Fallback to Sway
        sway = self._get_sway()
        if sway:
            result = sway.list_windows(include_minimized)
            if result:
                logger.debug("list_windows via Sway fallback: %d windows", len(result))
                return result

        return []

    def focus_window(self, identifier: str) -> bool:
        if not self._connected:
            self._try_connect()

        if self._connected:
            try:
                window_id = int(identifier)
                result = self._run(self._client.focus_window(window_id))
                if result:
                    return True
            except (ValueError, Exception) as e:
                logger.debug("Compositor focus_window failed: %s", e)

        sway = self._get_sway()
        if sway:
            return sway.focus_window(identifier)
        return False

    def get_focused_window(self) -> Optional[WindowInfo]:
        if not self._connected:
            self._try_connect()

        if self._connected:
            try:
                windows = self._run(self._client.list_windows())
                if windows:
                    for w in windows:
                        if w.get("focused"):
                            return self._to_window_info(w)
            except Exception:
                self._connected = False

        sway = self._get_sway()
        if sway:
            return sway.get_focused_window()
        return None

    def manage_window(self, identifier: str, action: str, **kwargs) -> bool:
        sway = self._get_sway()
        if sway:
            return sway.manage_window(identifier, action, **kwargs)
        logger.warning("manage_window not implemented for compositor backend")
        return False

    # ── Helpers ──

    @staticmethod
    def _to_window_info(w: dict) -> WindowInfo:
        return WindowInfo(
            identifier=str(w.get("window_id", 0)),
            title=w.get("title", "(unnamed)"),
            app_name=w.get("app_id", ""),
            pid=0,
            is_focused=w.get("focused", False),
            is_visible=True,
            x=w.get("x", 0),
            y=w.get("y", 0),
            width=w.get("width", 0),
            height=w.get("height", 0),
            extra={
                "window_id": w.get("window_id", 0),
                "app_id": w.get("app_id", ""),
                "backend": "compositor",
            },
        )


if __name__ == "__main__":
    wm = CompositorWindowManager()
    print("=== list_windows ===")
    wins = wm.list_windows()
    for w in wins:
        flag = "*" if w.is_focused else " "
        print(f"  {flag} [{w.identifier}] {w.title} ({w.app_name}) "
              f"@ {w.x},{w.y} {w.width}x{w.height}")
    print(f"  Total: {len(wins)}")
