"""Tests for marlow.kernel.vision_pipeline — Proactive Monitor + Vision Pipeline."""

import pytest
from marlow.kernel.vision_pipeline import (
    MonitorLevel,
    VisionCheck,
    VisionPipeline,
)
from marlow.kernel.sensor_fusion import SensorFusion, SensorTier


# ------------------------------------------------------------------
# MonitorLevel
# ------------------------------------------------------------------

class TestMonitorLevel:
    def test_monitor_level_ordering(self):
        assert MonitorLevel.PASSIVE < MonitorLevel.LIGHT
        assert MonitorLevel.LIGHT < MonitorLevel.ACTIVE
        assert MonitorLevel.ACTIVE < MonitorLevel.FULL


# ------------------------------------------------------------------
# VisionPipeline — get_monitor_level
# ------------------------------------------------------------------

class TestGetMonitorLevel:
    def setup_method(self):
        self.pipeline = VisionPipeline()

    def test_get_monitor_level_open_application(self):
        assert self.pipeline.get_monitor_level("open_application") == MonitorLevel.ACTIVE

    def test_get_monitor_level_type_text(self):
        assert self.pipeline.get_monitor_level("type_text") == MonitorLevel.LIGHT

    def test_get_monitor_level_take_screenshot(self):
        assert self.pipeline.get_monitor_level("take_screenshot") == MonitorLevel.PASSIVE

    def test_get_monitor_level_click(self):
        assert self.pipeline.get_monitor_level("click") == MonitorLevel.ACTIVE

    def test_get_monitor_level_unknown(self):
        assert self.pipeline.get_monitor_level("some_unknown_tool") == MonitorLevel.LIGHT


# ------------------------------------------------------------------
# VisionPipeline — should_check / should_screenshot / should_ocr
# ------------------------------------------------------------------

class TestShouldChecks:
    def setup_method(self):
        self.pipeline = VisionPipeline()

    def test_should_check_active_tool(self):
        assert self.pipeline.should_check("click") is True

    def test_should_check_passive_tool(self):
        assert self.pipeline.should_check("take_screenshot") is False

    def test_should_check_light_tool(self):
        assert self.pipeline.should_check("type_text") is True

    def test_should_screenshot_full_only(self):
        # No tool is FULL by default, so all should be False
        assert self.pipeline.should_screenshot("click") is False
        assert self.pipeline.should_screenshot("take_screenshot") is False

    def test_should_ocr_full_only(self):
        assert self.pipeline.should_ocr("click") is False
        assert self.pipeline.should_ocr("take_screenshot") is False


# ------------------------------------------------------------------
# VisionCheck
# ------------------------------------------------------------------

class TestVisionCheck:
    def test_vision_check_dataclass(self):
        vc = VisionCheck(
            level=MonitorLevel.ACTIVE,
            duration_ms=42.5,
            elements_found=3,
            active_window="Notepad",
        )
        assert vc.level == MonitorLevel.ACTIVE
        assert vc.duration_ms == 42.5
        assert vc.elements_found == 3
        assert vc.active_window == "Notepad"
        assert vc.dialog_detected is False
        assert vc.needs_escalation is False

    def test_vision_check_frozen(self):
        vc = VisionCheck(level=MonitorLevel.PASSIVE, duration_ms=0, elements_found=0)
        with pytest.raises(AttributeError):
            vc.level = MonitorLevel.FULL


# ------------------------------------------------------------------
# VisionPipeline — create_check + record + recent
# ------------------------------------------------------------------

class TestCreateAndRecord:
    def setup_method(self):
        self.pipeline = VisionPipeline()

    def test_create_check_records(self):
        check = self.pipeline.create_check("click", duration_ms=15.0, elements_found=2)
        assert check.level == MonitorLevel.ACTIVE
        assert check.elements_found == 2
        assert len(self.pipeline.get_recent_checks()) == 1

    def test_create_check_dialog_escalation(self):
        check = self.pipeline.create_check("click", dialog_detected=True)
        assert check.dialog_detected is True
        assert check.needs_escalation is True

    def test_get_recent_checks(self):
        for i in range(5):
            self.pipeline.create_check("click", duration_ms=float(i))
        recent = self.pipeline.get_recent_checks()
        assert len(recent) == 5

    def test_get_recent_checks_limit(self):
        for i in range(20):
            self.pipeline.create_check("click", duration_ms=float(i))
        recent = self.pipeline.get_recent_checks(3)
        assert len(recent) == 3
        # Should be the last 3
        assert recent[-1].duration_ms == 19.0


# ------------------------------------------------------------------
# VisionPipeline — reset + stats + fusion
# ------------------------------------------------------------------

class TestResetAndStats:
    def test_reset_clears_all(self):
        pipeline = VisionPipeline()
        pipeline.create_check("click")
        pipeline.create_check("type_text")
        assert pipeline.stats["total_checks"] == 2
        pipeline.reset()
        assert pipeline.stats["total_checks"] == 0

    def test_stats(self):
        pipeline = VisionPipeline()
        pipeline.create_check("click", dialog_detected=True)
        pipeline.create_check("type_text")
        pipeline.create_check("open_application", dialog_detected=True)
        stats = pipeline.stats
        assert stats["total_checks"] == 3
        assert stats["dialogs_detected"] == 2
        assert stats["escalations"] == 2
        assert "fusion" in stats

    def test_fusion_accessible(self):
        pipeline = VisionPipeline()
        assert isinstance(pipeline.fusion, SensorFusion)
