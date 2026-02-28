"""
Marlow UIA Utilities

Shared helpers for finding windows and elements via UI Automation.
Centralizes the window-finding pattern used across all tool modules.

Includes multi-property fuzzy search for robust element discovery:
  name → automation_id → help_text → class_name

/ Utilidades compartidas para encontrar ventanas y elementos via UIA.
/ Incluye busqueda fuzzy multi-propiedad para descubrimiento robusto.
"""

import re
import logging
from typing import Optional

logger = logging.getLogger("marlow.core.uia_utils")


# ── Levenshtein distance (basic, zero deps) ──

def _levenshtein(s1: str, s2: str) -> int:
    """
    Compute Levenshtein edit distance between two strings.
    Wagner-Fischer algorithm, O(m*n) time and O(min(m,n)) space.

    / Distancia de edicion Levenshtein entre dos strings.
    """
    if len(s1) < len(s2):
        return _levenshtein(s2, s1)

    if not s2:
        return len(s1)

    prev = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr = [i + 1]
        for j, c2 in enumerate(s2):
            # Insert, delete, or substitute
            curr.append(min(
                prev[j + 1] + 1,      # deletion
                curr[j] + 1,           # insertion
                prev[j] + (c1 != c2),  # substitution
            ))
        prev = curr

    return prev[-1]


def _similarity(s1: str, s2: str) -> float:
    """
    Normalized similarity score between 0.0 and 1.0.
    1.0 = identical, 0.0 = completely different.

    / Puntaje de similitud normalizado entre 0.0 y 1.0.
    """
    if s1 == s2:
        return 1.0
    max_len = max(len(s1), len(s2))
    if max_len == 0:
        return 1.0
    return 1.0 - (_levenshtein(s1, s2) / max_len)


# ── Window finding ──

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


# ── Element finding ──

# Property thresholds for fuzzy matching
# / Thresholds de similitud por propiedad
_THRESHOLDS = {
    "name": 0.7,
    "automation_id": 0.6,
    "help_text": 0.6,
    "class_name": 0.6,
}


def _get_element_bbox(element) -> Optional[dict]:
    """
    Get bounding box of a UIA element, or None if unavailable.

    / Obtener bounding box del elemento UIA.
    """
    try:
        rect = element.rectangle()
        return {
            "x": rect.left,
            "y": rect.top,
            "width": rect.width(),
            "height": rect.height(),
        }
    except Exception:
        return None


def _match_element(element, query_lower: str) -> Optional[dict]:
    """
    Check a single element against the query across multiple properties.
    Returns match info dict if any property meets its threshold, else None.

    / Evalua un elemento contra la query en multiples propiedades.
    """
    best_score = 0.0
    best_prop = None

    # Gather properties
    # / Recopilar propiedades del elemento
    name = (element.window_text() or "").strip()
    info = element.element_info
    auto_id = (getattr(info, "automation_id", "") or "").strip()
    help_text = (getattr(info, "help_text", "") or "").strip()
    class_name = (getattr(info, "class_name", "") or "").strip()
    control_type = (getattr(info, "control_type", "") or "").strip()

    props = {
        "name": name,
        "automation_id": auto_id,
        "help_text": help_text,
        "class_name": class_name,
    }

    for prop_name, prop_value in props.items():
        if not prop_value:
            continue

        prop_lower = prop_value.lower()
        threshold = _THRESHOLDS[prop_name]

        # Exact match → score 1.0 immediately
        if prop_lower == query_lower:
            return {
                "element": element,
                "property_matched": prop_name,
                "score": 1.0,
                "name": name,
                "automation_id": auto_id,
                "control_type": control_type,
                "bbox": _get_element_bbox(element),
            }

        # Whole-word containment → high score
        padded = f" {prop_lower} "
        if f" {query_lower} " in padded:
            score = 0.95
            if score > best_score and score >= threshold:
                best_score = score
                best_prop = prop_name
            continue

        # Starts-with match (e.g., "Save" matches "Save As...")
        if prop_lower.startswith(query_lower):
            score = 0.9
            if score > best_score and score >= threshold:
                best_score = score
                best_prop = prop_name
            continue

        # Fuzzy similarity
        score = _similarity(query_lower, prop_lower)
        if score >= threshold and score > best_score:
            best_score = score
            best_prop = prop_name

    if best_prop is not None:
        return {
            "element": element,
            "property_matched": best_prop,
            "score": round(best_score, 3),
            "name": name,
            "automation_id": auto_id,
            "control_type": control_type,
            "bbox": _get_element_bbox(element),
        }

    return None


def find_element_enhanced(
    parent: object,
    query: str,
    control_type: Optional[str] = None,
    max_depth: int = 5,
    max_results: int = 5,
) -> list[dict]:
    """
    Multi-property fuzzy search for UI elements.

    Searches: name → automation_id → help_text → class_name.
    Returns ranked candidates by similarity score.

    Args:
        parent: pywinauto window/element to search within.
        query: Text to search for (e.g., "Save", "btnSubmit", "Edit").
        control_type: Filter by control type (e.g., "Button", "Edit", "MenuItem").
        max_depth: Maximum tree depth to search.
        max_results: Maximum candidates to return.

    Returns:
        List of match dicts sorted by score (highest first):
        [{element, property_matched, score, name, automation_id, control_type, bbox}]

    / Busqueda fuzzy multi-propiedad para elementos UI.
    / Retorna candidatos rankeados por puntaje de similitud.
    """
    query_lower = query.lower().strip()
    if not query_lower:
        return []

    ct_lower = control_type.lower() if control_type else None
    candidates: list[dict] = []

    def _walk(element, depth: int) -> bool:
        """Walk tree. Returns True if perfect match found and we should stop."""
        if depth > max_depth:
            return False

        try:
            # Filter by control_type if specified
            # / Filtrar por control_type si se especifica
            if ct_lower:
                elem_ct = (getattr(element.element_info, "control_type", "") or "").lower()
                if elem_ct and elem_ct != ct_lower:
                    # Still search children — control_type filter only skips this element
                    pass
                else:
                    match = _match_element(element, query_lower)
                    if match:
                        if match["score"] == 1.0:
                            candidates.append(match)
                            return True  # Perfect match — stop early
                        candidates.append(match)
            else:
                match = _match_element(element, query_lower)
                if match:
                    if match["score"] == 1.0:
                        candidates.append(match)
                        return True  # Perfect match — stop early
                    candidates.append(match)

            # Search children
            for child in element.children():
                if _walk(child, depth + 1):
                    return True  # Propagate early stop

        except Exception:
            pass

        return False

    _walk(parent, 0)

    # Sort by score descending, take top N
    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates[:max_results]


def find_element_by_name(
    parent: object,
    name: str,
    max_depth: int = 5,
    depth: int = 0,
) -> Optional[object]:
    """
    Recursively search for an element by name in the UI tree.

    Compatibility wrapper around find_element_enhanced.
    Returns the first match (highest score) or None.

    / Busca recursivamente un elemento por nombre en el arbol UI.
    / Wrapper de compatibilidad — usa find_element_enhanced internamente.
    """
    # Only call enhanced search from the top level (depth == 0)
    # to avoid re-searching the same subtree at each recursion level
    if depth != 0:
        return _find_element_by_name_legacy(parent, name, max_depth, depth)

    results = find_element_enhanced(parent, name, max_depth=max_depth, max_results=1)
    if results:
        return results[0]["element"]
    return None


def _find_element_by_name_legacy(
    parent: object,
    name: str,
    max_depth: int = 5,
    depth: int = 0,
) -> Optional[object]:
    """
    Original recursive search kept for internal depth>0 calls.
    Should not be called externally — use find_element_by_name instead.
    """
    if depth > max_depth:
        return None

    try:
        text = parent.window_text() or ""
        name_lower = name.lower()
        text_lower = text.lower()

        if (
            text_lower == name_lower
            or text_lower.startswith(name_lower + " ")
            or text_lower.endswith(" " + name_lower)
            or (" " + name_lower + " ") in text_lower
        ):
            return parent

        auto_id = getattr(parent.element_info, "automation_id", "") or ""
        if name_lower == auto_id.lower():
            return parent

        for child in parent.children():
            found = _find_element_by_name_legacy(child, name, max_depth, depth + 1)
            if found is not None:
                return found

    except Exception:
        pass

    return None
