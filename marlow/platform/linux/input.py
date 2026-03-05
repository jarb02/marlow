"""Linux InputProvider — wtype (keyboard) + ydotool (mouse).

Keyboard input via wtype (Wayland virtual-keyboard-v1 protocol).
Mouse input via ydotool (requires ydotoold daemon) with wlrctl fallback.

Tested on Fedora 43 + Sway.

/ InputProvider Linux — wtype (teclado) + ydotool (mouse).
"""

from __future__ import annotations

import logging
import shutil
import subprocess

from marlow.platform.base import InputProvider

logger = logging.getLogger("marlow.platform.linux.input")

# Map common key names to wtype key symbols.
# wtype uses XKB key names (xdotool-style).
_KEY_MAP: dict[str, str] = {
    "return": "Return",
    "enter": "Return",
    "tab": "Tab",
    "escape": "Escape",
    "esc": "Escape",
    "backspace": "BackSpace",
    "delete": "Delete",
    "space": "space",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "home": "Home",
    "end": "End",
    "pageup": "Prior",
    "pagedown": "Next",
    "f1": "F1", "f2": "F2", "f3": "F3", "f4": "F4",
    "f5": "F5", "f6": "F6", "f7": "F7", "f8": "F8",
    "f9": "F9", "f10": "F10", "f11": "F11", "f12": "F12",
}

# Modifier key names to wtype modifier flags
_MODIFIER_MAP: dict[str, str] = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "alt",
    "shift": "shift",
    "super": "logo",
    "win": "logo",
    "mod4": "logo",
}


def _resolve_key(key: str) -> str:
    """Resolve a key name to its wtype XKB symbol."""
    return _KEY_MAP.get(key.lower(), key)


def _run(cmd: list[str], timeout: int = 5) -> bool:
    """Run a subprocess command, return True on success."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            stderr = r.stderr.strip()
            if stderr:
                logger.warning("Command %s failed: %s", cmd[0], stderr)
            return False
        return True
    except FileNotFoundError:
        logger.error("%s not installed", cmd[0])
        return False
    except subprocess.TimeoutExpired:
        logger.error("%s timed out after %ds", cmd[0], timeout)
        return False
    except Exception as e:
        logger.error("%s error: %s", cmd[0], e)
        return False


class WaylandInputProvider(InputProvider):
    """Input via wtype (keyboard) and ydotool (mouse) on Wayland/Sway."""

    def __init__(self):
        self._has_wtype = shutil.which("wtype") is not None
        self._has_ydotool = shutil.which("ydotool") is not None
        if not self._has_wtype:
            logger.warning("wtype not found — keyboard input will fail")
        if not self._has_ydotool:
            logger.info("ydotool not found — mouse input will use wlrctl fallback")

    # ── InputProvider interface ──

    def type_text(self, text: str) -> bool:
        """Type text into the focused window using wtype."""
        if not text:
            return True
        return _run(["wtype", "--", text])

    def press_key(self, key: str) -> bool:
        """Press a single key using wtype -k."""
        xkb = _resolve_key(key)
        # wtype -k <keysym>: press and release a key
        return _run(["wtype", "-k", xkb])

    def hotkey(self, *keys: str) -> bool:
        """Press a modifier+key combination using wtype -M/-m for modifiers.

        Example: hotkey('ctrl', 'shift', 't')
        Builds: wtype -M ctrl -M shift -k t -m shift -m ctrl
        """
        if not keys:
            return False

        modifiers: list[str] = []
        normal_keys: list[str] = []

        for k in keys:
            k_lower = k.lower()
            if k_lower in _MODIFIER_MAP:
                modifiers.append(_MODIFIER_MAP[k_lower])
            else:
                normal_keys.append(_resolve_key(k))

        if not normal_keys:
            # All keys are modifiers — press last one as key
            if modifiers:
                normal_keys.append(modifiers.pop())

        # Build wtype command:
        # -M <mod>  : hold modifier
        # -k <key>  : press and release key
        # -m <mod>  : release modifier
        cmd: list[str] = ["wtype"]

        for mod in modifiers:
            cmd.extend(["-M", mod])

        for nk in normal_keys:
            cmd.extend(["-k", nk])

        for mod in reversed(modifiers):
            cmd.extend(["-m", mod])

        return _run(cmd)

    def click(self, x: int, y: int, button: str = "left") -> bool:
        """Click at coordinates using ydotool."""
        button_code = {"left": "0x00", "right": "0x01", "middle": "0x02"}.get(
            button.lower(), "0x00"
        )

        if self._has_ydotool:
            # ydotool uses absolute positioning:
            # mousemove --absolute -x X -y Y
            # click <button_code>
            ok = _run([
                "ydotool", "mousemove", "--absolute", "-x", str(x), "-y", str(y),
            ])
            if not ok:
                return False
            return _run(["ydotool", "click", button_code])

        # Fallback: wlrctl (if available)
        if shutil.which("wlrctl"):
            return _run([
                "wlrctl", "pointer", "click",
                "--x", str(x), "--y", str(y),
            ])

        logger.error("No mouse input tool available (need ydotool or wlrctl)")
        return False

    def move_mouse(self, x: int, y: int) -> bool:
        """Move mouse to absolute coordinates using ydotool."""
        if self._has_ydotool:
            return _run([
                "ydotool", "mousemove", "--absolute", "-x", str(x), "-y", str(y),
            ])

        logger.error("ydotool not installed — cannot move mouse")
        return False


if __name__ == "__main__":
    provider = WaylandInputProvider()

    print("=== WaylandInputProvider self-test ===")
    print(f"  wtype available: {provider._has_wtype}")
    print(f"  ydotool available: {provider._has_ydotool}")

    print("\n--- press_key('Return') ---")
    ok = provider.press_key("Return")
    print(f"  Result: {ok}")

    print("\n--- hotkey('ctrl', 'l') ---")
    ok = provider.hotkey("ctrl", "l")
    print(f"  Result: {ok}")

    print("\n--- type_text('Marlow Linux test') ---")
    ok = provider.type_text("Marlow Linux test")
    print(f"  Result: {ok}")

    print("\n--- press_key('Escape') ---")
    ok = provider.press_key("Escape")
    print(f"  Result: {ok}")

    print("\nPASS: WaylandInputProvider self-test complete")
