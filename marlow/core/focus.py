"""
Marlow Focus Guard

Prevents Marlow from stealing the user's active window focus.

Before any focus-stealing operation (click_input, type_keys, pyautogui),
the focus guard saves the user's foreground window. After the operation,
it restores focus back.

Uses Win32 API directly:
- GetForegroundWindow() — save current focus
- SetForegroundWindow() — restore focus
- GetWindowTextW() — identify window for logging

/ Previene que Marlow robe el foco de la ventana activa del usuario.
"""

import ctypes
import ctypes.wintypes
import time
import logging
from contextlib import contextmanager
from typing import Optional

logger = logging.getLogger("marlow.core.focus")

# Module-level state: the user's foreground window before Marlow acts
_user_hwnd: Optional[int] = None


def get_foreground_window() -> tuple[int, str]:
    """
    Get the current foreground window handle and title.

    Returns:
        (hwnd, title) — handle and window title.

    / Obtiene el handle y título de la ventana activa actual.
    """
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    title = _get_window_title(hwnd)
    return hwnd, title


def _get_window_title(hwnd: int) -> str:
    """Get window title from HWND."""
    if not hwnd or not ctypes.windll.user32.IsWindow(hwnd):
        return ""
    buf = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


def save_user_focus() -> tuple[int, str]:
    """
    Save the user's current foreground window so it can be restored later.

    Returns:
        (hwnd, title) of the saved window.

    / Guarda la ventana activa del usuario para restaurarla después.
    """
    global _user_hwnd
    hwnd, title = get_foreground_window()
    _user_hwnd = hwnd
    logger.debug(f"Saved user focus: hwnd={hwnd} title='{title}'")
    return hwnd, title


def restore_user_focus() -> dict:
    """
    Restore focus to the user's previously saved foreground window.

    Call this after any operation that may have stolen focus
    (click_input, type_keys, pyautogui calls).

    Returns:
        Dictionary with restore result.

    / Restaura el foco a la ventana del usuario guardada previamente.
    """
    global _user_hwnd

    if _user_hwnd is None:
        return {"restored": False, "reason": "No saved user focus"}

    hwnd = _user_hwnd

    # Verify the window still exists
    if not ctypes.windll.user32.IsWindow(hwnd):
        _user_hwnd = None
        return {"restored": False, "reason": "Saved window no longer exists"}

    title = _get_window_title(hwnd)

    # Check if focus already correct
    current = ctypes.windll.user32.GetForegroundWindow()
    if current == hwnd:
        return {"restored": True, "window": title, "already_focused": True}

    # Restore focus — SetForegroundWindow may fail if our process doesn't
    # own the foreground. Use AttachThreadInput trick to work around it.
    result = _force_set_foreground(hwnd)

    if result:
        logger.debug(f"Restored user focus: '{title}'")
        return {"restored": True, "window": title}
    else:
        logger.warning(f"Could not restore focus to '{title}'")
        return {"restored": False, "window": title, "reason": "SetForegroundWindow failed"}


def _force_set_foreground(hwnd: int) -> bool:
    """
    Force-set the foreground window using the AttachThreadInput trick.
    Windows restricts SetForegroundWindow to the process that owns the
    foreground — this works around that limitation.
    """
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    # Try simple approach first
    if user32.SetForegroundWindow(hwnd):
        return True

    # AttachThreadInput trick: attach our thread to the foreground thread,
    # set foreground, then detach
    foreground_hwnd = user32.GetForegroundWindow()
    if not foreground_hwnd:
        return False

    foreground_tid = user32.GetWindowThreadProcessId(foreground_hwnd, None)
    our_tid = kernel32.GetCurrentThreadId()

    if foreground_tid != our_tid:
        user32.AttachThreadInput(our_tid, foreground_tid, True)

    try:
        user32.BringWindowToTop(hwnd)
        result = user32.SetForegroundWindow(hwnd)
    finally:
        if foreground_tid != our_tid:
            user32.AttachThreadInput(our_tid, foreground_tid, False)

    return bool(result)


@contextmanager
def preserve_focus():
    """
    Context manager that saves and restores the user's foreground window.

    Usage:
        with preserve_focus():
            element.click_input()  # May steal focus
        # Focus is automatically restored here

    / Context manager que guarda y restaura la ventana activa del usuario.
    """
    saved_hwnd, saved_title = save_user_focus()
    try:
        yield saved_hwnd
    finally:
        # Small delay to let the focus-stealing operation complete
        time.sleep(0.05)
        restore_user_focus()


async def restore_user_focus_tool() -> dict:
    """
    MCP tool: Restore focus to the user's window.

    Call this if focus was accidentally stolen and needs manual correction.

    / Herramienta MCP: Restaura el foco a la ventana del usuario.
    """
    result = restore_user_focus()
    current_hwnd, current_title = get_foreground_window()
    result["current_foreground"] = current_title
    return result
