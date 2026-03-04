"""Tests for marlow.kernel.plan_granularity — Adaptive Plan Granularity."""

import pytest
from marlow.kernel.plan_granularity import (
    GranularityConfig,
    GranularityLevel,
    GRANULARITY_CONFIGS,
    PlanGranularityAdapter,
)
from marlow.kernel.scoring.reliability import ReliabilityTracker


# ------------------------------------------------------------------
# GranularityLevel + GranularityConfig
# ------------------------------------------------------------------

class TestGranularityLevel:
    def test_granularity_level_constants(self):
        assert GranularityLevel.MINIMAL == "minimal"
        assert GranularityLevel.STANDARD == "standard"
        assert GranularityLevel.CAUTIOUS == "cautious"
        assert GranularityLevel.PARANOID == "paranoid"


class TestGranularityConfig:
    def test_granularity_config_dataclass(self):
        c = GRANULARITY_CONFIGS[GranularityLevel.STANDARD]
        assert c.level == "standard"
        assert c.verify_after_action is True
        assert isinstance(c.add_wait_after_action, float)

    def test_granularity_config_is_cautious(self):
        assert GRANULARITY_CONFIGS[GranularityLevel.CAUTIOUS].is_cautious is True
        assert GRANULARITY_CONFIGS[GranularityLevel.PARANOID].is_cautious is True

    def test_granularity_config_not_cautious(self):
        assert GRANULARITY_CONFIGS[GranularityLevel.MINIMAL].is_cautious is False
        assert GRANULARITY_CONFIGS[GranularityLevel.STANDARD].is_cautious is False

    def test_granularity_config_frozen(self):
        c = GRANULARITY_CONFIGS[GranularityLevel.STANDARD]
        with pytest.raises(AttributeError):
            c.level = "hacked"


# ------------------------------------------------------------------
# Predefined configs
# ------------------------------------------------------------------

class TestPredefinedConfigs:
    def test_predefined_configs_exist(self):
        assert len(GRANULARITY_CONFIGS) == 4
        for level in ("minimal", "standard", "cautious", "paranoid"):
            assert level in GRANULARITY_CONFIGS

    def test_minimal_config_values(self):
        c = GRANULARITY_CONFIGS["minimal"]
        assert c.verify_after_action is False
        assert c.verify_with_ocr is False
        assert c.add_wait_after_action == 0.0
        assert c.max_retries == 1
        assert c.add_fallback_steps is False

    def test_standard_config_values(self):
        c = GRANULARITY_CONFIGS["standard"]
        assert c.verify_after_action is True
        assert c.verify_with_ocr is False
        assert c.add_wait_after_action == 0.3
        assert c.max_retries == 2

    def test_cautious_config_values(self):
        c = GRANULARITY_CONFIGS["cautious"]
        assert c.verify_after_action is True
        assert c.verify_with_ocr is True
        assert c.add_wait_after_action == 0.5
        assert c.max_retries == 3
        assert c.add_fallback_steps is True

    def test_paranoid_config_values(self):
        c = GRANULARITY_CONFIGS["paranoid"]
        assert c.verify_after_action is True
        assert c.verify_with_ocr is True
        assert c.add_wait_after_action == 1.0
        assert c.max_retries == 4
        assert c.add_fallback_steps is True


# ------------------------------------------------------------------
# PlanGranularityAdapter — get_level
# ------------------------------------------------------------------

class TestGetLevel:
    def test_get_level_no_data_standard(self):
        """No reliability data -> standard (safe default)."""
        adapter = PlanGranularityAdapter()
        assert adapter.get_level("notepad") == GranularityLevel.STANDARD

    def test_get_level_no_tool_standard(self):
        """No tool_name -> None reliability -> standard."""
        adapter = PlanGranularityAdapter()
        assert adapter.get_level("notepad", "") == GranularityLevel.STANDARD

    def test_get_level_high_reliability_minimal(self):
        rt = ReliabilityTracker(min_samples=1)
        rt.record("click", 0.95, app_name="notepad")
        adapter = PlanGranularityAdapter(reliability_tracker=rt)
        assert adapter.get_level("notepad", "click") == GranularityLevel.MINIMAL

    def test_get_level_medium_reliability_standard(self):
        rt = ReliabilityTracker(min_samples=1)
        rt.record("click", 0.7, app_name="chrome")
        adapter = PlanGranularityAdapter(reliability_tracker=rt)
        assert adapter.get_level("chrome", "click") == GranularityLevel.STANDARD

    def test_get_level_low_reliability_cautious(self):
        rt = ReliabilityTracker(min_samples=1)
        rt.record("click", 0.4, app_name="legacy_app")
        adapter = PlanGranularityAdapter(reliability_tracker=rt)
        assert adapter.get_level("legacy_app", "click") == GranularityLevel.CAUTIOUS

    def test_get_level_very_low_paranoid(self):
        rt = ReliabilityTracker(min_samples=1)
        rt.record("click", 0.1, app_name="broken_app")
        adapter = PlanGranularityAdapter(reliability_tracker=rt)
        assert adapter.get_level("broken_app", "click") == GranularityLevel.PARANOID


# ------------------------------------------------------------------
# Overrides
# ------------------------------------------------------------------

class TestOverrides:
    def test_override_forces_level(self):
        adapter = PlanGranularityAdapter()
        adapter.set_override("notepad", GranularityLevel.PARANOID)
        assert adapter.get_level("notepad", "click") == GranularityLevel.PARANOID

    def test_clear_override(self):
        adapter = PlanGranularityAdapter()
        adapter.set_override("notepad", GranularityLevel.PARANOID)
        adapter.clear_override("notepad")
        # Back to default (standard, no reliability data)
        assert adapter.get_level("notepad") == GranularityLevel.STANDARD

    def test_override_case_insensitive(self):
        adapter = PlanGranularityAdapter()
        adapter.set_override("Notepad", GranularityLevel.MINIMAL)
        assert adapter.get_level("notepad") == GranularityLevel.MINIMAL


# ------------------------------------------------------------------
# get_config + format_summary
# ------------------------------------------------------------------

class TestGetConfigAndFormat:
    def test_get_config_returns_config(self):
        adapter = PlanGranularityAdapter()
        config = adapter.get_config("some_app")
        assert isinstance(config, GranularityConfig)
        assert config.level == GranularityLevel.STANDARD

    def test_format_summary_no_overrides(self):
        adapter = PlanGranularityAdapter()
        output = adapter.format_summary()
        assert "No overrides" in output

    def test_format_summary_with_overrides(self):
        adapter = PlanGranularityAdapter()
        adapter.set_override("notepad", GranularityLevel.MINIMAL)
        output = adapter.format_summary()
        assert "notepad" in output
        assert "minimal" in output
