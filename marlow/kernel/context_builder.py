"""Dynamic Context Builder — generates real-time system context for LLM prompts.

Collects live information from Marlow subsystems (desktop state, weather,
blackboard, user preferences) and formats it as a concise text block
injected into the system prompt before each LLM request.

Each section is optional and fault-tolerant — if a component is missing
or errors, it is silently skipped.

/ Generador de contexto dinamico para prompts del LLM.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any, Optional

logger = logging.getLogger("marlow.kernel.context_builder")


class ContextBuilder:
    """Build a dynamic context string from live Marlow subsystems.

    Parameters
    ----------
    platform : Platform or None
        Linux platform layer (windows, clipboard, etc.).
    blackboard : Blackboard or None
        Kernel shared state.
    desktop_weather : DesktopWeather or None
        Desktop stability tracker.
    location : dict or None
        ``{"city": str, "state": str, "timezone": str, ...}``
    """

    def __init__(
        self,
        platform: Any = None,
        blackboard: Any = None,
        desktop_weather: Any = None,
        location: Optional[dict] = None,
        app_knowledge: Any = None,
        memory: Any = None,
    ):
        self._platform = platform
        self._blackboard = blackboard
        self._weather = desktop_weather
        self._location = location or {}
        self._app_knowledge = app_knowledge
        self._memory = memory
        self._recent_events: list[dict] = []
        self._max_recent_events = 10
        # Async knowledge cache (updated periodically or on focus change)
        self._app_knowledge_cache: dict = {}
        self._knowledge_cache_app: str = ""  # which app the cache is for

    def build(self) -> str:
        """Build the dynamic context string. Safe to call every request."""
        sections: list[str] = []

        ctx = self._time_context()
        if ctx:
            sections.append(ctx)

        ctx = self._desktop_context()
        if ctx:
            sections.append(ctx)

        ctx = self._weather_context()
        if ctx:
            sections.append(ctx)

        ctx = self._preferences_context()
        if ctx:
            sections.append(ctx)

        ctx = self._blackboard_context()
        if ctx:
            sections.append(ctx)

        ctx = self._recent_actions_context()
        if ctx:
            sections.append(ctx)

        ctx = self._app_knowledge_context()
        if ctx:
            sections.append(ctx)

        ctx = self._recent_events_context()
        if ctx:
            sections.append(ctx)

        return "\n".join(sections)

    # ── Sections ──

    def _time_context(self) -> Optional[str]:
        """Current date, time, day of week, timezone."""
        try:
            tz_name = self._location.get("timezone")
            if tz_name:
                try:
                    import zoneinfo
                    tz = zoneinfo.ZoneInfo(tz_name)
                    now = datetime.datetime.now(tz)
                except Exception:
                    now = datetime.datetime.now().astimezone()
                    tz_name = str(now.tzinfo)
            else:
                now = datetime.datetime.now().astimezone()
                tz_name = str(now.tzinfo)

            time_str = now.strftime("%I:%M %p, %A %B %d %Y")
            ctx = "Current time: %s (%s)" % (time_str, tz_name)

            city = self._location.get("city")
            state = self._location.get("state")
            if city:
                loc_str = "%s, %s" % (city, state) if state else city
                ctx += "\nLocation: %s" % loc_str

            return ctx
        except Exception as e:
            logger.debug("time_context error: %s", e)
            return None

    def _desktop_context(self) -> Optional[str]:
        """List open windows (max 8) from the platform layer."""
        try:
            if not self._platform or not hasattr(self._platform, "windows"):
                return None

            windows = self._platform.windows.list_windows(include_minimized=False)
            if not windows:
                return "Desktop: no windows open"

            lines = []
            for w in windows[:8]:
                title = (w.title or "")[:60]
                app = w.app_name or ""
                if app and title:
                    lines.append("  - %s: %s" % (app, title))
                elif title:
                    lines.append("  - %s" % title)

            ctx = "Open windows (%d):\n%s" % (len(windows), "\n".join(lines))
            if len(windows) > 8:
                ctx += "\n  ... and %d more" % (len(windows) - 8)
            return ctx
        except Exception as e:
            logger.debug("desktop_context error: %s", e)
            return None

    def _weather_context(self) -> Optional[str]:
        """Desktop stability from DesktopWeather."""
        try:
            if not self._weather:
                return None
            return self._weather.format_for_planner()
        except Exception as e:
            logger.debug("weather_context error: %s", e)
            return None

    def _preferences_context(self) -> Optional[str]:
        """User preferences from ~/.marlow/memory/preferences.json."""
        try:
            from marlow.tools.memory import _load_category
            data = _load_category("preferences")
            if not data:
                return None

            lines = []
            for key, entry in list(data.items())[:10]:
                value = entry.get("value", entry) if isinstance(entry, dict) else entry
                lines.append("  - %s: %s" % (key, str(value)[:80]))

            if lines:
                return "User preferences:\n%s" % "\n".join(lines)
        except Exception as e:
            logger.debug("preferences_context error: %s", e)
        return None

    def _blackboard_context(self) -> Optional[str]:
        """Active goal and world state from the Blackboard."""
        try:
            if not self._blackboard:
                return None
            formatted = self._blackboard.format_for_planner()
            if formatted and "empty" not in formatted.lower():
                return "Kernel state:\n%s" % formatted
        except Exception as e:
            logger.debug("blackboard_context error: %s", e)
        return None

    def _recent_actions_context(self) -> Optional[str]:
        """Last 5 actions from MemorySystem short-term."""
        try:
            if not self._memory:
                return None
            recent = self._memory.get_recent_actions(5)
            if not recent:
                return None
            lines = []
            for entry in recent:
                tool = entry.content.get("tool", "?")
                success = entry.content.get("success", "?")
                dur = entry.content.get("duration_ms", "?")
                status = "OK" if success else "FAIL"
                err = entry.content.get("error", "")
                line = f"  - {tool}: {status} ({dur}ms)"
                if err:
                    line += f" — {err[:60]}"
                lines.append(line)
            return "Recent actions:\n%s" % "\n".join(lines)
        except Exception as e:
            logger.debug("recent_actions_context error: %s", e)
            return None

    def _app_knowledge_context(self) -> Optional[str]:
        """App knowledge for the focused window (from cache)."""
        try:
            if not self._app_knowledge_cache:
                return None
            cache = self._app_knowledge_cache
            lines = []

            # Reliability
            rel = cache.get("reliability")
            if rel is not None:
                lines.append(f"  - Reliability: {rel:.2f}")

            # Known elements (max 5)
            elems = cache.get("known_elements", {})
            if elems:
                elem_names = list(elems.keys())[:5]
                lines.append(f"  - Known elements: {', '.join(elem_names)}")

            # Error solutions (max 3)
            errors = cache.get("error_solutions", [])
            for err in errors[:3]:
                tool = err.get("tool", "?")
                solution = err.get("solution", "?")
                lines.append(f"  - {tool} error fix: {solution}")

            if not lines:
                return None
            app_name = cache.get("app_name", "unknown")
            return f"App context ({app_name}):\n" + "\n".join(lines)
        except Exception as e:
            logger.debug("app_knowledge_context error: %s", e)
            return None

    async def update_app_knowledge_cache(self, app_name: str) -> None:
        """Refresh the app knowledge cache for a given app.

        Called on focus change events or periodically by the daemon.
        Safe to call from any async context — queries AppKnowledgeManager.
        """
        if not self._app_knowledge or not app_name:
            return
        if app_name == self._knowledge_cache_app and self._app_knowledge_cache:
            return  # already cached for this app

        try:
            cache: dict = {"app_name": app_name}

            rel = await self._app_knowledge.get_reliability(app_name)
            cache["reliability"] = rel

            elems = await self._app_knowledge.get_known_elements(app_name)
            cache["known_elements"] = elems or {}

            # Collect recent error solutions
            solutions = []
            app_info = await self._app_knowledge.get_app_info(app_name)
            if app_info:
                # Check common error types
                for error_type in ("execution_error", "timeout", "element_not_found"):
                    sol = await self._app_knowledge.get_error_solution(
                        app_name, "", error_type,
                    )
                    if sol:
                        solutions.append({
                            "tool": error_type,
                            "solution": sol,
                        })
            cache["error_solutions"] = solutions

            self._app_knowledge_cache = cache
            self._knowledge_cache_app = app_name
        except Exception as e:
            logger.debug("update_app_knowledge_cache error: %s", e)

    # ── EventBus integration ──

    async def on_event(self, event) -> None:
        """EventBus handler — stores recent goal/world events for context."""
        try:
            summary = self._summarize_event(event)
            if not summary:
                return
            ts = datetime.datetime.fromtimestamp(
                event.timestamp,
            ).strftime("%H:%M:%S")
            self._recent_events.append({"time": ts, "summary": summary})
            if len(self._recent_events) > self._max_recent_events:
                self._recent_events.pop(0)

            # Refresh app knowledge cache on window focus changes
            if (
                hasattr(event, "event_type")
                and event.event_type in (
                    "world.window_changed", "world.focus_lost",
                )
                and self._app_knowledge
            ):
                window_title = getattr(event, "window_title", "") or ""
                if window_title:
                    # Extract app name from title (last part after ' - ')
                    app = window_title.rsplit(" - ", 1)[-1].strip()
                    if app:
                        import asyncio
                        try:
                            asyncio.ensure_future(
                                self.update_app_knowledge_cache(app),
                            )
                        except Exception:
                            pass
        except Exception as e:
            logger.debug("on_event error: %s", e)

    @staticmethod
    def _summarize_event(event) -> Optional[str]:
        """Build a short human-readable summary for an event."""
        et = event.event_type
        if et == "goal.started":
            return "Goal started: %s" % (getattr(event, "goal_text", "") or "")[:80]
        if et == "goal.completed":
            steps = getattr(event, "steps_executed", 0)
            return "Goal completed (%d steps)" % steps
        if et == "goal.failed":
            err = (getattr(event, "error", "") or "")[:60]
            return "Goal failed: %s" % err if err else "Goal failed"
        if et == "world.window_changed":
            ct = getattr(event, "change_type", "")
            title = (getattr(event, "window_title", "") or "")[:40]
            return "Window %s: %s" % (ct, title) if title else "Window %s" % ct
        if et == "world.focus_lost":
            expected = getattr(event, "expected_app", "")
            actual = getattr(event, "actual_app", "")
            return "Focus lost: expected %s, got %s" % (expected, actual)
        if et == "world.dialog_detected":
            title = (getattr(event, "dialog_title", "") or "")[:40]
            return "Dialog: %s" % title if title else "Dialog detected"
        if et == "world.dialog_handled":
            action = getattr(event, "action_taken", "")
            return "Dialog handled: %s" % action if action else "Dialog handled"
        return None

    def _recent_events_context(self) -> Optional[str]:
        """Format recent events for LLM context."""
        if not self._recent_events:
            return None
        lines = []
        for e in self._recent_events[-5:]:
            lines.append("  - [%s] %s" % (e["time"], e["summary"]))
        return "Recent events:\n%s" % "\n".join(lines)
