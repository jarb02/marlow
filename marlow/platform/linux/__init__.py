"""Linux platform backend — Sway/Wayland + AT-SPI2.

Provides concrete implementations of the platform ABCs for Linux
desktop environments running Sway (wlroots-based Wayland compositor).

/ Backend Linux — Sway/Wayland + AT-SPI2.
"""

from __future__ import annotations

import logging

from marlow.platform import Platform

logger = logging.getLogger("marlow.platform.linux")


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
        input=WaylandInputProvider(),
        screen=GrimScreenCapture(),
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
