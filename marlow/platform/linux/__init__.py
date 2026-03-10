"""Linux platform backend — Marlow Compositor / Sway + AT-SPI2.

Two mutually exclusive modes — NO mixing:
- Compositor mode: ALL providers use compositor IPC. Zero Sway/wtype/grim.
- Sway mode: ALL providers use Sway IPC + wtype + ydotool + grim.

/ Backend Linux — compositor puro O sway puro, nunca mixto.
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
    """Create and return a Platform instance with Linux backends.

    When the compositor socket exists, ALL desktop I/O goes through
    compositor IPC. Sway/wtype/grim are never loaded — not even as
    fallback. If the compositor IPC fails, the operation fails cleanly.
    """

    from .accessibility import AtSpiAccessibilityProvider
    from .audio import PipeWireAudioProvider
    from .system import LinuxSystemProvider
    from .ui_tree import AtSpiUITreeProvider

    use_compositor = _compositor_socket_exists()

    if use_compositor:
        # ── Compositor mode: 100% IPC, zero Sway ──
        logger.info("Platform: compositor (all providers via IPC)")
        from .compositor_windows import CompositorWindowManager
        from marlow.platform.compositor.input import CompositorInputProvider
        from marlow.platform.compositor.screenshot import CompositorScreenCapture

        wm = CompositorWindowManager()
        input_provider = CompositorInputProvider()
        screen_provider = CompositorScreenCapture()

        try:
            from marlow.platform.compositor.focus import CompositorFocusGuard
            focus = CompositorFocusGuard(window_manager=wm)
        except Exception as e:
            logger.warning("CompositorFocusGuard failed (%s), using stub", e)
            from marlow.platform.base import FocusGuard

            class _StubFocusGuard(FocusGuard):
                def save_user_focus(self): pass
                def restore_user_focus(self) -> bool: return True

            focus = _StubFocusGuard()
    else:
        # ── Sway mode: wtype + ydotool + grim + i3ipc ──
        logger.info("Platform: sway (wtype/grim/i3ipc)")
        from .compositor_windows import CompositorWindowManager
        from .input import WaylandInputProvider
        from .screenshot import GrimScreenCapture
        from .focus import SwayFocusGuard

        wm = CompositorWindowManager()
        input_provider = WaylandInputProvider()
        screen_provider = GrimScreenCapture()
        focus = SwayFocusGuard(window_manager=wm)

    # Optional providers (platform-agnostic, safe to load always)
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
        ocr = TesseractOCRProvider(screen_provider=screen_provider)
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
