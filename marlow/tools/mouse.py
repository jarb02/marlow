"""
Marlow Mouse Tool

Click elements by NAME (preferred) or by coordinates (fallback).
Uses silent methods first for background-mode compatibility.

Escalation order:
1. invoke() — silent, works in background (preferred)
2. click_input() — real mouse click, takes focus
3. pyautogui.click() — absolute coordinates fallback
"""

import logging
from typing import Optional

logger = logging.getLogger("marlow.tools.mouse")


async def click(
    element_name: Optional[str] = None,
    window_title: Optional[str] = None,
    x: Optional[int] = None,
    y: Optional[int] = None,
    button: str = "left",
    double_click: bool = False,
    use_silent: bool = True,
) -> dict:
    """
    Click an element by name or at specific coordinates.

    Preferred: Click by element_name (uses Accessibility Tree — works in background).
    Fallback: Click by x, y coordinates (uses real mouse — takes focus).

    Args:
        element_name: Name/text of the UI element to click (e.g., "Save", "File", "OK").
                      Marlow finds it in the Accessibility Tree and clicks it silently.
        window_title: Which window to search in. If None, searches all windows.
        x: X coordinate for absolute click (only if element_name not provided).
        y: Y coordinate for absolute click (only if element_name not provided).
        button: "left", "right", or "middle". Default: "left".
        double_click: Whether to double-click. Default: False.
        use_silent: Try silent invoke() first for background compatibility.
                    Default: True.

    Returns:
        Dictionary with click result and method used.
    
    / Hace click en un elemento por nombre o en coordenadas específicas.
    / Preferido: Click por element_name (usa Accessibility Tree — funciona en background).
    / Fallback: Click por coordenadas x, y (usa mouse real — toma el foco).
    """
    if element_name:
        return await _click_by_name(element_name, window_title, button, 
                                      double_click, use_silent)
    elif x is not None and y is not None:
        return await _click_by_coordinates(x, y, button, double_click)
    else:
        return {
            "error": "Provide either 'element_name' or both 'x' and 'y' coordinates.",
            "usage": {
                "by_name": "click(element_name='Save', window_title='Notepad')",
                "by_coords": "click(x=500, y=300)",
            }
        }


async def _click_by_name(
    element_name: str,
    window_title: Optional[str],
    button: str,
    double_click: bool,
    use_silent: bool,
) -> dict:
    """Find element by name in the Accessibility Tree and click it."""
    try:
        from pywinauto import Desktop
        from marlow.core.uia_utils import find_window, find_element_by_name
        from marlow.core.error_journal import _journal

        # Find the window
        if window_title:
            target_window, err = find_window(window_title)
            if err:
                return err
        else:
            desktop = Desktop(backend="uia")
            target_window = desktop.window(active_only=True)

        win_text = target_window.window_text()

        # Search for element by name
        element = find_element_by_name(target_window, element_name)

        if element is None:
            return {
                "error": f"Element '{element_name}' not found in window",
                "hint": "Try get_ui_tree() first to see available elements.",
            }

        # Consult error journal: does invoke() fail on this app?
        best = _journal.get_best_method("click", win_text)
        skip_invoke = best == "click_input"

        # Try silent method first (background-friendly)
        if use_silent and not skip_invoke:
            try:
                element.invoke()
                _journal.record_success("click", win_text, "invoke")
                return {
                    "success": True,
                    "method": "invoke (silent — background compatible)",
                    "element": element_name,
                    "window": win_text,
                }
            except Exception as e:
                _journal.record_failure("click", win_text, "invoke", str(e))
                logger.debug(f"Silent invoke failed for '{element_name}', falling back to click_input")
        elif skip_invoke:
            logger.debug(f"Journal says invoke fails on '{win_text}', skipping to click_input")

        # Fallback to real click (focus is saved/restored by server.py call_tool)
        if double_click:
            element.double_click_input()
        elif button == "right":
            element.right_click_input()
        else:
            element.click_input()

        _journal.record_success("click", win_text, "click_input")
        result = {
            "success": True,
            "method": "click_input (real mouse — focus restored)",
            "element": element_name,
            "window": win_text,
        }
        if skip_invoke:
            result["journal_hint"] = "Skipped invoke() — journal knows it fails on this app"
        return result

    except ImportError:
        return {"error": "pywinauto not installed. Run: pip install pywinauto"}
    except Exception as e:
        logger.error(f"Click by name error: {e}")
        return {"error": str(e)}


async def _click_by_coordinates(
    x: int, y: int, button: str, double_click: bool
) -> dict:
    """Click at absolute screen coordinates using pyautogui."""
    try:
        import pyautogui

        pyautogui.FAILSAFE = True  # Move mouse to corner to abort

        # Focus is saved/restored by server.py call_tool
        if double_click:
            pyautogui.doubleClick(x, y, button=button)
        else:
            pyautogui.click(x, y, button=button)

        return {
            "success": True,
            "method": "pyautogui (coordinates — focus restored)",
            "coordinates": {"x": x, "y": y},
            "button": button,
            "double_click": double_click,
        }

    except ImportError:
        return {"error": "pyautogui not installed. Run: pip install pyautogui"}
    except pyautogui.FailSafeException:
        return {
            "error": "PyAutoGUI failsafe triggered (mouse moved to screen corner)",
            "hint": "This is a safety feature. The action was aborted.",
        }
    except Exception as e:
        logger.error(f"Click by coordinates error: {e}")
        return {"error": str(e)}


def _find_element(parent: object, name: str, max_depth: int = 5, depth: int = 0) -> Optional[object]:
    """Backward-compatible alias for find_element_by_name."""
    from marlow.core.uia_utils import find_element_by_name
    return find_element_by_name(parent, name, max_depth, depth)
