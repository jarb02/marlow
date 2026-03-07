"""Linux WindowManager — Marlow Compositor IPC.

Wraps the async MarlowCompositorClient into the sync WindowManager ABC.
Used when running under the Marlow compositor (not Sway).

/ WindowManager Linux — IPC al compositor Marlow.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from marlow.platform.base import WindowInfo, WindowManager

logger = logging.getLogger("marlow.platform.linux.compositor_windows")


def _socket_exists() -> bool:
    """Check if the Marlow compositor IPC socket exists."""
    runtime_dir = os.environ.get(
        "XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"
    )
    return os.path.exists(os.path.join(runtime_dir, "marlow-compositor.sock"))


class CompositorWindowManager(WindowManager):
    """Window management via Marlow Compositor IPC (Unix socket + MessagePack)."""

    def __init__(self):
        from .compositor_client import MarlowCompositorClient
        self._client = MarlowCompositorClient()
        self._connected = False

    def _ensure_connected(self):
        """Connect to compositor if not already connected."""
        if not self._connected:
            try:
                asyncio.get_event_loop().run_until_complete(
                    self._client.connect()
                )
                self._connected = True
            except Exception as e:
                logger.error("Failed to connect to compositor: %s", e)
                self._connected = False

    def _run(self, coro):
        """Run an async coroutine synchronously."""
        self._ensure_connected()
        if not self._connected:
            return None
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If already in an async context, create a new loop
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    return pool.submit(asyncio.run, coro).result(timeout=5)
            return loop.run_until_complete(coro)
        except Exception as e:
            logger.error("IPC call failed: %s", e)
            self._connected = False
            return None

    # ── WindowManager interface ──

    def list_windows(self, include_minimized: bool = True) -> list[WindowInfo]:
        try:
            windows = self._run(self._client.list_windows())
            if not windows:
                return []
            result = []
            for w in windows:
                result.append(WindowInfo(
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
                ))
            return result
        except Exception as e:
            logger.error("list_windows failed: %s", e)
            return []

    def focus_window(self, identifier: str) -> bool:
        try:
            window_id = int(identifier)
            result = self._run(self._client.focus_window(window_id))
            return bool(result)
        except Exception as e:
            logger.error("focus_window failed: %s", e)
            return False

    def get_focused_window(self) -> Optional[WindowInfo]:
        try:
            windows = self._run(self._client.list_windows())
            if not windows:
                return None
            for w in windows:
                if w.get("focused"):
                    return WindowInfo(
                        identifier=str(w.get("window_id", 0)),
                        title=w.get("title", "(unnamed)"),
                        app_name=w.get("app_id", ""),
                        pid=0,
                        is_focused=True,
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
            return None
        except Exception as e:
            logger.error("get_focused_window failed: %s", e)
            return None

    def manage_window(self, identifier: str, action: str, **kwargs) -> bool:
        logger.warning(
            "manage_window not yet implemented for compositor backend "
            "(action=%s, id=%s)", action, identifier
        )
        return False


if __name__ == "__main__":
    if not _socket_exists():
        print("Compositor socket not found — is the compositor running?")
    else:
        wm = CompositorWindowManager()
        print("=== list_windows ===")
        wins = wm.list_windows()
        for w in wins:
            flag = "*" if w.is_focused else " "
            print(f"  {flag} [{w.identifier}] {w.title} ({w.app_name}) "
                  f"@ {w.x},{w.y} {w.width}x{w.height}")
        print(f"  Total: {len(wins)}")
        print("\nPASS: CompositorWindowManager self-test complete")
