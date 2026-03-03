"""Tests for marlow.kernel.planning (parser, template_planner, tool_filter, prompts)."""

import json

import pytest

from marlow.kernel.planning.parser import PlanParser
from marlow.kernel.planning.prompts import (
    CLARIFY_USER,
    PLAN_SYSTEM,
    PLAN_USER,
    REPLAN_USER,
)
from marlow.kernel.planning.template_planner import TemplatePlanner
from marlow.kernel.planning.tool_filter import (
    CDP_TOOLS,
    CORE_TOOLS,
    DIALOG_TOOLS,
    OCR_TOOLS,
    WINDOW_TOOLS,
    ToolFilter,
)


# ── Helpers ──


def _valid_json_plan(steps=None):
    """Build a valid JSON plan string."""
    if steps is None:
        steps = [
            {
                "id": "step_1",
                "tool_name": "click",
                "params": {"x": 100, "y": 200},
                "description": "Click on button",
                "risk": "low",
                "estimated_duration_ms": 2000,
            },
            {
                "id": "step_2",
                "tool_name": "type_text",
                "params": {"text": "hello"},
                "description": "Type hello",
                "risk": "medium",
                "estimated_duration_ms": 3000,
            },
        ]
    return json.dumps({
        "steps": steps,
        "context": {"target_app": "notepad.exe"},
    })


# ── TestPlanParser ──


class TestPlanParser:
    """PlanParser JSON extraction and normalization."""

    def test_parse_valid_json(self):
        """Well-formed JSON -> Plan with correct steps."""
        parser = PlanParser()
        plan, errors = parser.parse(_valid_json_plan(), "Test goal")

        assert plan is not None
        assert len(errors) == 0
        assert len(plan.steps) == 2
        assert plan.steps[0].tool_name == "click"
        assert plan.steps[1].tool_name == "type_text"
        assert plan.steps[1].params == {"text": "hello"}
        assert plan.goal_text == "Test goal"
        assert plan.context == {"target_app": "notepad.exe"}

    def test_parse_json_in_markdown(self):
        """```json ... ``` fenced -> extracted and parsed."""
        parser = PlanParser()
        raw = "Here's the plan:\n```json\n" + _valid_json_plan() + "\n```\nDone."
        plan, errors = parser.parse(raw, "Markdown test")

        assert plan is not None
        assert len(plan.steps) == 2

    def test_parse_with_preamble(self):
        """'Here's the plan: {...}' -> JSON extracted."""
        parser = PlanParser()
        raw = "Sure! Here's my plan:\n\n" + _valid_json_plan()
        plan, errors = parser.parse(raw, "Preamble test")

        assert plan is not None
        assert len(plan.steps) == 2

    def test_parse_trailing_comma(self):
        """Trailing comma fixed -> valid parse."""
        parser = PlanParser()
        raw = '{"steps": [{"id": "s1", "tool_name": "click", "params": {},}]}'
        plan, errors = parser.parse(raw, "Trailing comma")

        assert plan is not None
        assert len(plan.steps) == 1

    def test_parse_missing_tool(self):
        """Step without tool_name -> error reported, step skipped."""
        parser = PlanParser()
        raw = json.dumps({
            "steps": [
                {"id": "s1", "description": "No tool here"},
                {"id": "s2", "tool_name": "click", "params": {}},
            ],
        })
        plan, errors = parser.parse(raw)

        assert plan is not None
        assert len(plan.steps) == 1  # Only the valid step
        assert len(errors) == 1
        assert "missing tool_name" in errors[0]

    def test_parse_no_json(self):
        """Plain text -> error."""
        parser = PlanParser()
        plan, errors = parser.parse("Just open notepad please")

        assert plan is None
        assert len(errors) > 0
        assert "No valid JSON" in errors[0]

    def test_parse_alternative_keys(self):
        """'tool' instead of 'tool_name', 'parameters' instead of 'params'."""
        parser = PlanParser()
        raw = json.dumps({
            "steps": [
                {
                    "id": "s1",
                    "tool": "click",
                    "parameters": {"x": 50, "y": 100},
                    "description": "Click",
                },
            ],
        })
        plan, errors = parser.parse(raw)

        assert plan is not None
        assert plan.steps[0].tool_name == "click"
        assert plan.steps[0].params == {"x": 50, "y": 100}

    def test_parse_string_success_check(self):
        """success_check as string -> normalized to dict."""
        parser = PlanParser()
        raw = json.dumps({
            "steps": [
                {
                    "id": "s1",
                    "tool_name": "open_application",
                    "params": {"name": "Notepad"},
                    "success_check": "window_exists",
                },
            ],
        })
        plan, errors = parser.parse(raw)

        assert plan is not None
        check = plan.steps[0].success_check
        assert isinstance(check, dict)
        assert check["type"] == "window_exists"
        assert check["params"] == {}


# ── TestTemplatePlanner ──


class TestTemplatePlanner:
    """TemplatePlanner pattern matching."""

    def setup_method(self):
        self.planner = TemplatePlanner()

    def test_open_app(self):
        """'open Notepad' -> Plan with open_application step."""
        plan = self.planner.match("open Notepad")
        assert plan is not None
        assert len(plan.steps) == 1
        assert plan.steps[0].tool_name == "open_application"
        assert plan.steps[0].params["name"] == "Notepad"

    def test_type_text(self):
        """\"type 'hello'\" -> Plan with type_text step."""
        plan = self.planner.match("type 'hello'")
        assert plan is not None
        assert any(s.tool_name == "type_text" for s in plan.steps)
        text_step = next(s for s in plan.steps if s.tool_name == "type_text")
        assert text_step.params["text"] == "hello"

    def test_click(self):
        """'click on Save' -> Plan with click step."""
        plan = self.planner.match("click on Save")
        assert plan is not None
        assert plan.steps[0].tool_name == "click"
        assert plan.steps[0].params["target"] == "Save"

    def test_screenshot(self):
        """'take a screenshot' -> Plan with take_screenshot."""
        plan = self.planner.match("take a screenshot")
        assert plan is not None
        assert plan.steps[0].tool_name == "take_screenshot"

    def test_close_app(self):
        """'close Chrome' -> Plan with manage_window close."""
        plan = self.planner.match("close Chrome")
        assert plan is not None
        assert plan.steps[0].tool_name == "manage_window"
        assert plan.steps[0].params["action"] == "close"

    def test_save(self):
        """'save the file' -> Plan with hotkey Ctrl+S."""
        plan = self.planner.match("save the file")
        assert plan is not None
        assert plan.steps[0].tool_name == "hotkey"
        assert plan.steps[0].params["keys"] == "ctrl+s"

    def test_open_and_type(self):
        """\"open Notepad and type 'hello'\" -> Plan with 3 steps."""
        plan = self.planner.match("open Notepad and type 'hello'")
        assert plan is not None
        assert len(plan.steps) == 3
        assert plan.steps[0].tool_name == "open_application"
        assert plan.steps[1].tool_name == "wait_for_idle"
        assert plan.steps[2].tool_name == "type_text"
        assert plan.steps[2].params["text"] == "hello"

    def test_no_match(self):
        """'analyze quarterly financials' -> None (needs LLM)."""
        plan = self.planner.match("analyze quarterly financials")
        assert plan is None

    def test_case_insensitive(self):
        """'OPEN notepad' -> matches."""
        plan = self.planner.match("OPEN notepad")
        assert plan is not None
        assert plan.steps[0].tool_name == "open_application"


# ── TestToolFilter ──


class TestToolFilter:
    """ToolFilter relevance filtering."""

    def test_core_always_included(self):
        """Any goal -> core tools present."""
        tf = ToolFilter()
        tools = tf.filter_for_goal("do something random")
        for t in CORE_TOOLS:
            assert t in tools

    def test_keyword_adds_tools(self):
        """'search for text' -> OCR tools added."""
        tf = ToolFilter()
        tools = tf.filter_for_goal("search for text on screen")
        for t in OCR_TOOLS:
            assert t in tools

    def test_cdp_for_electron(self):
        """Electron framework -> CDP tools added."""
        tf = ToolFilter()
        tools = tf.filter_for_goal("click a button", app_framework="electron")
        for t in CDP_TOOLS:
            assert t in tools

    def test_dialog_always_included(self):
        """Dialog tools always present."""
        tf = ToolFilter()
        tools = tf.filter_for_goal("anything")
        for t in DIALOG_TOOLS:
            assert t in tools

    def test_format_for_prompt(self):
        """Formats as '- tool_name' list."""
        tf = ToolFilter()
        formatted = tf.format_for_prompt(["click", "type_text"])
        assert "- click" in formatted
        assert "- type_text" in formatted

    def test_filter_to_existing(self):
        """Only returns tools in all_tools set."""
        tf = ToolFilter(all_tools=["click", "type_text", "hotkey"])
        tools = tf.filter_for_goal("open browser and search")
        # Should not include tools outside all_tools
        for t in tools:
            assert t in ("click", "type_text", "hotkey")


# ── TestPromptTemplates ──


class TestPromptTemplates:
    """Verify prompt templates have expected placeholders."""

    def test_plan_system_has_placeholders(self):
        """{available_tools} and {app_knowledge} present."""
        assert "{available_tools}" in PLAN_SYSTEM
        assert "{app_knowledge}" in PLAN_SYSTEM

    def test_plan_user_has_placeholders(self):
        """{goal_text}, {active_window}, etc. present."""
        assert "{goal_text}" in PLAN_USER
        assert "{active_window}" in PLAN_USER
        assert "{open_windows}" in PLAN_USER
        assert "{screen_width}" in PLAN_USER
        assert "{screen_height}" in PLAN_USER
        assert "{additional_context}" in PLAN_USER

    def test_replan_has_placeholders(self):
        """{completed_steps}, {failed_step}, {error_message} present."""
        assert "{goal_text}" in REPLAN_USER
        assert "{completed_steps}" in REPLAN_USER
        assert "{failed_step}" in REPLAN_USER
        assert "{error_message}" in REPLAN_USER
        assert "{active_window}" in REPLAN_USER

    def test_clarify_has_placeholders(self):
        """{goal_text} and {ambiguity_reason} present."""
        assert "{goal_text}" in CLARIFY_USER
        assert "{ambiguity_reason}" in CLARIFY_USER
