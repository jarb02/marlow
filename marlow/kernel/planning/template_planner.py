"""Pattern-matching planner that handles common goals WITHOUT LLM.

Falls back to None when it can't match a known pattern.
This saves LLM calls for simple, well-known tasks.
"""

from __future__ import annotations

import re
from typing import Optional

from ..goal_engine import Plan, PlanStep

# Known patterns: (regex, method_name)
PATTERNS: list[tuple[str, str]] = [
    # Open app and type
    (
        r"(?i)^open\s+(.+?)\s+and\s+(?:type|write|enter)\s+"
        r"['\"](.+)['\"]$",
        "_plan_open_and_type",
    ),
    # Open an application
    (r"(?i)^(?:open|launch|start|run)\s+(.+)$", "_plan_open_app"),
    # Type text (optionally in specific app)
    (
        r"(?i)^(?:type|write|enter|input)\s+['\"](.+)['\"]\s*"
        r"(?:in\s+(.+))?$",
        "_plan_type_text",
    ),
    # Click on something
    (
        r"(?i)^(?:click|press|hit)\s+(?:on\s+)?(?:the\s+)?"
        r"['\"]?(.+?)['\"]?\s*(?:button|link|tab)?$",
        "_plan_click",
    ),
    # Take a screenshot
    (
        r"(?i)^(?:take|capture|grab)\s+(?:a\s+)?screenshot",
        "_plan_screenshot",
    ),
    # Close an application
    (r"(?i)^(?:close|exit|quit)\s+(.+)$", "_plan_close_app"),
    # Save file
    (r"(?i)^save\s+(?:the\s+)?(?:file|document)", "_plan_save"),

    # --- Spanish patterns ---
    # Abrir / abre aplicación
    (r"(?i)^(?:abre|abrir|inicia|lanza|ejecuta)\s+(.+)$", "_plan_open_app"),
    # Cerrar / cierra aplicación
    (r"(?i)^(?:cierra|cerrar|salir\s+de)\s+(.+)$", "_plan_close_app"),
    # Captura de pantalla
    (r"(?i)^(?:toma|captura|saca)\s+(?:una?\s+)?(?:captura|screenshot|foto)", "_plan_screenshot"),
    # Guardar archivo
    (r"(?i)^(?:guarda|guardar)\s+(?:el\s+)?(?:archivo|documento)", "_plan_save"),

    # --- Search patterns (EN + ES) ---
    # "search for X", "busca X", "look up X", "find X on the web"
    (
        r"(?i)^(?:search\s+(?:for\s+)?|busca(?:r)?\s+|look\s+up\s+|"
        r"find\s+|buscar\s+en\s+(?:google|internet)\s+|"
        r"muéstrame\s+(?:el\s+)?|show\s+me\s+(?:the\s+)?)"
        r"(.+)$",
        "_plan_search",
    ),
]


class TemplatePlanner:
    """Matches common goal patterns to pre-built plans.

    No LLM needed. Fast and deterministic.
    """

    def match(
        self, goal_text: str, context: dict = None,
    ) -> Optional[Plan]:
        """Try to match goal_text to a known pattern.

        Returns Plan if matched, None if the goal needs LLM planning.
        """
        for pattern, method_name in PATTERNS:
            m = re.match(pattern, goal_text.strip())
            if m:
                method = getattr(self, method_name, None)
                if method:
                    result = method(m, context or {})
                    if result is not None:
                        return result
        return None

    def _plan_open_app(self, match, context) -> Optional[Plan]:
        app = match.group(1).strip()
        # Too many words → not a simple app name, defer to LLM
        if len(app.split()) > 4:
            return None
        return Plan(
            goal_id="",
            goal_text=match.string,
            steps=[
                PlanStep(
                    id="step_1",
                    tool_name="open_application",
                    params={"name": app},
                    description=f"Open {app}",
                    risk="medium",
                    estimated_duration_ms=5000,
                    success_check={
                        "type": "window_exists",
                        "params": {"title_contains": app},
                    },
                ),
            ],
            context={"target_app": app},
        )

    def _plan_type_text(self, match, context) -> Plan:
        text = match.group(1)
        app = match.group(2) if match.lastindex >= 2 else ""
        steps: list[PlanStep] = []

        if app:
            steps.append(PlanStep(
                id="step_1",
                tool_name="focus_window",
                params={"title": app},
                description=f"Focus {app}",
                risk="low",
                estimated_duration_ms=1000,
            ))

        steps.append(PlanStep(
            id=f"step_{len(steps) + 1}",
            tool_name="type_text",
            params={"text": text},
            description=f"Type: {text[:50]}",
            risk="medium",
            estimated_duration_ms=2000 + len(text) * 50,
        ))

        return Plan(
            goal_id="",
            goal_text=match.string,
            steps=steps,
            context={"target_app": app},
        )

    def _plan_click(self, match, context) -> Plan:
        target = match.group(1).strip()
        return Plan(
            goal_id="",
            goal_text=match.string,
            steps=[
                PlanStep(
                    id="step_1",
                    tool_name="click",
                    params={"target": target},
                    description=f"Click on '{target}'",
                    risk="medium",
                    estimated_duration_ms=2000,
                ),
            ],
        )

    def _plan_screenshot(self, match, context) -> Plan:
        return Plan(
            goal_id="",
            goal_text=match.string,
            steps=[
                PlanStep(
                    id="step_1",
                    tool_name="take_screenshot",
                    params={},
                    description="Take a screenshot",
                    risk="low",
                    estimated_duration_ms=1000,
                ),
            ],
        )

    def _plan_close_app(self, match, context) -> Plan:
        app = match.group(1).strip()
        return Plan(
            goal_id="",
            goal_text=match.string,
            steps=[
                PlanStep(
                    id="step_1",
                    tool_name="manage_window",
                    params={"title": app, "action": "close"},
                    description=f"Close {app}",
                    risk="medium",
                    estimated_duration_ms=2000,
                ),
            ],
            context={"target_app": app},
        )

    def _plan_save(self, match, context) -> Plan:
        return Plan(
            goal_id="",
            goal_text=match.string,
            steps=[
                PlanStep(
                    id="step_1",
                    tool_name="hotkey",
                    params={"keys": "ctrl+s"},
                    description="Save file (Ctrl+S)",
                    risk="medium",
                    estimated_duration_ms=2000,
                    success_check={"type": "none"},
                ),
            ],
        )

    def _plan_search(self, match, context) -> Plan:
        """Plan a web search: launch firefox in shadow, move to user."""
        query = match.group(1).strip()
        # URL-encode query: replace spaces with +
        url_query = query.replace(" ", "+")
        search_url = f"https://www.google.com/search?q={url_query}"

        return Plan(
            goal_id="",
            goal_text=match.string,
            steps=[
                PlanStep(
                    id="step_1",
                    tool_name="launch_in_shadow",
                    params={"command": f"firefox {search_url}"},
                    description=f"Search for: {query}",
                    risk="low",
                    estimated_duration_ms=5000,
                    success_check={"type": "return_value", "params": {"key": "window_id"}},
                ),
                PlanStep(
                    id="step_2",
                    tool_name="move_to_user",
                    params={"window_id": "$window_id"},
                    description="Show search results to user",
                    risk="low",
                    estimated_duration_ms=1000,
                    success_check={"type": "return_value", "params": {"key": "success"}},
                ),
            ],
            context={"target_app": "firefox"},
        )

    def _plan_open_and_type(self, match, context) -> Plan:
        app = match.group(1).strip()
        text = match.group(2).strip()
        return Plan(
            goal_id="",
            goal_text=match.string,
            steps=[
                PlanStep(
                    id="step_1",
                    tool_name="open_application",
                    params={"name": app},
                    description=f"Open {app}",
                    risk="medium",
                    estimated_duration_ms=5000,
                    success_check={
                        "type": "window_exists",
                        "params": {"title_contains": app},
                    },
                ),
                PlanStep(
                    id="step_2",
                    tool_name="wait_for_idle",
                    params={"timeout_ms": 3000},
                    description=f"Wait for {app} to be ready",
                    risk="low",
                    estimated_duration_ms=3000,
                    skippable=True,
                ),
                PlanStep(
                    id="step_3",
                    tool_name="type_text",
                    params={"text": text},
                    description=f"Type: {text[:50]}",
                    risk="medium",
                    estimated_duration_ms=2000 + len(text) * 50,
                ),
            ],
            context={"target_app": app},
        )
