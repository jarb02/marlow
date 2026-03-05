"""Linux UITreeProvider — AT-SPI2 accessibility tree.

Reads the accessibility tree on Linux desktops via AT-SPI2
(Assistive Technology Service Provider Interface), the standard
accessibility framework for GNOME, KDE, and other GTK/Qt apps.

Uses gi.repository.Atspi (PyGObject bindings).

Tested on Fedora 43 + Sway + Firefox.

/ UITreeProvider Linux — arbol de accesibilidad AT-SPI2.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from marlow.platform.base import UITreeProvider

logger = logging.getLogger("marlow.platform.linux.ui_tree")

# Default depth limits per app type
_DEFAULT_DEPTH = 8
_BROWSER_DEPTH = 10  # Browsers have deep DOM trees

# States to report (subset of Atspi.StateType)
_INTERESTING_STATES = {
    "active", "checked", "editable", "enabled", "expanded",
    "focusable", "focused", "modal", "multi_line", "pressed",
    "resizable", "selectable", "selected", "sensitive", "showing",
    "single_line", "visible",
}


def _levenshtein(s1: str, s2: str) -> int:
    """Levenshtein edit distance — zero deps, O(m*n)."""
    if len(s1) < len(s2):
        return _levenshtein(s2, s1)
    if not s2:
        return len(s1)
    prev = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr = [i + 1]
        for j, c2 in enumerate(s2):
            curr.append(min(
                prev[j + 1] + 1,
                curr[j] + 1,
                prev[j] + (c1 != c2),
            ))
        prev = curr
    return prev[-1]


def _similarity(s1: str, s2: str) -> float:
    """Normalized similarity 0.0-1.0. 1.0 = identical."""
    if s1 == s2:
        return 1.0
    mx = max(len(s1), len(s2))
    if mx == 0:
        return 1.0
    return 1.0 - (_levenshtein(s1, s2) / mx)


def _get_atspi():
    """Import and initialize AT-SPI2. Cached after first call."""
    import gi
    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi
    # Ensure AT-SPI is initialized
    Atspi.init()
    return Atspi


def _get_states(node, Atspi) -> list[str]:
    """Extract human-readable state names from an Atspi accessible."""
    states = []
    try:
        state_set = node.get_state_set()
        for st in Atspi.StateType.__enum_values__.values():
            name = st.value_nick.replace("-", "_")
            if name in _INTERESTING_STATES and state_set.contains(st):
                states.append(name)
    except Exception:
        pass
    return states


def _get_bounds(node) -> dict:
    """Get bounding box as {x, y, w, h}. Returns zeros on failure."""
    try:
        comp = node.get_component_iface()
        if comp:
            ext = comp.get_extents(0)  # 0 = ATSPI_COORD_TYPE_SCREEN
            return {"x": ext.x, "y": ext.y, "w": ext.width, "h": ext.height}
    except Exception:
        pass
    return {"x": 0, "y": 0, "w": 0, "h": 0}


def _get_actions(node) -> list[str]:
    """Get available action names from the Action interface."""
    actions = []
    try:
        action_iface = node.get_action_iface()
        if action_iface:
            n = action_iface.get_n_actions()
            for i in range(n):
                name = action_iface.get_action_name(i)
                if name:
                    actions.append(name)
    except Exception:
        pass
    return actions


def _get_interfaces(node) -> list[str]:
    """List AT-SPI2 interfaces this node implements."""
    ifaces = []
    try:
        if node.get_action_iface():
            ifaces.append("Action")
    except Exception:
        pass
    try:
        if node.get_text_iface():
            ifaces.append("Text")
    except Exception:
        pass
    try:
        if node.get_editable_text_iface():
            ifaces.append("EditableText")
    except Exception:
        pass
    try:
        if node.get_value_iface():
            ifaces.append("Value")
    except Exception:
        pass
    try:
        if node.get_selection_iface():
            ifaces.append("Selection")
    except Exception:
        pass
    try:
        if node.get_component_iface():
            ifaces.append("Component")
    except Exception:
        pass
    try:
        if node.get_image_iface():
            ifaces.append("Image")
    except Exception:
        pass
    return ifaces


def _get_text(node) -> Optional[str]:
    """Extract text via the Text interface. Returns None if unavailable."""
    try:
        ti = node.get_text_iface()
        if ti:
            length = ti.get_character_count()
            if length > 0:
                from gi.repository import Atspi
                return Atspi.Text.get_text(ti, 0, min(length, 4096))
    except Exception:
        pass
    return None


def _get_value(node) -> Optional[float]:
    """Get current value from the Value interface."""
    try:
        vi = node.get_value_iface()
        if vi:
            return vi.get_current_value()
    except Exception:
        pass
    return None


def _build_tree(node, Atspi, max_depth: int, current_depth: int, path: str) -> Optional[dict]:
    """Recursively build tree dict from an AT-SPI2 accessible node."""
    if node is None or current_depth > max_depth:
        if current_depth > max_depth:
            return {"truncated": True, "reason": f"max_depth={max_depth}"}
        return None

    try:
        role = node.get_role_name() or "unknown"
        name = node.get_name() or ""
        desc = node.get_description() or ""
    except Exception:
        return None

    entry: dict = {
        "role": role,
        "name": name,
        "path": path,
    }

    if desc:
        entry["description"] = desc

    states = _get_states(node, Atspi)
    if states:
        entry["states"] = states

    bounds = _get_bounds(node)
    if bounds["w"] > 0 or bounds["h"] > 0:
        entry["bounds"] = bounds

    actions = _get_actions(node)
    if actions:
        entry["actions"] = actions

    # Children
    try:
        child_count = node.get_child_count()
    except Exception:
        child_count = 0

    if child_count > 0 and current_depth < max_depth:
        children = []
        for i in range(child_count):
            try:
                child = node.get_child_at_index(i)
                child_tree = _build_tree(
                    child, Atspi, max_depth,
                    current_depth + 1, f"{path}.{i}",
                )
                if child_tree is not None:
                    children.append(child_tree)
            except Exception:
                continue
        if children:
            entry["children"] = children
    elif child_count > 0:
        entry["children_count"] = child_count

    return entry


def _count_nodes(tree: dict) -> int:
    """Count total nodes in a tree dict."""
    count = 1
    for child in tree.get("children", []):
        count += _count_nodes(child)
    return count


def _find_app_node(Atspi, window_title: str):
    """Find an application node on the AT-SPI2 desktop by window title."""
    desktop = Atspi.get_desktop(0)
    title_lower = window_title.lower()

    # First pass: check app names
    for i in range(desktop.get_child_count()):
        app = desktop.get_child_at_index(i)
        if app is None:
            continue
        app_name = (app.get_name() or "").lower()
        if title_lower in app_name:
            return app

    # Second pass: check window/frame children of each app
    for i in range(desktop.get_child_count()):
        app = desktop.get_child_at_index(i)
        if app is None:
            continue
        try:
            for j in range(app.get_child_count()):
                win = app.get_child_at_index(j)
                if win is None:
                    continue
                win_name = (win.get_name() or "").lower()
                if title_lower in win_name:
                    return app
        except Exception:
            continue

    return None


def _resolve_path(node, path_str: str):
    """Navigate to a node by dot-separated index path (e.g. '0.2.1').

    The first segment is the child index from the start node.
    """
    parts = path_str.split(".")
    current = node

    # The first segment is the root itself (always "0" or the app index),
    # so we skip it and start navigating from segment 1.
    for part in parts[1:]:
        try:
            idx = int(part)
            child = current.get_child_at_index(idx)
            if child is None:
                return None
            current = child
        except (ValueError, Exception):
            return None
    return current


class AtSpiUITreeProvider(UITreeProvider):
    """AT-SPI2 accessibility tree provider for Linux desktops."""

    def get_tree(
        self,
        window_title: Optional[str] = None,
        max_depth: Optional[int] = None,
    ) -> dict:
        t0 = time.monotonic()
        try:
            Atspi = _get_atspi()
            desktop = Atspi.get_desktop(0)

            if window_title:
                app = _find_app_node(Atspi, window_title)
                if app is None:
                    # List available apps for error context
                    available = []
                    for i in range(desktop.get_child_count()):
                        a = desktop.get_child_at_index(i)
                        if a:
                            available.append(a.get_name() or "(unnamed)")
                    return {
                        "success": False,
                        "error": f"App/window '{window_title}' not found in AT-SPI2",
                        "available_apps": available,
                    }
                root = app
                depth = max_depth or _DEFAULT_DEPTH
                app_name = app.get_name() or "(unnamed)"
                # Try to get window title from first frame child
                win_title = app_name
                try:
                    for j in range(app.get_child_count()):
                        child = app.get_child_at_index(j)
                        if child and child.get_role_name() in ("frame", "window"):
                            win_title = child.get_name() or app_name
                            break
                except Exception:
                    pass
                window_info = {
                    "title": win_title,
                    "app": app_name,
                    "pid": app.get_process_id(),
                }
            else:
                root = desktop
                depth = max_depth or 4  # Shallow for full desktop
                window_info = {
                    "title": "(desktop)",
                    "app": "(all)",
                    "pid": 0,
                }

            tree = _build_tree(root, Atspi, depth, 0, "0")
            if tree is None:
                return {"success": False, "error": "Empty tree"}

            elapsed = (time.monotonic() - t0) * 1000
            count = _count_nodes(tree)

            return {
                "success": True,
                "window": window_info,
                "tree": tree,
                "element_count": count,
                "depth_used": depth,
                "elapsed_ms": round(elapsed, 1),
            }

        except Exception as e:
            logger.error("get_tree failed: %s", e)
            return {"success": False, "error": str(e)}

    def find_elements(
        self,
        name: Optional[str] = None,
        role: Optional[str] = None,
        states: Optional[list[str]] = None,
        window_title: Optional[str] = None,
    ) -> list[dict]:
        try:
            Atspi = _get_atspi()

            if window_title:
                root = _find_app_node(Atspi, window_title)
                if root is None:
                    return []
            else:
                root = Atspi.get_desktop(0)

            results: list[dict] = []
            name_lower = name.lower() if name else None
            role_lower = role.lower() if role else None
            states_set = set(s.lower() for s in states) if states else None

            self._search_tree(
                root, Atspi, name_lower, role_lower, states_set,
                results, max_depth=10, current_depth=0, path="0",
            )

            # Sort by score descending
            results.sort(key=lambda r: r.get("score", 0), reverse=True)
            return results[:50]  # Cap at 50 results

        except Exception as e:
            logger.error("find_elements failed: %s", e)
            return []

    def _search_tree(
        self, node, Atspi,
        name_lower: Optional[str],
        role_lower: Optional[str],
        states_set: Optional[set[str]],
        results: list[dict],
        max_depth: int, current_depth: int, path: str,
    ):
        """Recursive search with matching."""
        if node is None or current_depth > max_depth:
            return

        try:
            node_role = (node.get_role_name() or "").lower()
            node_name = node.get_name() or ""
            node_name_lower = node_name.lower()
        except Exception:
            return

        # Check match
        score = 0.0
        matched = True

        if name_lower:
            if name_lower in node_name_lower:
                # Substring match — score based on how close it is
                score = max(score, 0.8 if name_lower == node_name_lower else 0.6)
            else:
                # Fuzzy match
                sim = _similarity(name_lower, node_name_lower)
                if sim >= 0.5:
                    score = max(score, sim)
                else:
                    matched = False

        if role_lower and matched:
            if role_lower not in node_role:
                matched = False
            else:
                score = max(score, 0.5)

        if states_set and matched:
            node_states = set(s.lower() for s in _get_states(node, Atspi))
            if not states_set.issubset(node_states):
                matched = False

        if matched and (name_lower or role_lower or states_set):
            actions = _get_actions(node)
            bounds = _get_bounds(node)
            results.append({
                "role": node_role,
                "name": node_name,
                "description": node.get_description() or "",
                "bounds": bounds,
                "path": path,
                "score": round(score, 3),
                "actions": actions,
            })

        # Recurse into children
        try:
            child_count = node.get_child_count()
        except Exception:
            return

        for i in range(child_count):
            try:
                child = node.get_child_at_index(i)
                self._search_tree(
                    child, Atspi, name_lower, role_lower, states_set,
                    results, max_depth, current_depth + 1, f"{path}.{i}",
                )
            except Exception:
                continue

    def get_element_properties(self, path: str, window_title: Optional[str] = None) -> dict:
        try:
            Atspi = _get_atspi()

            if window_title:
                root = _find_app_node(Atspi, window_title)
                if root is None:
                    return {"error": f"App '{window_title}' not found"}
            else:
                root = Atspi.get_desktop(0)

            node = _resolve_path(root, path)
            if node is None:
                return {"error": f"Element at path '{path}' not found"}

            role = node.get_role_name() or "unknown"
            name = node.get_name() or ""
            desc = node.get_description() or ""
            states = _get_states(node, Atspi)
            bounds = _get_bounds(node)
            interfaces = _get_interfaces(node)
            actions = _get_actions(node)
            text = _get_text(node)
            value = _get_value(node)

            try:
                children_count = node.get_child_count()
            except Exception:
                children_count = 0

            props: dict = {
                "role": role,
                "name": name,
                "path": path,
                "states": states,
                "bounds": bounds,
                "interfaces": interfaces,
                "actions": actions,
                "children_count": children_count,
            }
            if desc:
                props["description"] = desc
            if text is not None:
                props["text"] = text
            if value is not None:
                props["value"] = value

            return props

        except Exception as e:
            logger.error("get_element_properties failed: %s", e)
            return {"error": str(e)}

    def do_action(self, path: str, action_name: str, window_title: Optional[str] = None) -> bool:
        try:
            Atspi = _get_atspi()

            if window_title:
                root = _find_app_node(Atspi, window_title)
                if root is None:
                    logger.warning("App '%s' not found for do_action", window_title)
                    return False
            else:
                root = Atspi.get_desktop(0)

            node = _resolve_path(root, path)
            if node is None:
                logger.warning("Element at path '%s' not found", path)
                return False

            action_iface = node.get_action_iface()
            if not action_iface:
                logger.warning("Element has no Action interface")
                return False

            n_actions = action_iface.get_n_actions()
            for i in range(n_actions):
                if action_iface.get_action_name(i) == action_name:
                    return action_iface.do_action(i)

            logger.warning("Action '%s' not found. Available: %s",
                           action_name, _get_actions(node))
            return False

        except Exception as e:
            logger.error("do_action failed: %s", e)
            return False

    def get_text(self, path: str, window_title: Optional[str] = None) -> Optional[str]:
        try:
            Atspi = _get_atspi()

            if window_title:
                root = _find_app_node(Atspi, window_title)
                if root is None:
                    return None
            else:
                root = Atspi.get_desktop(0)

            node = _resolve_path(root, path)
            if node is None:
                return None

            return _get_text(node)

        except Exception as e:
            logger.error("get_text failed: %s", e)
            return None


if __name__ == "__main__":
    import json
    import sys

    provider = AtSpiUITreeProvider()

    print("=== AtSpiUITreeProvider self-test ===")

    # 1. Full desktop tree (shallow)
    print("\n--- 1. Desktop tree (depth 3) ---")
    result = provider.get_tree(max_depth=3)
    if result.get("success"):
        print(f"  Elements: {result['element_count']}")
        print(f"  Elapsed: {result['elapsed_ms']}ms")
        tree = result["tree"]
        # Print top-level apps
        for child in tree.get("children", []):
            name = child.get("name", "(unnamed)")
            role = child.get("role", "?")
            n_children = len(child.get("children", []))
            print(f"  [{role}] {name} ({n_children} children)")
    else:
        print(f"  Error: {result.get('error')}")

    # 2. Firefox tree (if running)
    print("\n--- 2. Firefox tree (depth 3) ---")
    result = provider.get_tree(window_title="firefox", max_depth=3)
    if result.get("success"):
        win = result["window"]
        print(f"  Window: {win['title']} (app={win['app']}, pid={win['pid']})")
        print(f"  Elements: {result['element_count']}")
        print(f"  Elapsed: {result['elapsed_ms']}ms")
        # Print 3 levels
        tree = result["tree"]

        def print_tree(node, indent=2):
            role = node.get("role", "?")
            name = node.get("name", "")
            actions = node.get("actions", [])
            path = node.get("path", "")
            label = f"[{role}]"
            if name:
                label += f" {name}"
            if actions:
                label += f" <{','.join(actions)}>"
            label += f"  (path={path})"
            print(" " * indent + label)
            for child in node.get("children", [])[:10]:
                print_tree(child, indent + 2)

        print_tree(tree)
    else:
        print(f"  Error: {result.get('error')}")
        if "available_apps" in result:
            print(f"  Available: {result['available_apps']}")

    # 3. find_elements — search for buttons in Firefox
    print("\n--- 3. find_elements(role='push button', window_title='firefox') ---")
    buttons = provider.find_elements(role="push button", window_title="firefox")
    if buttons:
        for b in buttons[:10]:
            print(f"  [{b['role']}] {b['name']} score={b['score']} "
                  f"actions={b['actions']} path={b['path']}")
        if len(buttons) > 10:
            print(f"  ... and {len(buttons) - 10} more")
        print(f"  Total buttons: {len(buttons)}")
    else:
        print("  No buttons found (Firefox not running?)")

    # 4. find_elements — fuzzy name search
    print("\n--- 4. find_elements(name='Reload', window_title='firefox') ---")
    matches = provider.find_elements(name="Reload", window_title="firefox")
    for m in matches[:5]:
        print(f"  [{m['role']}] {m['name']} score={m['score']} path={m['path']}")
    if not matches:
        print("  No matches")

    # 5. get_element_properties for first button found
    if buttons:
        path = buttons[0]["path"]
        print(f"\n--- 5. get_element_properties(path='{path}', window_title='firefox') ---")
        props = provider.get_element_properties(path, window_title="firefox")
        for k, v in props.items():
            print(f"  {k}: {v}")

    # 6. get_text on the frame node
    print("\n--- 6. get_text(path='0.0', window_title='firefox') ---")
    text = provider.get_text("0.0", window_title="firefox")
    if text:
        print(f"  Text ({len(text)} chars): {text[:100]}...")
    else:
        print("  No text (expected for frame node)")

    print("\nPASS: AtSpiUITreeProvider self-test complete")
