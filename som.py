"""Linux SoMProvider — Set-of-Mark annotation on screenshots.

Overlays numbered labels [1], [2], [3]... on interactive UI elements
found via AT-SPI2, allowing the LLM to refer to elements by number.

Uses PIL for drawing, AT-SPI2 for element discovery, grim for screenshots.

/ SoMProvider Linux — anotacion Set-of-Mark en screenshots.
"""

from __future__ import annotations

import base64
import io
import logging
from typing import Optional

from marlow.platform.base import SoMProvider

logger = logging.getLogger("marlow.platform.linux.som")

# AT-SPI2 roles considered interactive
_INTERACTIVE_ROLES = {
    "push button", "toggle button", "radio button", "check box",
    "combo box", "text", "entry", "password text", "spin button",
    "slider", "menu item", "check menu item", "radio menu item",
    "link", "tab", "list item", "tree item", "table cell",
    "tool bar item", "page tab",
}

# Max elements to annotate (keeps image legible)
_MAX_ELEMENTS = 100

# Label colors
_LABEL_BG = (255, 140, 0, 200)   # Orange semi-transparent
_LABEL_FG = (255, 255, 255)       # White text
_OUTLINE_COLOR = (255, 140, 0)    # Orange outline


class LinuxSoMProvider(SoMProvider):
    """Set-of-Mark annotation using AT-SPI2 + grim + PIL."""

    def __init__(self, screen=None, ui_tree=None, input_provider=None):
        self._screen = screen
        self._ui_tree = ui_tree
        self._input = input_provider
        # Cache last annotation for som_click
        self._last_elements: list[dict] = []
        self._last_window_title: Optional[str] = None

    def get_annotated_screenshot(
        self,
        window_title: Optional[str] = None,
        max_depth: Optional[int] = None,
    ) -> dict:
        self._last_elements = []
        self._last_window_title = window_title

        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            return {"success": False, "error": "PIL (Pillow) not installed"}

        # ── Step 1: Get UI tree ──
        if self._ui_tree is None:
            return {"success": False, "error": "No UI tree provider"}

        tree_result = self._ui_tree.get_tree(
            window_title=window_title,
            max_depth=max_depth or 10,
        )
        if not tree_result.get("success"):
            return {"success": False, "error": tree_result.get("error", "Tree failed")}

        win_info = tree_result.get("window", {})

        # ── Step 2: Collect interactive elements with valid bounds ──
        elements = []
        self._collect_interactive(tree_result.get("tree", {}), elements)

        if not elements:
            return {
                "success": False,
                "error": "No interactive elements found",
                "window_title": win_info.get("title", ""),
            }

        # Cap elements
        elements = elements[:_MAX_ELEMENTS]

        # ── Step 3: Capture screenshot ──
        if self._screen is None:
            return {"success": False, "error": "No screen provider"}

        try:
            png_bytes = self._screen.screenshot(window_title=window_title)
        except Exception as e:
            return {"success": False, "error": f"Screenshot failed: {e}"}

        # ── Step 4: Draw labels ──
        try:
            img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
        except Exception as e:
            return {"success": False, "error": f"Image load failed: {e}"}

        # Get window offset if capturing specific window
        win_offset_x = 0
        win_offset_y = 0
        if window_title and win_info.get("title"):
            # Element bounds are in screen coords; screenshot is window-relative
            # Get window position from Sway
            try:
                import i3ipc
                conn = i3ipc.Connection()
                for leaf in conn.get_tree().leaves():
                    name = (leaf.name or "").lower()
                    if window_title.lower() in name:
                        win_offset_x = leaf.rect.x
                        win_offset_y = leaf.rect.y
                        break
            except Exception:
                pass

        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # Try to get a font
        try:
            font = ImageFont.truetype("/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf", 14)
        except Exception:
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
            except Exception:
                font = ImageFont.load_default()

        numbered_elements = []
        for i, elem in enumerate(elements, start=1):
            bounds = elem.get("bounds", {})
            x = bounds.get("x", 0) - win_offset_x
            y = bounds.get("y", 0) - win_offset_y
            w = bounds.get("w", 0)
            h = bounds.get("h", 0)

            # Skip elements outside screenshot bounds
            if x + w < 0 or y + h < 0 or x > img.width or y > img.height:
                continue

            # Draw element outline
            draw.rectangle(
                [x, y, x + w, y + h],
                outline=_OUTLINE_COLOR, width=2,
            )

            # Draw label background + text at top-left of element
            label = str(i)
            bbox = font.getbbox(label)
            text_w = bbox[2] - bbox[0] + 6
            text_h = bbox[3] - bbox[1] + 4
            label_x = max(0, x)
            label_y = max(0, y - text_h - 1)
            if label_y < 0:
                label_y = y

            draw.rectangle(
                [label_x, label_y, label_x + text_w, label_y + text_h],
                fill=_LABEL_BG,
            )
            draw.text(
                (label_x + 3, label_y + 1),
                label, fill=_LABEL_FG, font=font,
            )

            numbered_elements.append({
                "index": i,
                "name": elem.get("name", ""),
                "role": elem.get("role", ""),
                "bounds": bounds,
                "actions": elem.get("actions", []),
                "path": elem.get("path", ""),
            })

        # Composite overlay onto image
        result_img = Image.alpha_composite(img, overlay).convert("RGB")

        # Encode to PNG base64
        buf = io.BytesIO()
        result_img.save(buf, format="PNG")
        image_b64 = base64.b64encode(buf.getvalue()).decode()

        self._last_elements = numbered_elements

        return {
            "success": True,
            "image": image_b64,
            "elements": numbered_elements,
            "element_count": len(numbered_elements),
            "window_title": win_info.get("title", ""),
        }

    def som_click(self, index: int, action: str = "click") -> dict:
        if not self._last_elements:
            return {
                "success": False,
                "error": "No annotation cached. Call get_annotated_screenshot first.",
            }

        # Find element by index
        target = None
        for elem in self._last_elements:
            if elem["index"] == index:
                target = elem
                break

        if target is None:
            return {
                "success": False,
                "error": f"Index {index} not found. Valid: 1-{len(self._last_elements)}",
            }

        # Try AT-SPI2 action first
        if target.get("path") and target.get("actions") and self._ui_tree:
            action_name = action
            if action_name not in target["actions"] and target["actions"]:
                # Use first available action as fallback
                action_name = target["actions"][0]

            if action_name in target["actions"]:
                ok = self._ui_tree.do_action(
                    target["path"], action_name,
                    window_title=self._last_window_title,
                )
                if ok:
                    return {
                        "success": True,
                        "method": "atspi_action",
                        "action": action_name,
                        "element": target,
                    }

        # Fallback: coordinate click
        bounds = target.get("bounds", {})
        cx = bounds.get("x", 0) + bounds.get("w", 0) // 2
        cy = bounds.get("y", 0) + bounds.get("h", 0) // 2

        if cx == 0 and cy == 0:
            return {
                "success": False,
                "error": f"Element {index} has no valid bounds for click",
                "element": target,
            }

        if self._input:
            ok = self._input.click(cx, cy)
            return {
                "success": ok,
                "method": "coordinate_click",
                "coordinates": {"x": cx, "y": cy},
                "element": target,
            }

        return {
            "success": False,
            "error": "No input provider for coordinate click",
            "element": target,
        }

    def _collect_interactive(self, node: dict, results: list):
        """Recursively collect interactive elements with valid bounds."""
        if len(results) >= _MAX_ELEMENTS:
            return

        role = node.get("role", "")
        bounds = node.get("bounds", {})
        has_bounds = bounds.get("w", 0) > 0 and bounds.get("h", 0) > 0

        # Check if interactive
        is_interactive = (
            role in _INTERACTIVE_ROLES
            or bool(node.get("actions"))
        )

        if is_interactive and has_bounds:
            results.append({
                "name": node.get("name", ""),
                "role": role,
                "bounds": bounds,
                "actions": node.get("actions", []),
                "path": node.get("path", ""),
            })

        # Recurse children
        for child in node.get("children", []):
            if len(results) >= _MAX_ELEMENTS:
                break
            self._collect_interactive(child, results)
