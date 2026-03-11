"""RollbackExecutor — Reverts proactive actions when something goes wrong.

Maintains a journal of recent proactive actions with pre-action state.
Can rollback: launch_in_shadow (close), open_application (close),
move_to_user (move back), focus_window (restore previous focus).

Triggers: TORMENTA weather after proactive action, kill switch,
or proactive action failure.

/ Executor de rollback — revierte acciones proactivas cuando algo sale mal.
"""

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("marlow.kernel.rollback")


@dataclass
class ProactiveAction:
    """Record of a proactive action with pre-action state."""
    tool_name: str
    params: dict
    timestamp: float            # time.time()
    # Pre-action state from DesktopObserver
    pre_windows: dict = field(default_factory=dict)  # id -> WindowInfo
    pre_focused_id: Optional[int] = None
    # Post-action result
    result_data: Any = None
    rolled_back: bool = False

    @property
    def age_seconds(self) -> float:
        return time.time() - self.timestamp


# Rollback window: only rollback actions newer than this
ROLLBACK_WINDOW_SECONDS = 30.0
# Max journal size
MAX_JOURNAL = 20

# Tools that can be rolled back and their reversal strategy
REVERSIBLE_TOOLS = {
    "launch_in_shadow",
    "open_application",
    "move_to_user",
    "focus_window",
}


class RollbackExecutor:
    """Reverts proactive actions using compositor IPC.

    Subscribes to EventBus to auto-rollback when DesktopWeather
    reaches TORMENTA after a proactive action.
    """

    def __init__(
        self,
        pipeline: Any = None,
        desktop_observer: Any = None,
        event_bus: Any = None,
    ):
        self._pipeline = pipeline
        self._observer = desktop_observer
        self._event_bus = event_bus
        self._journal: deque[ProactiveAction] = deque(maxlen=MAX_JOURNAL)

    def record_action(
        self,
        tool_name: str,
        params: dict,
        result_data: Any = None,
    ):
        """Record a proactive action with pre-action state snapshot."""
        pre_windows = {}
        pre_focused_id = None

        if self._observer:
            try:
                state = self._observer.get_state()
                pre_windows = dict(state.windows)
                if state.focused_window:
                    pre_focused_id = state.focused_window.id
            except Exception:
                pass

        self._journal.append(ProactiveAction(
            tool_name=tool_name,
            params=params,
            timestamp=time.time(),
            pre_windows=pre_windows,
            pre_focused_id=pre_focused_id,
            result_data=result_data,
        ))

    async def rollback_last(self) -> bool:
        """Rollback the most recent proactive action."""
        if not self._journal:
            logger.debug("No actions to rollback")
            return False

        action = self._journal[-1]
        if action.rolled_back:
            logger.debug("Last action already rolled back")
            return False

        if action.age_seconds > ROLLBACK_WINDOW_SECONDS:
            logger.debug("Last action too old for rollback (%.0fs)", action.age_seconds)
            return False

        return await self._rollback_action(action)

    async def rollback_all_since(self, timestamp: float) -> int:
        """Rollback all proactive actions since a timestamp."""
        count = 0
        for action in reversed(self._journal):
            if action.timestamp < timestamp:
                break
            if action.rolled_back:
                continue
            if await self._rollback_action(action):
                count += 1
        return count

    async def on_weather_event(self, event):
        """EventBus handler: auto-rollback on TORMENTA."""
        climate = getattr(event, "data", {}).get("climate", "")
        if climate != "TORMENTA":
            return

        # Only rollback recent proactive actions (last 30s)
        cutoff = time.time() - ROLLBACK_WINDOW_SECONDS
        recent = [
            a for a in self._journal
            if a.timestamp >= cutoff and not a.rolled_back
        ]
        if recent:
            logger.warning(
                "TORMENTA detected — rolling back %d recent proactive actions",
                len(recent),
            )
            for action in reversed(recent):
                await self._rollback_action(action)

    async def _rollback_action(self, action: ProactiveAction) -> bool:
        """Attempt to rollback a single action."""
        tool = action.tool_name

        if tool not in REVERSIBLE_TOOLS:
            logger.info(
                "Action %s not reversible, skipping rollback", tool,
            )
            return False

        try:
            success = False

            if tool in ("launch_in_shadow", "open_application"):
                success = await self._rollback_launch(action)
            elif tool == "move_to_user":
                success = await self._rollback_move_to_user(action)
            elif tool == "focus_window":
                success = await self._rollback_focus(action)

            if success:
                action.rolled_back = True
                logger.info("Rolled back %s successfully", tool)
            else:
                logger.warning("Failed to rollback %s", tool)

            return success

        except Exception as e:
            logger.warning("Rollback error for %s: %s", tool, e)
            return False

    async def _rollback_launch(self, action: ProactiveAction) -> bool:
        """Close a window that was launched proactively."""
        if not self._pipeline or not self._observer:
            return False

        # Find the window that appeared after this action
        state = self._observer.get_state()
        pre_ids = set(action.pre_windows.keys())

        # New windows = current - pre
        new_windows = [
            wid for wid in state.windows
            if wid not in pre_ids
        ]

        if not new_windows:
            logger.debug("No new windows found for rollback")
            return False

        # Close the new window(s) via close_window tool
        for wid in new_windows:
            try:
                await self._pipeline.execute(
                    "close_window",
                    {"window_id": wid},
                    origin="proactive",
                )
            except Exception as e:
                logger.debug("close_window failed for %d: %s", wid, e)

        return True

    async def _rollback_move_to_user(self, action: ProactiveAction) -> bool:
        """Move a window back to shadow space."""
        if not self._pipeline:
            return False

        window_id = action.params.get("window_id")
        if window_id is None:
            return False

        try:
            result = await self._pipeline.execute(
                "move_to_shadow",
                {"window_id": window_id},
                origin="proactive",
            )
            return result.success
        except Exception:
            return False

    async def _rollback_focus(self, action: ProactiveAction) -> bool:
        """Restore focus to the previously focused window."""
        if not self._pipeline or action.pre_focused_id is None:
            return False

        try:
            result = await self._pipeline.execute(
                "focus_window",
                {"window_id": action.pre_focused_id},
                origin="proactive",
            )
            return result.success
        except Exception:
            return False
