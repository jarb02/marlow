"""
Learning from Demonstration (LfD) -- Marlow observa al usuario y aprende.

Flujo:
1. Usuario activa modo observacion
2. Usuario hace una tarea manualmente (abrir app, escribir, guardar, etc.)
3. DemonstrationRecorder captura eventos UIA + screenshots
4. PlanExtractor convierte la timeline en un plan Marlow reproducible
5. Plan se guarda en Plan Library para replay futuro

/ Marlow observa demostraciones del usuario y extrae planes reproducibles.
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, Any

logger = logging.getLogger("marlow.kernel.demonstration")


# ---------------------------------------------------------------
# Data Types
# ---------------------------------------------------------------

class DemoEventType(Enum):
    """Types of events captured during demonstration."""
    WINDOW_OPENED = "window_opened"
    WINDOW_CLOSED = "window_closed"
    WINDOW_FOCUSED = "window_focused"
    TEXT_TYPED = "text_typed"
    KEY_PRESSED = "key_pressed"
    ELEMENT_CLICKED = "element_clicked"
    MENU_SELECTED = "menu_selected"
    DIALOG_APPEARED = "dialog_appeared"
    SCREENSHOT = "screenshot"
    CUSTOM = "custom"


@dataclass
class DemoEvent:
    """A single event captured during user demonstration."""
    event_type: DemoEventType
    timestamp: float
    window_title: str = ""
    element_name: str = ""
    element_type: str = ""           # button, edit, menuitem, etc.
    value: str = ""                  # text typed, key pressed, etc.
    app_name: str = ""
    screenshot_path: str = ""
    raw_data: dict = field(default_factory=dict)

    @property
    def age_seconds(self) -> float:
        return time.time() - self.timestamp

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type.value,
            "timestamp": self.timestamp,
            "window_title": self.window_title,
            "element_name": self.element_name,
            "element_type": self.element_type,
            "value": self.value,
            "app_name": self.app_name,
            "screenshot_path": self.screenshot_path,
        }


@dataclass
class DemoStep:
    """A processed step extracted from demo events -- maps to a Marlow tool call."""
    tool_name: str
    params: dict
    description: str
    source_events: list[int] = field(default_factory=list)  # indices into event timeline
    confidence: float = 1.0  # how confident we are in this mapping

    def to_dict(self) -> dict:
        return {
            "tool": self.tool_name,
            "args": self.params,
            "description": self.description,
            "confidence": self.confidence,
        }


@dataclass
class Demonstration:
    """A complete recorded demonstration."""
    name: str
    description: str
    started_at: float
    ended_at: float = 0.0
    events: list[DemoEvent] = field(default_factory=list)
    extracted_steps: list[DemoStep] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        if self.ended_at > 0:
            return self.ended_at - self.started_at
        return time.time() - self.started_at

    @property
    def event_count(self) -> int:
        return len(self.events)

    @property
    def step_count(self) -> int:
        return len(self.extracted_steps)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "started_at": datetime.fromtimestamp(self.started_at).isoformat(),
            "ended_at": datetime.fromtimestamp(self.ended_at).isoformat() if self.ended_at else "",
            "duration_seconds": round(self.duration_seconds, 1),
            "event_count": self.event_count,
            "step_count": self.step_count,
            "events": [e.to_dict() for e in self.events],
            "extracted_steps": [s.to_dict() for s in self.extracted_steps],
            "screenshots": self.screenshots,
        }


# ---------------------------------------------------------------
# DemonstrationRecorder
# ---------------------------------------------------------------

class DemonstrationRecorder:
    """
    Records user demonstrations by capturing UIA events.

    Usage:
        recorder = DemonstrationRecorder()
        recorder.start("Save file in Notepad", "Demo of saving a text file")
        # ... user does the task manually ...
        demo = recorder.stop()
        # demo.events contains the captured timeline

    / Graba demostraciones del usuario capturando eventos UIA.
    """

    DEMO_DIR = os.path.expanduser("~/.marlow/demonstrations")
    SCREENSHOT_INTERVAL = 3.0  # seconds between auto-screenshots

    def __init__(self):
        self._recording = False
        self._current_demo: Optional[Demonstration] = None
        self._last_screenshot_time: float = 0.0
        self._last_window: str = ""
        self._last_focus_time: float = 0.0
        os.makedirs(self.DEMO_DIR, exist_ok=True)

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def current_demo(self) -> Optional[Demonstration]:
        return self._current_demo

    def start(self, name: str, description: str = "") -> Demonstration:
        """Start recording a new demonstration."""
        if self._recording:
            logger.warning("Already recording. Stop first.")
            return self._current_demo

        self._current_demo = Demonstration(
            name=name,
            description=description,
            started_at=time.time(),
        )
        self._recording = True
        self._last_window = ""
        self._last_screenshot_time = 0.0

        logger.info(f"Recording started: '{name}'")
        return self._current_demo

    def stop(self) -> Optional[Demonstration]:
        """Stop recording and return the demonstration."""
        if not self._recording or not self._current_demo:
            logger.warning("Not recording.")
            return None

        self._current_demo.ended_at = time.time()
        self._recording = False

        demo = self._current_demo
        logger.info(
            f"Recording stopped: '{demo.name}' -- "
            f"{demo.event_count} events, {demo.duration_seconds:.1f}s"
        )

        return demo

    def add_event(self, event: DemoEvent):
        """Add an event to the current recording."""
        if not self._recording or not self._current_demo:
            return

        # Deduplicate rapid focus events (same window within 0.5s)
        if event.event_type == DemoEventType.WINDOW_FOCUSED:
            if (event.window_title == self._last_window
                    and time.time() - self._last_focus_time < 0.5):
                return
            self._last_window = event.window_title
            self._last_focus_time = time.time()

        self._current_demo.events.append(event)
        logger.debug(f"Demo event: {event.event_type.value} -- {event.element_name or event.window_title}")

    def add_window_event(self, event_type: str, window_title: str, app_name: str = ""):
        """Convenience: add a window-related event."""
        type_map = {
            "window_opened": DemoEventType.WINDOW_OPENED,
            "window_closed": DemoEventType.WINDOW_CLOSED,
            "focus_changed": DemoEventType.WINDOW_FOCUSED,
        }
        demo_type = type_map.get(event_type, DemoEventType.CUSTOM)
        self.add_event(DemoEvent(
            event_type=demo_type,
            timestamp=time.time(),
            window_title=window_title,
            app_name=app_name,
        ))

    def add_keyboard_event(self, key: str, window_title: str = "", is_hotkey: bool = False):
        """Convenience: add a keyboard event."""
        if is_hotkey:
            self.add_event(DemoEvent(
                event_type=DemoEventType.KEY_PRESSED,
                timestamp=time.time(),
                window_title=window_title,
                value=key,
                raw_data={"is_hotkey": True},
            ))
        else:
            self.add_event(DemoEvent(
                event_type=DemoEventType.TEXT_TYPED,
                timestamp=time.time(),
                window_title=window_title,
                value=key,
            ))

    def add_click_event(self, element_name: str, element_type: str = "", window_title: str = ""):
        """Convenience: add a click event."""
        self.add_event(DemoEvent(
            event_type=DemoEventType.ELEMENT_CLICKED,
            timestamp=time.time(),
            window_title=window_title,
            element_name=element_name,
            element_type=element_type,
        ))

    def add_screenshot(self, path: str):
        """Add a screenshot to the timeline."""
        if not self._recording or not self._current_demo:
            return
        self._current_demo.screenshots.append(path)
        self.add_event(DemoEvent(
            event_type=DemoEventType.SCREENSHOT,
            timestamp=time.time(),
            screenshot_path=path,
        ))
        self._last_screenshot_time = time.time()

    def should_screenshot(self) -> bool:
        """Check if enough time has passed for another auto-screenshot."""
        return time.time() - self._last_screenshot_time >= self.SCREENSHOT_INTERVAL

    def save(self, demo: Optional[Demonstration] = None) -> str:
        """Save a demonstration to disk as JSON."""
        demo = demo or self._current_demo
        if not demo:
            return ""

        ts = datetime.fromtimestamp(demo.started_at).strftime("%Y%m%d_%H%M%S")
        safe_name = "".join(c if c.isalnum() or c in "-_ " else "" for c in demo.name)
        safe_name = safe_name.strip().replace(" ", "_")[:50]
        filename = f"{ts}_{safe_name}.json"
        filepath = os.path.join(self.DEMO_DIR, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(demo.to_dict(), f, indent=2, ensure_ascii=False)

        logger.info(f"Demonstration saved: {filepath}")
        return filepath

    def list_demos(self) -> list[dict]:
        """List all saved demonstrations."""
        demos = []
        demo_dir = Path(self.DEMO_DIR)
        if not demo_dir.exists():
            return demos
        for f in sorted(demo_dir.glob("*.json"), reverse=True):
            try:
                with open(f, encoding="utf-8") as fp:
                    data = json.load(fp)
                demos.append({
                    "file": f.name,
                    "name": data.get("name", ""),
                    "events": data.get("event_count", 0),
                    "steps": data.get("step_count", 0),
                    "duration": data.get("duration_seconds", 0),
                })
            except Exception:
                pass
        return demos

    def load_demo(self, filename: str) -> Optional[dict]:
        """Load a saved demonstration."""
        filepath = os.path.join(self.DEMO_DIR, filename)
        if not os.path.exists(filepath):
            return None
        with open(filepath, encoding="utf-8") as f:
            return json.load(f)


# ---------------------------------------------------------------
# PlanExtractor -- converts DemoEvent timeline into DemoSteps
# ---------------------------------------------------------------

class PlanExtractor:
    """
    Converts a timeline of DemoEvents into executable Marlow tool steps.

    Strategies:
    - Window opened -> open_application + wait_for_window
    - Window focused -> focus_window
    - Text typed (consecutive chars merged) -> type_text
    - Key pressed (hotkey) -> hotkey
    - Key pressed (single) -> press_key
    - Element clicked -> click
    - Dialog appeared -> handle_dialog

    The extractor merges consecutive events of the same type
    (e.g., individual keystrokes into a single type_text call)
    and adds focus_window before interactions to ensure correct targeting.

    / Convierte timeline de DemoEvents en pasos ejecutables de Marlow.
    """

    def extract(self, demo: Demonstration) -> list[DemoStep]:
        """
        Extract Marlow tool steps from a demonstration's event timeline.
        Returns list of DemoStep in execution order.
        """
        if not demo.events:
            return []

        steps: list[DemoStep] = []
        events = demo.events
        i = 0

        while i < len(events):
            event = events[i]

            if event.event_type == DemoEventType.WINDOW_OPENED:
                step = self._extract_open_app(event)
                if step:
                    steps.append(step)
                    # Add wait_for_window after open
                    steps.append(DemoStep(
                        tool_name="wait_for_window",
                        params={"title": event.window_title, "timeout": 10},
                        description=f"Wait for {event.window_title} to be ready",
                        source_events=[i],
                    ))

            elif event.event_type == DemoEventType.WINDOW_FOCUSED:
                step = self._extract_focus(event)
                if step:
                    steps.append(step)

            elif event.event_type == DemoEventType.TEXT_TYPED:
                # Merge consecutive text events in the same window
                merged_text, consumed = self._merge_text_events(events, i)
                if merged_text:
                    # Add focus before typing
                    if event.window_title:
                        steps.append(DemoStep(
                            tool_name="focus_window",
                            params={"title": event.window_title},
                            description=f"Focus {event.window_title} before typing",
                            source_events=[i],
                            confidence=0.9,
                        ))
                    steps.append(DemoStep(
                        tool_name="type_text",
                        params={"text": merged_text},
                        description=f"Type: {merged_text[:50]}{'...' if len(merged_text) > 50 else ''}",
                        source_events=list(range(i, i + consumed)),
                    ))
                    i += consumed - 1  # -1 because loop increments

            elif event.event_type == DemoEventType.KEY_PRESSED:
                if event.raw_data.get("is_hotkey"):
                    # Hotkey: add focus first
                    if event.window_title:
                        steps.append(DemoStep(
                            tool_name="focus_window",
                            params={"title": event.window_title},
                            description=f"Focus {event.window_title} before hotkey",
                            source_events=[i],
                            confidence=0.9,
                        ))
                    steps.append(DemoStep(
                        tool_name="hotkey",
                        params={"keys": event.value},
                        description=f"Hotkey: {event.value}",
                        source_events=[i],
                    ))
                else:
                    steps.append(DemoStep(
                        tool_name="press_key",
                        params={"key": event.value},
                        description=f"Press: {event.value}",
                        source_events=[i],
                    ))

            elif event.event_type == DemoEventType.ELEMENT_CLICKED:
                step = self._extract_click(event, i)
                if step:
                    # Add focus before click
                    if event.window_title:
                        steps.append(DemoStep(
                            tool_name="focus_window",
                            params={"title": event.window_title},
                            description=f"Focus {event.window_title} before click",
                            source_events=[i],
                            confidence=0.9,
                        ))
                    steps.append(step)

            elif event.event_type == DemoEventType.DIALOG_APPEARED:
                steps.append(DemoStep(
                    tool_name="handle_dialog",
                    params={},
                    description=f"Handle dialog: {event.window_title}",
                    source_events=[i],
                    confidence=0.7,
                ))

            # Skip SCREENSHOT, WINDOW_CLOSED, MENU_SELECTED, CUSTOM — no direct action

            i += 1

        # Post-process: remove redundant consecutive focus_window to same window
        steps = self._deduplicate_focus(steps)

        # Store in demonstration
        demo.extracted_steps = steps

        return steps

    def _extract_open_app(self, event: DemoEvent) -> Optional[DemoStep]:
        """Convert window_opened event to open_application step."""
        if not event.window_title:
            return None

        app_name = event.app_name or self._guess_app_name(event.window_title)

        return DemoStep(
            tool_name="open_application",
            params={"app_name": app_name},
            description=f"Open {app_name}",
            source_events=[],
        )

    def _extract_focus(self, event: DemoEvent) -> Optional[DemoStep]:
        """Convert focus event to focus_window step."""
        if not event.window_title:
            return None
        return DemoStep(
            tool_name="focus_window",
            params={"title": event.window_title},
            description=f"Focus: {event.window_title}",
            source_events=[],
        )

    def _extract_click(self, event: DemoEvent, index: int) -> Optional[DemoStep]:
        """Convert click event to click step."""
        if event.element_name:
            return DemoStep(
                tool_name="click",
                params={"name": event.element_name},
                description=f"Click: {event.element_name}",
                source_events=[index],
            )
        return None

    def _merge_text_events(self, events: list[DemoEvent], start: int) -> tuple[str, int]:
        """
        Merge consecutive TEXT_TYPED events in the same window.
        Returns (merged_text, number_of_events_consumed).
        """
        text_parts = []
        window = events[start].window_title
        count = 0

        for i in range(start, len(events)):
            e = events[i]
            if e.event_type == DemoEventType.TEXT_TYPED and e.window_title == window:
                text_parts.append(e.value)
                count += 1
            else:
                break

        return "".join(text_parts), max(count, 1)

    def _deduplicate_focus(self, steps: list[DemoStep]) -> list[DemoStep]:
        """Remove consecutive focus_window steps to the same window."""
        if not steps:
            return steps

        result = [steps[0]]
        for step in steps[1:]:
            if (step.tool_name == "focus_window"
                    and result[-1].tool_name == "focus_window"
                    and step.params.get("title") == result[-1].params.get("title")):
                continue
            result.append(step)

        return result

    def _guess_app_name(self, window_title: str) -> str:
        """Guess application name from window title."""
        _KNOWN_APPS = {
            "notepad": "Notepad",
            "calculator": "Calculator",
            "paint": "Paint",
            "wordpad": "WordPad",
            "explorer": "Explorer",
            "chrome": "Chrome",
            "firefox": "Firefox",
            "edge": "Edge",
            "code": "Code",
            "cmd": "cmd",
            "powershell": "PowerShell",
            "terminal": "Terminal",
            "excel": "Excel",
            "word": "Word",
            "outlook": "Outlook",
            "teams": "Teams",
            "slack": "Slack",
        }
        title_lower = window_title.lower()
        for key, name in _KNOWN_APPS.items():
            if key in title_lower:
                return name
        # Fallback: first word of title
        return window_title.split(" - ")[0].split(" ")[0]

    def steps_to_plan_json(self, steps: list[DemoStep]) -> str:
        """Convert steps to JSON for Plan Library storage."""
        return json.dumps(
            [s.to_dict() for s in steps],
            indent=2,
            ensure_ascii=False,
        )

    def format_for_review(self, steps: list[DemoStep]) -> str:
        """Format steps as human-readable text for review before saving."""
        if not steps:
            return "No steps extracted."
        lines = [f"Extracted plan ({len(steps)} steps):"]
        for i, step in enumerate(steps, 1):
            conf = f" [{step.confidence:.0%}]" if step.confidence < 1.0 else ""
            lines.append(f"  {i}. {step.tool_name}({step.params}){conf}")
            lines.append(f"     -- {step.description}")
        return "\n".join(lines)
