"""Linux platform backend — Marlow Compositor / Sway + AT-SPI2.

Auto-detects whether the Marlow Compositor is running (socket exists).
If yes: uses CompositorInputProvider + CompositorScreenCapture (IPC).
If no: falls back to WaylandInputProvider (wtype/ydotool) + GrimScreenCapture.

/ Backend Linux — auto-detect compositor vs Sway fallback.
"""

from __future__ import annotations

import logging
import os

from marlow.platform import Platform

logger = logging.getLogger("marlow.platform.linux")


def _compositor_socket_exists() -> bool:
    """Check if the Marlow Compositor IPC socket is available."""
    runtime_dir = os.environ.get(
        "XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"
    )
    path = os.path.join(runtime_dir, "marlow-compositor.sock")
    return os.path.exists(path)


def get_platform() -> Platform:
    """Create and return a Platform instance with Linux backends."""

    from .accessibility import AtSpiAccessibilityProvider
    from .audio import PipeWireAudioProvider
    from .system import LinuxSystemProvider
    from .ui_tree import AtSpiUITreeProvider
    # Compositor IPC with lazy connect + Sway fallback
    from .compositor_windows import CompositorWindowManager
    wm = CompositorWindowManager()

    # Auto-detect: compositor IPC vs Sway/Wayland fallback
    use_compositor = _compositor_socket_exists()

    if use_compositor:
        logger.info("Marlow Compositor detected — using IPC providers")
        from marlow.platform.compositor.input import CompositorInputProvider
        from marlow.platform.compositor.screenshot import CompositorScreenCapture
        input_provider = CompositorInputProvider()
        screen_provider = CompositorScreenCapture()
        try:
            from marlow.platform.compositor.focus import CompositorFocusGuard
            focus = CompositorFocusGuard(window_manager=wm)
        except Exception:
            from .focus import SwayFocusGuard
            focus = SwayFocusGuard(window_manager=wm)
    else:
        logger.info("No compositor socket — using Sway/Wayland providers")
        from .input import WaylandInputProvider
        from .screenshot import GrimScreenCapture
        from .focus import SwayFocusGuard
        input_provider = WaylandInputProvider()
        screen_provider = GrimScreenCapture()
        focus = SwayFocusGuard(window_manager=wm)

    # Optional providers
    ocr = None
    escalation = None
    cascade_recovery = None
    som = None
    waits = None
    clipboard = None
    visual_diff = None
    background = None

    try:
        from .ocr import TesseractOCRProvider
        ocr = TesseractOCRProvider()
    except Exception as e:
        logger.debug("OCR provider not available: %s", e)

    try:
        from .escalation import LinuxEscalationProvider
        escalation = LinuxEscalationProvider()
    except Exception as e:
        logger.debug("Escalation provider not available: %s", e)

    try:
        from .cascade_recovery import LinuxCascadeRecoveryProvider
        cascade_recovery = LinuxCascadeRecoveryProvider()
    except Exception as e:
        logger.debug("Cascade recovery not available: %s", e)

    try:
        from .som import LinuxSoMProvider
        som = LinuxSoMProvider()
    except Exception as e:
        logger.debug("SoM provider not available: %s", e)

    try:
        from .waits import LinuxWaitProvider
        waits = LinuxWaitProvider()
    except Exception as e:
        logger.debug("Wait provider not available: %s", e)

    try:
        from .clipboard import LinuxClipboardProvider
        clipboard = LinuxClipboardProvider()
    except Exception as e:
        logger.debug("Clipboard provider not available: %s", e)

    try:
        from .visual_diff import LinuxVisualDiffProvider
        visual_diff = LinuxVisualDiffProvider()
    except Exception as e:
        logger.debug("Visual diff not available: %s", e)

    try:
        from .background import LinuxBackgroundProvider
        background = LinuxBackgroundProvider()
    except Exception as e:
        logger.debug("Background provider not available: %s", e)

    return Platform(
        windows=wm,
        input=input_provider,
        screen=screen_provider,
        focus=focus,
        system=LinuxSystemProvider(),
        ui_tree=AtSpiUITreeProvider(),
        accessibility=AtSpiAccessibilityProvider(),
        audio=PipeWireAudioProvider(),
        name="linux",
        ocr=ocr,
        escalation=escalation,
        cascade_recovery=cascade_recovery,
        som=som,
        waits=waits,
        clipboard=clipboard,
        visual_diff=visual_diff,
        background=background,
    )
