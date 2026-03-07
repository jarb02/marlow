"""Compositor FocusGuard — Marlow Compositor IPC.

Saves and restores window focus via the compositor's dual-seat
system instead of Sway IPC.

/ FocusGuard via IPC directo al compositor Marlow.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from typing import Optional

from marlow.platform.base import FocusGuard, FocusSnapshot
from marlow.platform.linux.compositor_client import MarlowCompositorClient

logger = logging.getLogger("marlow.platform.compositor.focus")


def _run_async(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result(timeout=10)
    else:
        return asyncio.run(coro)


class CompositorFocusGuard(FocusGuard):
    """Save and restore focus via Marlow Compositor seat status."""

    def __init__(self, socket_path: str = None, window_manager=None):
        self._socket_path = socket_path
        self._wm = window_manager
        self._last_snapshot: Optional[FocusSnapshot] = None

    def save_user_focus(self) -> Optional[FocusSnapshot]:
        try:
            # Use the window manager to find the focused window
            if self._wm:
                focused = self._wm.get_focused_window()
                if focused:
                    snapshot = FocusSnapshot(
                        identifier=focused.identifier,
                        title=focused.title,
                    )
                    self._last_snapshot = snapshot
                    logger.debug("Saved focus: [%s] %s",
                                 snapshot.identifier, snapshot.title)
                    return snapshot

            logger.debug("No focused window to save")
            return None
        except Exception as e:
            logger.warning("save_user_focus failed: %s", e)
            return None

    def restore_user_focus(self, snapshot: Optional[FocusSnapshot] = None) -> bool:
        target = snapshot or self._last_snapshot
        if target is None:
            logger.debug("No focus snapshot to restore")
            return False
        try:
            wid = int(target.identifier)

            async def _restore():
                client = MarlowCompositorClient()
                await client.connect(self._socket_path)
                try:
                    return await client.focus_window(wid)
                finally:
                    await client.disconnect()

            ok = _run_async(_restore())
            if ok:
                logger.debug("Restored focus: [%s] %s",
                             target.identifier, target.title)
            else:
                logger.warning("Window no longer exists: [%s] %s",
                               target.identifier, target.title)
            return ok
        except Exception as e:
            logger.warning("restore_user_focus failed: %s", e)
            return False
