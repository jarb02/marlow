"""
Marlow UI Tree Tool

Reads the Windows UI Automation Accessibility Tree to understand
what's on screen without screenshots. This is the primary method
for Marlow to "see" the desktop — costs 0 tokens, works in background mode.

Adaptive depth: when depth="auto" (default), uses app_detector to pick
the optimal tree depth per framework. User can override with depth=N.

Inspired by sbroenne's approach: UI Automation first, screenshots last.
"""

import logging
from typing import Optional, Union

logger = logging.getLogger("marlow.tools.ui_tree")

# Optimal depth per framework — tuned for coverage vs performance
# / Profundidad optima por framework — ajustada para cobertura vs rendimiento
_DEPTH_MAP = {
    "winui3": 15,
    "uwp": 15,
    "win32": 15,
    "wpf": 15,
    "winforms": 12,
    "chromium": 8,
    "edge_webview2": 8,
    "electron": 5,
    "cef": 5,
}
_DEPTH_DEFAULT = 10  # Unknown framework fallback


def _resolve_depth(pid: int) -> tuple[int, str, Optional[str]]:
    """
    Pick optimal tree depth based on app framework.

    Returns (depth, reason, framework_name).
    / Elige profundidad optima del arbol segun framework de la app.
    """
    try:
        from marlow.core.app_detector import detect_framework
        info = detect_framework(pid)
        fw = info.get("framework", "unknown")
        if fw in _DEPTH_MAP:
            depth = _DEPTH_MAP[fw]
            return depth, f"auto ({fw})", fw
        return _DEPTH_DEFAULT, f"auto (unknown framework)", fw
    except Exception as e:
        logger.debug(f"Framework detection failed, using default depth: {e}")
        return _DEPTH_DEFAULT, "auto (detection failed)", None


async def get_ui_tree(
    window_title: Optional[str] = None,
    max_depth: Union[int, str] = "auto",
    include_invisible: bool = False,
) -> dict:
    """
    Get the UI Automation Accessibility Tree for a window or the desktop.

    This is Marlow's primary "vision" — it reads the structure of any window
    without needing screenshots. Cost: 0 tokens. Speed: ~10-50ms.

    Args:
        window_title: Title of the window to inspect. If None, uses the
                      currently focused window.
        max_depth: Tree depth. "auto" (default) picks optimal depth per app
                   framework. Pass an integer to override.
        include_invisible: Whether to include non-visible elements.

    Returns:
        Dictionary with the UI tree structure including:
        - Window info (title, size, position, framework)
        - Element hierarchy (buttons, text fields, menus, etc.)
        - Each element: name, type, value, enabled state, automation_id
        - depth_used, depth_reason: what depth was chosen and why

    / Obtiene el Árbol de Accesibilidad UI Automation de una ventana.
    / Esta es la "visión" principal de Marlow — lee la estructura de cualquier
    / ventana sin necesitar screenshots. Costo: 0 tokens. Velocidad: ~10-50ms.
    """
    try:
        from pywinauto import Desktop
        from marlow.core.uia_utils import find_window

        if window_title:
            target, err = find_window(window_title, max_suggestions=20)
            if err:
                return err
        else:
            desktop = Desktop(backend="uia")
            target = desktop.window(active_only=True)

        # Resolve depth: "auto" → framework-based, int → user override
        # / Resolver profundidad: "auto" → basada en framework, int → override del usuario
        pid = target.process_id()
        if max_depth == "auto":
            depth, depth_reason, framework = _resolve_depth(pid)
        else:
            depth = int(max_depth)
            framework = None
            depth_reason = f"user override ({depth})"
            # Still detect framework for metadata
            try:
                from marlow.core.app_detector import detect_framework
                info = detect_framework(pid)
                framework = info.get("framework", "unknown")
            except Exception:
                pass

        # Build the tree
        # Desktop.windows() returns UIAWrapper objects directly — no wrapper_object() needed
        tree = _build_element_tree(target, depth, include_invisible)

        # Get window info
        rect = target.rectangle()
        window_info = {
            "title": target.window_text(),
            "position": {"x": rect.left, "y": rect.top},
            "size": {"width": rect.width(), "height": rect.height()},
            "process_id": pid,
            "is_active": target.is_active(),
        }
        if framework:
            window_info["framework"] = framework

        return {
            "window": window_info,
            "elements": tree,
            "element_count": _count_elements(tree),
            "depth_used": depth,
            "depth_reason": depth_reason,
        }

    except ImportError:
        return {
            "error": "pywinauto is not installed. Run: pip install pywinauto",
            "hint": "Marlow requires pywinauto for UI Automation on Windows.",
        }
    except Exception as e:
        logger.error(f"Error reading UI tree: {e}")
        return {"error": str(e)}


def _build_element_tree(
    element: object, max_depth: int, include_invisible: bool, current_depth: int = 0
) -> dict:
    """Recursively build element tree from a pywinauto wrapper."""
    if current_depth > max_depth:
        return {"truncated": True, "reason": f"max_depth={max_depth} reached"}

    try:
        # Get element properties
        info = {
            "name": element.window_text() or "",
            "control_type": element.element_info.control_type or "Unknown",
            "automation_id": getattr(element.element_info, "automation_id", "") or "",
            "class_name": element.element_info.class_name or "",
            "is_enabled": element.is_enabled(),
            "is_visible": element.is_visible(),
        }

        # Skip invisible elements if not requested
        if not include_invisible and not info["is_visible"]:
            return None

        # Add value for input elements
        try:
            value = element.get_value()
            if value:
                info["value"] = value
        except (AttributeError, Exception):
            pass

        # Add patterns/capabilities
        patterns = []
        for pattern_name in [
            "Invoke", "Toggle", "SelectionItem", "ExpandCollapse",
            "Value", "RangeValue", "Scroll", "Text",
        ]:
            try:
                if hasattr(element, f"iface_{pattern_name.lower()}"):
                    patterns.append(pattern_name)
            except Exception:
                pass
        
        if patterns:
            info["patterns"] = patterns

        # Get children
        if current_depth < max_depth:
            children = []
            try:
                for child in element.children():
                    child_tree = _build_element_tree(
                        child, max_depth, include_invisible, current_depth + 1
                    )
                    if child_tree is not None:
                        children.append(child_tree)
            except Exception:
                pass

            if children:
                info["children"] = children

        return info

    except Exception as e:
        return {"error": str(e), "name": "unknown"}


def _count_elements(tree: dict) -> int:
    """Count total elements in a tree."""
    if not tree or "error" in tree:
        return 0
    count = 1
    for child in tree.get("children", []):
        count += _count_elements(child)
    return count
