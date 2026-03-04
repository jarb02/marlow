"""Tests for marlow.kernel.app_awareness — framework detection and method recommendation."""

import pytest
from marlow.kernel.app_awareness import AppAwareness


class TestKnownApps:
    def test_known_electron_vscode(self):
        aa = AppAwareness()
        assert aa.is_electron("main.py - Visual Studio Code") is True

    def test_known_electron_slack(self):
        aa = AppAwareness()
        assert aa.is_electron("Slack - General") is True

    def test_known_native_notepad(self):
        aa = AppAwareness()
        assert aa.is_native("Untitled - Notepad") is True

    def test_known_native_calculator(self):
        aa = AppAwareness()
        assert aa.is_native("Calculator") is True


class TestRecommendMethod:
    def test_recommend_cdp_for_electron(self):
        aa = AppAwareness()
        assert aa.recommend_method("index.js - Visual Studio Code") == "cdp"

    def test_recommend_uia_for_native(self):
        aa = AppAwareness()
        assert aa.recommend_method("Untitled - Notepad") == "uia"

    def test_recommend_uia_default(self):
        aa = AppAwareness()
        assert aa.recommend_method("Some Unknown App") == "uia"


class TestRegisterFramework:
    def test_register_framework(self):
        aa = AppAwareness()
        aa.register_framework("myapp", "electron")
        assert aa._detected_frameworks["myapp"] == "electron"

    def test_is_electron_after_register(self):
        aa = AppAwareness()
        assert aa.is_electron("MyApp - Editor") is False
        aa.register_framework("myapp", "electron")
        assert aa.is_electron("MyApp - Editor") is True
