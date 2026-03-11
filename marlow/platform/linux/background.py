"""Linux Background Mode — Sway workspace-based Shadow Mode.

Uses a dedicated Sway workspace for Marlow operations, invisible
to the user. Much more elegant than Windows dual-monitor approach.

/ Modo background Linux — Shadow Mode basado en workspaces de Sway.
"""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Optional

from marlow.platform.base import BackgroundProvider

logger = logging.getLogger("marlow.platform.linux.background")

# Agent workspace config
_AGENT_WS_NUM = 9
_AGENT_WS_NAME = "9:marlow-agent"


class SwayBackgroundProvider(BackgroundProvider):
    """Background mode via Sway workspace isolation."""

    def __init__(self, window_manager=None):
        self._wm = window_manager
        self._active = False
        self._agent_screen_only = False
        self._user_ws: Optional[str] = None  # workspace to return windows to

    def setup_background_mode(self, preferred_mode: Optional[str] = None) -> dict:
        # Create the agent workspace by switching to it and back
        current = self._get_current_workspace()
        self._user_ws = current

        # Create workspace (just referencing it creates it in Sway)
        ok = self._sway_cmd(f"workspace {_AGENT_WS_NAME}")
        if not ok:
            return {"error": "Failed to create agent workspace"}

        # Switch back to user workspace
        if current:
            self._sway_cmd(f"workspace {current}")

        self._active = True
        logger.info("Background mode active: workspace %s", _AGENT_WS_NAME)

        return {
            "success": True,
            "mode": "sway_workspace",
            "agent_workspace": _AGENT_WS_NAME,
            "user_workspace": current,
            "hint": "Marlow can now move windows to an invisible workspace.",
        }

    def move_to_agent_screen(self, window_title: Optional[str] = None) -> dict:
        if not self._active:
            setup = self.setup_background_mode()
            if "error" in setup:
                return setup

        # Save current workspace before moving
        self._user_ws = self._get_current_workspace()

        if window_title:
            # Move specific window by title
            # Escape title for sway criteria
            escaped = window_title.replace('"', '\\"')
            ok = self._sway_cmd(
                f'[title=".*{escaped}.*"] move workspace {_AGENT_WS_NAME}'
            )
            if not ok:
                return {
                    "error": f"Failed to move window '{window_title}' to agent workspace",
                    "hint": "Check that the window title matches exactly.",
                }
            return {
                "success": True,
                "moved": window_title,
                "to": _AGENT_WS_NAME,
            }
        else:
            # Move focused window
            ok = self._sway_cmd(f"move workspace {_AGENT_WS_NAME}")
            if not ok:
                return {"error": "Failed to move focused window to agent workspace"}
            return {
                "success": True,
                "moved": "focused_window",
                "to": _AGENT_WS_NAME,
            }

    def move_to_user_screen(self, window_title: Optional[str] = None) -> dict:
        target_ws = self._user_ws or self._get_current_workspace() or "1"

        if window_title:
            escaped = window_title.replace('"', '\\"')
            ok = self._sway_cmd(
                f'[title=".*{escaped}.*"] move workspace {target_ws}'
            )
            if not ok:
                return {
                    "error": f"Failed to move window '{window_title}' to user workspace",
                }
            return {
                "success": True,
                "moved": window_title,
                "to": target_ws,
            }
        else:
            # Switch to agent workspace first, move focused, switch back
            self._sway_cmd(f"workspace {_AGENT_WS_NAME}")
            ok = self._sway_cmd(f"move workspace {target_ws}")
            self._sway_cmd(f"workspace {target_ws}")
            if not ok:
                return {"error": "Failed to move window to user workspace"}
            return {
                "success": True,
                "moved": "focused_window",
                "to": target_ws,
            }

    def get_agent_screen_state(self) -> dict:
        # Get tree and find windows in agent workspace
        try:
            r = subprocess.run(
                ["swaymsg", "-t", "get_tree"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode != 0:
                return {"error": "Failed to get Sway tree"}

            tree = json.loads(r.stdout)
            agent_windows = self._find_windows_in_workspace(tree, _AGENT_WS_NAME)

            return {
                "success": True,
                "active": self._active,
                "agent_workspace": _AGENT_WS_NAME,
                "agent_screen_only": self._agent_screen_only,
                "windows": agent_windows,
                "window_count": len(agent_windows),
            }
        except Exception as e:
            return {"error": str(e)}

    def set_agent_screen_only(self, enabled: bool) -> dict:
        self._agent_screen_only = enabled
        if enabled and not self._active:
            self.setup_background_mode()
        return {
            "success": True,
            "agent_screen_only": enabled,
            "hint": "When enabled, Marlow only operates in its workspace."
                    if enabled else "Agent can now interact with user workspace.",
        }

    # ── Helpers ──

    def _sway_cmd(self, cmd: str) -> bool:
        """Run a swaymsg command."""
        try:
            r = subprocess.run(
                ["swaymsg", cmd],
                capture_output=True, text=True, timeout=5,
            )
            return r.returncode == 0
        except Exception as e:
            logger.error("swaymsg failed: %s", e)
            return False

    def _get_current_workspace(self) -> Optional[str]:
        """Get the name of the currently focused workspace."""
        try:
            r = subprocess.run(
                ["swaymsg", "-t", "get_workspaces"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode != 0:
                return None
            workspaces = json.loads(r.stdout)
            for ws in workspaces:
                if ws.get("focused"):
                    return ws.get("name")
        except Exception:
            pass
        return None

    def _find_windows_in_workspace(self, node: dict, ws_name: str) -> list[dict]:
        """Recursively find windows in a specific workspace."""
        results = []

        # Check if this node is the target workspace
        if node.get("type") == "workspace" and node.get("name") == ws_name:
            # Collect all window descendants
            return self._collect_windows(node)

        # Recurse into children
        for child in node.get("nodes", []):
            results.extend(self._find_windows_in_workspace(child, ws_name))
        for child in node.get("floating_nodes", []):
            results.extend(self._find_windows_in_workspace(child, ws_name))

        return results

    def _collect_windows(self, node: dict) -> list[dict]:
        """Collect all window nodes from a subtree."""
        results = []
        if node.get("type") == "con" and node.get("name"):
            results.append({
                "title": node.get("name", ""),
                "app_id": node.get("app_id", ""),
                "pid": node.get("pid", 0),
            })
        for child in node.get("nodes", []):
            results.extend(self._collect_windows(child))
        for child in node.get("floating_nodes", []):
            results.extend(self._collect_windows(child))
        return results


# Alias for __init__.py import compatibility
LinuxBackgroundProvider = SwayBackgroundProvider
