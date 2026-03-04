"""App Awareness — detects app frameworks and recommends interaction methods."""

from __future__ import annotations

import logging

logger = logging.getLogger("marlow.kernel.app_awareness")


class AppAwareness:
    """Tracks which framework each app uses and recommends the best
    interaction method.  Electron apps should prefer CDP, native apps use UIA.
    """

    # Known Electron apps (common ones, can be expanded)
    KNOWN_ELECTRON = {
        "code", "vscode", "visual studio code",  # VS Code
        "slack",
        "discord",
        "teams", "microsoft teams",
        "notion",
        "figma",
        "postman",
        "obsidian",
        "spotify",  # Electron-based desktop app
        "whatsapp",
        "telegram desktop",
        "1password",
    }

    KNOWN_NATIVE = {
        "notepad", "calculator", "explorer", "file explorer",
        "paint", "wordpad", "cmd", "powershell", "terminal",
        "task manager",
    }

    def __init__(self):
        self._detected_frameworks: dict[str, str] = {}  # app_name -> framework

    def register_framework(self, app_name: str, framework: str):
        """Register a detected framework for an app."""
        self._detected_frameworks[app_name.lower()] = framework.lower()

    def is_electron(self, window_title: str) -> bool:
        """Check if a window likely belongs to an Electron app."""
        title_lower = window_title.lower()
        # Check detected frameworks first
        for app, fw in self._detected_frameworks.items():
            if app in title_lower and "electron" in fw:
                return True
        # Check known list
        return any(app in title_lower for app in self.KNOWN_ELECTRON)

    def is_native(self, window_title: str) -> bool:
        """Check if a window is a known native Windows app."""
        title_lower = window_title.lower()
        return any(app in title_lower for app in self.KNOWN_NATIVE)

    def recommend_method(self, window_title: str) -> str:
        """Recommend interaction method for a window.

        Returns ``"cdp"`` for Electron, ``"uia"`` for native/default.
        """
        if self.is_electron(window_title):
            return "cdp"
        return "uia"

    async def detect_and_register(self, executor, window_title: str) -> str:
        """Use detect_app_framework tool to detect and register an app's
        framework.  Call this when first interacting with an unknown app.
        """
        try:
            result = await executor.execute(
                "detect_app_framework", {"window_title": window_title},
            )
            if result.success and result.data:
                framework = ""
                if isinstance(result.data, dict):
                    framework = result.data.get("framework", "")
                elif isinstance(result.data, str):
                    framework = result.data
                if framework:
                    self.register_framework(window_title, framework)
                    return framework
        except Exception:
            pass
        return "unknown"
