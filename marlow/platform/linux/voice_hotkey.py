"""Linux voice hotkey — stub implementation.

The Windows version uses the `keyboard` module for global hotkeys,
which requires root on Linux. This stub reports status and provides
a path forward (evdev with input group membership).

/ Hotkey de voz Linux — implementacion stub.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("marlow.platform.linux.voice_hotkey")

_hotkey_active = False


async def get_voice_hotkey_status() -> dict:
    """Report voice hotkey status on Linux."""
    return {
        "success": True,
        "hotkey_active": _hotkey_active,
        "hotkey": "ctrl+shift+m",
        "currently_recording": False,
        "last_transcribed_text": None,
        "platform_note": (
            "Voice hotkey requires evdev + input group on Linux. "
            "Use listen_for_command tool directly instead."
        ),
    }


async def toggle_voice_overlay() -> dict:
    """Voice overlay not yet implemented on Linux."""
    return {
        "success": False,
        "error": "Voice overlay not available on Linux (Sway). "
                 "Use speak/listen_for_command tools directly.",
    }
