"""
Marlow Background Mode Tools

Enables Marlow to work on a separate screen/area so it doesn't
interfere with the user's mouse and keyboard.

Modes:
- dual_monitor: Uses second monitor as agent workspace (preferred)
- offscreen: Moves windows beyond visible area (single monitor fallback)

/ Permite a Marlow trabajar en una pantalla separada para no
/ interferir con el mouse y teclado del usuario.
"""

import ctypes
import ctypes.wintypes
import logging
from typing import Optional

logger = logging.getLogger("marlow.tools.background")


class BackgroundManager:
    """Manages agent workspace for background automation."""

    def __init__(self):
        self.mode: Optional[str] = None  # "dual_monitor" | "offscreen" | None
        self.monitors: list[dict] = []
        self.agent_monitor: Optional[dict] = None
        self.primary_monitor: Optional[dict] = None
        self._moved_windows: dict[str, dict] = {}  # title → original position

    def _enumerate_monitors(self) -> list[dict]:
        """Detect all connected monitors using Win32 API."""
        monitors = []

        # Callback for EnumDisplayMonitors
        MONITORENUMPROC = ctypes.WINFUNCTYPE(
            ctypes.c_int,
            ctypes.c_ulong,       # hMonitor
            ctypes.c_ulong,       # hdcMonitor
            ctypes.POINTER(ctypes.wintypes.RECT),  # lprcMonitor
            ctypes.wintypes.LPARAM,  # dwData
        )

        def callback(hMonitor, hdcMonitor, lprcMonitor, dwData):
            rect = lprcMonitor.contents
            info = {
                "handle": hMonitor,
                "left": rect.left,
                "top": rect.top,
                "right": rect.right,
                "bottom": rect.bottom,
                "width": rect.right - rect.left,
                "height": rect.bottom - rect.top,
            }
            # Check if primary (contains 0,0)
            info["is_primary"] = (rect.left == 0 and rect.top == 0)
            monitors.append(info)
            return 1  # Continue enumeration

        cb = MONITORENUMPROC(callback)
        ctypes.windll.user32.EnumDisplayMonitors(None, None, cb, 0)

        self.monitors = monitors
        return monitors


# Module-level singleton
_manager = BackgroundManager()


async def setup_background_mode(
    preferred_mode: Optional[str] = None,
) -> dict:
    """
    Configure background mode based on available monitors.

    Auto-detects the best mode:
    - 2+ monitors → "dual_monitor" (uses second monitor for agent)
    - 1 monitor → "offscreen" (moves windows beyond screen edge)

    Args:
        preferred_mode: Force a specific mode: "dual_monitor" or "offscreen".
                       If None, auto-detects the best option.

    Returns:
        Dictionary with mode, monitor info, and agent workspace area.

    / Configura el modo background basado en los monitores disponibles.
    """
    monitors = _manager._enumerate_monitors()

    if not monitors:
        return {"error": "No monitors detected. This shouldn't happen on Windows."}

    # Identify primary monitor
    primary = next((m for m in monitors if m["is_primary"]), monitors[0])
    _manager.primary_monitor = primary

    # Determine mode
    if preferred_mode:
        mode = preferred_mode
    elif len(monitors) >= 2:
        mode = "dual_monitor"
    else:
        mode = "offscreen"

    _manager.mode = mode

    if mode == "dual_monitor":
        # Use the first non-primary monitor as agent workspace
        agent_mon = next((m for m in monitors if not m["is_primary"]), None)
        if not agent_mon:
            # All monitors are "primary" — use the second one
            agent_mon = monitors[1] if len(monitors) > 1 else monitors[0]
        _manager.agent_monitor = agent_mon

        return {
            "success": True,
            "mode": "dual_monitor",
            "monitors_detected": len(monitors),
            "primary_monitor": {
                "left": primary["left"], "top": primary["top"],
                "width": primary["width"], "height": primary["height"],
            },
            "agent_monitor": {
                "left": agent_mon["left"], "top": agent_mon["top"],
                "width": agent_mon["width"], "height": agent_mon["height"],
            },
            "hint": "Use move_to_agent_screen() to move windows to the agent workspace.",
        }

    else:  # offscreen
        # Place agent workspace to the right of visible area
        offscreen_x = primary["right"] + 100
        _manager.agent_monitor = {
            "left": offscreen_x,
            "top": 0,
            "right": offscreen_x + primary["width"],
            "bottom": primary["height"],
            "width": primary["width"],
            "height": primary["height"],
            "is_primary": False,
        }

        return {
            "success": True,
            "mode": "offscreen",
            "monitors_detected": 1,
            "primary_monitor": {
                "left": primary["left"], "top": primary["top"],
                "width": primary["width"], "height": primary["height"],
            },
            "agent_area": {
                "left": offscreen_x, "top": 0,
                "width": primary["width"], "height": primary["height"],
            },
            "hint": "Offscreen mode: windows moved beyond screen edge. "
                    "User won't see them, but Marlow can still interact via UIA.",
        }


async def move_to_agent_screen(window_title: str) -> dict:
    """
    Move a window to the agent workspace (second monitor or offscreen).

    Args:
        window_title: Title (or partial title) of the window to move.

    Returns:
        Dictionary with move result and new position.

    / Mueve una ventana al espacio de trabajo del agente.
    """
    if not _manager.mode:
        return {
            "error": "Background mode not set up. Call setup_background_mode() first.",
        }

    if not _manager.agent_monitor:
        return {"error": "No agent monitor configured."}

    try:
        from marlow.core.uia_utils import find_window

        target, err = find_window(window_title, list_available=False)
        if err:
            return err

        title = target.window_text()

        # Save original position for move_to_user_screen
        rect = target.rectangle()
        _manager._moved_windows[title] = {
            "x": rect.left,
            "y": rect.top,
            "width": rect.width(),
            "height": rect.height(),
        }

        # Move to agent monitor using Win32 API (UIAWrapper has no move_window)
        agent = _manager.agent_monitor
        margin = 10
        new_x = agent["left"] + margin
        new_y = agent.get("top", 0) + margin
        hwnd = target.handle

        # Clamp window size to fit within agent monitor
        # / Ajustar tamaño de ventana para que quepa en el monitor del agente
        win_w = rect.width()
        win_h = rect.height()
        max_w = agent["width"] - margin * 2
        max_h = agent["height"] - margin * 2
        resized = False

        if win_w > max_w:
            win_w = max(max_w, 400)  # minimum usable width
            resized = True
        if win_h > max_h:
            win_h = max(max_h, 300)  # minimum usable height
            resized = True

        ctypes.windll.user32.MoveWindow(
            hwnd, new_x, new_y, win_w, win_h, True
        )

        result = {
            "success": True,
            "window": title,
            "moved_to": _manager.mode,
            "new_position": {"x": new_x, "y": new_y},
            "new_size": {"width": win_w, "height": win_h},
            "original_position": _manager._moved_windows[title],
        }
        if resized:
            result["resized"] = True
            result["reason"] = (
                f"Window resized to fit agent monitor "
                f"({agent['width']}x{agent['height']})"
            )
        return result

    except Exception as e:
        return {"error": str(e)}


async def move_to_user_screen(window_title: str) -> dict:
    """
    Move a window back to the user's primary monitor.

    Args:
        window_title: Title (or partial title) of the window to move back.

    Returns:
        Dictionary with move result and new position.

    / Mueve una ventana de vuelta al monitor principal del usuario.
    """
    if not _manager.primary_monitor:
        return {"error": "Background mode not set up. Call setup_background_mode() first."}

    try:
        from marlow.core.uia_utils import find_window

        target, err = find_window(window_title, list_available=False)
        if err:
            return err

        title = target.window_text()

        # Restore to original position if we saved it, otherwise center on primary
        rect = target.rectangle()
        hwnd = target.handle
        original = _manager._moved_windows.pop(title, None)
        if original:
            ctypes.windll.user32.MoveWindow(
                hwnd, original["x"], original["y"],
                original["width"], original["height"], True
            )
            new_pos = {"x": original["x"], "y": original["y"]}
        else:
            primary = _manager.primary_monitor
            center_x = primary["left"] + primary["width"] // 4
            center_y = primary["top"] + primary["height"] // 4
            ctypes.windll.user32.MoveWindow(
                hwnd, center_x, center_y, rect.width(), rect.height(), True
            )
            new_pos = {"x": center_x, "y": center_y}

        return {
            "success": True,
            "window": title,
            "moved_to": "primary_monitor",
            "new_position": new_pos,
            "restored_original": original is not None,
        }

    except Exception as e:
        return {"error": str(e)}


def is_on_user_screen(x: int, y: int) -> bool:
    """
    Check if coordinates fall on the user's primary monitor.

    Returns True if (x, y) is within the primary monitor bounds.
    Returns False if background mode is not set up.

    / Verifica si las coordenadas están en el monitor del usuario.
    """
    if not _manager.primary_monitor:
        return False
    p = _manager.primary_monitor
    return (
        p["left"] <= x < p.get("right", p["left"] + p.get("width", 1920))
        and p.get("top", 0) <= y < p.get("bottom", p.get("top", 0) + p.get("height", 1080))
    )


def get_agent_move_coords() -> Optional[tuple[int, int]]:
    """
    Get safe coordinates for placing a window on the agent monitor.

    Returns (x, y) for the agent monitor, or None if not available.

    / Obtiene coordenadas seguras para colocar ventana en monitor del agente.
    """
    if not _manager.agent_monitor:
        return None
    agent = _manager.agent_monitor
    return (agent["left"] + 50, agent.get("top", 0) + 50)


def is_background_mode_active() -> bool:
    """Check if background mode is set up."""
    return _manager.mode is not None


async def get_agent_screen_state() -> dict:
    """
    Get the state of windows on the agent screen.

    Lists all windows currently on the agent monitor/area.

    Returns:
        Dictionary with agent windows, mode info, and monitor details.

    / Obtiene el estado de las ventanas en la pantalla del agente.
    """
    if not _manager.mode:
        return {
            "error": "Background mode not set up. Call setup_background_mode() first.",
        }

    try:
        from marlow.tools.windows import list_windows

        all_windows = await list_windows(include_minimized=False)
        if "error" in all_windows:
            return all_windows

        agent = _manager.agent_monitor
        if not agent:
            return {"error": "No agent monitor configured."}

        # Filter windows that are on the agent monitor
        agent_left = agent["left"]
        agent_right = agent.get("right", agent_left + agent.get("width", 1920))
        agent_top = agent.get("top", 0)
        agent_bottom = agent.get("bottom", agent_top + agent.get("height", 1080))

        agent_windows = []
        for win in all_windows.get("windows", []):
            wx = win["position"]["x"]
            wy = win["position"]["y"]
            # Window is on agent screen if its position is within agent monitor bounds
            if agent_left <= wx < agent_right and agent_top <= wy < agent_bottom:
                agent_windows.append(win)

        return {
            "mode": _manager.mode,
            "agent_monitor": {
                "left": agent_left, "top": agent_top,
                "width": agent.get("width", 0),
                "height": agent.get("height", 0),
            },
            "windows": agent_windows,
            "window_count": len(agent_windows),
            "tracked_windows": list(_manager._moved_windows.keys()),
        }

    except Exception as e:
        return {"error": str(e)}


async def set_agent_screen_only(enabled: bool) -> dict:
    """
    Enable or disable agent_screen_only mode.

    When enabled, open_application and manage_window auto-redirect
    windows to the agent monitor instead of the user's screen.

    Args:
        enabled: True to enable, False to disable.

    Returns:
        Dictionary with new state.

    / Activa o desactiva el modo agent_screen_only.
    """
    try:
        from marlow.core.config import MarlowConfig

        config = MarlowConfig.load()
        config.automation.agent_screen_only = enabled
        config.save()

        return {
            "success": True,
            "agent_screen_only": enabled,
            "background_mode": _manager.mode,
            "hint": (
                "Windows will auto-move to agent screen."
                if enabled
                else "Windows will stay where opened/moved."
            ),
        }
    except Exception as e:
        return {"error": str(e)}
