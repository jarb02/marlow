"""
Marlow Window Management Tools

List, focus, move, resize, minimize, maximize, and close windows.
"""

import ctypes
import logging
from typing import Optional

logger = logging.getLogger("marlow.tools.windows")


async def list_windows(include_minimized: bool = True) -> dict:
    """
    List all open windows with their titles, positions, and sizes.

    Args:
        include_minimized: Include minimized windows. Default: True.

    Returns:
        List of window information dictionaries.
    
    / Lista todas las ventanas abiertas con sus títulos, posiciones y tamaños.
    """
    try:
        from pywinauto import Desktop

        desktop = Desktop(backend="uia")
        windows = []

        for win in desktop.windows():
            try:
                title = win.window_text()
                if not title.strip():
                    continue

                rect = win.rectangle()
                is_minimized = rect.left == -32000  # Windows minimized sentinel

                if not include_minimized and is_minimized:
                    continue

                windows.append({
                    "title": title,
                    "position": {"x": rect.left, "y": rect.top},
                    "size": {
                        "width": rect.width() if not is_minimized else 0,
                        "height": rect.height() if not is_minimized else 0,
                    },
                    "is_minimized": is_minimized,
                    "is_active": win.is_active(),
                    "process_id": win.process_id(),
                })
            except Exception:
                continue

        return {
            "windows": windows,
            "count": len(windows),
        }

    except ImportError:
        return {"error": "pywinauto not installed."}
    except Exception as e:
        return {"error": str(e)}


async def focus_window(window_title: str) -> dict:
    """
    Bring a window to the foreground and give it focus.

    Args:
        window_title: Title (or partial title) of the window to focus.
    
    / Trae una ventana al frente y le da el foco.
    """
    try:
        from marlow.core.uia_utils import find_window

        target, err = find_window(window_title)
        if err:
            return err

        target.set_focus()

        return {
            "success": True,
            "window": target.window_text(),
            "action": "focused",
        }

    except Exception as e:
        return {"error": str(e)}


async def manage_window(
    window_title: str,
    action: str,
    x: Optional[int] = None,
    y: Optional[int] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> dict:
    """
    Perform window management actions: move, resize, minimize, maximize, 
    restore, or close.

    Args:
        window_title: Title of the window to manage.
        action: One of: "minimize", "maximize", "restore", "close",
                "move" (requires x, y), "resize" (requires width, height).
        x: New X position (for "move" action).
        y: New Y position (for "move" action).
        width: New width (for "resize" action).
        height: New height (for "resize" action).
    
    / Gestiona una ventana: mover, redimensionar, minimizar, maximizar, cerrar.
    """
    valid_actions = ["minimize", "maximize", "restore", "close", "move", "resize"]

    if action not in valid_actions:
        return {
            "error": f"Invalid action '{action}'",
            "valid_actions": valid_actions,
        }

    try:
        from marlow.core.uia_utils import find_window

        target, err = find_window(window_title, list_available=False)
        if err:
            return err

        title = target.window_text()

        if action == "minimize":
            target.minimize()
        elif action == "maximize":
            target.maximize()
        elif action == "restore":
            target.restore()
        elif action == "close":
            target.close()
        elif action == "move":
            if x is None or y is None:
                return {"error": "move requires x and y parameters"}
            rect = target.rectangle()
            hwnd = target.handle
            ctypes.windll.user32.MoveWindow(
                hwnd, x, y, rect.width(), rect.height(), True
            )
        elif action == "resize":
            if width is None or height is None:
                return {"error": "resize requires width and height parameters"}
            rect = target.rectangle()
            hwnd = target.handle
            ctypes.windll.user32.MoveWindow(
                hwnd, rect.left, rect.top, width, height, True
            )

        return {
            "success": True,
            "window": title,
            "action": action,
        }

    except Exception as e:
        return {"error": str(e)}
