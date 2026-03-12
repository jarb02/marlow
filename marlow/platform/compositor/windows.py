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
        action_lower = action.lower()
        try:
            wid = int(identifier)
        except ValueError:
            # Fuzzy match by title/app_id
            windows = self.list_windows()
            id_lower = identifier.lower()
            wid = None
            for w in windows:
                if id_lower in w.title.lower() or id_lower in w.app_name.lower():
                    wid = int(w.identifier)
                    break
            if wid is None:
                logger.warning("manage_window: window not found: %s", identifier)
                return False

        if action_lower == "close":
            return _run_async(self._with_client(
                lambda c, _wid=wid: c.close_window(_wid)
            ))
        elif action_lower == "minimize":
            return _run_async(self._with_client(
                lambda c, _wid=wid: c.minimize_window(_wid)
            ))
        elif action_lower == "maximize":
            return _run_async(self._with_client(
                lambda c, _wid=wid: c.maximize_window(_wid)
            ))
        else:
            logger.warning(
                "manage_window(%s, %s) unsupported action", identifier, action,
            )
            return False

    # ── Shadow Mode operations ──

    def launch_in_shadow(self, command: str) -> dict:
        """Launch a command in shadow_space, wait up to 10s for its window."""
        import time

        try:
            data = _run_async(self._with_client(
                lambda c: c.launch_in_shadow(command)
            ))
            if "error" in data:
                logger.warning("launch_in_shadow failed: %s", data["error"])
                return {"success": False, "error": data["error"]}

            logger.info("launch_in_shadow: %s -> %s (waiting for window)", command, data)

            # Poll for the shadow window to appear (app needs time to connect)
            for i in range(20):  # 20 x 0.5s = 10s max
                time.sleep(0.5)
                shadow = self.get_shadow_windows()
                if shadow:
                    win = shadow[0]
                    logger.info("launch_in_shadow: window appeared after %.1fs: %s",
                                (i + 1) * 0.5, win.title)
                    return {
                        "success": True,
                        "window_id": int(win.identifier),
                        "title": win.title,
                        "app_id": win.app_name,
                        **data,
                    }

            logger.warning("launch_in_shadow: window did not appear in 10s")
            return {"success": True, "warning": "window not yet visible", **data}
        except Exception as e:
            logger.error("launch_in_shadow error: %s", e)
            return {"success": False, "error": str(e)}

    def get_shadow_windows(self) -> list[WindowInfo]:
        """List all windows in shadow_space (invisible)."""
        try:
            windows = _run_async(self._with_client(
                lambda c: c.get_shadow_windows()
            ))
            logger.info("get_shadow_windows: %d windows", len(windows))
            result: list[WindowInfo] = []
            for w in windows:
                wid = w.get("window_id", 0)
                result.append(WindowInfo(
                    identifier=str(wid),
                    title=w.get("title", "(unnamed)"),
                    app_name=w.get("app_id", f"window_{wid}"),
                    pid=w.get("pid", 0),
                    is_focused=False,
                    is_visible=False,
                    x=w.get("x", 0),
                    y=w.get("y", 0),
                    width=w.get("width", 0),
                    height=w.get("height", 0),
                    extra={
                        "app_id": w.get("app_id", ""),
                        "compositor": "marlow",
                        "shadow": True,
                    },
                ))
            return result
        except Exception as e:
            logger.error("get_shadow_windows error: %s", e)
            return []

    def move_to_user(self, window_id: int) -> dict:
        """Promote a window from shadow_space to user_space."""
        try:
            ok = _run_async(self._with_client(
                lambda c: c.move_to_user(window_id)
            ))
            if ok:
                logger.info("move_to_user: window %d promoted", window_id)
                return {"success": True, "window_id": window_id}
            logger.warning("move_to_user failed for window %d", window_id)
            return {"success": False, "error": "compositor returned false"}
        except Exception as e:
            logger.error("move_to_user error: %s", e)
            return {"success": False, "error": str(e)}

    def move_to_shadow(self, window_id: int) -> dict:
        """Move a window from user_space to shadow_space."""
        try:
            ok = _run_async(self._with_client(
                lambda c: c.move_to_shadow(window_id)
            ))
            if ok:
                logger.info("move_to_shadow: window %d hidden", window_id)
                return {"success": True, "window_id": window_id}
            logger.warning("move_to_shadow failed for window %d", window_id)
            return {"success": False, "error": "compositor returned false"}
        except Exception as e:
            logger.error("move_to_shadow error: %s", e)
            return {"success": False, "error": str(e)}

    def request_screenshot(self, window_id: int = None, timeout: float = 5.0) -> bytes | None:
        """Request a screenshot via compositor IPC. Returns PNG bytes or None.

        Retries once on partial read (large payloads may exceed IPC buffer).
        """
        import base64

        for attempt in range(2):
            try:
                async def _screenshot(client):
                    return await client.request_screenshot(window_id=window_id, timeout=timeout)
                b64_data = _run_async(self._with_client(_screenshot))
                if b64_data:
                    return base64.b64decode(b64_data)
                return None
            except Exception as e:
                if attempt == 0 and "bytes read" in str(e):
                    logger.warning("Screenshot partial read, retrying...")
                    import time as _time
                    _time.sleep(0.2)
                    continue
                logger.error("request_screenshot error: %s", e)
                return None
        return None
