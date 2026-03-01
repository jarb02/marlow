"""
Marlow Set-of-Mark (SoM) Prompting

Annotates screenshots with numbered labels [1], [2], [3]... on each
interactive UI element. The LLM receives the annotated image + element
map, then refers to elements by number: "click [3]", "type in [5]".

/ Anota screenshots con labels numerados en cada elemento UI interactivo.
/ El LLM recibe la imagen anotada + mapa de elementos.
"""

import base64
import io
import logging
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("marlow.core.som")

# Interactive control types for filtering
# / Tipos de controles interactivos para filtrado
INTERACTIVE_TYPES = {
    "Button", "Edit", "ComboBox", "CheckBox", "RadioButton",
    "MenuItem", "Link", "Hyperlink", "TabItem", "ListItem",
    "Slider", "Spinner", "SplitButton", "ToggleButton",
    "TreeItem", "DataItem",
}

# Max elements to annotate (keeps image legible)
MAX_ELEMENTS = 100

# Last annotation stored for som_click
_last_elements: list[dict] = []


async def annotate_screenshot(
    window_title: Optional[str] = None,
    interactive_only: bool = True,
) -> dict:
    """
    Take a screenshot and overlay numbered labels on each UI element.

    Steps:
      1. Find window and walk UIA tree for elements with bounding boxes
      2. Filter by interactive control types if requested
      3. Take screenshot of the window
      4. Draw [1], [2], [3]... labels with orange background on each element
      5. Return annotated PNG image (base64) + element map

    / Tomar screenshot y superponer labels numerados en cada elemento UI.
    """
    global _last_elements

    try:
        from pywinauto import Desktop
        from marlow.core.uia_utils import find_window

        # ── Step 1: Find window ──
        resolved_title = window_title
        if window_title:
            win, err = find_window(window_title, list_available=True)
            if err:
                return err
        else:
            desktop = Desktop(backend="uia")
            win = desktop.window(active_only=True)
            try:
                resolved_title = win.window_text()
            except Exception:
                resolved_title = "(active window)"

        # Get window rectangle for coordinate conversion
        try:
            rect = win.rectangle()
            win_left, win_top = rect.left, rect.top
        except Exception as e:
            return {"error": f"Cannot get window rectangle: {e}"}

        # ── Step 2: Walk UIA tree and collect elements ──
        raw_elements = []
        _walk_elements(win, raw_elements, interactive_only,
                       max_depth=8, current_depth=0)

        if not raw_elements:
            return {
                "success": True,
                "count": 0,
                "elements": [],
                "hint": "No interactive elements found in this window.",
            }

        # Cap at MAX_ELEMENTS
        raw_elements = raw_elements[:MAX_ELEMENTS]

        # ── Step 3: Take screenshot ──
        from marlow.tools.screenshot import take_screenshot
        ss_result = await take_screenshot(
            window_title=window_title, quality=90,
        )

        if "error" in ss_result:
            return {"error": f"Screenshot failed: {ss_result['error']}"}

        # Decode screenshot and convert to RGBA for semi-transparent drawing
        img_data = base64.b64decode(ss_result["image_base64"])
        img = Image.open(io.BytesIO(img_data)).convert("RGBA")

        # Create transparent overlay for alpha compositing
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        font = _get_font(12)

        # ── Step 4: Draw labels ──
        element_map = []
        for i, elem in enumerate(raw_elements, 1):
            # Convert screen coords to image coords
            ex = elem["bbox"]["x"] - win_left
            ey = elem["bbox"]["y"] - win_top
            ew = elem["bbox"]["width"]
            eh = elem["bbox"]["height"]

            # Skip elements outside image bounds
            if ex + ew < 0 or ey + eh < 0:
                continue
            if ex >= img.width or ey >= img.height:
                continue

            # Clamp to image bounds
            ex = max(0, ex)
            ey = max(0, ey)

            label = f"[{i}]"

            # Measure text size
            text_bbox = draw.textbbox((0, 0), label, font=font)
            tw = text_bbox[2] - text_bbox[0] + 6   # padding
            th = text_bbox[3] - text_bbox[1] + 4

            # Position label at top-left of element bbox
            lx = min(ex, img.width - tw)
            ly = ey - th - 1  # Above the element
            if ly < 0:
                ly = ey + 1   # Below top edge if no room above

            lx = max(0, lx)
            ly = max(0, ly)

            # Semi-transparent orange background for label
            draw.rectangle(
                [lx, ly, lx + tw, ly + th],
                fill=(255, 140, 0, 200),
            )

            # White text
            draw.text(
                (lx + 3, ly + 1),
                label,
                fill=(255, 255, 255, 255),
                font=font,
            )

            # Thin orange border around element
            draw.rectangle(
                [ex, ey, min(ex + ew, img.width - 1),
                 min(ey + eh, img.height - 1)],
                outline=(255, 140, 0, 200),
                width=2,
            )

            element_map.append({
                "index": i,
                "name": elem["name"],
                "type": elem["control_type"],
                "automation_id": elem["automation_id"],
                "bbox": {
                    "x": elem["bbox"]["x"],
                    "y": elem["bbox"]["y"],
                    "width": ew,
                    "height": eh,
                },
            })

        # Composite overlay onto image
        result_img = Image.alpha_composite(img, overlay)

        # ── Step 5: Encode as PNG ──
        buf = io.BytesIO()
        result_img.save(buf, format="PNG")
        annotated_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

        # Store for som_click
        _last_elements = element_map

        return {
            "success": True,
            "image_base64": annotated_b64,
            "width": result_img.width,
            "height": result_img.height,
            "format": "png",
            "count": len(element_map),
            "elements": element_map,
            "window_title": resolved_title,
            "interactive_only": interactive_only,
            "hint": (
                f"{len(element_map)} elements annotated. "
                f"Use som_click(index) to click any element by its number."
            ),
        }

    except Exception as e:
        logger.error(f"SoM annotation failed: {e}")
        return {"error": f"SoM annotation failed: {e}"}


def _walk_elements(
    parent,
    results: list,
    interactive_only: bool,
    max_depth: int,
    current_depth: int,
):
    """
    Walk UIA tree collecting elements with valid bounding boxes.

    / Recorrer arbol UIA recolectando elementos con bboxes validos.
    """
    if current_depth >= max_depth:
        return
    if len(results) >= MAX_ELEMENTS:
        return

    try:
        children = parent.children()
    except Exception:
        return

    for child in children:
        if len(results) >= MAX_ELEMENTS:
            return

        try:
            # Get control type
            ctrl_type = ""
            try:
                ctrl_type = child.element_info.control_type or ""
            except Exception:
                pass

            # Check if this element matches the filter
            matches_filter = (not interactive_only) or (ctrl_type in INTERACTIVE_TYPES)

            if matches_filter:
                # Get bounding rectangle
                try:
                    rect = child.rectangle()
                    x, y = rect.left, rect.top
                    w = rect.width()
                    h = rect.height()
                except Exception:
                    w, h = 0, 0

                # Only add if visible and non-trivial size
                if w > 4 and h > 4:
                    name = ""
                    try:
                        name = child.window_text() or ""
                    except Exception:
                        pass

                    automation_id = ""
                    try:
                        automation_id = child.element_info.automation_id or ""
                    except Exception:
                        pass

                    results.append({
                        "name": name,
                        "control_type": ctrl_type,
                        "automation_id": automation_id,
                        "bbox": {"x": x, "y": y, "width": w, "height": h},
                    })

            # Always recurse into children to find nested interactive elements
            _walk_elements(child, results, interactive_only,
                           max_depth, current_depth + 1)

        except Exception:
            pass


async def click_by_index(
    index: int,
    elements_map: Optional[list] = None,
) -> dict:
    """
    Click element by its SoM index number.
    Uses the last annotation's element map if elements_map not provided.

    / Click en un elemento por su indice SoM.
    """
    global _last_elements

    elements = elements_map or _last_elements

    if not elements:
        return {
            "error": (
                "No SoM annotation available. "
                "Call get_annotated_screenshot first."
            ),
        }

    # Find element by index
    target = None
    for elem in elements:
        if elem["index"] == index:
            target = elem
            break

    if not target:
        max_idx = max(e["index"] for e in elements) if elements else 0
        return {
            "error": f"Index [{index}] not found. Valid range: [1]-[{max_idx}].",
            "available_count": len(elements),
        }

    # Calculate center of bounding box (screen coordinates)
    cx = target["bbox"]["x"] + target["bbox"]["width"] // 2
    cy = target["bbox"]["y"] + target["bbox"]["height"] // 2

    # Click at center
    from marlow.tools.mouse import click
    click_result = await click(x=cx, y=cy)

    return {
        "success": click_result.get("success", False),
        "index": index,
        "element": {
            "name": target["name"],
            "type": target["type"],
            "automation_id": target["automation_id"],
        },
        "clicked_at": {"x": cx, "y": cy},
        "click_method": click_result.get("method"),
    }


def _get_font(size: int):
    """Get a font for label drawing. Falls back to default."""
    try:
        return ImageFont.truetype("arial.ttf", size)
    except Exception:
        try:
            return ImageFont.truetype("segoeui.ttf", size)
        except Exception:
            return ImageFont.load_default()


# ── MCP Tool Wrappers ──

async def get_annotated_screenshot(
    window_title: Optional[str] = None,
    interactive_only: bool = True,
) -> dict:
    """
    MCP tool: annotate screenshot with numbered element labels.
    Returns annotated PNG image + element map for LLM interaction.

    / Herramienta MCP: screenshot anotado con labels numerados.
    """
    return await annotate_screenshot(
        window_title=window_title,
        interactive_only=interactive_only,
    )


async def som_click(
    index: int,
    window_title: Optional[str] = None,
) -> dict:
    """
    MCP tool: click element by SoM index from last annotation.
    If window_title given, re-annotates first to get fresh positions.

    / Herramienta MCP: click en elemento por indice SoM.
    """
    if window_title:
        # Re-annotate to get fresh element positions
        result = await annotate_screenshot(window_title=window_title)
        if "error" in result:
            return result
        return await click_by_index(index, result.get("elements"))

    return await click_by_index(index)
