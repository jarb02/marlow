"""
Marlow Smart Escalation Engine

Finds UI elements using a tiered approach:
  Step 1: UI Automation API — 0 tokens, ~10-50ms
  Step 2: OCR (Tesseract) — 0 tokens, ~200-500ms
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

    # ── Step 1: UI Automation Tree (0 tokens, ~10-50ms) ──
    step_start = time.perf_counter()
    uia_result = await _try_uia(target_lower, window_title)
    elapsed = round((time.perf_counter() - step_start) * 1000, 1)

    methods_tried.append({
        "method": "ui_automation",
        "success": uia_result["found"],
        "time_ms": elapsed,
    })

    if uia_result["found"]:
        result = {
            "success": True,
            "found": True,
            "method": "ui_automation",
            "element": uia_result.get("element_info"),
            "methods_tried": methods_tried,
            "tokens_cost": 0,
        }
        if click_if_found and uia_result.get("element_ref"):
            click_result = await _click_element(uia_result["element_ref"])
            result["clicked"] = click_result
        return result

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
        result = {
            "success": True,
            "found": True,
            "method": "ocr",
            "element": ocr_result.get("match"),
            "methods_tried": methods_tried,
            "tokens_cost": 0,
        }
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
    """Search for target in the UI Automation tree."""
    try:
        from marlow.tools.ui_tree import get_ui_tree

        tree = await get_ui_tree(
            window_title=window_title,
            max_depth=5,
            include_invisible=False,
        )

        if "error" in tree:
            return {"found": False, "error": tree["error"]}

        # Search the tree for target text
        match = _search_tree(tree.get("elements", {}), target)
        if match:
            # Try to get a reference to the actual UIA element for clicking
            element_ref = await _get_uia_element_ref(target, window_title)
            return {
                "found": True,
                "element_info": match,
                "element_ref": element_ref,
            }

        return {"found": False}

    except Exception as e:
        logger.debug(f"UIA search error: {e}")
        return {"found": False, "error": str(e)}


def _search_tree(node: dict, target: str) -> Optional[dict]:
    """Recursively search tree dict for target text."""
    if not isinstance(node, dict):
        return None

    # Check name and automation_id
    name = (node.get("name") or "").lower()
    auto_id = (node.get("automation_id") or "").lower()

    if target in name or target in auto_id:
        return {
            "name": node.get("name"),
            "control_type": node.get("control_type"),
            "automation_id": node.get("automation_id"),
        }

    # Search children
    for child in node.get("children", []):
        result = _search_tree(child, target)
        if result:
            return result

    return None


async def _get_uia_element_ref(target: str, window_title: Optional[str]):
    """Get a live pywinauto element reference for clicking."""
    try:
        from pywinauto import Desktop

        desktop = Desktop(backend="uia")
        if window_title:
            windows = desktop.windows(title_re=f".*{window_title}.*")
            if not windows:
                return None
            win = windows[0]
        else:
            win = desktop.window(active_only=True)

        from marlow.tools.mouse import _find_element
        return _find_element(win, target, max_depth=5)

    except Exception:
        return None


async def _click_element(element) -> dict:
    """Click a UIA element using silent invoke first."""
    try:
        element.invoke()
        return {"success": True, "method": "invoke (silent)"}
    except Exception:
        pass

    try:
        from marlow.core.focus import preserve_focus
        with preserve_focus():
            element.click_input()
        return {"success": True, "method": "click_input (focus restored)"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def _try_ocr(target: str, window_title: Optional[str]) -> dict:
    """Search for target text using OCR."""
    try:
        from marlow.tools.ocr import ocr_region

        result = await ocr_region(window_title=window_title, preprocess=True)

        if "error" in result:
            # Tesseract not installed — skip gracefully
            return {"found": False, "skipped": True, "reason": result["error"]}

        # Search OCR words for target
        for word in result.get("words", []):
            if target in word["text"].lower():
                bbox = word["bbox"]
                # Click center of the bounding box
                click_x = bbox["x"] + bbox["width"] // 2
                click_y = bbox["y"] + bbox["height"] // 2
                # Note: bbox coords are relative to the screenshot, which
                # corresponds to the window position. For window screenshots
                # we need to add the window offset.
                return {
                    "found": True,
                    "match": {
                        "text": word["text"],
                        "confidence": word["confidence"],
                        "bbox": bbox,
                    },
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
