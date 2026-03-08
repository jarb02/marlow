"""Marlow Compositor IPC Client — async Python client.

Connects to the compositor via Unix socket at
$XDG_RUNTIME_DIR/marlow-compositor.sock using MessagePack framing.

/ Cliente IPC async para el compositor Marlow.
"""

from __future__ import annotations

import asyncio
import os
import struct
from typing import Callable, Optional

import msgpack


def _default_socket_path() -> str:
    runtime_dir = os.environ.get(
        "XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"
    )
    return os.path.join(runtime_dir, "marlow-compositor.sock")


class MarlowCompositorClient:
    """Async IPC client for the Marlow Compositor."""

    def __init__(self):
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None

    async def connect(self, socket_path: str = None):
        """Connect to the compositor IPC socket."""
        path = socket_path or _default_socket_path()
        self._reader, self._writer = await asyncio.open_unix_connection(path)

    async def disconnect(self):
        """Close the connection."""
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None

    async def send_request(self, request: dict) -> dict:
        """Send a request and wait for response."""
        if not self._writer:
            raise RuntimeError("Not connected")

        payload = msgpack.packb(request, use_bin_type=True)
        self._writer.write(struct.pack("<I", len(payload)))
        self._writer.write(payload)
        await self._writer.drain()

        len_buf = await self._reader.readexactly(4)
        msg_len = struct.unpack("<I", len_buf)[0]
        payload = await self._reader.readexactly(msg_len)
        return msgpack.unpackb(payload, raw=False)

    # ─── Core commands ───

    async def ping(self) -> bool:
        """Ping the compositor."""
        resp = await self.send_request({"type": "Ping"})
        return resp.get("status") == "ok" and resp.get("data") == "pong"

    async def list_windows(self) -> list:
        """List all windows in user_space (visible)."""
        resp = await self.send_request({"type": "ListWindows"})
        if resp.get("status") == "ok":
            return resp.get("data", [])
        return []

    async def focus_window(self, window_id: int) -> bool:
        """Focus a window by ID."""
        resp = await self.send_request({
            "type": "FocusWindow",
            "window_id": window_id,
        })
        return resp.get("status") == "ok"

    async def get_window_info(self, window_id: int) -> dict | None:
        """Get detailed info for a specific window."""
        resp = await self.send_request({
            "type": "GetWindowInfo",
            "window_id": window_id,
        })
        if resp.get("status") == "ok":
            return resp.get("data")
        return None

    # ─── Input commands ───

    async def send_key(self, window_id: int, key: int, pressed: bool = True) -> bool:
        """Send a single key event (evdev keycode)."""
        resp = await self.send_request({
            "type": "SendKey",
            "window_id": window_id,
            "key": key,
            "pressed": pressed,
        })
        return resp.get("status") == "ok"

    async def send_text(self, window_id: int, text: str) -> dict:
        """Type text by synthesizing key events (US QWERTY)."""
        resp = await self.send_request({
            "type": "SendText",
            "window_id": window_id,
            "text": text,
        })
        if resp.get("status") == "ok":
            return resp.get("data", {})
        return {"error": resp.get("message", "Unknown error")}

    async def send_click(
        self, window_id: int, x: float, y: float, button: int = 1
    ) -> bool:
        """Click at (x, y) relative to a window.
        button: 1=left, 2=right, 3=middle
        """
        resp = await self.send_request({
            "type": "SendClick",
            "window_id": window_id,
            "x": x,
            "y": y,
            "button": button,
        })
        return resp.get("status") == "ok"

    async def send_hotkey(
        self, window_id: int, modifiers: list[str], key: str
    ) -> bool:
        """Send a hotkey combination (e.g. ["ctrl"], "c")."""
        resp = await self.send_request({
            "type": "SendHotkey",
            "window_id": window_id,
            "modifiers": modifiers,
            "key": key,
        })
        return resp.get("status") == "ok"

    # ─── Window management ───

    async def close_window(self, window_id: int) -> bool:
        """Send close request to a window."""
        resp = await self.send_request({
            "type": "CloseWindow",
            "window_id": window_id,
        })
        return resp.get("status") == "ok"

    async def minimize_window(self, window_id: int) -> bool:
        """Minimize (unmap) a window."""
        resp = await self.send_request({
            "type": "MinimizeWindow",
            "window_id": window_id,
        })
        return resp.get("status") == "ok"

    async def maximize_window(self, window_id: int) -> bool:
        """Maximize a window to fill the output."""
        resp = await self.send_request({
            "type": "MaximizeWindow",
            "window_id": window_id,
        })
        return resp.get("status") == "ok"

    # ─── Screenshot ───

    async def request_screenshot(
        self, window_id: int = None, timeout: float = 2.0
    ) -> str | None:
        """Request a screenshot. Returns base64 PNG or None.

        Polls until the screenshot is captured (max timeout seconds).
        """
        req = {"type": "RequestScreenshot"}
        if window_id is not None:
            req["window_id"] = window_id

        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            resp = await self.send_request(req)
            if resp.get("status") != "ok":
                return None
            data = resp.get("data", {})
            if isinstance(data, dict) and data.get("pending"):
                await asyncio.sleep(0.05)
                continue
            if isinstance(data, dict) and "image" in data:
                return data["image"]
            return None
        return None

    # ─── Seat status ───

    async def get_seat_status(self) -> dict:
        """Get dual-seat status: user_focus, agent_focus, conflict."""
        resp = await self.send_request({"type": "GetSeatStatus"})
        if resp.get("status") == "ok":
            return resp.get("data", {})
        return {}

    # ─── Shadow Mode ───

    async def get_shadow_windows(self) -> list:
        """List all windows in shadow_space (invisible)."""
        resp = await self.send_request({"type": "GetShadowWindows"})
        if resp.get("status") == "ok":
            return resp.get("data", [])
        return []

    async def launch_in_shadow(self, command: str) -> dict:
        """Launch a command and route its window to shadow_space."""
        resp = await self.send_request({
            "type": "LaunchInShadow",
            "command": command,
        })
        if resp.get("status") == "ok":
            return resp.get("data", {})
        return {"error": resp.get("message", "Unknown error")}

    async def move_to_shadow(self, window_id: int) -> bool:
        """Move a window from user_space to shadow_space."""
        resp = await self.send_request({
            "type": "MoveToShadow",
            "window_id": window_id,
        })
        return resp.get("status") == "ok"

    async def move_to_user(self, window_id: int) -> bool:
        """Move a window from shadow_space to user_space (promote)."""
        resp = await self.send_request({
            "type": "MoveToUser",
            "window_id": window_id,
        })
        return resp.get("status") == "ok"

    # ─── Event subscription ───

    async def subscribe(self, events: list[str] = None) -> bool:
        """Subscribe to compositor events."""
        resp = await self.send_request({
            "type": "Subscribe",
            "events": events or ["all"],
        })
        return resp.get("status") == "ok"

    async def read_event(self, timeout: float = 5.0) -> dict | None:
        """Read a single pushed event from the compositor."""
        try:
            len_buf = await asyncio.wait_for(
                self._reader.readexactly(4), timeout=timeout
            )
            msg_len = struct.unpack("<I", len_buf)[0]
            payload = await self._reader.readexactly(msg_len)
            return msgpack.unpackb(payload, raw=False)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError):
            return None
