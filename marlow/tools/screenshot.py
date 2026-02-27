"""
Marlow Screenshot Tool

Captures screenshots of the screen or specific windows.
LAST RESORT — always try UI Tree first (0 tokens vs ~1,500 tokens).

Includes:
- Full screen capture
- Per-window capture 
- Region capture
- Base64 encoding for MCP transport
"""

import io
import base64
import logging
from typing import Optional

logger = logging.getLogger("marlow.tools.screenshot")


async def take_screenshot(
    window_title: Optional[str] = None,
    region: Optional[dict] = None,
    quality: int = 85,
) -> dict:
    """
    Take a screenshot of the screen, a specific window, or a region.
    
    NOTE: This costs ~1,500 tokens when sent to an LLM with vision.
    Always prefer get_ui_tree() first — it costs 0 tokens.

    Args:
        window_title: Capture a specific window only. If None, captures 
                      full screen.
        region: Capture a specific region: {"x": 0, "y": 0, "width": 800, "height": 600}
        quality: JPEG quality (1-100). Lower = smaller file. Default: 85.

    Returns:
        Dictionary with:
        - image_base64: Base64 encoded image
        - width, height: Image dimensions
        - format: Image format used
    
    / Captura una screenshot de la pantalla, ventana específica, o región.
    / NOTA: Cuesta ~1,500 tokens con LLM Vision. Siempre usa get_ui_tree() primero.
    """
    try:
        import mss
        from PIL import Image

        if window_title:
            return await _capture_window(window_title, quality)
        elif region:
            return await _capture_region(region, quality)
        else:
            return await _capture_fullscreen(quality)

    except ImportError as e:
        missing = str(e).split("'")[-2] if "'" in str(e) else str(e)
        return {
            "error": f"Missing dependency: {missing}. Run: pip install mss Pillow",
        }
    except Exception as e:
        logger.error(f"Screenshot error: {e}")
        return {"error": str(e)}


async def _capture_fullscreen(quality: int) -> dict:
    """Capture the full screen."""
    import mss
    from PIL import Image

    with mss.mss() as sct:
        monitor = sct.monitors[0]  # All monitors combined
        screenshot = sct.grab(monitor)

        img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
        return _encode_image(img, quality, "fullscreen")


async def _capture_window(window_title: str, quality: int) -> dict:
    """Capture a specific window by title."""
    try:
        from marlow.core.uia_utils import find_window

        target, err = find_window(window_title, max_suggestions=20)
        if err:
            return err
        # capture_as_image() works WITHOUT activating the window
        # This is key for background mode
        img = target.capture_as_image()

        return _encode_image(img, quality, f"window: {target.window_text()}")

    except Exception as e:
        logger.error(f"Window capture error: {e}")
        return {"error": str(e)}


async def _capture_region(region: dict, quality: int) -> dict:
    """Capture a specific screen region."""
    import mss
    from PIL import Image

    monitor = {
        "left": region.get("x", 0),
        "top": region.get("y", 0),
        "width": region.get("width", 800),
        "height": region.get("height", 600),
    }

    with mss.mss() as sct:
        screenshot = sct.grab(monitor)
        img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
        return _encode_image(img, quality, "region")


def _encode_image(img: object, quality: int, source: str) -> dict:
    """Encode a PIL Image to base64 for MCP transport."""
    from PIL import Image

    # Convert to RGB if needed
    if img.mode != "RGB":
        img = img.convert("RGB")

    # Encode to JPEG (smaller than PNG for MCP transport)
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=quality)
    buffer.seek(0)

    image_base64 = base64.b64encode(buffer.read()).decode("utf-8")

    return {
        "image_base64": image_base64,
        "width": img.width,
        "height": img.height,
        "format": "jpeg",
        "source": source,
        "size_kb": round(len(image_base64) * 3 / 4 / 1024, 1),
        "hint": "⚠️ This image costs ~1,500 tokens. Use get_ui_tree() for 0-token alternative.",
    }
