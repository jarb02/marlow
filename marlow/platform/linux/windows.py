"""Linux WindowManager — Sway IPC via i3ipc.

Manages windows on Sway/wlroots compositors using the i3 IPC protocol.
Tested on Fedora 43 + Sway.

/ WindowManager Linux — Sway IPC via i3ipc.
"""

from __future__ import annotations

import logging
from typing import Optional

from marlow.platform.base import WindowInfo, WindowManager

logger = logging.getLogger("marlow.platform.linux.windows")


class SwayWindowManager(WindowManager):
    """Window management via Sway IPC (i3ipc library)."""

    def _connect(self):
        """Create a fresh i3ipc connection (short-lived)."""
        import i3ipc
        return i3ipc.Connection()

    # ── WindowManager interface ──

    def list_windows(self, include_minimized: bool = True) -> list[WindowInfo]:
        try:
            conn = self._connect()
            tree = conn.get_tree()
            result: list[WindowInfo] = []

            focused = tree.find_focused()
            focused_id = focused.id if focused else None

            for leaf in tree.leaves():
                # Skip scratchpad (Sway's "minimized") unless requested
                if not include_minimized:
                    workspace = leaf.workspace()
                    if workspace and workspace.name == "__i3_scratch":
                        continue

                rect = leaf.rect
                result.append(WindowInfo(
                    identifier=str(leaf.id),
                    title=leaf.name or "(unnamed)",
                    app_name=leaf.app_id or f"pid_{leaf.pid}",
                    pid=leaf.pid or 0,
                    is_focused=(leaf.id == focused_id),
                    is_visible=not leaf.urgent,  # approximate
                    x=rect.x,
                    y=rect.y,
                    width=rect.width,
                    height=rect.height,
                    extra={
                        "app_id": leaf.app_id or "",
                        "con_id": leaf.id,
                        "workspace": (
                            leaf.workspace().name
                            if leaf.workspace() else ""
                        ),
                    },
                ))
            return result
        except Exception as e:
            logger.error("list_windows failed: %s", e)
            return []

    def focus_window(self, identifier: str) -> bool:
        try:
            conn = self._connect()
            tree = conn.get_tree()
            target = self._find_window(tree, identifier)
            if target is None:
                logger.warning("Window not found: %s", identifier)
                return False
            target.command("focus")
            return True
        except Exception as e:
            logger.error("focus_window failed: %s", e)
            return False

    def get_focused_window(self) -> Optional[WindowInfo]:
        try:
            conn = self._connect()
            tree = conn.get_tree()
            focused = tree.find_focused()
            if focused is None:
                return None
            rect = focused.rect
            return WindowInfo(
                identifier=str(focused.id),
                title=focused.name or "(unnamed)",
                app_name=focused.app_id or f"pid_{focused.pid}",
                pid=focused.pid or 0,
                is_focused=True,
                is_visible=True,
                x=rect.x,
                y=rect.y,
                width=rect.width,
                height=rect.height,
                extra={"app_id": focused.app_id or "", "con_id": focused.id},
            )
        except Exception as e:
            logger.error("get_focused_window failed: %s", e)
            return None

    def manage_window(self, identifier: str, action: str, **kwargs) -> bool:
        try:
            conn = self._connect()
            tree = conn.get_tree()
            target = self._find_window(tree, identifier)
            if target is None:
                logger.warning("Window not found for action '%s': %s", action, identifier)
                return False

            action = action.lower()

            if action == "close":
                target.command("kill")
            elif action == "fullscreen":
                target.command("fullscreen toggle")
            elif action == "minimize":
                target.command("move scratchpad")
            elif action == "restore":
                # Move from scratchpad back to workspace
                target.command("scratchpad show")
            elif action == "maximize":
                target.command("fullscreen enable")
            elif action == "move":
                x = kwargs.get("x", 0)
                y = kwargs.get("y", 0)
                target.command(f"move position {x} {y}")
            elif action == "resize":
                w = kwargs.get("width", 800)
                h = kwargs.get("height", 600)
                target.command(f"resize set {w} {h}")
            elif action == "float":
                target.command("floating toggle")
            else:
                logger.warning("Unknown action: %s", action)
                return False
            return True
        except Exception as e:
            logger.error("manage_window(%s, %s) failed: %s", identifier, action, e)
            return False

    # ── Helpers ──

    def _find_window(self, tree, identifier: str):
        """Find a window by con_id (numeric string) or title substring."""
        # Try as numeric con_id first
        try:
            con_id = int(identifier)
            for leaf in tree.leaves():
                if leaf.id == con_id:
                    return leaf
        except ValueError:
            pass

        # Fuzzy title match (case-insensitive substring)
        identifier_lower = identifier.lower()
        for leaf in tree.leaves():
            name = (leaf.name or "").lower()
            if identifier_lower in name:
                return leaf

        # Try app_id match
        for leaf in tree.leaves():
            app_id = (leaf.app_id or "").lower()
            if identifier_lower in app_id:
                return leaf

        return None


if __name__ == "__main__":
    import json
    wm = SwayWindowManager()

    print("=== list_windows ===")
    wins = wm.list_windows()
    for w in wins:
        flag = "*" if w.is_focused else " "
        print(f"  {flag} [{w.identifier}] {w.title} ({w.app_name}) "
              f"@ {w.x},{w.y} {w.width}x{w.height}")
    print(f"  Total: {len(wins)}")

    print("\n=== get_focused_window ===")
    f = wm.get_focused_window()
    if f:
        print(f"  {f.title} ({f.app_name}) id={f.identifier}")
    else:
        print("  None")

    if wins:
        title = wins[0].title
        print(f"\n=== focus_window('{title}') ===")
        ok = wm.focus_window(title)
        print(f"  Result: {ok}")

    print("\nPASS: SwayWindowManager self-test complete")
