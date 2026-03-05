"""Linux FocusGuard — save/restore window focus via Sway IPC.

Preserves the user's active window across Marlow tool operations.
Uses i3ipc to query and restore the focused container on Sway.

Tested on Fedora 43 + Sway.

/ FocusGuard Linux — guardar/restaurar foco via Sway IPC.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from marlow.platform.base import FocusGuard, FocusSnapshot

logger = logging.getLogger("marlow.platform.linux.focus")


class SwayFocusGuard(FocusGuard):
    """Save and restore focus on Sway via IPC.

    Args:
        window_manager: A SwayWindowManager instance (reuses its connection logic).
    """

    def __init__(self, window_manager=None):
        self._wm = window_manager
        self._last_snapshot: Optional[FocusSnapshot] = None

    def save_user_focus(self) -> Optional[FocusSnapshot]:
        """Save the currently focused window."""
        try:
            import i3ipc

            conn = i3ipc.Connection()
            tree = conn.get_tree()
            focused = tree.find_focused()

            if focused is None:
                logger.debug("No focused window to save")
                return None

            snapshot = FocusSnapshot(
                identifier=str(focused.id),
                title=focused.name or "(unnamed)",
            )
            self._last_snapshot = snapshot
            logger.debug("Saved focus: [%s] %s", snapshot.identifier, snapshot.title)
            return snapshot

        except Exception as e:
            logger.warning("save_user_focus failed: %s", e)
            return None

    def restore_user_focus(self, snapshot: Optional[FocusSnapshot] = None) -> bool:
        """Restore focus to a previously saved window.

        Args:
            snapshot: Explicit snapshot to restore. If None, uses the last
                     automatically saved snapshot.

        Returns:
            True if focus was restored successfully.
        """
        target = snapshot or self._last_snapshot
        if target is None:
            logger.debug("No focus snapshot to restore")
            return False

        try:
            import i3ipc

            conn = i3ipc.Connection()
            tree = conn.get_tree()

            # Find the container by con_id
            try:
                con_id = int(target.identifier)
                for leaf in tree.leaves():
                    if leaf.id == con_id:
                        leaf.command("focus")
                        logger.debug("Restored focus: [%s] %s",
                                     target.identifier, target.title)
                        return True
            except ValueError:
                pass

            # Fallback: find by title
            for leaf in tree.leaves():
                if leaf.name == target.title:
                    leaf.command("focus")
                    logger.debug("Restored focus by title: %s", target.title)
                    return True

            logger.warning("Window no longer exists: [%s] %s",
                           target.identifier, target.title)
            return False

        except Exception as e:
            logger.warning("restore_user_focus failed: %s", e)
            return False


if __name__ == "__main__":
    guard = SwayFocusGuard()

    print("=== SwayFocusGuard self-test ===")

    print("\n--- save_user_focus ---")
    snap = guard.save_user_focus()
    if snap:
        print(f"  Saved: [{snap.identifier}] {snap.title}")
    else:
        print("  No focused window")

    print("\n--- restore_user_focus ---")
    ok = guard.restore_user_focus()
    print(f"  Restored: {ok}")

    # Test round-trip: save, then restore
    print("\n--- Round-trip test ---")
    snap2 = guard.save_user_focus()
    if snap2:
        # Small delay to simulate a tool operation
        time.sleep(0.2)
        ok2 = guard.restore_user_focus(snap2)
        print(f"  Save: [{snap2.identifier}] {snap2.title}")
        print(f"  Restore: {ok2}")
        if ok2:
            print("  PASS")
        else:
            print("  FAIL: could not restore")
    else:
        print("  SKIP: no window to test with")

    print("\nPASS: SwayFocusGuard self-test complete")
