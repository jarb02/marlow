"""Marlow Compositor platform backend.

Uses the Marlow Compositor's IPC socket for window management,
input, screenshots, and focus — instead of Sway/i3ipc/wtype/grim.
Reuses AT-SPI2, PipeWire, Tesseract, and system providers from
the Linux backend (they work on any Wayland compositor).

/ Backend para el compositor Marlow — IPC directo.
"""

from __future__ import annotations

import logging

from marlow.platform import Platform

logger = logging.getLogger("marlow.platform.compositor")


def get_platform(socket_path: str = None) -> Platform:
    """Create a Platform instance using the Marlow Compositor backend.

    Args:
        socket_path: Override for the compositor socket path.
                     Defaults to $XDG_RUNTIME_DIR/marlow-compositor.sock.
    """
    # Compositor-native providers (IPC socket)
    from .focus import CompositorFocusGuard
    from .input import CompositorInputProvider
    from .screenshot import CompositorScreenCapture
    from .windows import CompositorWindowManager

    # Reused from Linux backend (D-Bus / pipewire / shell — compositor-agnostic)
    from marlow.platform.linux.accessibility import AtSpiAccessibilityProvider
    from marlow.platform.linux.audio import PipeWireAudioProvider
    from marlow.platform.linux.system import LinuxSystemProvider
    from marlow.platform.linux.ui_tree import AtSpiUITreeProvider

    wm = CompositorWindowManager(socket_path=socket_path)
    focus = CompositorFocusGuard(socket_path=socket_path, window_manager=wm)

    # Optional providers — reuse Linux implementations (compositor-agnostic)
    ocr = None
    escalation = None
    cascade_recovery = None
    som = None
    waits = None
    clipboard = None
    visual_diff = None
    background = None

    try:
        from marlow.platform.linux.ocr import TesseractOCRProvider
        screen_cap = CompositorScreenCapture(socket_path=socket_path)
        ocr = TesseractOCRProvider(screen_provider=screen_cap)
    except Exception as e:
        logger.debug("OCR provider not available: %s", e)

    try:
        from marlow.platform.linux.escalation import LinuxEscalationProvider
        escalation = LinuxEscalationProvider()
    except Exception as e:
        logger.debug("Escalation provider not available: %s", e)

    try:
        from marlow.platform.linux.cascade_recovery import LinuxCascadeRecoveryProvider
        cascade_recovery = LinuxCascadeRecoveryProvider()
    except Exception as e:
        logger.debug("Cascade recovery provider not available: %s", e)

    try:
        from marlow.platform.linux.som import LinuxSoMProvider
        _som_screen = screen_cap if "screen_cap" in dir() else CompositorScreenCapture(socket_path=socket_path)
        som = LinuxSoMProvider(
            ui_tree=AtSpiUITreeProvider(),
            screen=_som_screen,
        )
    except Exception as e:
        logger.debug("SoM provider not available: %s", e)

    try:
        from marlow.platform.linux.waits import LinuxWaitProvider
        waits = LinuxWaitProvider()
    except Exception as e:
        logger.debug("Wait provider not available: %s", e)

    try:
        from marlow.platform.linux.clipboard import LinuxClipboardProvider
        clipboard = LinuxClipboardProvider()
    except Exception as e:
        logger.debug("Clipboard provider not available: %s", e)

    try:
        from marlow.platform.linux.visual_diff import LinuxVisualDiffProvider
        visual_diff = LinuxVisualDiffProvider()
    except Exception as e:
        logger.debug("Visual diff provider not available: %s", e)

    try:
        from marlow.platform.linux.background import LinuxBackgroundProvider
        background = LinuxBackgroundProvider()
    except Exception as e:
        logger.debug("Background provider not available: %s", e)

    return Platform(
        windows=wm,
        input=CompositorInputProvider(socket_path=socket_path),
        screen=CompositorScreenCapture(socket_path=socket_path),
        focus=focus,
        system=LinuxSystemProvider(),
        ui_tree=AtSpiUITreeProvider(),
        accessibility=AtSpiAccessibilityProvider(),
        audio=PipeWireAudioProvider(),
        name="compositor",
        ocr=ocr,
        escalation=escalation,
        cascade_recovery=cascade_recovery,
        som=som,
        waits=waits,
        clipboard=clipboard,
        visual_diff=visual_diff,
        background=background,
    )
