"""
Marlow Keyboard Tool

Type text, press keys, and execute keyboard shortcuts.
Uses silent methods first for background-mode compatibility.

Escalation order:
1. set_edit_text() — silent, works in background (preferred for text input)
2. type_keys() — real keyboard input, takes focus
3. pyautogui.write() — absolute fallback
"""

import time
import logging
from typing import Optional

logger = logging.getLogger("marlow.tools.keyboard")


async def type_text(
    text: str,
    element_name: Optional[str] = None,
    window_title: Optional[str] = None,
    use_silent: bool = True,
    clear_first: bool = False,
) -> dict:
    """
    Type text into an element or at the current cursor position.

    Preferred: Type by element_name using set_edit_text() — works in background.
    Fallback: Type at current cursor position using keyboard simulation.

    Args:
        text: The text to type.
        element_name: Name of the text field to type into (e.g., "Search", "Email").
                      If provided, Marlow finds it and types directly — no focus needed.
        window_title: Which window to search in. If provided without element_name,
                      auto-detects the main editable area (works with new Notepad, etc.).
        use_silent: Try set_edit_text() first for background compatibility.
        clear_first: Clear the field before typing. Default: False.

    Returns:
        Dictionary with typing result and method used.

    / Escribe texto en un elemento o en la posición actual del cursor.
    """
    if element_name:
        return await _type_by_name(text, element_name, window_title,
                                     use_silent, clear_first)
    elif window_title:
        # Auto-find the editable area in the target window
        return await _type_into_window(text, window_title, use_silent, clear_first)
    else:
        return await _type_direct(text)


def _find_editable_element(parent: object, max_depth: int = 6, depth: int = 0) -> Optional[object]:
    """
    Find the main editable element in a window by control type.
    Searches for Document or Edit controls that have a Value pattern.
    Works with new Windows 11 Notepad (RichEditD2DPT) and classic controls.

    / Encuentra el elemento editable principal buscando por control_type.
    """
    if depth > max_depth:
        return None

    try:
        ct = parent.element_info.control_type
        if ct in ("Document", "Edit"):
            return parent

        for child in parent.children():
            found = _find_editable_element(child, max_depth, depth + 1)
            if found is not None:
                return found
    except Exception:
        pass

    return None


def _set_text_silent(element: object, text: str, clear_first: bool) -> Optional[dict]:
    """
    Try all silent text-setting methods in order.
    Returns result dict on success, None on failure.

    Order:
    1. set_edit_text() — classic Edit controls
    2. iface_value.SetValue() — modern UIA controls (RichEditD2DPT, etc.)

    / Intenta todos los métodos silenciosos para escribir texto.
    """
    # Method 1: set_edit_text (classic controls)
    try:
        if clear_first:
            element.set_edit_text("")
        element.set_edit_text(text)
        return {
            "success": True,
            "method": "set_edit_text (silent — background compatible)",
        }
    except Exception:
        pass

    # Method 2: UIA ValuePattern.SetValue (modern controls like RichEditD2DPT)
    try:
        value_iface = element.iface_value
        if value_iface:
            if clear_first:
                value_iface.SetValue("")
            value_iface.SetValue(text)
            return {
                "success": True,
                "method": "ValuePattern.SetValue (silent — background compatible)",
            }
    except Exception:
        pass

    return None


def _is_win11_notepad(window: object) -> bool:
    """Check if a window is the new Windows 11 tabbed Notepad."""
    try:
        if window.element_info.class_name != "Notepad":
            return False
        # Confirm by looking for the RichEditD2DPT editor
        editor = _find_editable_element(window)
        return editor is not None and editor.element_info.class_name == "RichEditD2DPT"
    except Exception:
        return False


def _get_editor_content(editor: object) -> str:
    """Read the current text from an editor element via ValuePattern."""
    try:
        val = editor.iface_value
        if val:
            return val.CurrentValue or ""
    except Exception:
        pass
    try:
        return editor.window_text() or ""
    except Exception:
        return ""


def _ensure_safe_notepad_tab(window: object) -> Optional[dict]:
    """
    If the window is the new Win11 Notepad and the current tab has content,
    open a new empty tab before writing. Prevents overwriting user data.

    Returns info dict if a new tab was created, None otherwise.

    / Si la ventana es el nuevo Notepad de Win11 y el tab actual tiene
    / contenido, abre un tab nuevo antes de escribir. Previene sobreescribir
    / datos del usuario.
    """
    if not _is_win11_notepad(window):
        return None

    editor = _find_editable_element(window)
    if editor is None:
        return None

    content = _get_editor_content(editor)
    if not content.strip():
        # Current tab is empty — safe to write
        return None

    # Current tab has content — open a new tab
    logger.info("Notepad tab has existing content, opening new tab to protect user data")

    from marlow.core.uia_utils import find_element_by_name
    add_btn = find_element_by_name(window, "Add New Tab", max_depth=6)
    if add_btn is None:
        # Fallback: search by automation_id
        try:
            add_btn = _find_by_automation_id(window, "AddButton")
        except Exception:
            pass

    if add_btn is None:
        logger.warning("Could not find 'Add New Tab' button in Notepad")
        return None

    try:
        add_btn.invoke()
        # Wait for the new tab to initialize
        time.sleep(0.5)
        return {
            "new_tab_created": True,
            "reason": "Existing tab had content — opened new tab to protect user data",
            "preserved_content_length": len(content),
        }
    except Exception as e:
        logger.warning(f"Failed to create new tab: {e}")
        return None


def _find_by_automation_id(parent: object, auto_id: str, max_depth: int = 6, depth: int = 0) -> Optional[object]:
    """Find an element by automation_id (fallback search)."""
    if depth > max_depth:
        return None
    try:
        aid = getattr(parent.element_info, "automation_id", "") or ""
        if aid == auto_id:
            return parent
        for child in parent.children():
            found = _find_by_automation_id(child, auto_id, max_depth, depth + 1)
            if found is not None:
                return found
    except Exception:
        pass
    return None


async def _type_into_window(
    text: str,
    window_title: str,
    use_silent: bool,
    clear_first: bool,
) -> dict:
    """
    Auto-find the editable area in a window and type into it.
    Used when window_title is given but no element_name.
    Works with new Windows 11 Notepad, classic Edit controls, etc.

    / Auto-encuentra el area editable en una ventana y escribe en ella.
    """
    try:
        from marlow.core.uia_utils import find_window
        from marlow.core.error_journal import _journal

        target_window, err = find_window(window_title)
        if err:
            return err

        win_text = target_window.window_text()

        # Protect user data: if Notepad tab has content, open a new tab first
        tab_info = _ensure_safe_notepad_tab(target_window)

        # Find (or re-find) the editable element after potential tab switch
        element = _find_editable_element(target_window)

        if element is None:
            return {
                "error": f"No editable element found in '{window_title}'",
                "hint": "Use get_ui_tree() to inspect the window structure.",
            }

        ct = element.element_info.control_type
        cn = element.element_info.class_name

        # Consult error journal: does silent typing fail on this app?
        best = _journal.get_best_method("type_text", win_text)
        skip_silent = best == "type_keys"

        # Try silent methods first (background compatible)
        if use_silent and not skip_silent:
            result = _set_text_silent(element, text, clear_first)
            if result:
                _journal.record_success("type_text", win_text, "set_text_silent")
                result["window"] = win_text
                result["control"] = f"{ct} ({cn})"
                result["text_length"] = len(text)
                if tab_info:
                    result["notepad_protection"] = tab_info
                return result
            _journal.record_failure("type_text", win_text, "set_text_silent",
                                    f"Silent methods failed for {ct}/{cn}")
            logger.debug(f"Silent methods failed for {ct}/{cn}, falling back")
        elif skip_silent:
            logger.debug(f"Journal says silent typing fails on '{win_text}', skipping to type_keys")

        # Fallback to keyboard simulation (focus saved/restored by server.py)
        element.click_input()
        if clear_first:
            element.type_keys("^a{DELETE}", with_spaces=True)
        element.type_keys(text, with_spaces=True, with_newlines=True)

        _journal.record_success("type_text", win_text, "type_keys")
        result = {
            "success": True,
            "method": "type_keys (keyboard simulation)",
            "window": win_text,
            "control": f"{ct} ({cn})",
            "text_length": len(text),
        }
        if skip_silent:
            result["journal_hint"] = "Skipped silent methods — journal knows they fail on this app"
        if tab_info:
            result["notepad_protection"] = tab_info
        return result

    except ImportError:
        return {"error": "pywinauto not installed. Run: pip install pywinauto"}
    except Exception as e:
        logger.error(f"Type into window error: {e}")
        return {"error": str(e)}


async def _type_by_name(
    text: str,
    element_name: str,
    window_title: Optional[str],
    use_silent: bool,
    clear_first: bool,
) -> dict:
    """Find text field by name and type into it."""
    try:
        from pywinauto import Desktop
        from marlow.core.uia_utils import find_window, find_element_by_name
        from marlow.core.error_journal import _journal

        # Find window
        if window_title:
            target_window, err = find_window(window_title, list_available=False)
            if err:
                return err
        else:
            desktop = Desktop(backend="uia")
            target_window = desktop.window(active_only=True)

        win_text = target_window.window_text()

        # Find element by name first
        element = find_element_by_name(target_window, element_name)
        used_auto_detect = False

        # If not found by name, fall back to auto-detecting the editable area
        if element is None:
            element = _find_editable_element(target_window)
            if element is None:
                return {
                    "error": f"Element '{element_name}' not found",
                    "hint": "Use get_ui_tree() to see available elements.",
                }
            used_auto_detect = True
            logger.debug(f"'{element_name}' not found by name, using auto-detected editor")

        # Protect user data when auto-detecting into Notepad
        tab_info = None
        if used_auto_detect:
            tab_info = _ensure_safe_notepad_tab(target_window)
            if tab_info:
                # Re-find editor after new tab creation
                element = _find_editable_element(target_window)
                if element is None:
                    return {"error": "Editor not found after creating new tab"}

        # Consult error journal: does silent typing fail on this app?
        best = _journal.get_best_method("type_text", win_text)
        skip_silent = best == "type_keys"

        # Try silent methods first (background compatible)
        if use_silent and not skip_silent:
            result = _set_text_silent(element, text, clear_first)
            if result:
                _journal.record_success("type_text", win_text, "set_text_silent")
                result["element"] = element_name
                result["text_length"] = len(text)
                if tab_info:
                    result["notepad_protection"] = tab_info
                return result
            _journal.record_failure("type_text", win_text, "set_text_silent",
                                    f"Silent methods failed for '{element_name}'")
            logger.debug(f"Silent methods failed for '{element_name}'")
        elif skip_silent:
            logger.debug(f"Journal says silent typing fails on '{win_text}', skipping to type_keys")

        # Fallback to keyboard simulation (focus saved/restored by server.py)
        element.click_input()
        if clear_first:
            element.type_keys("^a{DELETE}", with_spaces=True)
        element.type_keys(text, with_spaces=True, with_newlines=True)

        _journal.record_success("type_text", win_text, "type_keys")
        result = {
            "success": True,
            "method": "type_keys (keyboard simulation)",
            "element": element_name,
            "text_length": len(text),
        }
        if skip_silent:
            result["journal_hint"] = "Skipped silent methods — journal knows they fail on this app"
        if tab_info:
            result["notepad_protection"] = tab_info
        return result

    except ImportError:
        return {"error": "pywinauto not installed. Run: pip install pywinauto"}
    except Exception as e:
        logger.error(f"Type by name error: {e}")
        return {"error": str(e)}


async def _type_direct(text: str) -> dict:
    """Type at current cursor position using pyautogui."""
    try:
        import pyautogui

        pyautogui.FAILSAFE = True
        # Focus is saved/restored by server.py call_tool
        pyautogui.write(text, interval=0.02)

        return {
            "success": True,
            "method": "pyautogui.write (direct keyboard)",
            "text_length": len(text),
        }

    except ImportError:
        return {"error": "pyautogui not installed. Run: pip install pyautogui"}
    except Exception as e:
        return {"error": str(e)}


async def press_key(key: str, times: int = 1) -> dict:
    """
    Press a single key or key combination.

    Args:
        key: Key to press. Examples: "enter", "tab", "escape", "f5",
             "up", "down", "left", "right", "delete", "backspace", "space".
        times: Number of times to press the key. Default: 1.

    Returns:
        Dictionary with result.
    
    / Presiona una tecla individual.
    """
    try:
        import pyautogui

        pyautogui.FAILSAFE = True
        # Focus is saved/restored by server.py call_tool
        for _ in range(times):
            pyautogui.press(key)

        return {
            "success": True,
            "key": key,
            "times": times,
        }
    except ImportError:
        return {"error": "pyautogui not installed."}
    except Exception as e:
        return {"error": str(e)}


async def hotkey(*keys: str) -> dict:
    """
    Execute a keyboard shortcut (hotkey combination).

    Args:
        *keys: Keys to press simultaneously. 
               Examples: hotkey("ctrl", "c") for copy,
                         hotkey("ctrl", "shift", "s") for save as,
                         hotkey("alt", "f4") to close window.

    Returns:
        Dictionary with result.
    
    / Ejecuta un atajo de teclado (combinación de teclas).
    """
    try:
        import pyautogui

        pyautogui.FAILSAFE = True
        # Focus is saved/restored by server.py call_tool
        pyautogui.hotkey(*keys)

        return {
            "success": True,
            "keys": list(keys),
            "combination": "+".join(keys),
        }
    except ImportError:
        return {"error": "pyautogui not installed."}
    except Exception as e:
        return {"error": str(e)}
