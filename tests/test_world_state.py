"""Tests for marlow.kernel.world_state and marlow.kernel.loop_guard."""

import pytest

from marlow.kernel.world_state import WindowInfo, WorldStateCapture, WorldStateSnapshot
from marlow.kernel.loop_guard import LoopGuard, LoopGuardResult


# ── Helpers ──

def _make_window(hwnd=1, title="Notepad", process="notepad.exe"):
    return WindowInfo(hwnd=hwnd, title=title, process_name=process)


def _make_snapshot(**kwargs):
    defaults = {
        "cycle_number": 1,
        "timestamp_mono": 100.0,
        "timestamp_utc": "2026-03-02T12:00:00.000Z",
    }
    defaults.update(kwargs)
    return WorldStateSnapshot(**defaults)


# ── WorldStateSnapshot ──


class TestWorldStateSnapshot:
    """Tests for the immutable desktop snapshot."""

    def test_frozen(self):
        """Snapshot fields cannot be modified."""
        snap = _make_snapshot()
        with pytest.raises(AttributeError):
            snap.cycle_number = 99

    def test_has_window(self):
        """has_window finds window by partial title (case-insensitive)."""
        snap = _make_snapshot(open_windows=(
            _make_window(1, "Notepad"),
            _make_window(2, "Google Chrome"),
        ))
        assert snap.has_window("notepad") is True
        assert snap.has_window("Chrome") is True
        assert snap.has_window("Firefox") is False

    def test_get_window(self):
        """get_window returns the matching WindowInfo."""
        w1 = _make_window(1, "Notepad")
        w2 = _make_window(2, "Google Chrome")
        snap = _make_snapshot(open_windows=(w1, w2))

        result = snap.get_window("chrome")
        assert result is not None
        assert result.title == "Google Chrome"
        assert result.hwnd == 2

    def test_get_window_not_found(self):
        """get_window returns None when no match."""
        snap = _make_snapshot(open_windows=(_make_window(1, "Notepad"),))
        assert snap.get_window("Firefox") is None

    def test_fingerprint(self):
        """Fingerprint returns a consistent 12-char hash."""
        snap = _make_snapshot(
            active_window=_make_window(1, "Notepad"),
            open_windows=(_make_window(1, "Notepad"),),
        )
        fp = snap.fingerprint()
        assert isinstance(fp, str)
        assert len(fp) == 12
        # Same snapshot → same fingerprint
        assert snap.fingerprint() == fp

    def test_fingerprint_changes(self):
        """Different state produces a different fingerprint."""
        snap1 = _make_snapshot(
            active_window=_make_window(1, "Notepad"),
            open_windows=(_make_window(1, "Notepad"),),
        )
        snap2 = _make_snapshot(
            active_window=_make_window(2, "Chrome"),
            open_windows=(_make_window(2, "Chrome"),),
        )
        assert snap1.fingerprint() != snap2.fingerprint()

    def test_diff_active_window_changed(self):
        """diff detects active window change."""
        snap1 = _make_snapshot(active_window=_make_window(1, "Notepad"))
        snap2 = _make_snapshot(active_window=_make_window(2, "Chrome"))
        changes = snap2.diff(snap1)
        assert "active_window" in changes
        assert changes["active_window"]["from"] == "Notepad"
        assert changes["active_window"]["to"] == "Chrome"

    def test_diff_window_count_changed(self):
        """diff detects window count change."""
        snap1 = _make_snapshot(open_windows=(_make_window(1, "A"),))
        snap2 = _make_snapshot(open_windows=(
            _make_window(1, "A"), _make_window(2, "B"),
        ))
        changes = snap2.diff(snap1)
        assert "window_count" in changes
        assert changes["window_count"]["from"] == 1
        assert changes["window_count"]["to"] == 2

    def test_diff_clipboard_changed(self):
        """diff detects clipboard hash change."""
        snap1 = _make_snapshot(clipboard_hash="abc123")
        snap2 = _make_snapshot(clipboard_hash="def456")
        changes = snap2.diff(snap1)
        assert changes["clipboard_changed"] is True

    def test_diff_no_changes(self):
        """diff returns empty dict when snapshots are identical."""
        snap = _make_snapshot(
            active_window=_make_window(1, "Notepad"),
            open_windows=(_make_window(1, "Notepad"),),
            clipboard_hash="abc",
            focused_element="btn",
        )
        changes = snap.diff(snap)
        assert changes == {}

    def test_active_window_title_property(self):
        """active_window_title returns title or empty string."""
        snap_with = _make_snapshot(active_window=_make_window(1, "Notepad"))
        assert snap_with.active_window_title == "Notepad"

        snap_without = _make_snapshot()
        assert snap_without.active_window_title == ""

    def test_active_process_property(self):
        """active_process returns process name or empty string."""
        snap = _make_snapshot(
            active_window=_make_window(1, "X", "explorer.exe"),
        )
        assert snap.active_process == "explorer.exe"

        snap_none = _make_snapshot()
        assert snap_none.active_process == ""

    def test_window_count_property(self):
        """window_count returns correct count."""
        snap = _make_snapshot(open_windows=(
            _make_window(1, "A"),
            _make_window(2, "B"),
            _make_window(3, "C"),
        ))
        assert snap.window_count == 3

    def test_diff_focused_element_changed(self):
        """diff detects focused element change."""
        snap1 = _make_snapshot(focused_element="button_save")
        snap2 = _make_snapshot(focused_element="text_input")
        changes = snap2.diff(snap1)
        assert "focused_element" in changes
        assert changes["focused_element"]["from"] == "button_save"
        assert changes["focused_element"]["to"] == "text_input"


# ── WorldStateCapture ──


class TestWorldStateCapture:
    """Tests for desktop state capture."""

    def test_capture_returns_snapshot(self):
        """capture() returns a WorldStateSnapshot."""
        wsc = WorldStateCapture()
        snap = wsc.capture()
        assert isinstance(snap, WorldStateSnapshot)
        assert snap.cycle_number == 1
        assert snap.timestamp_mono > 0
        assert snap.timestamp_utc.endswith("Z")

    def test_cycle_counter_increments(self):
        """Each capture increments the cycle number."""
        wsc = WorldStateCapture()
        s1 = wsc.capture()
        s2 = wsc.capture()
        s3 = wsc.capture()
        assert s1.cycle_number == 1
        assert s2.cycle_number == 2
        assert s3.cycle_number == 3

    def test_capture_with_goal_context(self):
        """goal_id and step_index are passed through."""
        wsc = WorldStateCapture()
        snap = wsc.capture(active_goal_id="g42", active_step_index=3)
        assert snap.active_goal_id == "g42"
        assert snap.active_step_index == 3


# ── LoopGuard ──


class TestLoopGuard:
    """Tests for 4-layer anti-loop protection."""

    def test_normal_operation(self):
        """10 different actions with changing state → all continue."""
        lg = LoopGuard()
        for i in range(10):
            result = lg.check(f"action_{i}", f"fp_{i}")
            assert result.should_continue is True

    def test_max_iterations(self):
        """25 cycles → blocked on the 25th."""
        lg = LoopGuard(max_iterations=25)
        for i in range(24):
            result = lg.check(f"action_{i}", f"fp_{i}")
            assert result.should_continue is True

        result = lg.check("action_24", "fp_24")
        assert result.should_continue is False
        assert result.layer == "max_iterations"
        assert "25" in result.reason

    def test_repetition_detected(self):
        """Same action 3x → blocked."""
        lg = LoopGuard(max_repetitions=3)
        lg.check("click:save", "fp_1")
        lg.check("click:save", "fp_2")
        result = lg.check("click:save", "fp_3")
        assert result.should_continue is False
        assert result.layer == "repetition"
        assert "click:save" in result.reason

    def test_repetition_different_actions(self):
        """Different actions → not blocked by repetition."""
        lg = LoopGuard(max_repetitions=3)
        lg.check("click:save", "fp_1")
        lg.check("type:hello", "fp_2")
        result = lg.check("click:save", "fp_3")
        assert result.should_continue is True

    def test_no_progress(self):
        """Same fingerprint 5x → blocked (first call sets baseline, next 5 are stale)."""
        lg = LoopGuard(max_no_progress=5)
        # First call sets the fingerprint
        lg.check("a1", "same_fp")
        # Next 5 with same fingerprint → no progress
        for i in range(4):
            result = lg.check(f"a{i+2}", "same_fp")
            assert result.should_continue is True
        result = lg.check("a6", "same_fp")
        assert result.should_continue is False
        assert result.layer == "no_progress"

    def test_progress_resets_counter(self):
        """Changing fingerprint resets the no-progress counter."""
        lg = LoopGuard(max_no_progress=5)
        lg.check("a1", "fp_a")
        lg.check("a2", "fp_a")  # +1 no progress
        lg.check("a3", "fp_a")  # +1 no progress
        lg.check("a4", "fp_a")  # +1 no progress
        # Change fingerprint → resets
        lg.check("a5", "fp_b")
        # 4 more same → still under 5
        for i in range(4):
            result = lg.check(f"a{i+6}", "fp_b")
            assert result.should_continue is True
        # 5th same → blocked
        result = lg.check("a10", "fp_b")
        assert result.should_continue is False
        assert result.layer == "no_progress"

    def test_token_budget(self):
        """Recording 50K tokens → blocked on next check."""
        lg = LoopGuard(max_tokens=50_000)
        lg.record_tokens(50_000)
        result = lg.check("action", "fp_new")
        assert result.should_continue is False
        assert result.layer == "token_budget"
        assert "50000" in result.reason

    def test_token_budget_incremental(self):
        """Incrementally adding tokens until budget exhausted."""
        lg = LoopGuard(max_tokens=100)
        lg.record_tokens(40)
        result = lg.check("a1", "fp_1")
        assert result.should_continue is True

        lg.record_tokens(70)  # total 110 > 100
        result = lg.check("a2", "fp_2")
        assert result.should_continue is False
        assert result.layer == "token_budget"

    def test_reset(self):
        """reset clears all counters — guard allows again."""
        lg = LoopGuard(max_iterations=5)
        for i in range(5):
            lg.check(f"a{i}", f"fp_{i}")
        # Should be blocked now
        assert lg.check("a5", "fp_5").should_continue is False

        lg.reset()
        # After reset, should be fine
        result = lg.check("a_new", "fp_new")
        assert result.should_continue is True
        assert lg.stats["iterations"] == 1

    def test_stats(self):
        """stats returns correct dictionary."""
        lg = LoopGuard()
        lg.check("click:save", "fp_1")
        lg.check("type:hello", "fp_1")
        lg.record_tokens(1500)

        stats = lg.stats
        assert stats["iterations"] == 2
        assert stats["tokens_used"] == 1500
        assert stats["no_progress_streak"] == 1  # fp_1 repeated
        assert stats["last_actions"] == ["click:save", "type:hello"]

    def test_suggestion_max_iterations(self):
        """max_iterations layer gives meaningful suggestion."""
        lg = LoopGuard(max_iterations=1)
        result = lg.check("a", "fp")
        assert result.suggestion != ""
        assert "replan" in result.suggestion.lower() or "abort" in result.suggestion.lower()

    def test_suggestion_repetition(self):
        """repetition layer gives meaningful suggestion."""
        lg = LoopGuard(max_repetitions=2)
        lg.check("same", "fp_1")
        result = lg.check("same", "fp_2")
        assert result.suggestion != ""
        assert "alternative" in result.suggestion.lower() or "skip" in result.suggestion.lower()

    def test_suggestion_no_progress(self):
        """no_progress layer gives meaningful suggestion."""
        lg = LoopGuard(max_no_progress=1)
        lg.check("a1", "same")
        result = lg.check("a2", "same")
        assert result.suggestion != ""
        assert "replan" in result.suggestion.lower() or "progress" in result.suggestion.lower()

    def test_suggestion_token_budget(self):
        """token_budget layer gives meaningful suggestion."""
        lg = LoopGuard(max_tokens=1)
        lg.record_tokens(10)
        result = lg.check("a", "fp")
        assert result.suggestion != ""
        assert "abort" in result.suggestion.lower() or "best-effort" in result.suggestion.lower()
