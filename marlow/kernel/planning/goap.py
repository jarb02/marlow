"""GOAP — Goal-Oriented Action Planning with A* search.

Defines preconditions/effects for core Marlow tools and finds the
cheapest action sequence to reach a goal state. Free, instant (<1ms),
no API cost.

Tier: TemplatePlanner (trivial) -> GOAP (medium) -> LLM (complex)

Based on F.E.A.R. (2005) GOAP implementation by Jeff Orkin.

/ Planificacion orientada a metas con busqueda A*.
"""

import logging
import heapq
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("marlow.kernel.planning.goap")


@dataclass(frozen=True)
class GOAPAction:
    """A plannable action with preconditions and effects."""
    tool_name: str
    description: str
    preconditions: frozenset[tuple[str, bool]]  # {("app_running", True), ...}
    effects: frozenset[tuple[str, bool]]         # {("text_typed", True)}
    cost: float = 1.0                            # lower = preferred
    params_template: dict = field(default_factory=dict)  # default params


class GOAPWorldState:
    """Mutable world state as key-value pairs."""

    def __init__(self, state: Optional[dict[str, bool]] = None):
        self._state: dict[str, bool] = dict(state) if state else {}

    def get(self, key: str, default: bool = False) -> bool:
        return self._state.get(key, default)

    def set(self, key: str, value: bool):
        self._state[key] = value

    def satisfies(self, conditions: frozenset[tuple[str, bool]]) -> bool:
        """Check if all conditions are met."""
        return all(self._state.get(k, False) == v for k, v in conditions)

    def unsatisfied_count(self, conditions: frozenset[tuple[str, bool]]) -> int:
        """Count how many conditions are NOT met (heuristic for A*)."""
        return sum(1 for k, v in conditions if self._state.get(k, False) != v)

    def apply(self, effects: frozenset[tuple[str, bool]]) -> "GOAPWorldState":
        """Return new state with effects applied."""
        new_state = dict(self._state)
        for k, v in effects:
            new_state[k] = v
        return GOAPWorldState(new_state)

    def copy(self) -> "GOAPWorldState":
        return GOAPWorldState(dict(self._state))

    def as_dict(self) -> dict[str, bool]:
        return dict(self._state)

    def __eq__(self, other):
        if isinstance(other, GOAPWorldState):
            return self._state == other._state
        return False

    def __hash__(self):
        return hash(frozenset(self._state.items()))


@dataclass
class _AStarNode:
    """Internal node for A* search."""
    state: GOAPWorldState
    actions: list[GOAPAction]  # actions taken to reach this state
    g_cost: float              # actual cost so far
    h_cost: float              # heuristic (unsatisfied goals)

    @property
    def f_cost(self) -> float:
        return self.g_cost + self.h_cost

    def __lt__(self, other):
        return self.f_cost < other.f_cost


# === Define GOAP actions for core Marlow tools ===

GOAP_ACTIONS: list[GOAPAction] = [
    # Opening apps
    GOAPAction(
        tool_name="open_application",
        description="Open an application",
        preconditions=frozenset(),
        effects=frozenset([("app_running", True), ("app_window_visible", True)]),
        cost=2.0,
    ),

    # Focus
    GOAPAction(
        tool_name="focus_window",
        description="Focus a window",
        preconditions=frozenset([("app_window_visible", True)]),
        effects=frozenset([("has_focus", True)]),
        cost=0.5,
    ),

    # Typing
    GOAPAction(
        tool_name="type_text",
        description="Type text into focused element",
        preconditions=frozenset([("has_focus", True)]),
        effects=frozenset([("text_typed", True)]),
        cost=1.0,
    ),

    # Click
    GOAPAction(
        tool_name="click",
        description="Click an element",
        preconditions=frozenset([("has_focus", True)]),
        effects=frozenset([("element_clicked", True)]),
        cost=1.0,
    ),

    # Press key
    GOAPAction(
        tool_name="press_key",
        description="Press a keyboard key",
        preconditions=frozenset([("has_focus", True)]),
        effects=frozenset([("key_pressed", True)]),
        cost=0.5,
    ),

    # Hotkey
    GOAPAction(
        tool_name="hotkey",
        description="Press a key combination",
        preconditions=frozenset([("has_focus", True)]),
        effects=frozenset([("hotkey_executed", True)]),
        cost=0.5,
    ),

    # Save (Ctrl+S pattern)
    GOAPAction(
        tool_name="hotkey",
        description="Save file with Ctrl+S",
        preconditions=frozenset([("has_focus", True), ("text_typed", True)]),
        effects=frozenset([("file_saved", True), ("save_dialog_open", True)]),
        cost=1.0,
        params_template={"keys": "ctrl+s"},
    ),

    # Handle save dialog
    GOAPAction(
        tool_name="type_text",
        description="Type filename in save dialog",
        preconditions=frozenset([("save_dialog_open", True)]),
        effects=frozenset([("filename_typed", True)]),
        cost=1.0,
    ),

    # Confirm save
    GOAPAction(
        tool_name="press_key",
        description="Press Enter to confirm",
        preconditions=frozenset([("filename_typed", True)]),
        effects=frozenset([("save_confirmed", True)]),
        cost=0.5,
        params_template={"key": "enter"},
    ),

    # Take screenshot
    GOAPAction(
        tool_name="take_screenshot",
        description="Take a screenshot",
        preconditions=frozenset(),
        effects=frozenset([("screenshot_taken", True)]),
        cost=0.5,
    ),

    # List windows
    GOAPAction(
        tool_name="list_windows",
        description="List all open windows",
        preconditions=frozenset(),
        effects=frozenset([("windows_listed", True)]),
        cost=0.3,
    ),

    # Wait for idle
    GOAPAction(
        tool_name="wait_for_idle",
        description="Wait for UI to become idle",
        preconditions=frozenset(),
        effects=frozenset([("ui_idle", True)]),
        cost=1.5,
    ),

    # Handle dialog
    GOAPAction(
        tool_name="handle_dialog",
        description="Handle a dialog",
        preconditions=frozenset([("dialog_present", True)]),
        effects=frozenset([("dialog_present", False), ("dialog_handled", True)]),
        cost=1.0,
    ),

    # Close app
    GOAPAction(
        tool_name="manage_window",
        description="Close application window",
        preconditions=frozenset([("app_window_visible", True)]),
        effects=frozenset([("app_running", False), ("app_window_visible", False)]),
        cost=1.0,
        params_template={"action": "close"},
    ),

    # OCR
    GOAPAction(
        tool_name="ocr_region",
        description="Read text from screen region",
        preconditions=frozenset([("app_window_visible", True)]),
        effects=frozenset([("text_read", True)]),
        cost=1.5,
    ),

    # Smart find
    GOAPAction(
        tool_name="smart_find",
        description="Find UI element by name",
        preconditions=frozenset([("app_window_visible", True)]),
        effects=frozenset([("element_found", True)]),
        cost=2.0,
    ),
]


class GOAPPlanner:
    """GOAP planner using A* search.

    Finds cheapest sequence of actions to transform current world state
    into goal state. Free, instant (<1ms), no API cost.

    Based on F.E.A.R. (2005) GOAP implementation by Jeff Orkin.
    """

    MAX_SEARCH_DEPTH = 15
    MAX_NODES_EXPLORED = 500

    def __init__(self, actions: Optional[list[GOAPAction]] = None):
        self._actions = actions or list(GOAP_ACTIONS)

    def add_action(self, action: GOAPAction):
        """Register a new action."""
        self._actions.append(action)

    def plan(
        self,
        current_state: GOAPWorldState,
        goal_conditions: frozenset[tuple[str, bool]],
    ) -> Optional[list[GOAPAction]]:
        """Find cheapest action sequence from current state to goal.

        Returns list of GOAPAction in execution order, or None if no plan found.
        Uses A* search with heuristic = number of unsatisfied goal conditions.
        """
        # Already satisfied?
        if current_state.satisfies(goal_conditions):
            return []

        start_node = _AStarNode(
            state=current_state.copy(),
            actions=[],
            g_cost=0.0,
            h_cost=current_state.unsatisfied_count(goal_conditions),
        )

        open_set: list[_AStarNode] = [start_node]
        visited: set[int] = set()  # hashes of visited states
        nodes_explored = 0

        while open_set and nodes_explored < self.MAX_NODES_EXPLORED:
            current = heapq.heappop(open_set)
            nodes_explored += 1

            # Goal reached?
            if current.state.satisfies(goal_conditions):
                logger.info(
                    "GOAP plan found: %d actions, cost=%.1f, explored=%d",
                    len(current.actions), current.g_cost, nodes_explored,
                )
                return current.actions

            # Skip visited states
            state_hash = hash(current.state)
            if state_hash in visited:
                continue
            visited.add(state_hash)

            # Depth limit
            if len(current.actions) >= self.MAX_SEARCH_DEPTH:
                continue

            # Expand: try all applicable actions
            for action in self._actions:
                if current.state.satisfies(action.preconditions):
                    new_state = current.state.apply(action.effects)
                    new_g = current.g_cost + action.cost
                    new_h = new_state.unsatisfied_count(goal_conditions)

                    new_node = _AStarNode(
                        state=new_state,
                        actions=current.actions + [action],
                        g_cost=new_g,
                        h_cost=new_h,
                    )
                    heapq.heappush(open_set, new_node)

        logger.warning("GOAP: no plan found after exploring %d nodes", nodes_explored)
        return None

    def plan_from_goal_text(self, goal_text: str) -> Optional[list[GOAPAction]]:
        """Try to create a GOAP plan from natural language goal.

        Maps common goal patterns to world state + goal conditions.
        Returns None if goal is too complex for GOAP.
        """
        text = goal_text.lower().strip()

        # Pattern: "open X and type Y"
        if "open" in text and "type" in text:
            current = GOAPWorldState()
            goal = frozenset([("app_running", True), ("text_typed", True)])
            return self.plan(current, goal)

        # Pattern: "open X, type Y, save as Z"
        if "open" in text and "save" in text:
            current = GOAPWorldState()
            goal = frozenset([("app_running", True), ("text_typed", True), ("save_confirmed", True)])
            return self.plan(current, goal)

        # Pattern: "take screenshot"
        if "screenshot" in text:
            current = GOAPWorldState()
            goal = frozenset([("screenshot_taken", True)])
            return self.plan(current, goal)

        # Pattern: "list windows"
        if "list" in text and "window" in text:
            current = GOAPWorldState()
            goal = frozenset([("windows_listed", True)])
            return self.plan(current, goal)

        # Pattern: "open X"
        if "open" in text or "launch" in text or "start" in text:
            current = GOAPWorldState()
            goal = frozenset([("app_running", True), ("has_focus", True)])
            return self.plan(current, goal)

        # Pattern: "close X"
        if "close" in text or "exit" in text:
            current = GOAPWorldState({"app_running": True, "app_window_visible": True})
            goal = frozenset([("app_running", False)])
            return self.plan(current, goal)

        # Too complex for GOAP
        return None

    @property
    def action_count(self) -> int:
        return len(self._actions)
