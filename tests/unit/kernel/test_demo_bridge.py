"""Tests for marlow.kernel.demo_bridge — UIA event to DemoEvent bridge + keyboard flush."""

import os
import time

import pytest

from marlow.kernel.demonstration import DemoEventType, DemonstrationRecorder
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


# ---------------------------------------------------------------
# _demo_flush_keyboard (server.py helper)
# ---------------------------------------------------------------

class TestDemoFlushKeyboard:
    """Tests for the keyboard event flush logic in server.py."""

    @pytest.fixture
    def recorder(self, tmp_path):
        rec = DemonstrationRecorder()
        rec.DEMO_DIR = str(tmp_path / "demos")
        os.makedirs(rec.DEMO_DIR, exist_ok=True)
        rec.start("Keyboard Test")
        return rec

    @pytest.fixture
    def flush(self):
        from marlow.server import _demo_flush_keyboard, _demo_keyboard_events
        # Clear global state before each test
        _demo_keyboard_events.clear()
        return _demo_flush_keyboard

    @pytest.fixture
    def kb_events(self):
        from marlow.server import _demo_keyboard_events
        return _demo_keyboard_events

    def test_printable_text(self, flush, kb_events, recorder):
        kb_events.extend([
            {"key": "H", "time": 1.0, "window": "Notepad", "is_hotkey": False},
            {"key": "i", "time": 1.1, "window": "Notepad", "is_hotkey": False},
        ])
        flush(recorder)
        evs = [e for e in recorder.current_demo.events if e.event_type == DemoEventType.TEXT_TYPED]
        assert len(evs) == 1
        assert evs[0].value == "Hi"

    def test_hotkey(self, flush, kb_events, recorder):
        kb_events.append({"key": "ctrl+s", "time": 1.0, "window": "Notepad", "is_hotkey": True})
        flush(recorder)
        evs = [e for e in recorder.current_demo.events if e.event_type == DemoEventType.KEY_PRESSED]
        assert len(evs) == 1
        assert evs[0].value == "ctrl+s"
        assert evs[0].raw_data["is_hotkey"] is True

    def test_hotkey_flushes_text_buffer(self, flush, kb_events, recorder):
        kb_events.extend([
            {"key": "a", "time": 1.0, "window": "Notepad", "is_hotkey": False},
            {"key": "b", "time": 1.1, "window": "Notepad", "is_hotkey": False},
            {"key": "ctrl+s", "time": 1.2, "window": "Notepad", "is_hotkey": True},
        ])
        flush(recorder)
        evs = recorder.current_demo.events
        # text "ab" then hotkey ctrl+s
        text_evs = [e for e in evs if e.event_type == DemoEventType.TEXT_TYPED]
        key_evs = [e for e in evs if e.event_type == DemoEventType.KEY_PRESSED]
        assert len(text_evs) == 1
        assert text_evs[0].value == "ab"
        assert len(key_evs) == 1
        assert key_evs[0].value == "ctrl+s"

    def test_enter_flushes_text(self, flush, kb_events, recorder):
        kb_events.extend([
            {"key": "h", "time": 1.0, "window": "Notepad", "is_hotkey": False},
            {"key": "i", "time": 1.1, "window": "Notepad", "is_hotkey": False},
            {"key": "enter", "time": 1.2, "window": "Notepad", "is_hotkey": False},
        ])
        flush(recorder)
        evs = recorder.current_demo.events
        assert evs[0].event_type == DemoEventType.TEXT_TYPED
        assert evs[0].value == "hi"
        assert evs[1].event_type == DemoEventType.TEXT_TYPED  # "enter" via add_keyboard_event
        assert evs[1].value == "enter"

    def test_space_appended_to_text(self, flush, kb_events, recorder):
        kb_events.extend([
            {"key": "a", "time": 1.0, "window": "Notepad", "is_hotkey": False},
            {"key": "space", "time": 1.1, "window": "Notepad", "is_hotkey": False},
            {"key": "b", "time": 1.2, "window": "Notepad", "is_hotkey": False},
        ])
        flush(recorder)
        evs = [e for e in recorder.current_demo.events if e.event_type == DemoEventType.TEXT_TYPED]
        assert len(evs) == 1
        assert evs[0].value == "a b"

    def test_backspace_removes_from_buffer(self, flush, kb_events, recorder):
        kb_events.extend([
            {"key": "a", "time": 1.0, "window": "Notepad", "is_hotkey": False},
            {"key": "b", "time": 1.1, "window": "Notepad", "is_hotkey": False},
            {"key": "backspace", "time": 1.2, "window": "Notepad", "is_hotkey": False},
        ])
        flush(recorder)
        evs = [e for e in recorder.current_demo.events if e.event_type == DemoEventType.TEXT_TYPED]
        assert len(evs) == 1
        assert evs[0].value == "a"

    def test_backspace_on_empty_buffer(self, flush, kb_events, recorder):
        kb_events.append({"key": "backspace", "time": 1.0, "window": "Notepad", "is_hotkey": False})
        flush(recorder)
        evs = [e for e in recorder.current_demo.events if e.event_type == DemoEventType.TEXT_TYPED]
        assert len(evs) == 1
        assert evs[0].value == "backspace"

    def test_window_change_flushes_text(self, flush, kb_events, recorder):
        kb_events.extend([
            {"key": "a", "time": 1.0, "window": "Notepad", "is_hotkey": False},
            {"key": "b", "time": 1.1, "window": "Word", "is_hotkey": False},
        ])
        flush(recorder)
        evs = [e for e in recorder.current_demo.events if e.event_type == DemoEventType.TEXT_TYPED]
        assert len(evs) == 2
        assert evs[0].value == "a"
        assert evs[0].window_title == "Notepad"
        assert evs[1].value == "b"
        assert evs[1].window_title == "Word"

    def test_special_key(self, flush, kb_events, recorder):
        kb_events.append({"key": "tab", "time": 1.0, "window": "Notepad", "is_hotkey": False})
        flush(recorder)
        evs = [e for e in recorder.current_demo.events if e.event_type == DemoEventType.TEXT_TYPED]
        assert len(evs) == 1
        assert evs[0].value == "tab"

    def test_empty_events(self, flush, kb_events, recorder):
        flush(recorder)
        assert recorder.current_demo.event_count == 0
