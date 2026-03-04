"""Typed events for the Marlow Kernel EventBus.

All events are immutable frozen dataclasses with a dotted event_type
string (e.g. "goal.started", "action.completed", "world.dialog_detected").

Categories: goal, action, world, system, audio.

/ Eventos tipados para el EventBus del Kernel.
"""

import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Optional


class EventPriority(IntEnum):
    """Event dispatch priority. Lower = dispatched first."""
    CRITICAL = 0    # Kill switch, crashes
    HIGH = 1        # Dialogs, errors
    NORMAL = 2      # Goal steps, tool execution
    LOW = 3         # Logging, metrics
    BACKGROUND = 4  # Cleanup, maintenance


@dataclass(frozen=True)
class Event:
    """Base event class. All events are immutable."""
    event_type: str          # e.g. "goal.started", "action.completed"
    timestamp: float = field(default_factory=time.time)
    source: str = ""         # component that emitted the event
    correlation_id: str = "" # links related events (e.g. goal_id)
    priority: EventPriority = EventPriority.NORMAL
    data: dict = field(default_factory=dict)

    @property
    def category(self) -> str:
        """First part of event_type: 'goal' from 'goal.started'."""
        return self.event_type.split(".")[0] if "." in self.event_type else self.event_type


# === Goal Events ===

@dataclass(frozen=True)
class GoalStarted(Event):
    event_type: str = "goal.started"
    goal_text: str = ""

@dataclass(frozen=True)
class GoalCompleted(Event):
    event_type: str = "goal.completed"
    goal_text: str = ""
    success: bool = True
    steps_executed: int = 0

@dataclass(frozen=True)
class GoalFailed(Event):
    event_type: str = "goal.failed"
    goal_text: str = ""
    error: str = ""
    step_index: int = 0

@dataclass(frozen=True)
class GoalReplanning(Event):
    event_type: str = "goal.replanning"
    reason: str = ""
    attempt: int = 0


# === Action Events ===

@dataclass(frozen=True)
class ActionStarting(Event):
    event_type: str = "action.starting"
    tool_name: str = ""
    params: dict = field(default_factory=dict)
    pre_score: float = 0.0
    priority: EventPriority = EventPriority.NORMAL

@dataclass(frozen=True)
class ActionCompleted(Event):
    event_type: str = "action.completed"
    tool_name: str = ""
    success: bool = True
    duration_ms: float = 0.0

@dataclass(frozen=True)
class ActionFailed(Event):
    event_type: str = "action.failed"
    tool_name: str = ""
    error: str = ""
    priority: EventPriority = EventPriority.HIGH


# === World Events ===

@dataclass(frozen=True)
class DialogDetected(Event):
    event_type: str = "world.dialog_detected"
    dialog_title: str = ""
    dialog_type: str = ""  # from DialogType enum
    priority: EventPriority = EventPriority.HIGH

@dataclass(frozen=True)
class DialogHandled(Event):
    event_type: str = "world.dialog_handled"
    dialog_title: str = ""
    action_taken: str = ""

@dataclass(frozen=True)
class WindowChanged(Event):
    event_type: str = "world.window_changed"
    change_type: str = ""  # appeared, disappeared, focus_lost, focus_gained
    window_title: str = ""

@dataclass(frozen=True)
class FocusLost(Event):
    event_type: str = "world.focus_lost"
    expected_app: str = ""
    actual_app: str = ""
    priority: EventPriority = EventPriority.HIGH


# === System Events ===

@dataclass(frozen=True)
class KillSwitchActivated(Event):
    event_type: str = "system.kill_switch"
    priority: EventPriority = EventPriority.CRITICAL

@dataclass(frozen=True)
class InterruptReceived(Event):
    event_type: str = "system.interrupt"
    interrupt_priority: str = ""  # P0-P4
    interrupt_source: str = ""
    description: str = ""


# === Audio Events ===

@dataclass(frozen=True)
class SpeechStarted(Event):
    event_type: str = "audio.speech_started"

@dataclass(frozen=True)
class SpeechEnded(Event):
    event_type: str = "audio.speech_ended"
    transcript: str = ""

@dataclass(frozen=True)
class TTSStarted(Event):
    event_type: str = "audio.tts_started"
    text: str = ""
    engine: str = ""

@dataclass(frozen=True)
class TTSCompleted(Event):
    event_type: str = "audio.tts_completed"
    engine: str = ""


# === Convenience: all event types for registration ===
ALL_EVENT_TYPES = [
    "goal.started", "goal.completed", "goal.failed", "goal.replanning",
    "action.starting", "action.completed", "action.failed",
    "world.dialog_detected", "world.dialog_handled", "world.window_changed", "world.focus_lost",
    "system.kill_switch", "system.interrupt",
    "audio.speech_started", "audio.speech_ended", "audio.tts_started", "audio.tts_completed",
]

ALL_CATEGORIES = ["goal", "action", "world", "system", "audio"]
