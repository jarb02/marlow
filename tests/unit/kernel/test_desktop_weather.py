"""Tests for marlow.kernel.desktop_weather — World State Trends."""

import time
import pytest
from marlow.kernel.desktop_weather import (
    Climate,
    WeatherReport,
    DesktopWeather,
)


# ------------------------------------------------------------------
# Climate enum
# ------------------------------------------------------------------

class TestClimate:
    def test_climate_enum_values(self):
        assert Climate.ESTABLE.value == "estable"
        assert Climate.OCUPADO.value == "ocupado"
        assert Climate.INESTABLE.value == "inestable"
        assert Climate.TORMENTA.value == "tormenta"


# ------------------------------------------------------------------
# WeatherReport
# ------------------------------------------------------------------

class TestWeatherReport:
    def test_weather_report_dataclass(self):
        r = WeatherReport(
            climate=Climate.ESTABLE, dialog_rate=0.0, error_rate=0.0,
            window_change_rate=1.0, active_windows=3, confidence=0.9,
        )
        assert r.climate == Climate.ESTABLE
        assert r.dialog_rate == 0.0
        assert r.active_windows == 3

    def test_weather_report_safe_estable(self):
        r = WeatherReport(Climate.ESTABLE, 0, 0, 0, 0, 0.9)
        assert r.is_safe_to_execute is True

    def test_weather_report_safe_ocupado(self):
        r = WeatherReport(Climate.OCUPADO, 0, 0, 8.0, 6, 0.7)
        assert r.is_safe_to_execute is True

    def test_weather_report_not_safe_tormenta(self):
        r = WeatherReport(Climate.TORMENTA, 5.0, 6.0, 20.0, 10, 1.0)
        assert r.is_safe_to_execute is False

    def test_weather_report_should_pause_tormenta(self):
        r = WeatherReport(Climate.TORMENTA, 5.0, 6.0, 20.0, 10, 1.0)
        assert r.should_pause is True

    def test_weather_report_should_not_pause_estable(self):
        r = WeatherReport(Climate.ESTABLE, 0, 0, 0, 0, 0.9)
        assert r.should_pause is False

    def test_weather_report_should_not_pause_inestable(self):
        r = WeatherReport(Climate.INESTABLE, 3.0, 1.0, 5.0, 4, 0.75)
        assert r.should_pause is False

    def test_weather_report_frozen(self):
        r = WeatherReport(Climate.ESTABLE, 0, 0, 0, 0, 0.9)
        with pytest.raises(AttributeError):
            r.climate = Climate.TORMENTA


# ------------------------------------------------------------------
# DesktopWeather — basic
# ------------------------------------------------------------------

class TestDesktopWeather:
    def setup_method(self):
        self.weather = DesktopWeather()

    def test_initial_report_estable(self):
        """No events = calm."""
        report = self.weather.get_report()
        assert report.climate == Climate.ESTABLE
        assert report.dialog_rate == 0.0
        assert report.error_rate == 0.0

    def test_record_dialog_increases_rate(self):
        for _ in range(5):
            self.weather.record_dialog()
        report = self.weather.get_report()
        assert report.dialog_rate > 0

    def test_record_error_increases_rate(self):
        for _ in range(5):
            self.weather.record_error()
        report = self.weather.get_report()
        assert report.error_rate > 0

    def test_record_window_change(self):
        for _ in range(10):
            self.weather.record_window_change()
        report = self.weather.get_report()
        assert report.window_change_rate > 0

    def test_update_window_count(self):
        self.weather.update_window_count(7)
        report = self.weather.get_report()
        assert report.active_windows == 7

    def test_update_window_count_negative_clamped(self):
        self.weather.update_window_count(-5)
        report = self.weather.get_report()
        assert report.active_windows == 0


# ------------------------------------------------------------------
# DesktopWeather — classification
# ------------------------------------------------------------------

class TestClassification:
    def test_classify_ocupado_many_windows(self):
        weather = DesktopWeather()
        weather.update_window_count(8)
        report = weather.get_report()
        assert report.climate == Climate.OCUPADO

    def test_classify_ocupado_window_changes(self):
        weather = DesktopWeather(buffer_seconds=60)
        # Simulate 10 rapid window changes (rate > 6/min threshold)
        for _ in range(10):
            weather.record_window_change()
        report = weather.get_report()
        # With 10 events in ~0s, rate is very high
        assert report.climate in (Climate.OCUPADO, Climate.INESTABLE)

    def test_classify_inestable_dialog_rate(self):
        weather = DesktopWeather()
        # 3 dialogs in rapid succession -> rate >= 2/min
        for _ in range(3):
            weather.record_dialog()
        report = weather.get_report()
        assert report.climate in (Climate.INESTABLE, Climate.TORMENTA)

    def test_classify_tormenta_error_rate(self):
        weather = DesktopWeather()
        # 6 errors in rapid succession -> rate >= 5/min
        for _ in range(6):
            weather.record_error()
        report = weather.get_report()
        assert report.climate == Climate.TORMENTA

    def test_classify_tormenta_dialog_rate(self):
        weather = DesktopWeather()
        # 5 dialogs in rapid succession -> rate >= 4/min
        for _ in range(5):
            weather.record_dialog()
        report = weather.get_report()
        assert report.climate == Climate.TORMENTA


# ------------------------------------------------------------------
# DesktopWeather — buffer + format
# ------------------------------------------------------------------

class TestBufferAndFormat:
    def test_buffer_trimming(self):
        """Old events outside buffer_seconds are removed."""
        weather = DesktopWeather(buffer_seconds=2)
        # Inject old timestamps directly
        old_time = time.time() - 10
        weather._dialog_events.append(old_time)
        weather._error_events.append(old_time)
        weather._window_events.append(old_time)
        weather._trim_buffers()
        assert len(weather._dialog_events) == 0
        assert len(weather._error_events) == 0
        assert len(weather._window_events) == 0

    def test_format_for_planner(self):
        weather = DesktopWeather()
        weather.update_window_count(3)
        output = weather.format_for_planner()
        assert "Desktop climate: estable" in output
        assert "active windows: 3" in output

    def test_confidence_range(self):
        weather = DesktopWeather()
        report = weather.get_report()
        assert 0.0 <= report.confidence <= 1.0
