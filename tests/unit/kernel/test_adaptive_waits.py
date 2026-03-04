"""Tests for marlow.kernel.adaptive_waits — EMA per app timing."""

import time
import pytest
from marlow.kernel.adaptive_waits import AppTiming, AdaptiveWaits


class TestAppTiming:
    def test_app_timing_dataclass(self):
        t = AppTiming(app_name="notepad", ema_seconds=1.0, sample_count=5)
        assert t.app_name == "notepad"
        assert t.ema_seconds == 1.0
        assert t.sample_count == 5

    def test_app_timing_recommended_wait(self):
        """recommended_wait = EMA * 1.5."""
        t = AppTiming(app_name="app", ema_seconds=2.0, sample_count=1)
        assert t.recommended_wait == pytest.approx(3.0)

    def test_app_timing_recommended_wait_clamped_min(self):
        """recommended_wait floor is 0.5."""
        t = AppTiming(app_name="app", ema_seconds=0.1, sample_count=1)
        assert t.recommended_wait == 0.5

    def test_app_timing_recommended_wait_clamped_max(self):
        """recommended_wait ceiling is 15.0."""
        t = AppTiming(app_name="app", ema_seconds=20.0, sample_count=1)
        assert t.recommended_wait == 15.0

    def test_app_timing_has_data(self):
        assert AppTiming(app_name="a", sample_count=0).has_data is False
        assert AppTiming(app_name="a", sample_count=1).has_data is True


class TestAdaptiveWaits:
    def setup_method(self):
        self.waits = AdaptiveWaits()

    def test_get_wait_unknown_app(self):
        """Unknown app returns DEFAULT_WAIT (2.0)."""
        assert self.waits.get_wait("some_obscure_app") == AdaptiveWaits.DEFAULT_WAIT

    def test_get_wait_known_baseline_notepad(self):
        """Notepad baseline = 0.8 * 1.5 = 1.2."""
        wait = self.waits.get_wait("notepad")
        assert wait == pytest.approx(0.8 * 1.5)

    def test_get_wait_known_baseline_vscode(self):
        """VS Code baseline = 4.0 * 1.5 = 6.0."""
        wait = self.waits.get_wait("code")
        assert wait == pytest.approx(4.0 * 1.5)

    def test_get_wait_case_insensitive(self):
        """App name matching is case-insensitive."""
        assert self.waits.get_wait("Notepad") == self.waits.get_wait("notepad")

    def test_record_first_observation(self):
        self.waits.record("myapp", 1.5)
        timing = self.waits.get_timing("myapp")
        assert timing is not None
        assert timing.ema_seconds == 1.5
        assert timing.sample_count == 1
        assert timing.min_observed == 1.5
        assert timing.max_observed == 1.5

    def test_record_ema_update(self):
        """EMA converges toward repeated observations."""
        # Record 1.0 several times
        for _ in range(10):
            self.waits.record("myapp", 1.0)
        timing = self.waits.get_timing("myapp")
        # After many observations of 1.0, EMA should be close to 1.0
        assert abs(timing.ema_seconds - 1.0) < 0.1

    def test_record_min_max_tracked(self):
        self.waits.record("myapp", 2.0)
        self.waits.record("myapp", 0.5)
        self.waits.record("myapp", 5.0)
        timing = self.waits.get_timing("myapp")
        assert timing.min_observed == 0.5
        assert timing.max_observed == 5.0

    def test_get_wait_after_recording(self):
        """Learned data takes priority over baselines."""
        # Record fast times for notepad (baseline is 0.8)
        for _ in range(5):
            self.waits.record("notepad", 0.3)
        wait = self.waits.get_wait("notepad")
        # Should use learned EMA (~0.3), not baseline (0.8)
        assert wait < 0.8 * 1.5  # less than baseline * buffer

    def test_get_timing_exists(self):
        self.waits.record("myapp", 1.0)
        assert self.waits.get_timing("myapp") is not None

    def test_get_timing_not_exists(self):
        assert self.waits.get_timing("nonexistent") is None

    def test_format_for_planner(self):
        self.waits.record("notepad", 0.8)
        self.waits.record("chrome", 3.0)
        output = self.waits.format_for_planner()
        assert "notepad" in output
        assert "chrome" in output
        assert "App launch timings:" in output

    def test_format_for_planner_empty(self):
        output = self.waits.format_for_planner()
        assert "No app timing data" in output

    def test_record_clamps_minimum(self):
        """Actual time below 0.1 is clamped to 0.1."""
        self.waits.record("app", 0.001)
        timing = self.waits.get_timing("app")
        assert timing.ema_seconds >= 0.1

    def test_get_all_timings(self):
        self.waits.record("a", 1.0)
        self.waits.record("b", 2.0)
        all_t = self.waits.get_all_timings()
        assert "a" in all_t
        assert "b" in all_t
        assert len(all_t) == 2
