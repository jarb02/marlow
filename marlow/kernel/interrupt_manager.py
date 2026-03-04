"""InterruptManager — Priority stack for suspend/resume during task execution.

5 priority levels (P0-P4) with hysteresis and cooldown to prevent
thrashing. Task stack allows suspending the current task to handle
a higher-priority interrupt, then resuming where it left off.

Based on Game AI interrupt patterns from RTS games.

/ Gestor de interrupciones con stack de prioridad para suspend/resume.
"""

import logging
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional, Any

logger = logging.getLogger("marlow.kernel.interrupt_manager")


class Priority(IntEnum):
    """Interrupt priority levels. Lower number = higher priority."""
    P0_CRITICAL = 0   # App crash, OS error -> abort
    P1_HIGH = 1       # Blocking dialog -> suspend task, resolve, resume
    P2_MEDIUM = 2     # Focus lost unexpectedly -> verify context
    P3_LOW = 3        # Windows notification -> ignore
    P4_NOISE = 4      # Tooltip, hover effect -> ignore completely


@dataclass
class Interrupt:
    """An interrupt event that may need handling."""
    priority: Priority
    source: str           # what caused it: "dialog", "focus_lost", "crash", "notification"
    description: str      # human-readable description
    data: dict = field(default_factory=dict)  # additional context
    timestamp: float = field(default_factory=time.time)

    @property
    def is_blocking(self) -> bool:
        """Does this interrupt block the current task?"""
        return self.priority <= Priority.P1_HIGH


@dataclass
class SuspendedTask:
    """A task that was suspended by an interrupt."""
    goal_id: str
    step_index: int        # which step was executing
    tool_name: str         # what tool was running
    params: dict           # tool parameters
    expected_app: str      # which app should be active
    suspended_at: float = field(default_factory=time.time)
    interrupt: Optional[Interrupt] = None  # what caused the suspension

    @property
    def age_seconds(self) -> float:
        return time.time() - self.suspended_at


class InterruptManager:
    """Manages interruptions during task execution.

    Features:
    - 5 priority levels (P0-P4)
    - Task stack: suspend current task, handle interrupt, resume
    - Anti-thrashing: cooldown between interrupts, hysteresis
    - Configurable thresholds

    Based on Game AI interrupt patterns from RTS games.
    """

    # Hysteresis: interrupt must be this much MORE priority (lower number) to interrupt
    HYSTERESIS = 1  # must be at least 1 level higher priority

    def __init__(
        self,
        cooldown: float = 2.0,
        max_stack_depth: int = 5,
    ):
        self._task_stack: list[SuspendedTask] = []
        self._last_interrupt_time: float = 0.0
        self._cooldown = cooldown
        self._max_stack_depth = max_stack_depth
        self._current_priority: Priority = Priority.P4_NOISE  # lowest by default
        self._interrupt_history: list[Interrupt] = []
        self._max_history = 50

    @property
    def stack_depth(self) -> int:
        return len(self._task_stack)

    @property
    def has_suspended_tasks(self) -> bool:
        return len(self._task_stack) > 0

    @property
    def current_priority(self) -> Priority:
        return self._current_priority

    def set_current_priority(self, priority: Priority):
        """Set priority of the currently executing task."""
        self._current_priority = priority

    def should_interrupt(self, interrupt: Interrupt) -> bool:
        """Decide whether an interrupt should pause the current task.

        Rules:
        1. P0 ALWAYS interrupts (critical)
        2. P3 and P4 never interrupt
        3. Cooldown: no interrupts within cooldown period
        4. Stack depth: don't nest too deep
        5. Hysteresis: interrupt must be significantly higher priority
        """
        # P0 always interrupts
        if interrupt.priority == Priority.P0_CRITICAL:
            return True

        # P3 and P4 never interrupt
        if interrupt.priority >= Priority.P3_LOW:
            return False

        # Cooldown check
        if time.time() - self._last_interrupt_time < self._cooldown:
            logger.debug(f"Interrupt {interrupt.source} rejected: cooldown active")
            return False

        # Stack depth check
        if self.stack_depth >= self._max_stack_depth:
            logger.warning(f"Interrupt {interrupt.source} rejected: stack full ({self.stack_depth})")
            return False

        # Hysteresis: interrupt must be at least HYSTERESIS levels more priority
        if interrupt.priority >= self._current_priority - self.HYSTERESIS:
            logger.debug(
                f"Interrupt {interrupt.source} (P{interrupt.priority}) rejected: "
                f"not enough priority over current P{self._current_priority}"
            )
            return False

        return True

    def suspend_task(
        self,
        goal_id: str,
        step_index: int,
        tool_name: str,
        params: dict,
        expected_app: str,
        interrupt: Interrupt,
    ) -> SuspendedTask:
        """Suspend the current task and push it onto the stack.

        Returns the suspended task for reference.
        """
        suspended = SuspendedTask(
            goal_id=goal_id,
            step_index=step_index,
            tool_name=tool_name,
            params=params,
            expected_app=expected_app,
            interrupt=interrupt,
        )
        self._task_stack.append(suspended)
        self._last_interrupt_time = time.time()

        # Record in history
        self._interrupt_history.append(interrupt)
        if len(self._interrupt_history) > self._max_history:
            self._interrupt_history.pop(0)

        logger.info(
            f"Task suspended at step {step_index} ({tool_name}) "
            f"by P{interrupt.priority} interrupt: {interrupt.description}"
        )
        return suspended

    def resume_task(self) -> Optional[SuspendedTask]:
        """Pop and return the most recently suspended task.

        Returns None if stack is empty.
        """
        if not self._task_stack:
            return None

        task = self._task_stack.pop()
        logger.info(
            f"Resuming task: step {task.step_index} ({task.tool_name}), "
            f"was suspended {task.age_seconds:.1f}s ago"
        )
        return task

    def classify_event(
        self,
        event_type: str,
        title: str = "",
        message: str = "",
    ) -> Interrupt:
        """Classify a desktop event into an Interrupt with appropriate priority.

        Event types: "dialog", "crash", "not_responding", "focus_lost",
                     "notification", "window_appeared", "window_disappeared"
        """
        title_lower = title.lower()
        msg_lower = message.lower()

        # P0: Crashes and critical errors
        if event_type in ("crash", "not_responding"):
            return Interrupt(Priority.P0_CRITICAL, event_type, f"App critical: {title}")

        if any(w in msg_lower for w in ["fatal", "crash", "unrecoverable"]):
            return Interrupt(Priority.P0_CRITICAL, "crash", f"Fatal error: {title}")

        # P1: Blocking dialogs
        if event_type == "dialog":
            # Error dialogs
            if any(w in title_lower for w in ["error", "warning"]):
                return Interrupt(
                    Priority.P1_HIGH, "dialog", f"Error dialog: {title}",
                    {"title": title, "message": message},
                )
            # File exists / overwrite
            if any(w in msg_lower for w in ["already exists", "replace", "overwrite"]):
                return Interrupt(
                    Priority.P1_HIGH, "dialog", f"File exists: {title}",
                    {"title": title, "message": message},
                )
            # Confirmation dialogs
            if any(w in msg_lower for w in ["are you sure", "do you want", "save changes"]):
                return Interrupt(
                    Priority.P1_HIGH, "dialog", f"Confirmation: {title}",
                    {"title": title, "message": message},
                )
            # Generic dialog
            return Interrupt(
                Priority.P2_MEDIUM, "dialog", f"Dialog: {title}",
                {"title": title, "message": message},
            )

        # P2: Focus changes
        if event_type == "focus_lost":
            return Interrupt(Priority.P2_MEDIUM, "focus_lost", f"Lost focus from: {title}")

        if event_type == "window_disappeared":
            return Interrupt(Priority.P2_MEDIUM, "window_disappeared", f"Window closed: {title}")

        # P3: Notifications
        if event_type == "notification":
            return Interrupt(Priority.P3_LOW, "notification", f"Notification: {title}")

        if event_type == "window_appeared":
            # Check if it looks like a notification
            if any(w in title_lower for w in ["update", "notification", "toast"]):
                return Interrupt(Priority.P3_LOW, "notification", f"Notification window: {title}")
            return Interrupt(Priority.P2_MEDIUM, "window_appeared", f"New window: {title}")

        # P4: Everything else
        return Interrupt(Priority.P4_NOISE, event_type, f"Event: {event_type} - {title}")

    def get_recent_interrupts(self, seconds: int = 60) -> list[Interrupt]:
        """Get interrupts from the last N seconds."""
        cutoff = time.time() - seconds
        return [i for i in self._interrupt_history if i.timestamp >= cutoff]

    def get_interrupt_rate(self, seconds: int = 60) -> float:
        """Get interrupts per minute over the last N seconds."""
        recent = self.get_recent_interrupts(seconds)
        if seconds <= 0:
            return 0.0
        return len(recent) * 60.0 / seconds

    def clear_stack(self):
        """Clear the entire task stack (e.g., on abort)."""
        count = len(self._task_stack)
        self._task_stack.clear()
        if count:
            logger.info(f"Cleared {count} suspended task(s) from stack")
