"""Linux WindowManager — Marlow Compositor IPC (sync) with Sway fallback.

Uses a direct synchronous Unix socket + MessagePack. No asyncio.
Reconnects lazily on each call if disconnected.

Shadow Mode methods: launch_in_shadow, get_shadow_windows, move_to_user,
move_to_shadow — same sync socket pattern as list_windows.

/ WindowManager Linux — socket sync al compositor Marlow + Shadow Mode.
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
    """Window management: compositor IPC (sync) first, Sway fallback."""

    def __init__(self):
        self._sock: socket.socket | None = None
        self._sway_fallback = None

    def _ensure_connected(self) -> bool:
        """Connect to compositor socket if not already connected."""
        if self._sock is not None:
            return True

        path = _socket_path()
        if not os.path.exists(path):
            return False

        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(3)
            s.connect(path)
            self._sock = s
            logger.info("Connected to compositor IPC: %s", path)
            return True
        except Exception as e:
            logger.debug("Compositor connect failed: %s", e)
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
            logger.debug("IPC send/recv failed: %s", e)
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

    def _get_sway(self):
        """Lazy-init Sway fallback."""
        if self._sway_fallback is None:
            try:
                from .windows import SwayWindowManager
                self._sway_fallback = SwayWindowManager()
            except Exception:
                pass
        return self._sway_fallback

    # ── WindowManager interface ──

    def list_windows(self, include_minimized: bool = True) -> list[WindowInfo]:
        # Try compositor IPC
        resp = self._send({"type": "ListWindows"})
        if resp and resp.get("status") == "ok":
            windows = resp.get("data", [])
            logger.info("list_windows via compositor: %d windows", len(windows))
            return [self._to_info(w) for w in windows]

        # Fallback to Sway
        sway = self._get_sway()
        if sway:
            try:
                result = sway.list_windows(include_minimized)
                if result:
                    return result
            except Exception:
                pass

        return []

    def focus_window(self, identifier: str) -> bool:
        try:
            wid = int(identifier)
        except ValueError:
            # Not a numeric ID — try Sway
            sway = self._get_sway()
            return sway.focus_window(identifier) if sway else False

        resp = self._send({"type": "FocusWindow", "window_id": wid})
        if resp and resp.get("status") == "ok":
            return True

        sway = self._get_sway()
        return sway.focus_window(identifier) if sway else False

    def get_focused_window(self) -> Optional[WindowInfo]:
        resp = self._send({"type": "ListWindows"})
        if resp and resp.get("status") == "ok":
            for w in resp.get("data", []):
                if w.get("focused"):
                    return self._to_info(w)

        sway = self._get_sway()
        return sway.get_focused_window() if sway else None

    def manage_window(self, identifier: str, action: str, **kwargs) -> bool:
        sway = self._get_sway()
        if sway:
            return sway.manage_window(identifier, action, **kwargs)
        return False

    # ── Shadow Mode operations ──

    def launch_in_shadow(self, command: str) -> dict:
        """Launch a command and route its window to shadow_space.

        / Lanza un comando y enruta su ventana al shadow_space.
        """
        resp = self._send({"type": "LaunchInShadow", "command": command})
        if resp and resp.get("status") == "ok":
            data = resp.get("data", {})
            logger.info("launch_in_shadow: %s → %s", command, data)
            return {"success": True, **data}
        msg = resp.get("message", "IPC failed") if resp else "Compositor not available"
        logger.warning("launch_in_shadow failed: %s", msg)
        return {"success": False, "error": msg}

    def get_shadow_windows(self) -> list[WindowInfo]:
        """List all windows in shadow_space (invisible).

        / Lista ventanas en shadow_space (invisibles al usuario).
        """
        resp = self._send({"type": "GetShadowWindows"})
        if resp and resp.get("status") == "ok":
            windows = resp.get("data", [])
            logger.info("get_shadow_windows: %d windows", len(windows))
            return [self._to_info(w, shadow=True) for w in windows]
        return []

    def move_to_user(self, window_id: int) -> dict:
        """Promote a window from shadow_space to user_space.

        / Promueve una ventana de shadow a visible.
        """
        resp = self._send({"type": "MoveToUser", "window_id": window_id})
        if resp and resp.get("status") == "ok":
            logger.info("move_to_user: window %d promoted", window_id)
            return {"success": True, "window_id": window_id}
        msg = resp.get("message", "IPC failed") if resp else "Compositor not available"
        logger.warning("move_to_user failed: %s", msg)
        return {"success": False, "error": msg}

    def move_to_shadow(self, window_id: int) -> dict:
        """Move a window from user_space to shadow_space.

        / Mueve una ventana de visible a shadow (invisible).
        """
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
