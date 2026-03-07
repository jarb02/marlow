"""Compositor ScreenCapture — Marlow Compositor IPC.

Captures screenshots directly from the compositor's render buffer
via IPC, instead of using grim (which needs wlr-screencopy).

/ ScreenCapture via IPC directo al compositor Marlow.
"""

from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import logging
from typing import Optional

from marlow.platform.base import ScreenCapture
from marlow.platform.linux.compositor_client import MarlowCompositorClient

logger = logging.getLogger("marlow.platform.compositor.screenshot")


def _run_async(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result(timeout=10)
    else:
        return asyncio.run(coro)


class CompositorScreenCapture(ScreenCapture):
    """Screenshot capture via Marlow Compositor IPC."""

    def __init__(self, socket_path: str = None):
        self._socket_path = socket_path

    def screenshot(
        self,
        window_title: Optional[str] = None,
        region: Optional[tuple[int, int, int, int]] = None,
    ) -> bytes:
        try:
            async def _capture():
                client = MarlowCompositorClient()
                await client.connect(self._socket_path)
                try:
                    window_id = None
                    if window_title:
                        window_id = await self._find_window_id(
                            client, window_title
                        )
                    b64 = await client.request_screenshot(
                        window_id=window_id, timeout=5.0
                    )
                    if b64 is None:
                        raise RuntimeError("Compositor returned no screenshot")
                    return base64.b64decode(b64)
                finally:
                    await client.disconnect()

            data = _run_async(_capture())

            if region and data:
                return self._crop_region(data, region)
            return data

        except Exception as e:
            raise RuntimeError(f"Compositor screenshot failed: {e}") from e

    async def _find_window_id(self, client, title: str) -> Optional[int]:
        """Find window ID by title substring."""
        windows = await client.list_windows()
        title_lower = title.lower()
        for w in windows:
            wt = (w.get("title") or "").lower()
            app = (w.get("app_id") or "").lower()
            if title_lower in wt or title_lower in app:
                return w.get("id")
        return None

    @staticmethod
    def _crop_region(png_data: bytes, region: tuple[int, int, int, int]) -> bytes:
        """Crop PNG data to a region. Requires PIL."""
        import io
        from PIL import Image

        img = Image.open(io.BytesIO(png_data))
        x, y, w, h = region
        cropped = img.crop((x, y, x + w, y + h))
        buf = io.BytesIO()
        cropped.save(buf, format="PNG")
        return buf.getvalue()
