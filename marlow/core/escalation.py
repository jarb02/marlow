"""
Marlow Smart Escalation Engine

Finds UI elements using a tiered approach:
  Step 1: UI Automation API — 0 tokens, ~10-50ms
  Step 2: OCR (Windows OCR / Tesseract) — 0 tokens, ~50-500ms
  Step 3: Screenshot + LLM Vision — ~1,500 tokens (last resort)

Each step is tried in order. If a cheaper method succeeds, the
expensive ones are never called.
"""

import time
import logging
from typing import Optional

logger = logging.getLogger("marlow.core.escalation")


async def smart_find(
    target: str,
    window_title: Optional[str] = None,
    click_if_found: bool = False,
) -> dict:
    """
    Find a UI element using escalating methods.

    Tries UIA tree first (free), then OCR (free), then screenshot (costly).
    Optionally clicks the element if found via UIA or OCR.

    Args:
        target: Text/name of the element to find (e.g., "File", "Save", "Submit").
        window_title: Window to search in. If None, uses active window.
        click_if_found: If True and element is found via UIA/OCR, click it.

    Returns:
        Dictionary with found status, method used, methods_tried list,
        and optionally screenshot data for LLM Vision.

    / Encuentra un elemento UI usando métodos escalonados.
    / Intenta UIA tree primero (gratis), luego OCR (gratis), luego screenshot (costoso).
    """
    methods_tried = []
    target_lower = target.lower()

    # Consult error journal: does UIA fail on this app?
    from marlow.core.error_journal import _journal
    best = _journal.get_best_method("smart_find", window_title)
    skip_uia = best == "ocr"

    # ── Step 1: UI Automation Tree (0 tokens, ~10-50ms) ──
    if not skip_uia:
        step_start = time.perf_counter()
        uia_result = await _try_uia(target_lower, window_title)
        elapsed = round((time.perf_counter() - step_start) * 1000, 1)

        methods_tried.append({
            "method": "ui_automation",
            "success": uia_result["found"],
            "time_ms": elapsed,
        })

        if uia_result["found"]:
            _journal.record_success("smart_find", window_title, "ui_automation")
            result = {
                "success": True,
                "found": True,
                "method": "ui_automation",
                "element": uia_result.get("element_info"),
                "methods_tried": methods_tried,
                "tokens_cost": 0,
            }
            if uia_result.get("partial_matches"):
                result["partial_matches"] = uia_result["partial_matches"]
                result["hint"] = uia_result.get("hint")
            if click_if_found and uia_result.get("element_ref"):
                click_result = await _click_element(uia_result["element_ref"])
                result["clicked"] = click_result
            return result

        # UIA failed — record it
        _journal.record_failure("smart_find", window_title, "ui_automation",
                                f"Element '{target}' not found via UIA")
    else:
        methods_tried.append({
            "method": "ui_automation",
            "skipped": True,
            "reason": "journal_says_uia_fails_on_this_app",
        })
        logger.debug(f"Journal says UIA fails on '{window_title}', starting at OCR")

    # ── Step 2: OCR (0 tokens, ~200-500ms) ──
    step_start = time.perf_counter()
    ocr_result = await _try_ocr(target_lower, window_title)
    elapsed = round((time.perf_counter() - step_start) * 1000, 1)

    methods_tried.append({
        "method": "ocr",
        "success": ocr_result["found"],
        "time_ms": elapsed,
        "skipped": ocr_result.get("skipped", False),
    })

    if ocr_result["found"]:
        _journal.record_success("smart_find", window_title, "ocr")
        result = {
            "success": True,
            "found": True,
            "method": "ocr",
            "element": ocr_result.get("match"),
            "methods_tried": methods_tried,
            "tokens_cost": 0,
        }
        if skip_uia:
            result["journal_hint"] = "Skipped UIA — journal knows it fails on this app"
        if click_if_found and ocr_result.get("click_coords"):
            coords = ocr_result["click_coords"]
            from marlow.tools.mouse import click
            click_res = await click(x=coords["x"], y=coords["y"])
            result["clicked"] = click_res
        return result

    # ── Step 3: Screenshot fallback (~1,500 tokens) ──
    step_start = time.perf_counter()
    screenshot_result = await _try_screenshot(window_title)
    elapsed = round((time.perf_counter() - step_start) * 1000, 1)

    methods_tried.append({
        "method": "screenshot",
        "success": screenshot_result.get("image_base64") is not None,
        "time_ms": elapsed,
    })

    if "error" in screenshot_result:
        return {
            "success": False,
            "found": False,
            "error": screenshot_result["error"],
            "methods_tried": methods_tried,
        }

    return {
        "success": True,
        "found": False,
        "method": "screenshot",
        "requires_vision": True,
        "image_base64": screenshot_result.get("image_base64"),
        "image_width": screenshot_result.get("width"),
        "image_height": screenshot_result.get("height"),
        "hint": f"UIA and OCR couldn't find '{target}'. Showing screenshot for LLM Vision.",
        "methods_tried": methods_tried,
        "tokens_cost": 1500,
    }


async def _try_uia(target: str, window_title: Optional[str]) -> dict:
    """
    Search for target in the UI Automation tree using fuzzy multi-property search.

    / Busca el target en el arbol UIA con busqueda fuzzy multi-propiedad.
    """
    try:
        from pywinauto import Desktop
        from marlow.core.uia_utils import find_window, find_element_enhanced

        if window_title:
            win, err = find_window(window_title, list_available=False)
            if err:
                return {"found": False, "error": err.get("error", "Window not found")}
        else:
            desktop = Desktop(backend="uia")
            win = desktop.window(active_only=True)

        candidates = find_element_enhanced(win, target, max_depth=5, max_results=5)

        if not candidates:
            return {"found": False}

        best = candidates[0]

        if best["score"] > 0.8:
            # Strong match — use directly
            # / Match fuerte — usar directamente
            element_info = {
                "name": best["name"],
                "control_type": best["control_type"],
                "automation_id": best["automation_id"],
                "property_matched": best["property_matched"],
                "score": best["score"],
                "bbox": best["bbox"],
            }
            return {
                "found": True,
                "element_info": element_info,
                "element_ref": best["element"],
            }

        if best["score"] >= 0.6:
            # Partial matches — include for LLM to decide
            # / Matches parciales — incluir para que el LLM decida
            partial_matches = []
            for c in candidates:
                partial_matches.append({
                    "name": c["name"],
                    "control_type": c["control_type"],
                    "automation_id": c["automation_id"],
                    "property_matched": c["property_matched"],
                    "score": c["score"],
                    "bbox": c["bbox"],
                })
            return {
                "found": True,
                "element_info": partial_matches[0],
                "element_ref": best["element"],
                "partial_matches": partial_matches,
                "hint": f"Best match score {best['score']} — partial matches included for review.",
            }

        return {"found": False}

    except Exception as e:
        logger.debug(f"UIA search error: {e}")
        return {"found": False, "error": str(e)}


async def _click_element(element) -> dict:
    """Click a UIA element using silent invoke first."""
    try:
        element.invoke()
        return {"success": True, "method": "invoke (silent)"}
    except Exception:
        pass

    try:
        element.click_input()
        return {"success": True, "method": "click_input"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def find_elements(
    query: str,
    window_title: Optional[str] = None,
    control_type: Optional[str] = None,
) -> dict:
    """
    Multi-property fuzzy search for UI elements.

    Searches name, automation_id, help_text, and class_name using
    Levenshtein distance for fuzzy matching. Returns top 5 ranked candidates.

    Args:
        query: Text to search for (e.g., "Save", "btnSubmit", "Edit field").
        window_title: Window to search in. If None, uses active window.
        control_type: Filter by type (e.g., "Button", "Edit", "MenuItem").

    Returns:
        Dictionary with candidates list, each containing:
        name, automation_id, control_type, property_matched, score, bbox.

    / Busqueda fuzzy multi-propiedad para elementos UI.
    / Retorna top 5 candidatos rankeados por score de similitud.
    """
    try:
        from pywinauto import Desktop
        from marlow.core.uia_utils import find_window, find_element_enhanced

        if window_title:
            win, err = find_window(window_title, list_available=True)
            if err:
                return err
        else:
            desktop = Desktop(backend="uia")
            win = desktop.window(active_only=True)

        candidates = find_element_enhanced(
            win, query, control_type=control_type, max_depth=5, max_results=5,
        )

        results = []
        for c in candidates:
            results.append({
                "name": c["name"],
                "automation_id": c["automation_id"],
                "control_type": c["control_type"],
                "property_matched": c["property_matched"],
                "score": c["score"],
                "bbox": c["bbox"],
            })

        return {
            "success": True,
            "query": query,
            "control_type_filter": control_type,
            "candidates": results,
            "count": len(results),
            "window": window_title or "(active window)",
        }

    except Exception as e:
        return {"error": str(e)}


async def _try_ocr(target: str, window_title: Optional[str]) -> dict:
    """Search for target text using OCR (Windows OCR primary, Tesseract fallback)."""
    try:
        from marlow.tools.ocr import ocr_region

        result = await ocr_region(window_title=window_title)

        if "error" in result:
            return {"found": False, "skipped": True, "reason": result["error"]}

        # Search OCR words for target
        # Words have flat bbox: {text, x, y, width, height} (both engines)
        for word in result.get("words", []):
            if target in word["text"].lower():
                # Click center of the bounding box
                # / Click al centro del bounding box
                click_x = word["x"] + word["width"] // 2
                click_y = word["y"] + word["height"] // 2
                # Note: bbox coords are relative to the screenshot, which
                # corresponds to the window position. For window screenshots
                # we need to add the window offset.
                match_info = {
                    "text": word["text"],
                    "x": word["x"],
                    "y": word["y"],
                    "width": word["width"],
                    "height": word["height"],
                }
                if "confidence" in word:
                    match_info["confidence"] = word["confidence"]
                return {
                    "found": True,
                    "match": match_info,
                    "click_coords": {"x": click_x, "y": click_y},
                }

        # Also check full text
        full_text = result.get("text", "")
        if target in full_text.lower():
            return {
                "found": True,
                "match": {"text": target, "in_full_text": True},
                "click_coords": None,
            }

        return {"found": False}

    except Exception as e:
        logger.debug(f"OCR search error: {e}")
        return {"found": False, "skipped": True, "reason": str(e)}


async def _try_screenshot(window_title: Optional[str]) -> dict:
    """Take a screenshot as the final fallback for LLM Vision."""
    try:
        from marlow.tools.screenshot import take_screenshot
        return await take_screenshot(window_title=window_title, quality=85)
    except Exception as e:
        return {"error": str(e)}
