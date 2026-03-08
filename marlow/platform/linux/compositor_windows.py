"""Linux WindowManager — Marlow Compositor IPC (sync). No Sway fallback.

Uses a direct synchronous Unix socket + MessagePack. No asyncio.
Reconnects lazily on each call if disconnected.

When compositor IPC fails, operations fail cleanly — no Sway fallback.
Sway fallback caused crashes (SIGABRT) when sway was stale/dead.

Shadow Mode: launch_in_shadow, get_shadow_windows, move_to_user,
move_to_shadow — same sync socket pattern as list_windows.

/ WindowManager Linux — socket sync al compositor, sin fallback a Sway.
"""

from __future__ import annotations

import logging
import os
import socket
import struct
from typing import Optional

import msgpack

from marlow.platform.base import WindowInfo, WindowManager

logger = logging.getLogger("marlow.platform.linux.compositor_windows")


def _socket_path() -> str:
    runtime_dir = os.environ.get(
        "XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"
    )
    return os.path.join(runtime_dir, "marlow-compositor.sock")


class CompositorWindowManager(WindowManager):
    """Window management via compositor IPC only. No Sway fallback."""

    def __init__(self):
        self._sock: socket.socket | None = None

    def _ensure_connected(self) -> bool:
        """Connect to compositor socket if not already connected."""
        if self._sock is not None:
            return True

        path = _socket_path()
        if not os.path.exists(path):
            logger.debug("Compositor socket not found: %s", path)
            return False

        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(3)
            s.connect(path)
            self._sock = s
            logger.info("Connected to compositor IPC: %s", path)
            return True
        except Exception as e:
            logger.warning("Compositor connect failed: %s", e)
            return False

    def _send(self, request: dict) -> dict | None:
        """Send a request and receive the response. Returns None on error."""
        if not self._ensure_connected():
            return None

        try:
            payload = msgpack.packb(request, use_bin_type=True)
            self._sock.sendall(struct.pack("<I", len(payload)) + payload)

            # Read 4-byte length header
            len_buf = self._recv_exact(4)
            if len_buf is None:
                raise ConnectionError("Failed to read response length")
            msg_len = struct.unpack("<I", len_buf)[0]

            # Read response body
            resp_buf = self._recv_exact(msg_len)
            if resp_buf is None:
                raise ConnectionError("Failed to read response body")

            return msgpack.unpackb(resp_buf, raw=False)
        except Exception as e:
            logger.warning("IPC send/recv failed: %s", e)
            self._disconnect()
            return None

    def _recv_exact(self, n: int) -> bytes | None:
        """Read exactly n bytes from socket."""
        data = b""
        while len(data) < n:
            chunk = self._sock.recv(n - len(data))
            if not chunk:
                return None
            data += chunk
        return data

    def _disconnect(self):
        """Close socket so next call retries."""
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    # ── WindowManager interface ──

    def list_windows(self, include_minimized: bool = True) -> list[WindowInfo]:
        resp = self._send({"type": "ListWindows"})
        if resp and resp.get("status") == "ok":
            windows = resp.get("data", [])
            logger.info("list_windows: %d windows", len(windows))
            return [self._to_info(w) for w in windows]
        logger.warning("list_windows: compositor IPC failed")
        return []

    def focus_window(self, identifier: str) -> bool:
        # Try numeric window_id first
        try:
            wid = int(identifier)
            resp = self._send({"type": "FocusWindow", "window_id": wid})
            if resp and resp.get("status") == "ok":
                return True
            logger.warning("focus_window(%d): %s", wid,
                           resp.get("message", "failed") if resp else "IPC failed")
            return False
        except ValueError:
            pass

        # String identifier — search by title/app_id in window list
        resp = self._send({"type": "ListWindows"})
        if not resp or resp.get("status") != "ok":
            logger.warning("focus_window('%s'): can't list windows", identifier)
            return False

        id_lower = identifier.lower()
        for w in resp.get("data", []):
            title = (w.get("title") or "").lower()
            app_id = (w.get("app_id") or "").lower()
            if id_lower in title or id_lower in app_id:
                wid = w.get("window_id")
                if wid is not None:
                    focus_resp = self._send({"type": "FocusWindow", "window_id": wid})
                    if focus_resp and focus_resp.get("status") == "ok":
                        logger.info("focus_window('%s'): focused window %d (%s)",
                                    identifier, wid, w.get("title"))
                        return True

        logger.warning("focus_window('%s'): no matching window found", identifier)
        return False

    def get_focused_window(self) -> Optional[WindowInfo]:
        resp = self._send({"type": "ListWindows"})
        if resp and resp.get("status") == "ok":
            for w in resp.get("data", []):
                if w.get("focused"):
                    return self._to_info(w)
        return None

    def manage_window(self, identifier: str, action: str, **kwargs) -> bool:
        """Manage a window: close, minimize, maximize via compositor IPC."""
        # Resolve window_id from identifier
        try:
            wid = int(identifier)
        except ValueError:
            # Search by title/app_id
            wid = self._find_window_id(identifier)
            if wid is None:
                logger.warning("manage_window: window '%s' not found", identifier)
                return False

        action_lower = action.lower()
        type_map = {
            "close": "CloseWindow",
            "minimize": "MinimizeWindow",
            "maximize": "MaximizeWindow",
        }

        ipc_type = type_map.get(action_lower)
        if not ipc_type:
            logger.warning("manage_window: unsupported action '%s'", action)
            return False

        resp = self._send({"type": ipc_type, "window_id": wid})
        if resp and resp.get("status") == "ok":
            logger.info("manage_window: %s window %d", action_lower, wid)
            return True
        msg = resp.get("message", "IPC failed") if resp else "Compositor not available"
        logger.warning("manage_window(%s, %s) failed: %s", identifier, action, msg)
        return False

    def _find_window_id(self, identifier: str) -> int | None:
        """Search for a window ID by title or app_id substring."""
        resp = self._send({"type": "ListWindows"})
        if not resp or resp.get("status") != "ok":
            return None
        id_lower = identifier.lower()
        for w in resp.get("data", []):
            title = (w.get("title") or "").lower()
            app_id = (w.get("app_id") or "").lower()
            if id_lower in title or id_lower in app_id:
                return w.get("window_id")
        return None

    # ── Shadow Mode operations ──

    def launch_in_shadow(self, command: str) -> dict:
        """Launch a command and route its window to shadow_space."""
        resp = self._send({"type": "LaunchInShadow", "command": command})
        if resp and resp.get("status") == "ok":
            data = resp.get("data", {})
            logger.info("launch_in_shadow: %s -> %s", command, data)
            return {"success": True, **data}
        msg = resp.get("message", "IPC failed") if resp else "Compositor not available"
        logger.warning("launch_in_shadow failed: %s", msg)
        return {"success": False, "error": msg}

    def get_shadow_windows(self) -> list[WindowInfo]:
        """List all windows in shadow_space (invisible)."""
        resp = self._send({"type": "GetShadowWindows"})
        if resp and resp.get("status") == "ok":
            windows = resp.get("data", [])
            logger.info("get_shadow_windows: %d windows", len(windows))
            return [self._to_info(w, shadow=True) for w in windows]
        return []

    def move_to_user(self, window_id: int) -> dict:
        """Promote a window from shadow_space to user_space."""
        resp = self._send({"type": "MoveToUser", "window_id": window_id})
        if resp and resp.get("status") == "ok":
            data = resp.get("data", {})
            logger.info("move_to_user: window %d promoted", window_id)
            return {"success": True, "window_id": window_id, **data}
        msg = resp.get("message", "IPC failed") if resp else "Compositor not available"
        logger.warning("move_to_user failed: %s", msg)
        return {"success": False, "error": msg}

    def move_to_shadow(self, window_id: int) -> dict:
        """Move a window from user_space to shadow_space."""
        resp = self._send({"type": "MoveToShadow", "window_id": window_id})
        if resp and resp.get("status") == "ok":
            logger.info("move_to_shadow: window %d hidden", window_id)
            return {"success": True, "window_id": window_id}
        msg = resp.get("message", "IPC failed") if resp else "Compositor not available"
        logger.warning("move_to_shadow failed: %s", msg)
        return {"success": False, "error": msg}

    # ── Helpers ──

    @staticmethod
    def _to_info(w: dict, shadow: bool = False) -> WindowInfo:
        return WindowInfo(
            identifier=str(w.get("window_id", 0)),
            title=w.get("title") or "(unnamed)",
            app_name=w.get("app_id") or "",
            pid=0,
            is_focused=w.get("focused", False),
            is_visible=not shadow,
            x=w.get("x", 0),
            y=w.get("y", 0),
            width=w.get("width", 0),
            height=w.get("height", 0),
            extra={
                "window_id": w.get("window_id", 0),
                "app_id": w.get("app_id", ""),
                "backend": "compositor",
                "shadow": shadow,
            },
        )


if __name__ == "__main__":
    wm = CompositorWindowManager()
    print("=== list_windows (user_space) ===")
    wins = wm.list_windows()
    for w in wins:
        flag = "*" if w.is_focused else " "
        print(f"  {flag} [{w.identifier}] {w.title} ({w.app_name}) "
              f"@ {w.x},{w.y} {w.width}x{w.height}")
    print(f"  Total: {len(wins)}")

    print("\n=== get_shadow_windows ===")
    shadow = wm.get_shadow_windows()
    for w in shadow:
        print(f"  [shadow] [{w.identifier}] {w.title} ({w.app_name})")
    print(f"  Total: {len(shadow)}")
