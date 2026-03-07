"""GoalEngine -- manages the lifecycle of a goal from intent to completion.

States (13)::

    IDLE -> PARSING_INTENT -> GENERATING_PLAN -> VALIDATING_PLAN ->
    AWAITING_CONFIRMATION -> EXECUTING -> VERIFYING_STEP -> COMPLETED
                                    |                |
                            HANDLING_FAILURE -> REPLANNING -> VALIDATING_PLAN
                            AWAITING_CLARIFICATION -> GENERATING_PLAN
                            CANCELLED / FAILED

The GoalEngine sits ABOVE the MarlowKernel -- it generates plans
and feeds them to the Kernel for execution. The Kernel handles
the observe->act->score loop; the GoalEngine handles plan->validate->replan.
"""

from __future__ import annotations

import enum
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .plan_validator import PlanValidator, ValidationResult
from .replan import COMMON_FAILURE_HANDLERS, ReplanDecision
from .success_checker import SuccessChecker

logger = logging.getLogger("marlow.goal_engine")


class GoalState(enum.Enum):
    """Possible states for the GoalEngine."""

    IDLE = "idle"
    PARSING_INTENT = "parsing_intent"
    AWAITING_CLARIFICATION = "awaiting_clarification"
    GENERATING_PLAN = "generating_plan"
    VALIDATING_PLAN = "validating_plan"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    EXECUTING = "executing"
    VERIFYING_STEP = "verifying_step"
    HANDLING_FAILURE = "handling_failure"
    REPLANNING = "replanning"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class PlanStep:
    """A step in an execution plan."""

    id: str
    tool_name: str
    params: dict = field(default_factory=dict)
    description: str = ""
    expected_app: str = ""
    risk: str = "low"  # low|medium|high|critical
    requires_confirmation: bool = False
    success_check: Optional[dict] = None
    estimated_duration_ms: float = 3000.0
    skippable: bool = False
    alternative: Optional[dict] = None  # Alternative approach if this fails
    retries: int = 0
    max_retries: int = 2
    status: str = "pending"  # pending|running|completed|failed|skipped


@dataclass
class Plan:
    """Complete execution plan for a goal."""

    goal_id: str
    goal_text: str
    steps: list[PlanStep] = field(default_factory=list)
    context: dict = field(default_factory=dict)
    estimated_total_ms: float = 0.0
    requires_confirmation: bool = False
    metadata: dict = field(default_factory=dict)


@dataclass
class GoalResult:
    """Final result of a goal execution."""

    goal_id: str
    goal_text: str
    success: bool
    partial: bool = False
    steps_completed: int = 0
    steps_total: int = 0
    avg_score: float = 0.0
    errors: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    replan_count: int = 0
    result_summary: str = ""


class GoalEngine:
    """Manages goal lifecycle through 13 states.

    Usage::

        engine = GoalEngine(
            plan_generator=my_llm_planner,
            tool_executor=my_executor,
            success_checker=SuccessChecker(tools),
        )
        result = await engine.execute_goal("Open Notepad and type hello")

    Parameters
    ----------
    * **plan_generator** (Callable or None):
        ``async (goal_text, context) -> Plan``.
    * **tool_executor** (Callable or None):
        ``async (tool_name, params) -> ToolResult``.
    * **success_checker** (SuccessChecker or None):
        Verifies step outcomes.
    * **plan_validator** (PlanValidator or None):
        Validates plan structure and safety.
    * **confirmation_handler** (Callable or None):
        ``async (plan) -> bool``.
    * **clarification_handler** (Callable or None):
        ``async (question) -> str``.
    * **progress_callback** (Callable or None):
        ``async (step_num, total, description) -> None``.
    * **available_tools** (list of str):
        Known tool names for validation.
    """

    # Plan limits (from Research #3)
    MAX_STEPS = 20
    MAX_DURATION_MS = 300_000  # 5 minutes
    MAX_RETRIES_PER_STEP = 3
    MAX_REPLANS = 3
    MAX_LLM_CALLS = 10

    def __init__(
        self,
        plan_generator: Callable = None,
        tool_executor: Callable = None,
        success_checker: SuccessChecker = None,
        plan_validator: PlanValidator = None,
        confirmation_handler: Callable = None,
        clarification_handler: Callable = None,
        progress_callback: Callable = None,
        available_tools: list[str] = None,
    ):
        self._plan_generator = plan_generator
        self._tool_executor = tool_executor
        self._checker = success_checker or SuccessChecker()
        self._validator = plan_validator or PlanValidator(available_tools or [])
        self._confirmation_handler = confirmation_handler
        self._clarification_handler = clarification_handler
        self._progress_callback = progress_callback

        # State
        self._state = GoalState.IDLE
        self._plan: Optional[Plan] = None
        self._current_step_index = 0
        self._replan_count = 0
        self._llm_calls = 0
        self._scores: list[float] = []
        self._errors: list[str] = []
        self._started_at: float = 0.0
        self._state_history: list[GoalState] = []

    # ── Main Entry Point ──

    async def execute_goal(
        self,
        goal_text: str,
        context: dict = None,
        pre_built_plan: Plan = None,
    ) -> GoalResult:
        """Execute a goal end-to-end.

        Can receive either:
        - ``goal_text`` alone -> will use plan_generator (LLM) to create plan
        - ``pre_built_plan`` -> skip planning, go straight to validation
        """
        self._reset()
        self._started_at = time.monotonic()
        goal_id = uuid.uuid4().hex[:12]

        try:
            # Phase 1: Get a plan
            if pre_built_plan:
                self._plan = pre_built_plan
                self._plan.goal_id = goal_id
                self._set_state(GoalState.VALIDATING_PLAN)
            else:
                self._set_state(GoalState.PARSING_INTENT)
                self._plan = await self._generate_plan(
                    goal_id, goal_text, context or {},
                )

            if self._state == GoalState.FAILED:
                return self._build_result(goal_id, goal_text)

            # Phase 2: Validate
            self._set_state(GoalState.VALIDATING_PLAN)
            validation = self._validator.validate(self._plan)

            if not validation.is_valid:
                # Try regenerating once
                if (
                    self._plan_generator
                    and self._llm_calls < self.MAX_LLM_CALLS
                ):
                    logger.warning(
                        f"Plan invalid: {validation.errors}. Regenerating...",
                    )
                    self._plan = await self._generate_plan(
                        goal_id, goal_text, context or {},
                    )
                    validation = self._validator.validate(self._plan)

                if not validation.is_valid:
                    self._errors.append(
                        f"Plan validation failed: {validation.errors}",
                    )
                    self._set_state(GoalState.FAILED)
                    return self._build_result(goal_id, goal_text)

            # Apply safety enforcement from validator
            self._plan = validation.enforced_plan or self._plan

            # Phase 3: Confirmation (if needed)
            if self._plan.requires_confirmation:
                self._set_state(GoalState.AWAITING_CONFIRMATION)
                if self._confirmation_handler:
                    confirmed = await self._confirmation_handler(self._plan)
                    if not confirmed:
                        self._set_state(GoalState.CANCELLED)
                        return self._build_result(goal_id, goal_text)
                else:
                    logger.warning(
                        "Plan needs confirmation but no handler",
                    )

            # Phase 4: Execute step by step
            self._set_state(GoalState.EXECUTING)
            await self._execute_plan()

            # Phase 5: Build result
            return self._build_result(goal_id, goal_text)

        except Exception as e:
            logger.error(f"GoalEngine error: {e}", exc_info=True)
            self._errors.append(
                f"Engine error: {type(e).__name__}: {e}",
            )
            self._set_state(GoalState.FAILED)
            return self._build_result(goal_id, goal_text)

    # ── Planning ──

    async def _generate_plan(
        self, goal_id: str, goal_text: str, context: dict,
    ) -> Plan:
        """Use LLM to generate a plan."""
        if not self._plan_generator:
            self._errors.append("No plan generator configured")
            self._set_state(GoalState.FAILED)
            return Plan(goal_id=goal_id, goal_text=goal_text)

        self._set_state(GoalState.GENERATING_PLAN)
        self._llm_calls += 1

        try:
            plan = await self._plan_generator(goal_text, context)
            plan.goal_id = goal_id
            plan.goal_text = goal_text
            return plan
        except Exception as e:
            self._errors.append(f"Plan generation failed: {e}")
            self._set_state(GoalState.FAILED)
            return Plan(goal_id=goal_id, goal_text=goal_text)

    # ── Execution ──

    async def _execute_plan(self):
        """Execute plan steps one by one with verification."""
        from .types import ToolResult

        while self._current_step_index < len(self._plan.steps):
            if self._state in (GoalState.FAILED, GoalState.CANCELLED):
                break

            step = self._plan.steps[self._current_step_index]
            step.status = "running"

            # Progress callback
            if self._progress_callback:
                await self._progress_callback(
                    self._current_step_index + 1,
                    len(self._plan.steps),
                    step.description,
                )

            # Execute the step
            self._set_state(GoalState.EXECUTING)

            if self._tool_executor:
                result = await self._tool_executor(
                    step.tool_name, step.params,
                )
            else:
                result = ToolResult.fail(
                    "No tool executor", tool_name=step.tool_name,
                )

            # Verify the step
            self._set_state(GoalState.VERIFYING_STEP)
            check_passed = None
            if step.success_check:
                check_passed = await self._checker.check(step.success_check)

            # Determine outcome
            step_success = result.success and (check_passed is not False)

            if step_success:
                step.status = "completed"
                self._scores.append(1.0 if check_passed else 0.85)
                # Capture result summary from last successful step
                if result.data:
                    try:
                        data = result.data
                        if isinstance(data, dict):
                            # Extract meaningful summary from tool result
                            summary_parts = []
                            for key in ("windows", "elements", "text", "output", "result", "matches", "items"):
                                if key in data:
                                    val = data[key]
                                    if isinstance(val, list):
                                        summary_parts.append(f"{len(val)} {key}")
                                        # Include first few items
                                        for item in val[:5]:
                                            if isinstance(item, dict):
                                                name = item.get("title") or item.get("name") or item.get("app_id") or str(item)[:60]
                                                summary_parts.append(f"  - {name}")
                                            else:
                                                summary_parts.append(f"  - {str(item)[:60]}")
                                    elif isinstance(val, str):
                                        summary_parts.append(f"{key}: {val[:200]}")
                                    else:
                                        summary_parts.append(f"{key}: {val}")
                            if summary_parts:
                                self._last_result_summary = "\n".join(summary_parts)
                            elif "success" in data:
                                self._last_result_summary = str(data)[:300]
                        elif isinstance(data, str):
                            self._last_result_summary = data[:300]
                        elif isinstance(data, list):
                            self._last_result_summary = f"{len(data)} items"
                    except Exception:
                        pass
                self._current_step_index += 1
            else:
                # Handle failure
                step.status = "failed"
                error_msg = result.error or "Unknown error"

                self._set_state(GoalState.HANDLING_FAILURE)
                decision = ReplanDecision.decide(
                    step=step,
                    error=error_msg,
                    retry_count=step.retries,
                    max_retries=step.max_retries,
                    check_passed=check_passed,
                )

                if decision == "retry":
                    step.retries += 1
                    step.status = "pending"
                    continue

                elif decision == "handle_known":
                    handler_steps = ReplanDecision.get_handler(error_msg)
                    if handler_steps:
                        for h in handler_steps:
                            if self._tool_executor:
                                await self._tool_executor(
                                    h["tool"], h.get("params", {}),
                                )
                    step.retries += 1
                    step.status = "pending"
                    continue

                elif decision == "skip":
                    step.status = "skipped"
                    self._current_step_index += 1
                    continue

                elif decision == "replan":
                    if (
                        self._replan_count < self.MAX_REPLANS
                        and self._plan_generator
                    ):
                        self._set_state(GoalState.REPLANNING)
                        self._replan_count += 1
                        self._llm_calls += 1

                        try:
                            new_plan = await self._plan_generator(
                                self._plan.goal_text,
                                {
                                    **self._plan.context,
                                    "completed_steps": [
                                        {
                                            "id": s.id,
                                            "description": s.description,
                                        }
                                        for s in self._plan.steps[
                                            : self._current_step_index
                                        ]
                                        if s.status == "completed"
                                    ],
                                    "failed_step": step.description,
                                    "error": error_msg,
                                    "replan": True,
                                },
                            )
                            # Replace remaining steps with new plan
                            completed = self._plan.steps[
                                : self._current_step_index
                            ]
                            self._plan.steps = completed + new_plan.steps
                            self._set_state(GoalState.EXECUTING)
                            continue
                        except Exception as e:
                            self._errors.append(f"Replan failed: {e}")

                    # Replan exhausted or failed
                    self._errors.append(
                        f"Step failed after replan: {step.description}",
                    )
                    self._scores.append(0.0)
                    self._current_step_index += 1

                else:  # abort
                    self._errors.append(f"Aborting: {error_msg}")
                    self._set_state(GoalState.FAILED)
                    break

        # Done with all steps (or failed/cancelled)
        if self._state not in (GoalState.FAILED, GoalState.CANCELLED):
            self._set_state(GoalState.COMPLETED)

    # ── Helpers ──

    def _build_result(self, goal_id: str, goal_text: str) -> GoalResult:
        steps = self._plan.steps if self._plan else []
        completed = sum(1 for s in steps if s.status == "completed")
        total = len(steps)
        avg = (
            sum(self._scores) / len(self._scores) if self._scores else 0.0
        )

        return GoalResult(
            goal_id=goal_id,
            goal_text=goal_text,
            success=self._state == GoalState.COMPLETED and not self._errors,
            partial=completed > 0 and completed < total,
            steps_completed=completed,
            steps_total=total,
            avg_score=round(avg, 4),
            errors=self._errors,
            duration_s=round(time.monotonic() - self._started_at, 2),
            replan_count=self._replan_count,
            result_summary=self._last_result_summary,
        )

    def _reset(self):
        """Reset engine state for a new goal."""
        self._state = GoalState.IDLE
        self._plan = None
        self._current_step_index = 0
        self._replan_count = 0
        self._llm_calls = 0
        self._scores = []
        self._errors = []
        self._last_result_summary = ""
        self._started_at = 0.0
        self._state_history = []

    def _set_state(self, new_state: GoalState):
        """Transition to a new state."""
        old = self._state
        self._state = new_state
        self._state_history.append(new_state)
        if old != new_state:
            logger.debug(f"GoalEngine: {old.value} -> {new_state.value}")

    @property
    def state(self) -> GoalState:
        """Current engine state."""
        return self._state

    @property
    def current_step(self) -> int:
        """Index of the current step being executed."""
        return self._current_step_index

    @property
    def plan(self) -> Optional[Plan]:
        """The active plan."""
        return self._plan

    @property
    def state_history(self) -> list[GoalState]:
        """All states visited during this goal."""
        return list(self._state_history)
