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
    from .cascade_recovery import LinuxCascadeRecoveryProvider
    from .escalation import LinuxEscalationProvider
    from .focus import SwayFocusGuard
    from .input import WaylandInputProvider
    from .ocr import TesseractOCRProvider
    from .screenshot import GrimScreenCapture
    from .som import LinuxSoMProvider
    from .system import LinuxSystemProvider
    from .ui_tree import AtSpiUITreeProvider
    from .windows import SwayWindowManager

    wm = SwayWindowManager()
    focus = SwayFocusGuard(window_manager=wm)
    screen = GrimScreenCapture()
    ui_tree = AtSpiUITreeProvider()
    inp = WaylandInputProvider()
    ocr = TesseractOCRProvider(screen_provider=screen)
    escalation = LinuxEscalationProvider(ui_tree=ui_tree, ocr=ocr, screen=screen)
    cascade = LinuxCascadeRecoveryProvider(escalation=escalation, ocr=ocr)
    som = LinuxSoMProvider(screen=screen, ui_tree=ui_tree, input_provider=inp)

    return Platform(
        windows=wm,
        input=inp,
        screen=screen,
        focus=focus,
        system=LinuxSystemProvider(),
        ui_tree=ui_tree,
        accessibility=AtSpiAccessibilityProvider(),
        audio=PipeWireAudioProvider(),
        name="linux",
        ocr=ocr,
        escalation=escalation,
        cascade_recovery=cascade,
        som=som,
    )
