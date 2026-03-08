"""Linux push-to-talk — compositor-driven via trigger file.

The compositor (KMS backend) owns all input devices exclusively.
Push-to-talk is handled by the compositor intercepting Super+V
and writing to /tmp/marlow-voice-trigger.

/ Push-to-talk Linux — compositor escribe trigger file en Super+V.
"""

from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger("marlow.platform.linux.voice_hotkey")

TRIGGER_FILE = "/tmp/marlow-voice-trigger"


class PushToTalkListener:
    """Listen for Super+V push-to-talk via compositor trigger file.

    The compositor writes "press" or "release" to /tmp/marlow-voice-trigger
    when the user presses/releases Super+V.

    Usage:
        listener = PushToTalkListener()
        pressed = listener.wait_for_press()  # blocks until Super+V pressed
        while listener.is_held():
            # record audio
        listener.close()
    """

    def __init__(self):
        self._stop = False
        # Clean up stale trigger file on init
        self._clear_trigger()

    def _read_trigger(self) -> str:
        """Read the trigger file contents."""
        try:
            if os.path.exists(TRIGGER_FILE):
                with open(TRIGGER_FILE, "r") as f:
                    return f.read().strip()
        except Exception:
            pass
        return ""

    def _clear_trigger(self):
        """Remove the trigger file."""
        try:
            if os.path.exists(TRIGGER_FILE):
                os.unlink(TRIGGER_FILE)
        except Exception:
            pass

    def wait_for_press(self, timeout: float = None) -> bool:
        """Block until Super+V is pressed (compositor writes 'press').

        Polls the trigger file every 50ms.
        """
        self._clear_trigger()
        start = time.monotonic()
        poll_interval = 0.05  # 50ms

        while not self._stop:
            state = self._read_trigger()
            if state == "press":
                logger.debug("Push-to-talk: PRESSED (compositor trigger)")
                return True

            if timeout and (time.monotonic() - start) > timeout:
                return False

            time.sleep(poll_interval)

        return False

    def is_held(self) -> bool:
        """Check if Super+V is still held (trigger file says 'press').

        Returns False once compositor writes 'release'.
        """
        if self._stop:
            return False
        state = self._read_trigger()
        if state == "release":
            self._clear_trigger()
            return False
        # "press" or empty (compositor hasn't updated yet) = still held
        return state == "press"

    def close(self):
        """Stop listener and clean up."""
        self._stop = True
        self._clear_trigger()
        logger.info("Push-to-talk listener closed")


# -- Stub exports for compatibility --

_hotkey_active = False


async def get_voice_hotkey_status() -> dict:
    """Report voice hotkey status on Linux."""
    trigger_exists = os.path.exists(TRIGGER_FILE)
    return {
        "success": True,
        "hotkey_active": _hotkey_active,
        "hotkey": "super+v (push-to-talk, compositor-driven)",
        "currently_recording": trigger_exists and open(TRIGGER_FILE).read().strip() == "press"
            if trigger_exists else False,
        "last_transcribed_text": None,
        "mechanism": "compositor trigger file (/tmp/marlow-voice-trigger)",
        "platform_note": (
            "Push-to-talk via Super+V. The compositor intercepts the keybind "
            "and signals via /tmp/marlow-voice-trigger. "
            "Use voice_daemon.py --push-to-talk for full experience."
        ),
    }


async def toggle_voice_overlay() -> dict:
    """Voice overlay not yet implemented on Linux."""
    return {
        "success": False,
        "error": "Voice overlay not available on Linux compositor. "
                 "Use speak/listen_for_command tools directly.",
    }
