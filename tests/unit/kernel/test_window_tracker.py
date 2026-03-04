"""Tests for marlow.kernel.window_tracker — window state tracking between steps."""

import pytest
from marlow.kernel.window_tracker import WindowTracker, WindowSnapshot, WindowChange


def _win(title: str, hwnd: int, pid: int = 100, is_active: bool = False,
         left: int = 0, top: int = 0, right: int = 800, bottom: int = 600) -> dict:
    """Helper to build a window dict matching list_windows output."""
    return {
        "title": title,
        "hwnd": hwnd,
        "pid": pid,
        "rect": {"left": left, "top": top, "right": right, "bottom": bottom},
        "is_active": is_active,
    }


class TestRecordSnapshot:
    def test_record_snapshot_empty(self):
        tracker = WindowTracker()
        tracker.record_snapshot([])
        assert len(tracker._snapshots) == 1
        assert tracker._snapshots[0] == []

    def test_record_snapshot_stores_data(self):
        tracker = WindowTracker()
        windows = [_win("Notepad", 1001, pid=200, is_active=True)]
        tracker.record_snapshot(windows)

        assert len(tracker._snapshots) == 1
        snap = tracker._snapshots[0]
        assert len(snap) == 1
        assert snap[0].title == "Notepad"
        assert snap[0].hwnd == 1001
        assert snap[0].pid == 200
        assert snap[0].is_active is True
        assert snap[0].rect == (0, 0, 800, 600)

    def test_max_history_trimming(self):
        tracker = WindowTracker(max_history=3)
        for i in range(5):
            tracker.record_snapshot([_win(f"Win{i}", hwnd=i)])
        assert len(tracker._snapshots) == 3
        # Oldest kept should be Win2
        assert tracker._snapshots[0][0].title == "Win2"


class TestDetectChanges:
    def test_detect_no_changes(self):
        tracker = WindowTracker()
        windows = [_win("Notepad", 1, is_active=True), _win("Explorer", 2)]
        tracker.record_snapshot(windows)
        tracker.record_snapshot(windows)
        changes = tracker.detect_changes()
        assert changes == []

    def test_detect_single_snapshot_returns_empty(self):
        tracker = WindowTracker()
        tracker.record_snapshot([_win("Notepad", 1)])
        assert tracker.detect_changes() == []

    def test_detect_window_appeared(self):
        tracker = WindowTracker()
        tracker.record_snapshot([_win("Notepad", 1, is_active=True)])
        tracker.record_snapshot([
            _win("Notepad", 1, is_active=True),
            _win("Save As", 2),
        ])
        changes = tracker.detect_changes()
        appeared = [c for c in changes if c.change_type == "appeared"]
        assert len(appeared) == 1
        assert appeared[0].window_title == "Save As"

    def test_detect_window_appeared_ignores_blank_title(self):
        tracker = WindowTracker()
        tracker.record_snapshot([_win("Notepad", 1, is_active=True)])
        tracker.record_snapshot([
            _win("Notepad", 1, is_active=True),
            _win("", 2),
            _win("   ", 3),
        ])
        changes = tracker.detect_changes()
        appeared = [c for c in changes if c.change_type == "appeared"]
        assert len(appeared) == 0

    def test_detect_window_disappeared(self):
        tracker = WindowTracker()
        tracker.record_snapshot([
            _win("Notepad", 1, is_active=True),
            _win("Calculator", 2),
        ])
        tracker.record_snapshot([_win("Notepad", 1, is_active=True)])
        changes = tracker.detect_changes()
        gone = [c for c in changes if c.change_type == "disappeared"]
        assert len(gone) == 1
        assert gone[0].window_title == "Calculator"

    def test_detect_title_changed(self):
        tracker = WindowTracker()
        tracker.record_snapshot([_win("Untitled - Notepad", 1, is_active=True)])
        tracker.record_snapshot([_win("readme.txt - Notepad", 1, is_active=True)])
        changes = tracker.detect_changes()
        title_changes = [c for c in changes if c.change_type == "title_changed"]
        assert len(title_changes) == 1
        assert title_changes[0].old_value == "Untitled - Notepad"
        assert title_changes[0].new_value == "readme.txt - Notepad"

    def test_detect_focus_changed(self):
        tracker = WindowTracker()
        tracker.record_snapshot([
            _win("Notepad", 1, is_active=True),
            _win("Explorer", 2, is_active=False),
        ])
        tracker.record_snapshot([
            _win("Notepad", 1, is_active=False),
            _win("Explorer", 2, is_active=True),
        ])
        changes = tracker.detect_changes()
        types = {c.change_type: c for c in changes}
        assert "focus_lost" in types
        assert types["focus_lost"].window_title == "Notepad"
        assert "focus_gained" in types
        assert types["focus_gained"].window_title == "Explorer"


class TestExpectedApp:
    def test_expected_app_active(self):
        tracker = WindowTracker()
        tracker.set_expected_app("notepad.exe")
        tracker.record_snapshot([_win("Untitled - Notepad", 1, is_active=True)])
        assert tracker.is_expected_app_active() is True

    def test_expected_app_not_active(self):
        tracker = WindowTracker()
        tracker.set_expected_app("notepad.exe")
        tracker.record_snapshot([_win("File Explorer", 1, is_active=True)])
        assert tracker.is_expected_app_active() is False

    def test_no_expected_app_returns_true(self):
        tracker = WindowTracker()
        tracker.record_snapshot([_win("Anything", 1, is_active=True)])
        assert tracker.is_expected_app_active() is True

    def test_no_active_window_returns_false(self):
        tracker = WindowTracker()
        tracker.set_expected_app("notepad.exe")
        tracker.record_snapshot([_win("Notepad", 1, is_active=False)])
        assert tracker.is_expected_app_active() is False


class TestHelpers:
    def test_get_active_window_title(self):
        tracker = WindowTracker()
        tracker.record_snapshot([
            _win("Background", 1),
            _win("Active App", 2, is_active=True),
        ])
        assert tracker.get_active_window_title() == "Active App"

    def test_get_active_window_title_empty(self):
        tracker = WindowTracker()
        assert tracker.get_active_window_title() == ""

    def test_window_appeared_since_last(self):
        tracker = WindowTracker()
        tracker.record_snapshot([_win("Notepad", 1, is_active=True)])
        tracker.record_snapshot([
            _win("Notepad", 1, is_active=True),
            _win("Error Dialog", 2),
        ])
        assert tracker.window_appeared_since_last("Error") is True
        assert tracker.window_appeared_since_last("Save") is False

    def test_window_appeared_since_last_case_insensitive(self):
        tracker = WindowTracker()
        tracker.record_snapshot([_win("Notepad", 1, is_active=True)])
        tracker.record_snapshot([
            _win("Notepad", 1, is_active=True),
            _win("Save As Dialog", 2),
        ])
        assert tracker.window_appeared_since_last("save as") is True
