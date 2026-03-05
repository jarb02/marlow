"""Tests for marlow.kernel.demonstration — LfD DemonstrationRecorder."""

import json
import os
import time

import pytest

from marlow.kernel.demonstration import (
    DemoEvent,
    DemoEventType,
    DemoStep,
    Demonstration,
    DemonstrationRecorder,
    PlanExtractor,
)


# ---------------------------------------------------------------
# DemoEventType
# ---------------------------------------------------------------

class TestDemoEventType:
    def test_enum_values(self):
        assert DemoEventType.WINDOW_OPENED.value == "window_opened"
        assert DemoEventType.WINDOW_CLOSED.value == "window_closed"
        assert DemoEventType.WINDOW_FOCUSED.value == "window_focused"
        assert DemoEventType.TEXT_TYPED.value == "text_typed"
        assert DemoEventType.KEY_PRESSED.value == "key_pressed"
        assert DemoEventType.ELEMENT_CLICKED.value == "element_clicked"
        assert DemoEventType.MENU_SELECTED.value == "menu_selected"
        assert DemoEventType.DIALOG_APPEARED.value == "dialog_appeared"
        assert DemoEventType.SCREENSHOT.value == "screenshot"
        assert DemoEventType.CUSTOM.value == "custom"


# ---------------------------------------------------------------
# DemoEvent
# ---------------------------------------------------------------

class TestDemoEvent:
    def test_dataclass_defaults(self):
        ev = DemoEvent(event_type=DemoEventType.WINDOW_OPENED, timestamp=1000.0)
        assert ev.event_type == DemoEventType.WINDOW_OPENED
        assert ev.timestamp == 1000.0
        assert ev.window_title == ""
        assert ev.element_name == ""
        assert ev.element_type == ""
        assert ev.value == ""
        assert ev.app_name == ""
        assert ev.screenshot_path == ""
        assert ev.raw_data == {}

    def test_to_dict(self):
        ev = DemoEvent(
            event_type=DemoEventType.ELEMENT_CLICKED,
            timestamp=1234.5,
            window_title="Notepad",
            element_name="Save",
            element_type="button",
        )
        d = ev.to_dict()
        assert d["event_type"] == "element_clicked"
        assert d["timestamp"] == 1234.5
        assert d["window_title"] == "Notepad"
        assert d["element_name"] == "Save"
        assert d["element_type"] == "button"

    def test_age_seconds(self):
        ev = DemoEvent(event_type=DemoEventType.CUSTOM, timestamp=time.time() - 5.0)
        assert ev.age_seconds >= 4.9


# ---------------------------------------------------------------
# DemoStep
# ---------------------------------------------------------------

class TestDemoStep:
    def test_dataclass_defaults(self):
        step = DemoStep(tool_name="click", params={"name": "OK"}, description="Click OK")
        assert step.tool_name == "click"
        assert step.params == {"name": "OK"}
        assert step.description == "Click OK"
        assert step.source_events == []
        assert step.confidence == 1.0

    def test_to_dict(self):
        step = DemoStep(
            tool_name="type_text",
            params={"text": "hello"},
            description="Type hello",
            confidence=0.9,
        )
        d = step.to_dict()
        assert d["tool"] == "type_text"
        assert d["args"] == {"text": "hello"}
        assert d["description"] == "Type hello"
        assert d["confidence"] == 0.9


# ---------------------------------------------------------------
# Demonstration
# ---------------------------------------------------------------

class TestDemonstration:
    def test_dataclass_defaults(self):
        demo = Demonstration(name="test", description="desc", started_at=1000.0)
        assert demo.name == "test"
        assert demo.description == "desc"
        assert demo.started_at == 1000.0
        assert demo.ended_at == 0.0
        assert demo.events == []
        assert demo.extracted_steps == []
        assert demo.screenshots == []

    def test_duration_with_end(self):
        demo = Demonstration(name="t", description="", started_at=100.0, ended_at=110.0)
        assert demo.duration_seconds == 10.0

    def test_duration_without_end(self):
        demo = Demonstration(name="t", description="", started_at=time.time() - 3.0)
        assert demo.duration_seconds >= 2.9

    def test_event_count(self):
        demo = Demonstration(name="t", description="", started_at=0.0)
        assert demo.event_count == 0
        demo.events.append(DemoEvent(event_type=DemoEventType.CUSTOM, timestamp=1.0))
        assert demo.event_count == 1

    def test_step_count(self):
        demo = Demonstration(name="t", description="", started_at=0.0)
        assert demo.step_count == 0
        demo.extracted_steps.append(DemoStep(tool_name="x", params={}, description="x"))
        assert demo.step_count == 1

    def test_to_dict(self):
        demo = Demonstration(
            name="Save File",
            description="Demo save",
            started_at=1700000000.0,
            ended_at=1700000010.0,
        )
        demo.events.append(DemoEvent(event_type=DemoEventType.WINDOW_OPENED, timestamp=1700000001.0))
        demo.screenshots.append("/tmp/shot.jpg")

        d = demo.to_dict()
        assert d["name"] == "Save File"
        assert d["description"] == "Demo save"
        assert d["duration_seconds"] == 10.0
        assert d["event_count"] == 1
        assert d["step_count"] == 0
        assert len(d["events"]) == 1
        assert d["screenshots"] == ["/tmp/shot.jpg"]


# ---------------------------------------------------------------
# DemonstrationRecorder
# ---------------------------------------------------------------

class TestRecorder:
    @pytest.fixture
    def recorder(self, tmp_path):
        rec = DemonstrationRecorder()
        rec.DEMO_DIR = str(tmp_path / "demos")
        os.makedirs(rec.DEMO_DIR, exist_ok=True)
        return rec

    def test_start(self, recorder):
        demo = recorder.start("Test Demo", "A test")
        assert demo is not None
        assert demo.name == "Test Demo"
        assert demo.description == "A test"
        assert recorder.is_recording is True

    def test_is_recording_default(self, recorder):
        assert recorder.is_recording is False

    def test_stop(self, recorder):
        recorder.start("Test")
        demo = recorder.stop()
        assert demo is not None
        assert demo.ended_at > 0
        assert recorder.is_recording is False

    def test_stop_not_recording(self, recorder):
        result = recorder.stop()
        assert result is None

    def test_start_while_recording(self, recorder):
        demo1 = recorder.start("First")
        demo2 = recorder.start("Second")
        # Returns existing demo, doesn't start a new one
        assert demo2 is demo1

    def test_add_event(self, recorder):
        recorder.start("Test")
        ev = DemoEvent(
            event_type=DemoEventType.ELEMENT_CLICKED,
            timestamp=time.time(),
            element_name="OK",
        )
        recorder.add_event(ev)
        assert recorder.current_demo.event_count == 1
        assert recorder.current_demo.events[0].element_name == "OK"

    def test_add_event_not_recording(self, recorder):
        ev = DemoEvent(event_type=DemoEventType.CUSTOM, timestamp=time.time())
        recorder.add_event(ev)
        # Should silently ignore
        assert recorder.current_demo is None

    def test_add_window_event(self, recorder):
        recorder.start("Test")
        recorder.add_window_event("window_opened", "Notepad", "notepad.exe")
        assert recorder.current_demo.event_count == 1
        ev = recorder.current_demo.events[0]
        assert ev.event_type == DemoEventType.WINDOW_OPENED
        assert ev.window_title == "Notepad"
        assert ev.app_name == "notepad.exe"

    def test_add_window_event_unknown_type(self, recorder):
        recorder.start("Test")
        recorder.add_window_event("unknown_type", "Notepad")
        ev = recorder.current_demo.events[0]
        assert ev.event_type == DemoEventType.CUSTOM

    def test_add_keyboard_event_text(self, recorder):
        recorder.start("Test")
        recorder.add_keyboard_event("Hello World", "Notepad")
        ev = recorder.current_demo.events[0]
        assert ev.event_type == DemoEventType.TEXT_TYPED
        assert ev.value == "Hello World"

    def test_add_keyboard_event_hotkey(self, recorder):
        recorder.start("Test")
        recorder.add_keyboard_event("Ctrl+S", "Notepad", is_hotkey=True)
        ev = recorder.current_demo.events[0]
        assert ev.event_type == DemoEventType.KEY_PRESSED
        assert ev.value == "Ctrl+S"
        assert ev.raw_data["is_hotkey"] is True

    def test_add_click_event(self, recorder):
        recorder.start("Test")
        recorder.add_click_event("Save", "button", "Notepad")
        ev = recorder.current_demo.events[0]
        assert ev.event_type == DemoEventType.ELEMENT_CLICKED
        assert ev.element_name == "Save"
        assert ev.element_type == "button"
        assert ev.window_title == "Notepad"

    def test_add_screenshot(self, recorder):
        recorder.start("Test")
        recorder.add_screenshot("/tmp/shot.jpg")
        assert recorder.current_demo.event_count == 1
        assert recorder.current_demo.screenshots == ["/tmp/shot.jpg"]
        ev = recorder.current_demo.events[0]
        assert ev.event_type == DemoEventType.SCREENSHOT
        assert ev.screenshot_path == "/tmp/shot.jpg"

    def test_add_screenshot_not_recording(self, recorder):
        recorder.add_screenshot("/tmp/shot.jpg")
        # Should silently ignore

    def test_should_screenshot(self, recorder):
        recorder.start("Test")
        # Initially last_screenshot_time is 0.0, so interval has passed
        assert recorder.should_screenshot() is True

    def test_should_screenshot_after_recent(self, recorder):
        recorder.start("Test")
        recorder._last_screenshot_time = time.time()
        assert recorder.should_screenshot() is False

    def test_dedup_focus_events(self, recorder):
        recorder.start("Test")
        recorder.add_event(DemoEvent(
            event_type=DemoEventType.WINDOW_FOCUSED,
            timestamp=time.time(),
            window_title="Notepad",
        ))
        # Rapid second focus on same window (within 0.5s) should be deduped
        recorder.add_event(DemoEvent(
            event_type=DemoEventType.WINDOW_FOCUSED,
            timestamp=time.time(),
            window_title="Notepad",
        ))
        assert recorder.current_demo.event_count == 1

    def test_no_dedup_different_windows(self, recorder):
        recorder.start("Test")
        recorder.add_event(DemoEvent(
            event_type=DemoEventType.WINDOW_FOCUSED,
            timestamp=time.time(),
            window_title="Notepad",
        ))
        recorder.add_event(DemoEvent(
            event_type=DemoEventType.WINDOW_FOCUSED,
            timestamp=time.time(),
            window_title="Explorer",
        ))
        assert recorder.current_demo.event_count == 2

    def test_save_and_list(self, recorder):
        recorder.start("My Demo", "description")
        recorder.add_click_event("Button1", "button")
        demo = recorder.stop()

        path = recorder.save(demo)
        assert path != ""
        assert os.path.exists(path)

        demos = recorder.list_demos()
        assert len(demos) == 1
        assert demos[0]["name"] == "My Demo"
        assert demos[0]["events"] == 1

    def test_load_demo(self, recorder):
        recorder.start("Load Test")
        recorder.add_click_event("OK", "button")
        demo = recorder.stop()

        path = recorder.save(demo)
        filename = os.path.basename(path)

        loaded = recorder.load_demo(filename)
        assert loaded is not None
        assert loaded["name"] == "Load Test"
        assert loaded["event_count"] == 1

    def test_load_demo_not_found(self, recorder):
        result = recorder.load_demo("nonexistent.json")
        assert result is None


# ---------------------------------------------------------------
# PlanExtractor
# ---------------------------------------------------------------

def _make_demo(*events: DemoEvent) -> Demonstration:
    """Helper: create a Demonstration with the given events."""
    demo = Demonstration(name="test", description="", started_at=1000.0, ended_at=1010.0)
    demo.events = list(events)
    return demo


class TestPlanExtractor:
    @pytest.fixture
    def extractor(self):
        return PlanExtractor()

    def test_empty_demo(self, extractor):
        demo = _make_demo()
        steps = extractor.extract(demo)
        assert steps == []

    def test_window_opened(self, extractor):
        demo = _make_demo(DemoEvent(
            event_type=DemoEventType.WINDOW_OPENED,
            timestamp=1.0,
            window_title="Untitled - Notepad",
            app_name="notepad.exe",
        ))
        steps = extractor.extract(demo)
        assert len(steps) == 2
        assert steps[0].tool_name == "open_application"
        assert steps[0].params["app_name"] == "notepad.exe"
        assert steps[1].tool_name == "wait_for_window"
        assert steps[1].params["title"] == "Untitled - Notepad"

    def test_window_focused(self, extractor):
        demo = _make_demo(DemoEvent(
            event_type=DemoEventType.WINDOW_FOCUSED,
            timestamp=1.0,
            window_title="Notepad",
        ))
        steps = extractor.extract(demo)
        assert len(steps) == 1
        assert steps[0].tool_name == "focus_window"
        assert steps[0].params["title"] == "Notepad"

    def test_window_focused_no_title(self, extractor):
        demo = _make_demo(DemoEvent(
            event_type=DemoEventType.WINDOW_FOCUSED,
            timestamp=1.0,
        ))
        steps = extractor.extract(demo)
        assert steps == []

    def test_text_typed(self, extractor):
        demo = _make_demo(DemoEvent(
            event_type=DemoEventType.TEXT_TYPED,
            timestamp=1.0,
            window_title="Notepad",
            value="Hello",
        ))
        steps = extractor.extract(demo)
        # focus_window + type_text
        assert len(steps) == 2
        assert steps[0].tool_name == "focus_window"
        assert steps[1].tool_name == "type_text"
        assert steps[1].params["text"] == "Hello"

    def test_text_merged(self, extractor):
        demo = _make_demo(
            DemoEvent(event_type=DemoEventType.TEXT_TYPED, timestamp=1.0, window_title="Notepad", value="He"),
            DemoEvent(event_type=DemoEventType.TEXT_TYPED, timestamp=1.1, window_title="Notepad", value="llo"),
            DemoEvent(event_type=DemoEventType.TEXT_TYPED, timestamp=1.2, window_title="Notepad", value=" World"),
        )
        steps = extractor.extract(demo)
        type_steps = [s for s in steps if s.tool_name == "type_text"]
        assert len(type_steps) == 1
        assert type_steps[0].params["text"] == "Hello World"
        assert type_steps[0].source_events == [0, 1, 2]

    def test_text_different_windows(self, extractor):
        demo = _make_demo(
            DemoEvent(event_type=DemoEventType.TEXT_TYPED, timestamp=1.0, window_title="Notepad", value="abc"),
            DemoEvent(event_type=DemoEventType.TEXT_TYPED, timestamp=1.1, window_title="Word", value="xyz"),
        )
        steps = extractor.extract(demo)
        type_steps = [s for s in steps if s.tool_name == "type_text"]
        assert len(type_steps) == 2
        assert type_steps[0].params["text"] == "abc"
        assert type_steps[1].params["text"] == "xyz"

    def test_key_pressed(self, extractor):
        demo = _make_demo(DemoEvent(
            event_type=DemoEventType.KEY_PRESSED,
            timestamp=1.0,
            value="Enter",
        ))
        steps = extractor.extract(demo)
        assert len(steps) == 1
        assert steps[0].tool_name == "press_key"
        assert steps[0].params["key"] == "Enter"

    def test_hotkey(self, extractor):
        demo = _make_demo(DemoEvent(
            event_type=DemoEventType.KEY_PRESSED,
            timestamp=1.0,
            window_title="Notepad",
            value="Ctrl+S",
            raw_data={"is_hotkey": True},
        ))
        steps = extractor.extract(demo)
        assert len(steps) == 2
        assert steps[0].tool_name == "focus_window"
        assert steps[1].tool_name == "hotkey"
        assert steps[1].params["keys"] == "Ctrl+S"

    def test_click(self, extractor):
        demo = _make_demo(DemoEvent(
            event_type=DemoEventType.ELEMENT_CLICKED,
            timestamp=1.0,
            window_title="Notepad",
            element_name="Save",
        ))
        steps = extractor.extract(demo)
        assert len(steps) == 2
        assert steps[0].tool_name == "focus_window"
        assert steps[1].tool_name == "click"
        assert steps[1].params["name"] == "Save"

    def test_click_no_name(self, extractor):
        demo = _make_demo(DemoEvent(
            event_type=DemoEventType.ELEMENT_CLICKED,
            timestamp=1.0,
            window_title="Notepad",
            element_name="",
        ))
        steps = extractor.extract(demo)
        assert steps == []

    def test_dialog(self, extractor):
        demo = _make_demo(DemoEvent(
            event_type=DemoEventType.DIALOG_APPEARED,
            timestamp=1.0,
            window_title="Save As",
        ))
        steps = extractor.extract(demo)
        assert len(steps) == 1
        assert steps[0].tool_name == "handle_dialog"
        assert steps[0].confidence == 0.7

    def test_screenshot_skipped(self, extractor):
        demo = _make_demo(DemoEvent(
            event_type=DemoEventType.SCREENSHOT,
            timestamp=1.0,
            screenshot_path="/tmp/shot.jpg",
        ))
        steps = extractor.extract(demo)
        assert steps == []

    def test_focus_before_type(self, extractor):
        demo = _make_demo(DemoEvent(
            event_type=DemoEventType.TEXT_TYPED,
            timestamp=1.0,
            window_title="Notepad",
            value="test",
        ))
        steps = extractor.extract(demo)
        assert steps[0].tool_name == "focus_window"
        assert steps[0].params["title"] == "Notepad"
        assert steps[1].tool_name == "type_text"

    def test_focus_before_hotkey(self, extractor):
        demo = _make_demo(DemoEvent(
            event_type=DemoEventType.KEY_PRESSED,
            timestamp=1.0,
            window_title="Notepad",
            value="Ctrl+Z",
            raw_data={"is_hotkey": True},
        ))
        steps = extractor.extract(demo)
        assert steps[0].tool_name == "focus_window"
        assert steps[1].tool_name == "hotkey"

    def test_dedup_focus(self, extractor):
        demo = _make_demo(
            DemoEvent(event_type=DemoEventType.WINDOW_FOCUSED, timestamp=1.0, window_title="Notepad"),
            DemoEvent(event_type=DemoEventType.TEXT_TYPED, timestamp=2.0, window_title="Notepad", value="hi"),
        )
        steps = extractor.extract(demo)
        # focus from WINDOW_FOCUSED + focus before type_text should dedup to 1 focus
        focus_steps = [s for s in steps if s.tool_name == "focus_window"]
        assert len(focus_steps) == 1

    def test_guess_app_name_known(self, extractor):
        assert extractor._guess_app_name("Untitled - Notepad") == "Notepad"
        assert extractor._guess_app_name("Google Chrome") == "Chrome"
        assert extractor._guess_app_name("Microsoft Excel - Book1") == "Excel"

    def test_guess_app_name_unknown(self, extractor):
        assert extractor._guess_app_name("MyCustomApp - Main Window") == "MyCustomApp"
        assert extractor._guess_app_name("SomeApp") == "SomeApp"

    def test_steps_to_plan_json(self, extractor):
        steps = [
            DemoStep(tool_name="click", params={"name": "OK"}, description="Click OK"),
            DemoStep(tool_name="type_text", params={"text": "hi"}, description="Type hi"),
        ]
        result = extractor.steps_to_plan_json(steps)
        parsed = json.loads(result)
        assert len(parsed) == 2
        assert parsed[0]["tool"] == "click"
        assert parsed[1]["tool"] == "type_text"

    def test_format_for_review(self, extractor):
        steps = [
            DemoStep(tool_name="click", params={"name": "OK"}, description="Click OK"),
            DemoStep(tool_name="type_text", params={"text": "hi"}, description="Type", confidence=0.9),
        ]
        text = extractor.format_for_review(steps)
        assert "Extracted plan (2 steps):" in text
        assert "click" in text
        assert "type_text" in text
        assert "[90%]" in text

    def test_format_for_review_empty(self, extractor):
        assert extractor.format_for_review([]) == "No steps extracted."

    def test_full_notepad_flow(self, extractor):
        """Simulate: open Notepad -> type text -> Ctrl+S -> type filename -> Enter"""
        demo = _make_demo(
            DemoEvent(event_type=DemoEventType.WINDOW_OPENED, timestamp=1.0,
                      window_title="Untitled - Notepad", app_name="notepad.exe"),
            DemoEvent(event_type=DemoEventType.WINDOW_FOCUSED, timestamp=2.0,
                      window_title="Untitled - Notepad"),
            DemoEvent(event_type=DemoEventType.TEXT_TYPED, timestamp=3.0,
                      window_title="Untitled - Notepad", value="Hello World"),
            DemoEvent(event_type=DemoEventType.KEY_PRESSED, timestamp=4.0,
                      window_title="Untitled - Notepad", value="Ctrl+S",
                      raw_data={"is_hotkey": True}),
            DemoEvent(event_type=DemoEventType.DIALOG_APPEARED, timestamp=5.0,
                      window_title="Save As"),
            DemoEvent(event_type=DemoEventType.TEXT_TYPED, timestamp=6.0,
                      window_title="Save As", value="hello.txt"),
            DemoEvent(event_type=DemoEventType.KEY_PRESSED, timestamp=7.0,
                      value="Enter"),
        )
        steps = extractor.extract(demo)

        tool_sequence = [s.tool_name for s in steps]
        # open_application, wait_for_window, focus_window (deduped),
        # type_text, focus_window, hotkey, handle_dialog,
        # focus_window, type_text, press_key
        assert "open_application" in tool_sequence
        assert "wait_for_window" in tool_sequence
        assert "type_text" in tool_sequence
        assert "hotkey" in tool_sequence
        assert "handle_dialog" in tool_sequence
        assert "press_key" in tool_sequence

        # Verify text content
        type_steps = [s for s in steps if s.tool_name == "type_text"]
        assert len(type_steps) == 2
        assert type_steps[0].params["text"] == "Hello World"
        assert type_steps[1].params["text"] == "hello.txt"

    def test_extract_stores_in_demo(self, extractor):
        demo = _make_demo(DemoEvent(
            event_type=DemoEventType.ELEMENT_CLICKED,
            timestamp=1.0,
            window_title="App",
            element_name="OK",
        ))
        steps = extractor.extract(demo)
        assert demo.extracted_steps is steps
        assert demo.step_count > 0
