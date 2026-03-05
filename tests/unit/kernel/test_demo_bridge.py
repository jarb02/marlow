"""Tests for marlow.kernel.demo_bridge — UIA event to DemoEvent bridge."""

import os
import time

import pytest

from marlow.kernel.demonstration import DemonstrationRecorder
from marlow.kernel.demo_bridge import DemoBridge


@pytest.fixture
def recorder(tmp_path):
    rec = DemonstrationRecorder()
    rec.DEMO_DIR = str(tmp_path / "demos")
    os.makedirs(rec.DEMO_DIR, exist_ok=True)
    return rec


@pytest.fixture
def bridge(recorder):
    return DemoBridge(recorder)


class TestDemoBridge:
    def test_process_window_opened(self, bridge, recorder):
        recorder.start("Test")
        bridge.process_uia_events([{
            "type": "window_opened",
            "element_name": "Untitled - Notepad",
            "process_name": "notepad.exe",
            "timestamp": "2025-01-01T00:00:00",
        }])
        assert recorder.current_demo.event_count == 1
        ev = recorder.current_demo.events[0]
        assert ev.window_title == "Untitled - Notepad"
        assert ev.app_name == "notepad.exe"

    def test_process_focus_changed(self, bridge, recorder):
        recorder.start("Test")
        bridge.process_uia_events([{
            "type": "focus_changed",
            "element_name": "Save Button",
            "process_name": "notepad.exe",
            "timestamp": "2025-01-01T00:00:00",
        }])
        assert recorder.current_demo.event_count == 1

    def test_process_window_closed(self, bridge, recorder):
        recorder.start("Test")
        bridge.process_uia_events([{
            "type": "window_closed",
            "element_name": "Notepad",
            "process_name": "notepad.exe",
            "timestamp": "2025-01-01T00:00:00",
        }])
        assert recorder.current_demo.event_count == 1

    def test_ignores_empty_title(self, bridge, recorder):
        recorder.start("Test")
        bridge.process_uia_events([{
            "type": "window_opened",
            "element_name": "",
            "process_name": "notepad.exe",
            "timestamp": "2025-01-01T00:00:00",
        }])
        assert recorder.current_demo.event_count == 0

    def test_ignores_when_not_recording(self, bridge, recorder):
        # Not started, so not recording
        bridge.process_uia_events([{
            "type": "window_opened",
            "element_name": "Notepad",
            "process_name": "notepad.exe",
            "timestamp": "2025-01-01T00:00:00",
        }])
        assert bridge.processed_count == 0

    def test_process_keyboard(self, bridge, recorder):
        recorder.start("Test")
        bridge.process_keyboard_event("Hello", "Notepad")
        assert recorder.current_demo.event_count == 1
        ev = recorder.current_demo.events[0]
        assert ev.value == "Hello"

    def test_process_keyboard_hotkey(self, bridge, recorder):
        recorder.start("Test")
        bridge.process_keyboard_event("Ctrl+S", "Notepad", is_hotkey=True)
        ev = recorder.current_demo.events[0]
        assert ev.value == "Ctrl+S"
        assert ev.raw_data["is_hotkey"] is True

    def test_processed_count(self, bridge, recorder):
        recorder.start("Test")
        assert bridge.processed_count == 0
        bridge.process_uia_events([
            {"type": "window_opened", "element_name": "A", "process_name": "a.exe", "timestamp": ""},
            {"type": "focus_changed", "element_name": "B", "process_name": "b.exe", "timestamp": ""},
        ])
        bridge.process_keyboard_event("x", "A")
        assert bridge.processed_count == 3

    def test_reset(self, bridge, recorder):
        recorder.start("Test")
        bridge.process_keyboard_event("x", "A")
        assert bridge.processed_count == 1
        bridge.reset()
        assert bridge.processed_count == 0
