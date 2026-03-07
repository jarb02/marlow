"""Compositor InputProvider — Marlow Compositor IPC.

Sends keyboard and mouse input via the compositor's IPC socket
instead of wtype/ydotool. Input is injected directly into the
compositor's virtual input device — no external tools needed.

/ InputProvider para el compositor Marlow via IPC directo.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from typing import Optional

from marlow.platform.base import InputProvider
from marlow.platform.linux.compositor_client import MarlowCompositorClient

logger = logging.getLogger("marlow.platform.compositor.input")

# XKB key name → evdev keycode (linux/input-event-codes.h)
_KEY_TO_EVDEV: dict[str, int] = {
    "escape": 1, "esc": 1,
    "1": 2, "2": 3, "3": 4, "4": 5, "5": 6,
    "6": 7, "7": 8, "8": 9, "9": 10, "0": 11,
    "minus": 12, "equal": 13,
    "backspace": 14, "tab": 15,
    "q": 16, "w": 17, "e": 18, "r": 19, "t": 20,
    "y": 21, "u": 22, "i": 23, "o": 24, "p": 25,
    "bracketleft": 26, "bracketright": 27,
    "return": 28, "enter": 28,
    "a": 30, "s": 31, "d": 32, "f": 33, "g": 34,
    "h": 35, "j": 36, "k": 37, "l": 38,
    "semicolon": 39, "apostrophe": 40, "grave": 41,
    "backslash": 43,
    "z": 44, "x": 45, "c": 46, "v": 47, "b": 48,
    "n": 49, "m": 50,
    "comma": 51, "period": 52, "slash": 53,
    "space": 57,
    "f1": 59, "f2": 60, "f3": 61, "f4": 62,
    "f5": 63, "f6": 64, "f7": 65, "f8": 66,
    "f9": 67, "f10": 68, "f11": 87, "f12": 88,
    "home": 102, "up": 103, "pageup": 104, "prior": 104,
    "left": 105, "right": 106,
    "end": 107, "down": 108, "pagedown": 109, "next": 109,
    "insert": 110, "delete": 111,
}

_MODIFIER_NAMES = {"ctrl", "control", "alt", "shift", "super", "win", "mod4", "logo"}


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


class CompositorInputProvider(InputProvider):
    """Input via Marlow Compositor IPC — no wtype/ydotool needed."""

    def __init__(self, socket_path: str = None):
        self._socket_path = socket_path

    async def _with_client(self, fn):
        client = MarlowCompositorClient()
        await client.connect(self._socket_path)
        try:
            return await fn(client)
        finally:
            await client.disconnect()

    async def _get_focused_id(self, client) -> Optional[int]:
        """Get the focused window ID from seat status."""
        status = await client.get_seat_status()
        wid = status.get("user_focus") or status.get("agent_focus")
        if wid:
            return int(wid)
        # Fallback: first window in list
        windows = await client.list_windows()
        if windows:
            return windows[0].get("id", 0)
        return None

    def type_text(self, text: str) -> bool:
        if not text:
            return True
        try:
            async def _do(client):
                wid = await self._get_focused_id(client)
                if wid is None:
                    return False
                result = await client.send_text(wid, text)
                return "error" not in result

            return _run_async(self._with_client(_do))
        except Exception as e:
            logger.error("type_text failed: %s", e)
            return False

    def press_key(self, key: str) -> bool:
        try:
            evdev = _KEY_TO_EVDEV.get(key.lower())
            if evdev is None:
                logger.warning("Unknown key: %s", key)
                return False

            async def _do(client):
                wid = await self._get_focused_id(client)
                if wid is None:
                    return False
                ok1 = await client.send_key(wid, evdev, pressed=True)
                ok2 = await client.send_key(wid, evdev, pressed=False)
                return ok1 and ok2

            return _run_async(self._with_client(_do))
        except Exception as e:
            logger.error("press_key failed: %s", e)
            return False

    def hotkey(self, *keys: str) -> bool:
        if not keys:
            return False
        try:
            modifiers = []
            normal_keys = []
            for k in keys:
                if k.lower() in _MODIFIER_NAMES:
                    # Normalize modifier names
                    mod = k.lower()
                    if mod in ("control", "ctrl"):
                        mod = "ctrl"
                    elif mod in ("super", "win", "mod4", "logo"):
                        mod = "super"
                    modifiers.append(mod)
                else:
                    normal_keys.append(k)

            if not normal_keys and modifiers:
                normal_keys.append(modifiers.pop())

            async def _do(client):
                wid = await self._get_focused_id(client)
                if wid is None:
                    return False
                return await client.send_hotkey(wid, modifiers, normal_keys[0])

            return _run_async(self._with_client(_do))
        except Exception as e:
            logger.error("hotkey failed: %s", e)
            return False

    def click(self, x: int, y: int, button: str = "left") -> bool:
        try:
            btn_code = {"left": 1, "right": 2, "middle": 3}.get(
                button.lower(), 1
            )

            async def _do(client):
                wid = await self._get_focused_id(client)
                if wid is None:
                    return False
                return await client.send_click(wid, float(x), float(y), btn_code)

            return _run_async(self._with_client(_do))
        except Exception as e:
            logger.error("click failed: %s", e)
            return False

    def move_mouse(self, x: int, y: int) -> bool:
        # Compositor IPC doesn't have a move-only command yet.
        # A click with no button release could simulate it, but
        # for now we log and return False.
        logger.warning("move_mouse not yet supported by Marlow Compositor IPC")
        return False
