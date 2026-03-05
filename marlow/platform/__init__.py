"""Platform auto-detection and singleton access.

Usage::

    from marlow.platform import platform

    windows = platform.windows.list_windows()
    platform.input.type_text("Hello")
    png = platform.screen.screenshot()
    platform.focus.save_user_focus()

/ Auto-deteccion de plataforma y acceso singleton.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import (
        AccessibilityProvider,
        AudioProvider,
        FocusGuard,
        InputProvider,
        ScreenCapture,
        SystemProvider,
        UITreeProvider,
        WindowManager,
    )


@dataclass
class Platform:
    """Container holding all platform-specific implementations."""

    windows: WindowManager
    input: InputProvider
    screen: ScreenCapture
    focus: FocusGuard
    system: SystemProvider
    ui_tree: UITreeProvider
    accessibility: AccessibilityProvider
    audio: AudioProvider
    name: str  # "windows" or "linux"


def _create_platform() -> Platform:
    """Detect the current platform and instantiate the correct backend."""

    if sys.platform == "win32":
        # Windows backend — not implemented yet as a platform module.
        # The existing marlow.tools.* / marlow.core.* code handles Windows.
        raise NotImplementedError(
            "Windows platform layer not yet refactored. "
            "Use the existing marlow.tools.* modules directly."
        )

    elif sys.platform == "linux":
        from .linux import get_platform
        return get_platform()

    else:
        raise RuntimeError(
            f"Unsupported platform: {sys.platform}. "
            f"Marlow supports 'win32' and 'linux'."
        )


# Lazy singleton — created on first access.
_platform: Platform | None = None


def get_platform() -> Platform:
    """Return the platform singleton, creating it on first call."""
    global _platform
    if _platform is None:
        _platform = _create_platform()
    return _platform


# Convenience: `from marlow.platform import platform`
# This works as a module-level attribute via __getattr__.
def __getattr__(name: str):
    if name == "platform":
        return get_platform()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
