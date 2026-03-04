"""Tests for GOAP + DesktopWeather integration in AutonomousMarlow."""

from marlow.kernel.integration import AutonomousMarlow


class TestGOAPIntegration:
    def setup_method(self):
        self.marlow = AutonomousMarlow.__new__(AutonomousMarlow)
        # Minimal init — only what we need
        from marlow.kernel.planning.goap import GOAPPlanner
        from marlow.kernel.desktop_weather import DesktopWeather
        self.marlow._goap = GOAPPlanner()
        self.marlow._weather = DesktopWeather()

    def test_goap_accessible(self):
        """goap_planner property returns the GOAPPlanner instance."""
        assert self.marlow._goap is not None
        assert self.marlow._goap.action_count > 0

    def test_weather_accessible(self):
        """desktop_weather returns DesktopWeather instance."""
        from marlow.kernel.desktop_weather import Climate
        report = self.marlow._weather.get_report()
        assert report.climate == Climate.ESTABLE

    def test_goap_planner_has_actions(self):
        """GOAP planner ships with predefined actions."""
        from marlow.kernel.planning.goap import GOAP_ACTIONS
        assert self.marlow._goap.action_count == len(GOAP_ACTIONS)
