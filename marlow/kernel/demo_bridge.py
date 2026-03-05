"""
Bridge que conecta el DemonstrationRecorder con el UIA Event Monitor.
Cuando el monitor detecta eventos (window open, focus change),
los traduce a DemoEvents y los alimenta al recorder.

/ Bridge entre UIA monitor y DemonstrationRecorder.
"""

import logging
from typing import Optional

logger = logging.getLogger("marlow.kernel.demo_bridge")


class DemoBridge:
    """
    Connects UIA event monitor output to DemonstrationRecorder input.

    Polls get_ui_events() periodically and converts raw UIA events
    into typed DemoEvents for the recorder.

    Usage:
        bridge = DemoBridge(recorder)
        bridge.process_uia_events(raw_events)

    / Conecta la salida del monitor UIA con la entrada del recorder.
    """

    def __init__(self, recorder):
        self._recorder = recorder
        self._processed_count = 0

    @property
    def processed_count(self) -> int:
        return self._processed_count

    def process_uia_events(self, raw_events: list[dict]):
        """
        Convert raw UIA events from get_ui_events() into DemoEvents.

        Raw event format (from uia_events.py):
        {
            "type": "window_opened" | "window_closed" | "focus_changed",
            "element_name": str,
            "process_name": str,
            "timestamp": str (ISO),
        }
        """
        if not self._recorder.is_recording:
            return

        for event in raw_events:
            event_type = event.get("type", "")
            title = event.get("element_name", "")
            process = event.get("process_name", "")

            if not title:
                continue

            if event_type in ("window_opened", "window_closed", "focus_changed"):
                self._recorder.add_window_event(
                    event_type=event_type,
                    window_title=title,
                    app_name=process,
                )
                self._processed_count += 1

            elif event_type == "structure_changed":
                element_name = event.get("element_name", "")
                if element_name:
                    self._recorder.add_click_event(
                        element_name=element_name,
                        element_type=event.get("control_type", ""),
                        window_title=title,
                    )
                    self._processed_count += 1

    def process_keyboard_event(self, key: str, window_title: str = "", is_hotkey: bool = False):
        """Process a keyboard event from the keyboard hook."""
        if not self._recorder.is_recording:
            return
        self._recorder.add_keyboard_event(key, window_title, is_hotkey)
        self._processed_count += 1

    def reset(self):
        """Reset the processed count."""
        self._processed_count = 0
