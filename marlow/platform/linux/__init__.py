"""Linux platform backend — Sway/Wayland + AT-SPI2.

Provides concrete implementations of the platform ABCs for Linux
desktop environments running Sway (wlroots-based Wayland compositor).

/ Backend Linux — Sway/Wayland + AT-SPI2.
"""

from __future__ import annotations

from marlow.platform import Platform


def get_platform() -> Platform:
    """Create and return a Platform instance with Linux backends."""

    from .accessibility import AtSpiAccessibilityProvider
    from .audio import PipeWireAudioProvider
    from .focus import SwayFocusGuard
    from .input import WaylandInputProvider
    from .screenshot import GrimScreenCapture
    from .system import LinuxSystemProvider
    from .ui_tree import AtSpiUITreeProvider
    from .windows import SwayWindowManager

    wm = SwayWindowManager()
    focus = SwayFocusGuard(window_manager=wm)

    return Platform(
        windows=wm,
        input=WaylandInputProvider(),
        screen=GrimScreenCapture(),
        focus=focus,
        system=LinuxSystemProvider(),
        ui_tree=AtSpiUITreeProvider(),
        accessibility=AtSpiAccessibilityProvider(),
        audio=PipeWireAudioProvider(),
        name="linux",
    )
