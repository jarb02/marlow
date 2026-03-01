"""
Marlow Cascade Recovery — Multi-step recovery when smart_find fails.

5-step pipeline that progressively tries harder strategies to find a UI element:
  Step 1: Wait & retry (app may be loading)
  Step 2: Check for blocking dialogs
  Step 3: Wide fuzzy search (lower thresholds)
  Step 4: OCR text search with bounding boxes
  Step 5: Screenshot for LLM vision

Each step records its result in the Error Journal for future optimization.

/ Pipeline de recuperacion en 5 pasos cuando smart_find no encuentra un elemento.
  Cada paso intenta una estrategia mas agresiva. Registra resultados en Error Journal.
"""

import asyncio
import logging
import time
from typing import Optional

logger = logging.getLogger("marlow.core.cascade_recovery")


async def cascade_find(
    target: str,
    window_title: Optional[str] = None,
    timeout: float = 10.0,
) -> dict:
    """
    Multi-step recovery pipeline for finding UI elements.

    Tries progressively harder strategies within the given timeout:
      Step 1 (0-2s):  Wait & retry smart_find (app may be loading)
      Step 2 (2-4s):  Check for blocking dialogs
      Step 3 (4-6s):  Wide fuzzy search (threshold 0.4)
      Step 4 (6-8s):  OCR text search with bounding boxes
      Step 5 (8-10s): Screenshot for LLM vision

    Args:
        target: Text/name of the element to find.
        window_title: Window to search in. If None, uses active window.
        timeout: Maximum time in seconds (clamped to 5-30s).

    Returns:
        {found, method, element_info, attempts, ...}

    / Pipeline de recuperacion multi-paso para encontrar elementos UI.
    """
    timeout = max(5.0, min(timeout, 30.0))
    start = time.perf_counter()
    attempts: list[dict] = []

    from marlow.core.error_journal import _journal

    def _elapsed() -> float:
        return time.perf_counter() - start

    def _remaining() -> float:
        return timeout - _elapsed()

    # ── Step 1: Wait & retry (app may still be loading) ──
    step_start = time.perf_counter()
    logger.debug(f"Cascade step 1: wait & retry for '{target}'")

    await asyncio.sleep(min(1.5, _remaining()))

    if _remaining() <= 0:
        attempts.append({"step": 1, "method": "wait_retry", "result": "timeout"})
        return _build_result(False, None, None, attempts, target, _elapsed())

    retry_result = await _retry_smart_find(target, window_title)
    step_time = round((time.perf_counter() - step_start) * 1000, 1)
    attempts.append({
        "step": 1,
        "method": "wait_retry",
        "time_ms": step_time,
        "result": "found" if retry_result.get("found") else "not_found",
    })

    if retry_result.get("found"):
        _journal.record_success("cascade_find", window_title, "wait_retry")
        return _build_result(
            True, "wait_retry", retry_result.get("element_info"), attempts,
            target, _elapsed(),
        )

    # ── Step 2: Check for blocking dialogs ──
    if _remaining() <= 0:
        return _build_result(False, None, None, attempts, target, _elapsed())

    step_start = time.perf_counter()
    logger.debug(f"Cascade step 2: check dialogs")

    dialog_result = await _check_dialogs()
    step_time = round((time.perf_counter() - step_start) * 1000, 1)

    if dialog_result.get("dialog_found"):
        attempts.append({
            "step": 2,
            "method": "dialog_check",
            "time_ms": step_time,
            "result": "dialog_blocking",
            "dialog": dialog_result.get("dialog_info"),
        })
        # Report the dialog — the LLM should handle it
        return _build_result(
            False, "dialog_blocking", None, attempts, target, _elapsed(),
            dialog_info=dialog_result.get("dialog_info"),
        )
    else:
        attempts.append({
            "step": 2,
            "method": "dialog_check",
            "time_ms": step_time,
            "result": "no_dialog",
        })

    # ── Step 3: Wide fuzzy search (lower thresholds) ──
    if _remaining() <= 0:
        return _build_result(False, None, None, attempts, target, _elapsed())

    step_start = time.perf_counter()
    logger.debug(f"Cascade step 3: wide fuzzy search for '{target}'")

    fuzzy_result = await _wide_fuzzy_search(target, window_title)
    step_time = round((time.perf_counter() - step_start) * 1000, 1)

    if fuzzy_result.get("candidates"):
        candidates = fuzzy_result["candidates"]
        attempts.append({
            "step": 3,
            "method": "fuzzy_wide",
            "time_ms": step_time,
            "result": "partial_matches",
            "candidate_count": len(candidates),
        })
        _journal.record_success("cascade_find", window_title, "fuzzy_wide")
        return _build_result(
            True, "fuzzy_wide", candidates[0], attempts, target, _elapsed(),
            partial_matches=candidates,
            hint=f"Exact match not found. {len(candidates)} similar element(s) found with relaxed matching.",
        )
    else:
        attempts.append({
            "step": 3,
            "method": "fuzzy_wide",
            "time_ms": step_time,
            "result": "not_found",
        })

    # ── Step 4: OCR text search ──
    if _remaining() <= 0:
        return _build_result(False, None, None, attempts, target, _elapsed())

    step_start = time.perf_counter()
    logger.debug(f"Cascade step 4: OCR search for '{target}'")

    ocr_result = await _ocr_search(target, window_title)
    step_time = round((time.perf_counter() - step_start) * 1000, 1)

    if ocr_result.get("found"):
        attempts.append({
            "step": 4,
            "method": "ocr",
            "time_ms": step_time,
            "result": "found",
        })
        _journal.record_success("cascade_find", window_title, "ocr")
        return _build_result(
            True, "ocr", ocr_result.get("match"), attempts, target, _elapsed(),
            click_coords=ocr_result.get("click_coords"),
        )
    else:
        attempts.append({
            "step": 4,
            "method": "ocr",
            "time_ms": step_time,
            "result": ocr_result.get("reason", "not_found"),
        })

    # ── Step 5: Screenshot for LLM ──
    if _remaining() <= 0:
        return _build_result(False, None, None, attempts, target, _elapsed())

    step_start = time.perf_counter()
    logger.debug(f"Cascade step 5: screenshot fallback")

    screenshot_result = await _take_screenshot(window_title)
    step_time = round((time.perf_counter() - step_start) * 1000, 1)

    if screenshot_result.get("image_base64"):
        attempts.append({
            "step": 5,
            "method": "screenshot",
            "time_ms": step_time,
            "result": "captured",
        })
        return _build_result(
            False, "screenshot", None, attempts, target, _elapsed(),
            requires_vision=True,
            image_base64=screenshot_result.get("image_base64"),
            image_width=screenshot_result.get("width"),
            image_height=screenshot_result.get("height"),
            hint=f"Could not find '{target}' via UIA, fuzzy search, or OCR. "
                 f"Screenshot provided for visual inspection.",
        )
    else:
        attempts.append({
            "step": 5,
            "method": "screenshot",
            "time_ms": step_time,
            "result": "failed",
        })

    # All steps exhausted
    _journal.record_failure(
        "cascade_find", window_title, "all_steps",
        f"All 5 cascade steps failed to find '{target}'",
    )
    return _build_result(False, None, None, attempts, target, _elapsed())


# ─────────────────────────────────────────────────────────────
# Step implementations
# ─────────────────────────────────────────────────────────────

async def _retry_smart_find(target: str, window_title: Optional[str]) -> dict:
    """
    Step 1: Retry the UIA + OCR search (app may have finished loading).

    / Paso 1: Reintentar busqueda UIA + OCR (la app puede haber terminado de cargar).
    """
    try:
        from marlow.core.uia_utils import find_window, find_element_enhanced

        if window_title:
            win, err = find_window(window_title, list_available=False)
            if err:
                return {"found": False}
        else:
            from pywinauto import Desktop
            desktop = Desktop(backend="uia")
            try:
                win = desktop.window(active_only=True)
            except Exception:
                return {"found": False}

        candidates = find_element_enhanced(win, target, max_depth=5, max_results=1)
        if candidates and candidates[0]["score"] > 0.8:
            c = candidates[0]
            return {
                "found": True,
                "element_info": {
                    "name": c["name"],
                    "control_type": c["control_type"],
                    "automation_id": c["automation_id"],
                    "score": c["score"],
                    "bbox": c["bbox"],
                },
            }
        return {"found": False}

    except Exception as e:
        logger.debug(f"Retry smart_find error: {e}")
        return {"found": False}


async def _check_dialogs() -> dict:
    """
    Step 2: Check if there's a dialog blocking the target window.

    / Paso 2: Verificar si hay un dialogo bloqueando la ventana objetivo.
    """
    try:
        from marlow.core.dialog_handler import handle_dialog
        result = await handle_dialog(action="report")

        dialogs = result.get("dialogs", [])
        if dialogs:
            # Return info about the first blocking dialog
            d = dialogs[0]
            return {
                "dialog_found": True,
                "dialog_info": {
                    "window_title": d.get("window_title"),
                    "dialog_type": d.get("dialog_type"),
                    "texts": d.get("texts", []),
                    "button_names": d.get("button_names", []),
                    "suggested_action": d.get("suggested_action"),
                },
            }
        return {"dialog_found": False}

    except Exception as e:
        logger.debug(f"Dialog check error: {e}")
        return {"dialog_found": False}


async def _wide_fuzzy_search(target: str, window_title: Optional[str]) -> dict:
    """
    Step 3: Fuzzy search with lower thresholds (0.4 instead of 0.6).
    Searches deeper (max_depth=8) and returns more results.

    / Paso 3: Busqueda fuzzy con thresholds mas bajos (0.4 en vez de 0.6).
    """
    try:
        from marlow.core.uia_utils import find_window, _match_element, _get_element_bbox

        if window_title:
            win, err = find_window(window_title, list_available=False)
            if err:
                return {"candidates": []}
        else:
            from pywinauto import Desktop
            desktop = Desktop(backend="uia")
            try:
                win = desktop.window(active_only=True)
            except Exception:
                return {"candidates": []}

        # Walk tree with low threshold
        target_lower = target.lower().strip()
        candidates: list[dict] = []

        def _walk(element, depth: int) -> None:
            if depth > 8:
                return
            try:
                # Manual low-threshold matching
                name = (element.window_text() or "").strip()
                info = element.element_info
                auto_id = (getattr(info, "automation_id", "") or "").strip()
                control_type = (getattr(info, "control_type", "") or "").strip()

                # Check name similarity with low threshold
                from marlow.core.uia_utils import _similarity
                for prop_name, prop_value in [("name", name), ("automation_id", auto_id)]:
                    if not prop_value:
                        continue
                    score = _similarity(target_lower, prop_value.lower())
                    if score >= 0.4:  # Low threshold
                        bbox = _get_element_bbox(element)
                        candidates.append({
                            "name": name,
                            "automation_id": auto_id,
                            "control_type": control_type,
                            "property_matched": prop_name,
                            "score": round(score, 3),
                            "bbox": bbox,
                        })
                        return  # Don't double-count

                for child in element.children():
                    _walk(child, depth + 1)
            except Exception:
                pass

        _walk(win, 0)

        # Sort by score descending
        candidates.sort(key=lambda c: c["score"], reverse=True)
        return {"candidates": candidates[:5]}

    except Exception as e:
        logger.debug(f"Wide fuzzy search error: {e}")
        return {"candidates": []}


async def _ocr_search(target: str, window_title: Optional[str]) -> dict:
    """
    Step 4: Search for target text using OCR with bounding boxes.

    / Paso 4: Buscar el texto objetivo usando OCR con bounding boxes.
    """
    try:
        from marlow.tools.ocr import ocr_region

        result = await ocr_region(window_title=window_title)

        if "error" in result:
            return {"found": False, "reason": result["error"]}

        target_lower = target.lower()
        for word in result.get("words", []):
            if target_lower in word["text"].lower():
                click_x = word["x"] + word["width"] // 2
                click_y = word["y"] + word["height"] // 2
                return {
                    "found": True,
                    "match": {
                        "text": word["text"],
                        "x": word["x"],
                        "y": word["y"],
                        "width": word["width"],
                        "height": word["height"],
                        "method": "ocr",
                    },
                    "click_coords": {"x": click_x, "y": click_y},
                }

        # Check full text
        full_text = result.get("text", "")
        if target_lower in full_text.lower():
            return {
                "found": True,
                "match": {"text": target, "in_full_text": True, "method": "ocr"},
                "click_coords": None,
            }

        return {"found": False, "reason": "not_in_ocr_text"}

    except Exception as e:
        logger.debug(f"OCR search error: {e}")
        return {"found": False, "reason": str(e)}


async def _take_screenshot(window_title: Optional[str]) -> dict:
    """
    Step 5: Take screenshot for LLM vision fallback.

    / Paso 5: Tomar screenshot como fallback para vision LLM.
    """
    try:
        from marlow.tools.screenshot import take_screenshot
        return await take_screenshot(window_title=window_title, quality=85)
    except Exception as e:
        logger.debug(f"Screenshot error: {e}")
        return {"error": str(e)}


# ─────────────────────────────────────────────────────────────
# Result builder
# ─────────────────────────────────────────────────────────────

def _build_result(
    found: bool,
    method: Optional[str],
    element_info: Optional[dict],
    attempts: list[dict],
    target: str,
    elapsed: float,
    **kwargs,
) -> dict:
    """
    Build standardized cascade_find result dict.

    / Construye dict de resultado estandarizado para cascade_find.
    """
    result = {
        "success": True,
        "found": found,
        "target": target,
        "method": method,
        "element_info": element_info,
        "elapsed_seconds": round(elapsed, 2),
        "steps_tried": len(attempts),
        "attempts": attempts,
    }
    result.update(kwargs)
    return result
