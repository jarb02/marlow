"""MarlowKernel — the autonomous decision loop.

States: IDLE -> PLANNING -> EXECUTING -> EVALUATING -> IDLE
                                |
                          RECOVERING -> EXECUTING
                          PAUSED (user requested)
                          ERROR (unrecoverable)

The loop runs as an asyncio task. Each cycle:
1. OBSERVE: Capture WorldState snapshot
2. DECIDE: Check plan, pick next action
3. ACT: Execute action via SmartExecutor
4. EVALUATE: Score result, update state, decide next

When no goal is active, the kernel sits in IDLE and polls slowly.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .config import KernelConfig
from .knowledge import AppKnowledgeManager
from .loop_guard import LoopGuard
from .memory import MemorySystem
from .scoring.scorer import ActionScorer, StepVerdict
from .security.manager import SecurityManager
from .types import ToolResult
from .world_state import WorldStateCapture, WorldStateSnapshot

logger = logging.getLogger("marlow.kernel")


class KernelState(enum.Enum):
    """Possible states for the decision loop."""

    IDLE = "idle"
    PLANNING = "planning"
    EXECUTING = "executing"
    EVALUATING = "evaluating"
    RECOVERING = "recovering"
    PAUSED = "paused"
    ERROR = "error"
    SHUTDOWN = "shutdown"


@dataclass
class PlanStep:
    """A single step in a plan."""

    tool_name: str
    params: dict = field(default_factory=dict)
    description: str = ""
    expected_app: str = ""
    expected_duration_ms: float = 3000.0
    success_check: Optional[dict] = None  # How to verify success
    retries: int = 0
    max_retries: int = 2


@dataclass
class GoalContext:
    """Active goal being worked on."""

    goal_id: str
    title: str
    plan: list[PlanStep] = field(default_factory=list)
    current_step: int = 0
    scores: list[float] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    started_at: float = 0.0  # monotonic


class MarlowKernel:
    """The autonomous decision loop.

    Usage::

        kernel = MarlowKernel(tool_executor=my_executor_func)
        await kernel.start()
        await kernel.submit_goal("Open Notepad and type hello")
        # ... kernel works autonomously ...
        await kernel.shutdown()
    """

    def __init__(
        self,
        tool_executor: Callable = None,
        config: KernelConfig = None,
        security: SecurityManager = None,
        scorer: ActionScorer = None,
        memory: MemorySystem = None,
        knowledge: AppKnowledgeManager = None,
        world_capture: WorldStateCapture = None,
    ):
        self._config = config or KernelConfig()
        self._security = security or SecurityManager()
        self._scorer = scorer or ActionScorer()
        self._memory = memory
        self._knowledge = knowledge
        self._world_capture = world_capture or WorldStateCapture()
        self._tool_executor = tool_executor  # async callable(tool_name, params) -> ToolResult

        # State
        self._state = KernelState.IDLE
        self._goal: Optional[GoalContext] = None
        self._world: Optional[WorldStateSnapshot] = None
        self._prev_world: Optional[WorldStateSnapshot] = None
        self._loop_guard = LoopGuard(
            max_iterations=self._config.max_iterations,
            max_tokens=50_000,
        )

        # Control
        self._running = False
        self._loop_task: Optional[asyncio.Task] = None
        self._goal_queue: asyncio.Queue = asyncio.Queue()
        self._cancel_event = asyncio.Event()

        # Stats
        self._cycle_count = 0
        self._total_actions = 0
        self._state_history: list[tuple[float, KernelState]] = []

        # Callbacks
        self._on_state_change: Optional[Callable] = None
        self._on_action_complete: Optional[Callable] = None
        self._on_goal_complete: Optional[Callable] = None
        self._confirmation_handler: Optional[Callable] = None  # async (tool, params, reason) -> bool

    # ── Lifecycle ──

    async def start(self):
        """Start the kernel loop."""
        if self._running:
            return
        self._running = True
        self._set_state(KernelState.IDLE)
        self._loop_task = asyncio.create_task(self._main_loop())
        logger.info("Kernel started")

    async def shutdown(self):
        """Graceful shutdown."""
        self._running = False
        self._cancel_event.set()
        self._set_state(KernelState.SHUTDOWN)
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
        # Persist short-term memory
        if self._memory:
            await self._memory.persist_short_term()
        logger.info("Kernel shutdown complete")

    async def pause(self):
        """Pause execution (user requested)."""
        self._set_state(KernelState.PAUSED)

    async def resume(self):
        """Resume from pause."""
        if self._state == KernelState.PAUSED:
            self._set_state(
                KernelState.EXECUTING if self._goal else KernelState.IDLE,
            )

    # ── Goal Management ──

    async def submit_goal(
        self, title: str, plan: list[dict] = None,
    ) -> str:
        """Submit a new goal for the kernel to work on.

        Parameters
        ----------
        * **title** (str): Human-readable goal description.
        * **plan** (list of dict or None):
            Optional list of ``{"tool_name": str, "params": dict, ...}``.
            If None, kernel will need LLM to plan (Tier 3).

        Returns
        -------
        str
            The generated goal_id.
        """
        goal_id = uuid.uuid4().hex[:12]
        steps = []
        if plan:
            for step in plan:
                steps.append(PlanStep(
                    tool_name=step.get("tool_name", ""),
                    params=step.get("params", {}),
                    description=step.get("description", ""),
                    expected_app=step.get("expected_app", ""),
                    expected_duration_ms=step.get(
                        "expected_duration_ms", 3000.0,
                    ),
                    success_check=step.get("success_check"),
                    max_retries=step.get("max_retries", 2),
                ))

        goal = GoalContext(
            goal_id=goal_id,
            title=title,
            plan=steps,
            started_at=time.monotonic(),
        )
        await self._goal_queue.put(goal)
        logger.info(f"Goal submitted: {title} ({goal_id})")
        return goal_id

    async def cancel_goal(self):
        """Cancel the current goal."""
        if self._goal:
            logger.info(f"Goal cancelled: {self._goal.title}")
            self._goal = None
            self._loop_guard.reset()
            self._set_state(KernelState.IDLE)

    # ── Main Loop ──

    async def _main_loop(self):
        """The heartbeat of Marlow."""
        while self._running:
            try:
                if self._state == KernelState.IDLE:
                    await self._idle_tick()
                elif self._state == KernelState.EXECUTING:
                    await self._execute_cycle()
                elif self._state == KernelState.EVALUATING:
                    await self._evaluate_cycle()
                elif self._state == KernelState.RECOVERING:
                    await self._recover_cycle()
                elif self._state == KernelState.PAUSED:
                    await asyncio.sleep(1.0)
                elif self._state == KernelState.ERROR:
                    await asyncio.sleep(5.0)
                else:
                    await asyncio.sleep(0.5)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Kernel error: {e}", exc_info=True)
                self._set_state(KernelState.ERROR)
                await asyncio.sleep(1.0)

    async def _idle_tick(self):
        """IDLE state: wait for goals."""
        try:
            goal = await asyncio.wait_for(
                self._goal_queue.get(),
                timeout=self._config.loop_frequency_idle,
            )
            self._goal = goal
            self._loop_guard.reset()

            if goal.plan is not None:
                # Plan provided (even if empty) — go straight to executing
                self._set_state(KernelState.EXECUTING)
            else:
                # No plan — would need LLM (Tier 3)
                self._set_state(KernelState.PLANNING)
                logger.warning("LLM planning not implemented yet (Tier 3)")
                self._set_state(KernelState.IDLE)
                self._goal = None
        except asyncio.TimeoutError:
            pass  # No goals, stay idle

    async def _execute_cycle(self):
        """One cycle of EXECUTING: observe -> security check -> act."""
        if not self._goal or self._goal.current_step >= len(self._goal.plan):
            self._set_state(KernelState.EVALUATING)
            return

        self._cycle_count += 1
        step = self._goal.plan[self._goal.current_step]

        # 1. OBSERVE: Capture world state
        self._prev_world = self._world
        self._world = self._world_capture.capture(
            active_goal_id=self._goal.goal_id,
            active_step_index=self._goal.current_step,
        )

        # 2. LOOP GUARD: Check if we should continue
        action_sig = f"{step.tool_name}:{hash(str(step.params)) % 10000}"
        fingerprint = self._world.fingerprint()
        guard_result = self._loop_guard.check(action_sig, fingerprint)

        if not guard_result.should_continue:
            logger.warning(f"LoopGuard triggered: {guard_result.reason}")
            self._goal.errors.append(guard_result.reason)
            # Loop guard = goal is stuck, go straight to evaluation
            self._set_state(KernelState.EVALUATING)
            return

        # 3. SECURITY: Check permission
        sec_decision = self._security.check_action(
            step.tool_name, step.params, self._goal.goal_id,
        )

        if not sec_decision.allowed:
            logger.warning(f"Security blocked: {sec_decision.reasons}")
            self._goal.errors.append(f"Security: {sec_decision.reasons}")
            # Skip this step
            self._goal.current_step += 1
            return

        if sec_decision.needs_confirmation:
            if self._confirmation_handler:
                confirmed = await self._confirmation_handler(
                    step.tool_name, step.params, sec_decision.reasons,
                )
                if not confirmed:
                    logger.info(f"User denied: {step.tool_name}")
                    self._goal.current_step += 1
                    return
            else:
                logger.warning(
                    f"Confirmation needed but no handler: {step.tool_name}",
                )
                self._goal.current_step += 1
                return

        # 4. ACT: Execute the tool
        start_time = time.monotonic()

        if self._tool_executor:
            result = await self._tool_executor(step.tool_name, step.params)
        else:
            result = ToolResult.fail("No tool executor configured")

        duration_ms = (time.monotonic() - start_time) * 1000

        # 5. Capture post-action state
        post_world = self._world_capture.capture(
            active_goal_id=self._goal.goal_id,
            active_step_index=self._goal.current_step,
        )

        # 6. SCORE the action
        score = self._scorer.score(
            tool_name=step.tool_name,
            tool_success=result.success,
            tool_error=result.error,
            check_passed=None,  # success_check not implemented yet (Tier 3)
            state_before=self._world,
            state_after=post_world,
            expected_app=step.expected_app,
            duration_ms=duration_ms,
            expected_duration_ms=step.expected_duration_ms,
            app_name=step.expected_app,
        )

        self._world = post_world
        self._goal.scores.append(score.composite)
        self._total_actions += 1

        # 7. REMEMBER
        if self._memory:
            self._memory.remember_short(
                {
                    "tool": step.tool_name,
                    "success": result.success,
                    "score": score.composite,
                    "duration_ms": duration_ms,
                },
                category="action",
                tool_name=step.tool_name,
                goal_id=self._goal.goal_id,
            )

        # 8. LEARN
        if self._knowledge:
            await self._knowledge.record_action(
                app_name=step.expected_app or "unknown",
                success=result.success,
            )
            if not result.success and result.error:
                await self._knowledge.record_error(
                    app_name=step.expected_app or "unknown",
                    tool_name=step.tool_name,
                    error_type="execution_error",
                    error_message=result.error,
                )

        # 9. DECIDE next action based on verdict
        verdict = self._scorer.decide(score)

        if self._on_action_complete:
            await self._on_action_complete(step, result, score, verdict)

        if verdict in (StepVerdict.STEP_OK, StepVerdict.STEP_PARTIAL):
            # Move to next step
            self._goal.current_step += 1
        elif verdict == StepVerdict.STEP_RETRY:
            step.retries += 1
            if step.retries >= step.max_retries:
                logger.warning(f"Max retries for step: {step.description}")
                self._goal.current_step += 1  # Give up on this step
            # else: retry same step (don't increment)
        elif verdict == StepVerdict.STEP_ALTERNATIVE:
            self._set_state(KernelState.RECOVERING)
            return
        else:  # STEP_FAILED
            self._goal.errors.append(f"Step failed: {step.tool_name}")
            self._set_state(KernelState.RECOVERING)
            return

        # Check if plan is complete
        if self._goal.current_step >= len(self._goal.plan):
            self._set_state(KernelState.EVALUATING)

        # Small yield to event loop
        await asyncio.sleep(self._config.loop_frequency_executing)

    async def _evaluate_cycle(self):
        """EVALUATING: Goal is done (or all steps exhausted)."""
        if not self._goal:
            self._set_state(KernelState.IDLE)
            return

        avg_score = (
            sum(self._goal.scores) / len(self._goal.scores)
            if self._goal.scores
            else 0.0
        )

        success = (
            avg_score >= self._config.scoring_threshold_success
            and not self._goal.errors
        )
        partial = avg_score >= self._config.scoring_threshold_partial

        result = {
            "goal_id": self._goal.goal_id,
            "title": self._goal.title,
            "success": success,
            "partial": partial,
            "avg_score": round(avg_score, 4),
            "steps_completed": self._goal.current_step,
            "steps_total": len(self._goal.plan),
            "errors": self._goal.errors,
            "duration_s": round(time.monotonic() - self._goal.started_at, 2),
        }

        logger.info(
            f"Goal {'completed' if success else 'failed'}: "
            f"{self._goal.title} (score: {avg_score:.2f})",
        )

        if self._on_goal_complete:
            await self._on_goal_complete(result)

        self._goal = None
        self._loop_guard.reset()
        self._set_state(KernelState.IDLE)

    async def _recover_cycle(self):
        """RECOVERING: Try to salvage the current goal."""
        if not self._goal:
            self._set_state(KernelState.IDLE)
            return

        # Simple recovery: skip current step and continue
        # Tier 3 will add re-planning via LLM
        self._goal.current_step += 1

        if self._goal.current_step >= len(self._goal.plan):
            self._set_state(KernelState.EVALUATING)
        else:
            self._set_state(KernelState.EXECUTING)

    # ── Internal ──

    def _set_state(self, new_state: KernelState):
        """Transition to a new state, record in history, fire callback."""
        old = self._state
        self._state = new_state
        self._state_history.append((time.monotonic(), new_state))
        # Keep history bounded
        if len(self._state_history) > 100:
            self._state_history = self._state_history[-50:]

        if old != new_state:
            logger.debug(f"State: {old.value} -> {new_state.value}")
            if self._on_state_change:
                try:
                    coro = self._on_state_change(old, new_state)
                    if asyncio.iscoroutine(coro):
                        asyncio.ensure_future(coro)
                except Exception:
                    pass  # Callback errors must not break state machine

    # ── Properties ──

    @property
    def state(self) -> KernelState:
        """Current kernel state."""
        return self._state

    @property
    def is_busy(self) -> bool:
        """Whether the kernel is actively working."""
        return self._state in (
            KernelState.PLANNING,
            KernelState.EXECUTING,
            KernelState.EVALUATING,
            KernelState.RECOVERING,
        )

    @property
    def current_goal(self) -> Optional[GoalContext]:
        """The goal currently being worked on."""
        return self._goal

    @property
    def stats(self) -> dict:
        """Current kernel statistics."""
        return {
            "state": self._state.value,
            "cycle_count": self._cycle_count,
            "total_actions": self._total_actions,
            "current_goal": self._goal.title if self._goal else None,
            "current_step": self._goal.current_step if self._goal else 0,
            "loop_guard": self._loop_guard.stats,
        }
