"""
Marlow UIA Utilities

Shared helpers for finding windows and elements via UI Automation.
Centralizes the window-finding pattern used across all tool modules.

/ Utilidades compartidas para encontrar ventanas y elementos via UIA.
"""

import re
import logging
from typing import Optional

logger = logging.getLogger("marlow.core.uia_utils")


def find_window(
    window_title: str,
    list_available: bool = True,
    max_suggestions: int = 15,
) -> tuple:
    """
    Find a window by title using pywinauto UIA backend.

    Args:
        window_title: Partial title to match (regex-escaped automatically).
        list_available: Include available window titles in error response.
        max_suggestions: Max window titles to list on failure.

    Returns:
        (window_object, None) on success.
        (None, error_dict) on failure.

    / Encuentra una ventana por titulo usando pywinauto UIA backend.
    """
    from pywinauto import Desktop

    desktop = Desktop(backend="uia")
    windows = desktop.windows(title_re=f".*{re.escape(window_title)}.*")

    if not windows:
        error: dict = {"error": f"Window '{window_title}' not found"}
        if list_available:
            error["available_windows"] = [
                w.window_text() for w in desktop.windows()
                if w.window_text().strip()
            ][:max_suggestions]
        return None, error

    return windows[0], None


def find_element_by_name(
    parent: object,
    name: str,
    max_depth: int = 5,
    depth: int = 0,
) -> Optional[object]:
    """
    Recursively search for an element by name in the UI tree.

    Matches by window_text (whole-word) or automation_id (exact).
    Returns the first match or None.

    / Busca recursivamente un elemento por nombre en el arbol UI.
    """
    if depth > max_depth:
        return None

    try:
        text = parent.window_text() or ""
        name_lower = name.lower()
        text_lower = text.lower()

        # Exact match or whole-word match (not substring of larger words)
        if (
            text_lower == name_lower
            or text_lower.startswith(name_lower + " ")
            or text_lower.endswith(" " + name_lower)
            or (" " + name_lower + " ") in text_lower
        ):
            return parent

        # Also check automation_id (exact match)
        auto_id = getattr(parent.element_info, "automation_id", "") or ""
        if name_lower == auto_id.lower():
            return parent

        # Search children
        for child in parent.children():
            found = find_element_by_name(child, name, max_depth, depth + 1)
            if found is not None:
                return found

    except Exception:
        pass

    return None
