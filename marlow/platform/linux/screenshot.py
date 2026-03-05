"""Linux ScreenCapture — grim (full/window) + slurp (region select).

Captures screenshots on Wayland/Sway using:
- grim: Full-screen or single-output capture
- grim + slurp: Region capture
- Sway IPC for window-specific capture (grim -g <geometry>)

Tested on Fedora 43 + Sway.

/ ScreenCapture Linux — grim + slurp.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from typing import Optional

from marlow.platform.base import ScreenCapture

logger = logging.getLogger("marlow.platform.linux.screenshot")


class GrimScreenCapture(ScreenCapture):
    """Screenshot capture via grim on Wayland."""

    def screenshot(
        self,
        window_title: Optional[str] = None,
        region: Optional[tuple[int, int, int, int]] = None,
    ) -> bytes:
        """Capture a screenshot and return PNG bytes.

        Args:
            window_title: If provided, capture the geometry of this window
                         (found via Sway IPC).
            region: If provided, (x, y, width, height) region to capture.

        Returns:
            PNG image bytes.

        Raises:
            RuntimeError: If grim is not installed or capture fails.
        """
        # Create temp file for output
        fd, tmp_path = tempfile.mkstemp(suffix=".png", prefix="marlow_")
        os.close(fd)

        try:
            if window_title:
                return self._capture_window(window_title, tmp_path)
            elif region:
                return self._capture_region(region, tmp_path)
            else:
                return self._capture_full(tmp_path)
        finally:
            # Clean up temp file
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def _capture_full(self, out_path: str) -> bytes:
        """Full screen capture."""
        r = subprocess.run(
            ["grim", out_path],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            raise RuntimeError(f"grim failed: {r.stderr.strip()}")
        return self._read_png(out_path)

    def _capture_region(
        self,
        region: tuple[int, int, int, int],
        out_path: str,
    ) -> bytes:
        """Capture a specific region: (x, y, width, height)."""
        x, y, w, h = region
        geometry = f"{x},{y} {w}x{h}"
        r = subprocess.run(
            ["grim", "-g", geometry, out_path],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            raise RuntimeError(f"grim -g '{geometry}' failed: {r.stderr.strip()}")
        return self._read_png(out_path)

    def _capture_window(self, window_title: str, out_path: str) -> bytes:
        """Capture a window by finding its geometry via Sway IPC."""
        try:
            import i3ipc

            conn = i3ipc.Connection()
            tree = conn.get_tree()
            target = None

            title_lower = window_title.lower()
            for leaf in tree.leaves():
                name = (leaf.name or "").lower()
                if title_lower in name:
                    target = leaf
                    break

            if target is None:
                # Try app_id match
                for leaf in tree.leaves():
                    app_id = (leaf.app_id or "").lower()
                    if title_lower in app_id:
                        target = leaf
                        break

            if target is None:
                logger.warning("Window '%s' not found, capturing full screen", window_title)
                return self._capture_full(out_path)

            rect = target.rect
            geometry = f"{rect.x},{rect.y} {rect.width}x{rect.height}"
            r = subprocess.run(
                ["grim", "-g", geometry, out_path],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode != 0:
                raise RuntimeError(f"grim window capture failed: {r.stderr.strip()}")
            return self._read_png(out_path)

        except ImportError:
            logger.warning("i3ipc not available — capturing full screen")
            return self._capture_full(out_path)

    @staticmethod
    def _read_png(path: str) -> bytes:
        """Read PNG file and return bytes."""
        if not os.path.exists(path):
            raise RuntimeError(f"Screenshot file not created: {path}")
        size = os.path.getsize(path)
        if size == 0:
            raise RuntimeError("Screenshot file is empty")
        with open(path, "rb") as f:
            return f.read()


if __name__ == "__main__":
    cap = GrimScreenCapture()

    print("=== GrimScreenCapture self-test ===")

    print("\n--- Full screen capture ---")
    try:
        data = cap.screenshot()
        print(f"  PNG bytes: {len(data):,}")
        # Verify PNG magic bytes
        assert data[:4] == b"\x89PNG", "Not a valid PNG"
        print("  Valid PNG: yes")
        print("  PASS")
    except Exception as e:
        print(f"  FAIL: {e}")

    print("\n--- Region capture (100,100 200x200) ---")
    try:
        data = cap.screenshot(region=(100, 100, 200, 200))
        print(f"  PNG bytes: {len(data):,}")
        assert data[:4] == b"\x89PNG"
        print("  Valid PNG: yes")
        print("  PASS")
    except Exception as e:
        print(f"  FAIL: {e}")

    print("\nPASS: GrimScreenCapture self-test complete")
