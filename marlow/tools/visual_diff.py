"""
Marlow Visual Diff Tool

Compares before/after screenshots to verify that an action produced
the expected visual change. Useful for confirming clicks, text entry,
window state changes, etc.

/ Compara screenshots antes/despues para verificar cambios visuales.
"""

import io
import time
import uuid
import base64
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger("marlow.tools.visual_diff")

# Temporary store for "before" states
_diff_states: dict[str, dict] = {}

# Auto-cleanup: discard states older than 5 minutes
_MAX_STATE_AGE = 300


def _cleanup_old_states():
    """Remove diff states older than 5 minutes."""
    now = time.time()
    expired = [k for k, v in _diff_states.items() if now - v["_created"] > _MAX_STATE_AGE]
    for k in expired:
        del _diff_states[k]


def _decode_base64_image(b64_str: str):
    """Decode a base64 string into a PIL Image."""
    from PIL import Image
    img_bytes = base64.b64decode(b64_str)
    return Image.open(io.BytesIO(img_bytes))


async def visual_diff(
    window_title: Optional[str] = None,
    description: str = "",
) -> dict:
    """
    Capture the 'before' state of a window for later comparison.

    Call this BEFORE performing an action. Then call visual_diff_compare()
    with the returned diff_id AFTER the action to see what changed.

    Args:
        window_title: Window to capture. If None, captures full screen.
        description: Optional description of what you're about to do.

    Returns:
        Dictionary with diff_id to use in visual_diff_compare().

    / Captura el estado 'antes' de una ventana para comparar despues.
    """
    _cleanup_old_states()

    from marlow.tools.screenshot import take_screenshot

    shot = await take_screenshot(window_title=window_title)
    if "error" in shot:
        return shot

    diff_id = uuid.uuid4().hex[:8]
    _diff_states[diff_id] = {
        "before_base64": shot["image_base64"],
        "before_width": shot.get("width"),
        "before_height": shot.get("height"),
        "window": window_title,
        "description": description,
        "timestamp": datetime.now().isoformat(),
        "_created": time.time(),
    }

    return {
        "success": True,
        "diff_id": diff_id,
        "status": "before_captured",
        "window": window_title,
        "hint": f"Perform your action, then call visual_diff_compare(diff_id='{diff_id}') to see changes.",
    }


async def visual_diff_compare(diff_id: str) -> dict:
    """
    Capture the 'after' state and compare with the 'before' state.

    Args:
        diff_id: The ID returned by visual_diff().

    Returns:
        Dictionary with change_percent, changed flag, and comparison details.

    / Captura el estado 'despues' y lo compara con el 'antes'.
    """
    if diff_id not in _diff_states:
        return {"error": f"No 'before' state found for diff_id: {diff_id}. It may have expired (5 min max)."}

    state = _diff_states.pop(diff_id)

    from marlow.tools.screenshot import take_screenshot
    from PIL import ImageChops

    after_shot = await take_screenshot(window_title=state["window"])
    if "error" in after_shot:
        return after_shot

    try:
        before_img = _decode_base64_image(state["before_base64"])
        after_img = _decode_base64_image(after_shot["image_base64"])

        # Resize to match if dimensions differ
        if before_img.size != after_img.size:
            after_img = after_img.resize(before_img.size)

        # Convert to RGB for consistent comparison
        before_rgb = before_img.convert("RGB")
        after_rgb = after_img.convert("RGB")

        # Pixel-level difference
        diff = ImageChops.difference(before_rgb, after_rgb)
        diff_pixels = sum(1 for p in diff.getdata() if sum(p) > 30)
        total_pixels = before_rgb.width * before_rgb.height
        change_percent = round((diff_pixels / total_pixels) * 100, 2) if total_pixels > 0 else 0

        # Find bounding box of changed region
        bbox = diff.getbbox()
        changed_region = None
        if bbox:
            changed_region = {
                "x": bbox[0], "y": bbox[1],
                "width": bbox[2] - bbox[0],
                "height": bbox[3] - bbox[1],
            }

        return {
            "success": True,
            "diff_id": diff_id,
            "changed": change_percent > 0.5,
            "change_percent": change_percent,
            "changed_pixels": diff_pixels,
            "total_pixels": total_pixels,
            "changed_region": changed_region,
            "before_size": f"{before_rgb.width}x{before_rgb.height}",
            "after_size": f"{after_img.width}x{after_img.height}",
            "description": state["description"],
        }

    except Exception as e:
        logger.error(f"Visual diff comparison error: {e}")
        return {"error": str(e)}
