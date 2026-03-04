"""Tests for marlow.kernel.planning.goap — GOAP local planner."""

import pytest
from marlow.kernel.planning.goap import (
    GOAPAction,
    GOAPWorldState,
    GOAPPlanner,
    GOAP_ACTIONS,
)


# ------------------------------------------------------------------
# GOAPWorldState
# ------------------------------------------------------------------

class TestGOAPWorldState:
    def test_get_default(self):
        s = GOAPWorldState()
        assert s.get("nonexistent") is False
        assert s.get("x", True) is True

    def test_set_get(self):
        s = GOAPWorldState()
        s.set("app_running", True)
        assert s.get("app_running") is True

    def test_satisfies_true(self):
        s = GOAPWorldState({"app_running": True, "has_focus": True})
        conds = frozenset([("app_running", True), ("has_focus", True)])
        assert s.satisfies(conds) is True

    def test_satisfies_false(self):
        s = GOAPWorldState({"app_running": True})
        conds = frozenset([("app_running", True), ("has_focus", True)])
        assert s.satisfies(conds) is False

    def test_satisfies_empty(self):
        """Empty conditions are always satisfied."""
        s = GOAPWorldState()
        assert s.satisfies(frozenset()) is True

    def test_unsatisfied_count(self):
        s = GOAPWorldState({"app_running": True})
        conds = frozenset([("app_running", True), ("has_focus", True), ("text_typed", True)])
        assert s.unsatisfied_count(conds) == 2

    def test_apply(self):
        s = GOAPWorldState({"app_running": False})
        effects = frozenset([("app_running", True), ("has_focus", True)])
        new_s = s.apply(effects)
        assert new_s.get("app_running") is True
        assert new_s.get("has_focus") is True
        # Original unchanged
        assert s.get("app_running") is False

    def test_copy_is_independent(self):
        s = GOAPWorldState({"x": True})
        c = s.copy()
        c.set("x", False)
        assert s.get("x") is True
        assert c.get("x") is False

    def test_equality(self):
        a = GOAPWorldState({"x": True, "y": False})
        b = GOAPWorldState({"x": True, "y": False})
        c = GOAPWorldState({"x": True})
        assert a == b
        assert a != c

    def test_hash(self):
        a = GOAPWorldState({"x": True})
        b = GOAPWorldState({"x": True})
        assert hash(a) == hash(b)


# ------------------------------------------------------------------
# GOAPAction
# ------------------------------------------------------------------

class TestGOAPAction:
    def test_action_dataclass(self):
        a = GOAPAction(
            tool_name="click",
            description="Click element",
            preconditions=frozenset([("has_focus", True)]),
            effects=frozenset([("element_clicked", True)]),
            cost=1.0,
        )
        assert a.tool_name == "click"
        assert a.cost == 1.0
        assert ("has_focus", True) in a.preconditions
        assert ("element_clicked", True) in a.effects

    def test_action_frozen(self):
        a = GOAPAction(
            tool_name="click", description="",
            preconditions=frozenset(), effects=frozenset(),
        )
        with pytest.raises(AttributeError):
            a.cost = 99.0


# ------------------------------------------------------------------
# GOAPPlanner — core A* search
# ------------------------------------------------------------------

class TestGOAPPlanner:
    def setup_method(self):
        self.planner = GOAPPlanner()

    def test_already_satisfied(self):
        """If goal is already met, return empty plan."""
        state = GOAPWorldState({"screenshot_taken": True})
        goal = frozenset([("screenshot_taken", True)])
        plan = self.planner.plan(state, goal)
        assert plan == []

    def test_plan_open_app(self):
        """Open app: should find open_application + focus_window."""
        state = GOAPWorldState()
        goal = frozenset([("app_running", True), ("has_focus", True)])
        plan = self.planner.plan(state, goal)
        assert plan is not None
        assert len(plan) >= 2
        tool_names = [a.tool_name for a in plan]
        assert "open_application" in tool_names
        assert "focus_window" in tool_names

    def test_plan_type_text(self):
        """Given focus, should find type_text directly."""
        state = GOAPWorldState({"has_focus": True})
        goal = frozenset([("text_typed", True)])
        plan = self.planner.plan(state, goal)
        assert plan is not None
        assert len(plan) == 1
        assert plan[0].tool_name == "type_text"

    def test_plan_screenshot(self):
        """Screenshot has no preconditions — 1 action."""
        state = GOAPWorldState()
        goal = frozenset([("screenshot_taken", True)])
        plan = self.planner.plan(state, goal)
        assert plan is not None
        assert len(plan) == 1
        assert plan[0].tool_name == "take_screenshot"

    def test_plan_list_windows(self):
        """list_windows has no preconditions — 1 action."""
        state = GOAPWorldState()
        goal = frozenset([("windows_listed", True)])
        plan = self.planner.plan(state, goal)
        assert plan is not None
        assert len(plan) == 1
        assert plan[0].tool_name == "list_windows"

    def test_plan_save_flow(self):
        """Full save flow: open + type + hotkey(save) + type(filename) + enter."""
        state = GOAPWorldState()
        goal = frozenset([("app_running", True), ("text_typed", True), ("save_confirmed", True)])
        plan = self.planner.plan(state, goal)
        assert plan is not None
        assert len(plan) >= 5
        tool_names = [a.tool_name for a in plan]
        assert "open_application" in tool_names
        assert "press_key" in tool_names  # confirm enter

    def test_plan_close_app(self):
        """Close app with manage_window."""
        state = GOAPWorldState({"app_running": True, "app_window_visible": True})
        goal = frozenset([("app_running", False)])
        plan = self.planner.plan(state, goal)
        assert plan is not None
        tool_names = [a.tool_name for a in plan]
        assert "manage_window" in tool_names

    def test_plan_impossible_goal(self):
        """Goal that can't be reached returns None."""
        state = GOAPWorldState()
        goal = frozenset([("impossible_state_xyz", True)])
        plan = self.planner.plan(state, goal)
        assert plan is None

    def test_plan_respects_preconditions(self):
        """Can't type without focus — must get focus first."""
        state = GOAPWorldState({"app_window_visible": True})
        goal = frozenset([("text_typed", True)])
        plan = self.planner.plan(state, goal)
        assert plan is not None
        tool_names = [a.tool_name for a in plan]
        # Must focus before typing
        assert "focus_window" in tool_names
        focus_idx = tool_names.index("focus_window")
        type_idx = tool_names.index("type_text")
        assert focus_idx < type_idx

    def test_plan_cost_ordering(self):
        """Cheaper actions should be preferred by A*."""
        # Create two actions with same effect but different cost
        cheap = GOAPAction(
            tool_name="cheap_tool", description="Cheap",
            preconditions=frozenset(), effects=frozenset([("result", True)]),
            cost=0.1,
        )
        expensive = GOAPAction(
            tool_name="expensive_tool", description="Expensive",
            preconditions=frozenset(), effects=frozenset([("result", True)]),
            cost=10.0,
        )
        planner = GOAPPlanner(actions=[cheap, expensive])
        state = GOAPWorldState()
        goal = frozenset([("result", True)])
        plan = planner.plan(state, goal)
        assert plan is not None
        assert plan[0].tool_name == "cheap_tool"

    def test_planner_action_count(self):
        assert self.planner.action_count == len(GOAP_ACTIONS)

    def test_add_action(self):
        initial = self.planner.action_count
        self.planner.add_action(GOAPAction(
            tool_name="custom", description="Custom",
            preconditions=frozenset(), effects=frozenset([("custom", True)]),
        ))
        assert self.planner.action_count == initial + 1


# ------------------------------------------------------------------
# GOAPPlanner — plan_from_goal_text
# ------------------------------------------------------------------

class TestGOAPPlanFromGoalText:
    def setup_method(self):
        self.planner = GOAPPlanner()

    def test_plan_from_goal_text_open(self):
        plan = self.planner.plan_from_goal_text("open Notepad")
        assert plan is not None
        tool_names = [a.tool_name for a in plan]
        assert "open_application" in tool_names

    def test_plan_from_goal_text_open_and_type(self):
        plan = self.planner.plan_from_goal_text("open Notepad and type 'hello'")
        assert plan is not None
        tool_names = [a.tool_name for a in plan]
        assert "open_application" in tool_names
        assert "type_text" in tool_names

    def test_plan_from_goal_text_screenshot(self):
        plan = self.planner.plan_from_goal_text("take a screenshot")
        assert plan is not None
        assert len(plan) == 1
        assert plan[0].tool_name == "take_screenshot"

    def test_plan_from_goal_text_close(self):
        plan = self.planner.plan_from_goal_text("close Notepad")
        assert plan is not None
        tool_names = [a.tool_name for a in plan]
        assert "manage_window" in tool_names

    def test_plan_from_goal_text_complex_returns_none(self):
        """Unrecognized goal returns None."""
        plan = self.planner.plan_from_goal_text("refactor the authentication module")
        assert plan is None

    def test_plan_from_goal_text_list_windows(self):
        plan = self.planner.plan_from_goal_text("list all windows")
        assert plan is not None
        assert plan[0].tool_name == "list_windows"
