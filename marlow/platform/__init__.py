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

import os
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

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
    name: str  # "windows", "linux", or "compositor"
    # Optional providers — may be None on some platforms
    ocr: Any = None
    escalation: Any = None
    cascade_recovery: Any = None
    som: Any = None
    waits: Any = None
    clipboard: Any = None
    visual_diff: Any = None
    background: Any = None


def _compositor_socket_path() -> str | None:
    """Return the compositor socket path if it exists, else None."""
    runtime_dir = os.environ.get(
        "XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}" if hasattr(os, "getuid") else ""
    )
    if not runtime_dir:
        return None
    path = os.path.join(runtime_dir, "marlow-compositor.sock")
    if os.path.exists(path):
        return path
    return None


def _create_platform() -> Platform:
    """Detect the current platform and instantiate the correct backend."""

    if sys.platform == "win32":
        raise NotImplementedError(
            "Windows platform layer not yet refactored. "
            "Use the existing marlow.tools.* modules directly."
        )

    elif sys.platform == "linux":
        # Check if Marlow Compositor is running (socket exists)
        compositor_sock = _compositor_socket_path()
        if compositor_sock:
            from .compositor import get_platform
            return get_platform(socket_path=compositor_sock)

        # Fallback: Sway / generic Wayland
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
def __getattr__(name: str):
    if name == "platform":
        return get_platform()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
