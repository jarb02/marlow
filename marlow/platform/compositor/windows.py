"""Compositor WindowManager — Marlow Compositor IPC.

Manages windows via the Marlow Compositor's Unix socket IPC
instead of Sway's i3 IPC protocol.

/ WindowManager para el compositor Marlow via IPC directo.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from typing import Optional

from marlow.platform.base import WindowInfo, WindowManager
from marlow.platform.linux.compositor_client import MarlowCompositorClient

logger = logging.getLogger("marlow.platform.compositor.windows")


def _run_async(coro):
    """Run an async coroutine from sync code."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result(timeout=10)
    else:
        return asyncio.run(coro)


class CompositorWindowManager(WindowManager):
    """Window management via Marlow Compositor IPC."""

    def __init__(self, socket_path: str = None):
        self._socket_path = socket_path

    async def _with_client(self, fn):
        """Connect, call fn(client), disconnect."""
        client = MarlowCompositorClient()
        await client.connect(self._socket_path)
        try:
            return await fn(client)
        finally:
            await client.disconnect()

    def list_windows(self, include_minimized: bool = True) -> list[WindowInfo]:
        try:
            async def _list(client):
                return await client.list_windows()

            windows = _run_async(self._with_client(_list))
            logger.info("IPC list_windows returned %d windows: %s", len(windows), windows)
            result: list[WindowInfo] = []

            for w in windows:
                wid = w.get("window_id", 0)
                result.append(WindowInfo(
                    identifier=str(wid),
                    title=w.get("title", "(unnamed)"),
                    app_name=w.get("app_id", f"window_{wid}"),
                    pid=w.get("pid", 0),
                    is_focused=w.get("focused", False),
                    is_visible=True,
                    x=w.get("x", 0),
                    y=w.get("y", 0),
                    width=w.get("width", 0),
                    height=w.get("height", 0),
                    extra={
                        "app_id": w.get("app_id", ""),
                        "compositor": "marlow",
                        "space": w.get("space", "user"),
                    },
                ))
            return result
        except Exception as e:
            logger.error("list_windows failed: %s", e)
            return []

    def focus_window(self, identifier: str) -> bool:
        try:
            # Try numeric window id first
            try:
                wid = int(identifier)
                return _run_async(self._with_client(
                    lambda c: c.focus_window(wid)
                ))
            except ValueError:
                pass

            # Fuzzy title/app_id match
            windows = self.list_windows()
            id_lower = identifier.lower()
            for w in windows:
                if id_lower in w.title.lower() or id_lower in w.app_name.lower():
                    wid = int(w.identifier)
                    return _run_async(self._with_client(
                        lambda c, _wid=wid: c.focus_window(_wid)
                    ))
            logger.warning("Window not found: %s", identifier)
            return False
        except Exception as e:
            logger.error("focus_window failed: %s", e)
            return False

    def get_focused_window(self) -> Optional[WindowInfo]:
        try:
            windows = self.list_windows()
            for w in windows:
                if w.is_focused:
                    return w
            return windows[0] if windows else None
        except Exception as e:
            logger.error("get_focused_window failed: %s", e)
            return None

    def manage_window(self, identifier: str, action: str, **kwargs) -> bool:
        logger.warning(
            "manage_window(%s, %s) not yet supported by Marlow Compositor IPC",
            identifier, action,
        )
        return False
