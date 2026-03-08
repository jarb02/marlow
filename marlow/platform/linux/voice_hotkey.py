"""Linux push-to-talk via evdev — Super+V keybind.

Uses evdev to listen for Super (Meta_L) + V key combo.
Requires user to be in the `input` group: sudo usermod -aG input $USER

Falls back to a subprocess-based approach using `wev` if evdev unavailable.

/ Push-to-talk Linux via evdev — Super+V.
"""

from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger("marlow.platform.linux.voice_hotkey")


class PushToTalkListener:
    """Listen for Super+V push-to-talk keybind via evdev.

    Usage:
        listener = PushToTalkListener()
        pressed = listener.wait_for_press()  # blocks until Super+V pressed
        while listener.is_held():
            # record audio
        listener.close()
    """

    # evdev key codes
    KEY_LEFTMETA = 125
    KEY_V = 47

    def __init__(self):
        self._held = False
        self._pressed_event = threading.Event()
        self._stop = False
        self._device = None
        self._thread = None
        self._keys_down: set[int] = set()

        self._start_listener()

    def _start_listener(self):
        """Find keyboard device and start listener thread."""
        try:
            import evdev
        except ImportError:
            logger.error(
                "evdev not installed. Run: pip install evdev\n"
                "Also ensure user is in input group: sudo usermod -aG input $USER"
            )
            return

        # Find a keyboard device
        devices = [evdev.InputDevice(path) for path in evdev.list_devices()]
        keyboard = None
        for dev in devices:
            caps = dev.capabilities(verbose=False)
            # EV_KEY = 1, check if it has typical keyboard keys
            if 1 in caps:
                key_codes = caps[1]
                # A real keyboard should have alphabetic keys (KEY_A=30..KEY_Z=51)
                if self.KEY_V in key_codes and self.KEY_LEFTMETA in key_codes:
                    keyboard = dev
                    break

        if keyboard is None:
            logger.error("No keyboard device found via evdev")
            for dev in devices:
                dev.close()
            return

        # Close non-selected devices
        for dev in devices:
            if dev != keyboard:
                dev.close()

        self._device = keyboard
        logger.info("Listening on device: %s (%s)", keyboard.name, keyboard.path)

        self._thread = threading.Thread(
            target=self._listen_loop, daemon=True, name="ptt-evdev",
        )
        self._thread.start()

    def _listen_loop(self):
        """Read evdev events and track Super+V state."""
        import evdev

        try:
            for event in self._device.read_loop():
                if self._stop:
                    break
                if event.type != evdev.ecodes.EV_KEY:
                    continue

                key_event = evdev.categorize(event)

                if key_event.keystate == evdev.KeyEvent.key_down:
                    self._keys_down.add(event.code)

                    # Check for Super+V combo
                    if (self.KEY_LEFTMETA in self._keys_down
                            and event.code == self.KEY_V):
                        self._held = True
                        self._pressed_event.set()
                        logger.debug("Push-to-talk: PRESSED")

                elif key_event.keystate == evdev.KeyEvent.key_up:
                    self._keys_down.discard(event.code)

                    # Release if either Super or V is released
                    if event.code in (self.KEY_LEFTMETA, self.KEY_V):
                        if self._held:
                            self._held = False
                            logger.debug("Push-to-talk: RELEASED")

        except OSError:
            if not self._stop:
                logger.error("Lost connection to keyboard device")
        except Exception as e:
            if not self._stop:
                logger.error("evdev listener error: %s", e)

    def wait_for_press(self, timeout: float = None) -> bool:
        """Block until Super+V is pressed. Returns True if pressed."""
        self._pressed_event.clear()
        if self._device is None:
            # No evdev — simulate with a long wait (voice daemon fallback)
            logger.warning("No evdev device — push-to-talk unavailable")
            time.sleep(timeout or 60)
            return False
        result = self._pressed_event.wait(timeout=timeout)
        return result

    def is_held(self) -> bool:
        """Check if Super+V is still held down."""
        return self._held

    def close(self):
        """Stop listener and close device."""
        self._stop = True
        self._pressed_event.set()  # unblock wait_for_press
        if self._device:
            try:
                self._device.close()
            except Exception:
                pass
            self._device = None
        logger.info("Push-to-talk listener closed")


# -- Stub exports for compatibility --

_hotkey_active = False


async def get_voice_hotkey_status() -> dict:
    """Report voice hotkey status on Linux."""
    return {
        "success": True,
        "hotkey_active": _hotkey_active,
        "hotkey": "super+v (push-to-talk)",
        "currently_recording": False,
        "last_transcribed_text": None,
        "platform_note": (
            "Push-to-talk via Super+V (evdev). "
            "Requires input group membership. "
            "Use voice_daemon.py --push-to-talk for full experience."
        ),
    }


async def toggle_voice_overlay() -> dict:
    """Voice overlay not yet implemented on Linux."""
    return {
        "success": False,
        "error": "Voice overlay not available on Linux (Sway). "
                 "Use speak/listen_for_command tools directly.",
    }
