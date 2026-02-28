"""
Marlow Smart Wait Tools

Intelligent waiting for UI elements, text, windows, and idle states.
All waits use polling loops with configurable timeout and interval,
and check the kill switch between iterations.

/ Herramientas de espera inteligente para elementos UI, texto, ventanas
/ y estados idle.
"""

import asyncio
import logging
import time
from typing import Optional

logger = logging.getLogger("marlow.tools.wait")


async def wait_for_element(
    name: str,
    window_title: Optional[str] = None,
    timeout: int = 30,
    interval: float = 1.0,
) -> dict:
    """
    Wait for a UI element to appear in the Accessibility Tree.

    Polls find_element_by_name every `interval` seconds until found or timeout.

    Args:
        name: Name/text of the element to wait for (e.g., "Save", "OK").
        window_title: Window to search in. If None, searches active window.
        timeout: Max seconds to wait. Default: 30.
        interval: Seconds between checks. Default: 1.

    Returns:
        Dictionary with element info on success, error on timeout.

    / Espera a que un elemento UI aparezca en el Accessibility Tree.
    """
    timeout = min(max(timeout, 1), 120)
    interval = min(max(interval, 0.5), 10.0)

    try:
        from pywinauto import Desktop
        from marlow.core.uia_utils import find_window, find_element_by_name

        deadline = time.monotonic() + timeout
        checks = 0

        while time.monotonic() < deadline:
            checks += 1

            # Find window each iteration (it may appear mid-wait)
            try:
                if window_title:
                    target_window, err = find_window(window_title, list_available=False)
                    if err:
                        await asyncio.sleep(interval)
                        continue
                else:
                    desktop = Desktop(backend="uia")
                    target_window = desktop.window(active_only=True)

                element = find_element_by_name(target_window, name, max_depth=5)
                if element is not None:
                    elapsed = round(time.monotonic() - (deadline - timeout), 2)
                    info = {
                        "name": element.window_text(),
                        "control_type": element.element_info.control_type,
                    }
                    try:
                        rect = element.rectangle()
                        info["position"] = {
                            "x": rect.left,
                            "y": rect.top,
                            "width": rect.width(),
                            "height": rect.height(),
                        }
                    except Exception:
                        pass

                    return {
                        "success": True,
                        "found": True,
                        "element": info,
                        "elapsed_seconds": elapsed,
                        "checks": checks,
                    }
            except Exception:
                pass

            await asyncio.sleep(interval)

        return {
            "error": f"Element '{name}' not found after {timeout}s ({checks} checks)",
            "timeout": timeout,
            "window": window_title,
        }

    except ImportError:
        return {"error": "pywinauto not installed. Run: pip install pywinauto"}
    except Exception as e:
        logger.error(f"wait_for_element error: {e}")
        return {"error": str(e)}


async def wait_for_text(
    text: str,
    window_title: Optional[str] = None,
    timeout: int = 30,
    interval: float = 2.0,
) -> dict:
    """
    Wait for specific text to appear on screen using OCR.

    Polls ocr_region every `interval` seconds. Case insensitive.

    Args:
        text: Text to wait for.
        window_title: Window to OCR. If None, captures full screen.
        timeout: Max seconds to wait. Default: 30.
        interval: Seconds between checks. Default: 2.

    Returns:
        Dictionary with text location on success, error on timeout.

    / Espera a que un texto aparezca en pantalla usando OCR.
    """
    timeout = min(max(timeout, 1), 120)
    interval = min(max(interval, 1.0), 10.0)

    try:
        from marlow.tools.ocr import ocr_region
    except ImportError:
        return {"error": "pytesseract not installed. Run: pip install pytesseract"}

    text_lower = text.lower()
    deadline = time.monotonic() + timeout
    checks = 0

    try:
        while time.monotonic() < deadline:
            checks += 1

            try:
                result = await ocr_region(
                    window_title=window_title,
                    preprocess=True,
                )

                if "error" in result:
                    await asyncio.sleep(interval)
                    continue

                # Search words for the target text
                for word in result.get("words", []):
                    if text_lower in word["text"].lower():
                        elapsed = round(time.monotonic() - (deadline - timeout), 2)
                        bbox = word["bbox"]
                        return {
                            "success": True,
                            "found": True,
                            "matched_text": word["text"],
                            "confidence": word.get("confidence"),
                            "position": {
                                "x": bbox["x"] + bbox["width"] // 2,
                                "y": bbox["y"] + bbox["height"] // 2,
                            },
                            "bbox": bbox,
                            "elapsed_seconds": elapsed,
                            "checks": checks,
                        }

                # Also check full text for multi-word matches
                full_text = result.get("text", "")
                if text_lower in full_text.lower():
                    elapsed = round(time.monotonic() - (deadline - timeout), 2)
                    # Find surrounding context
                    idx = full_text.lower().index(text_lower)
                    start = max(0, idx - 40)
                    end = min(len(full_text), idx + len(text) + 40)
                    context = full_text[start:end].strip()

                    return {
                        "success": True,
                        "found": True,
                        "matched_text": text,
                        "context": context,
                        "position": None,
                        "elapsed_seconds": elapsed,
                        "checks": checks,
                    }
            except Exception:
                pass

            await asyncio.sleep(interval)

        return {
            "error": f"Text '{text}' not found after {timeout}s ({checks} checks)",
            "timeout": timeout,
            "window": window_title,
        }

    except Exception as e:
        logger.error(f"wait_for_text error: {e}")
        return {"error": str(e)}


async def wait_for_window(
    title: str,
    timeout: int = 30,
    interval: float = 1.0,
) -> dict:
    """
    Wait for a window with the given title to appear.

    Polls the window list every `interval` seconds.

    Args:
        title: Window title (or partial title) to wait for.
        timeout: Max seconds to wait. Default: 30.
        interval: Seconds between checks. Default: 1.

    Returns:
        Dictionary with window info on success, error on timeout.

    / Espera a que una ventana con el titulo dado aparezca.
    """
    timeout = min(max(timeout, 1), 120)
    interval = min(max(interval, 0.5), 10.0)

    try:
        import re
        from pywinauto import Desktop

        title_lower = title.lower()
        deadline = time.monotonic() + timeout
        checks = 0

        while time.monotonic() < deadline:
            checks += 1

            try:
                desktop = Desktop(backend="uia")
                windows = desktop.windows(title_re=f".*{re.escape(title)}.*")

                if windows:
                    win = windows[0]
                    elapsed = round(time.monotonic() - (deadline - timeout), 2)

                    info = {
                        "title": win.window_text(),
                        "class_name": win.element_info.class_name,
                    }
                    try:
                        rect = win.rectangle()
                        info["position"] = {
                            "x": rect.left,
                            "y": rect.top,
                            "width": rect.width(),
                            "height": rect.height(),
                        }
                    except Exception:
                        pass

                    return {
                        "success": True,
                        "found": True,
                        "window": info,
                        "elapsed_seconds": elapsed,
                        "checks": checks,
                    }
            except Exception:
                pass

            await asyncio.sleep(interval)

        return {
            "error": f"Window '{title}' not found after {timeout}s ({checks} checks)",
            "timeout": timeout,
        }

    except ImportError:
        return {"error": "pywinauto not installed. Run: pip install pywinauto"}
    except Exception as e:
        logger.error(f"wait_for_window error: {e}")
        return {"error": str(e)}


async def wait_for_idle(
    window_title: Optional[str] = None,
    timeout: int = 30,
    stable_seconds: float = 2.0,
) -> dict:
    """
    Wait until the screen/window stops changing (idle state).

    Takes screenshots every second and compares consecutive frames.
    When frames don't change for `stable_seconds`, considers idle.

    Args:
        window_title: Window to monitor. If None, monitors full screen.
        timeout: Max seconds to wait. Default: 30.
        stable_seconds: How many seconds of no change = idle. Default: 2.

    Returns:
        Dictionary with idle status and timing info.

    / Espera a que la pantalla/ventana deje de cambiar (estado idle).
    """
    timeout = min(max(timeout, 2), 120)
    stable_seconds = min(max(stable_seconds, 1.0), 10.0)

    try:
        import mss
        from PIL import Image
        import io

        deadline = time.monotonic() + timeout
        last_stable_start = time.monotonic()
        prev_bytes: Optional[bytes] = None
        comparisons = 0

        while time.monotonic() < deadline:
            comparisons += 1

            # Capture frame
            try:
                current_bytes = await _capture_frame(window_title)
            except Exception:
                await asyncio.sleep(1.0)
                continue

            if prev_bytes is not None:
                # Compare frames
                changed = current_bytes != prev_bytes
                if changed:
                    last_stable_start = time.monotonic()
                else:
                    stable_duration = time.monotonic() - last_stable_start
                    if stable_duration >= stable_seconds:
                        elapsed = round(time.monotonic() - (deadline - timeout), 2)
                        return {
                            "success": True,
                            "idle": True,
                            "stable_seconds": round(stable_duration, 2),
                            "elapsed_seconds": elapsed,
                            "comparisons": comparisons,
                        }

            prev_bytes = current_bytes
            await asyncio.sleep(1.0)

        elapsed = round(time.monotonic() - (deadline - timeout), 2)
        return {
            "error": f"Screen still changing after {timeout}s ({comparisons} comparisons)",
            "idle": False,
            "timeout": timeout,
            "elapsed_seconds": elapsed,
        }

    except ImportError as e:
        return {"error": f"Missing dependency: {e}"}
    except Exception as e:
        logger.error(f"wait_for_idle error: {e}")
        return {"error": str(e)}


async def _capture_frame(window_title: Optional[str]) -> bytes:
    """
    Capture a low-quality screenshot as bytes for comparison.
    Uses mss for speed. Returns raw pixel bytes.

    / Captura screenshot de baja calidad como bytes para comparacion.
    """
    import mss
    from PIL import Image
    import io

    loop = asyncio.get_running_loop()

    def _grab():
        with mss.mss() as sct:
            if window_title:
                # Try to capture specific window region
                try:
                    from marlow.core.uia_utils import find_window
                    win, err = find_window(window_title, list_available=False)
                    if win and not err:
                        rect = win.rectangle()
                        monitor = {
                            "left": rect.left,
                            "top": rect.top,
                            "width": max(rect.width(), 1),
                            "height": max(rect.height(), 1),
                        }
                        img = sct.grab(monitor)
                        # Downscale for faster comparison
                        pil_img = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
                        pil_img = pil_img.resize(
                            (pil_img.width // 4, pil_img.height // 4),
                            Image.NEAREST,
                        )
                        return pil_img.tobytes()
                except Exception:
                    pass

            # Full screen fallback
            monitor = sct.monitors[1]
            img = sct.grab(monitor)
            pil_img = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
            pil_img = pil_img.resize(
                (pil_img.width // 4, pil_img.height // 4),
                Image.NEAREST,
            )
            return pil_img.tobytes()

    return await loop.run_in_executor(None, _grab)
