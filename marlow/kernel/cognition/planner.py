"""LLM-backed plan generator for GoalEngine.

Pipeline::

    goal_text + context
        → ToolFilter  (select relevant tools)
        → Prompt templates  (PLAN_SYSTEM + PLAN_USER / REPLAN_USER)
        → LLMProvider.generate()
        → PlanParser.parse()
        → Plan

Implements the ``async (goal_text, context) -> Plan`` interface
expected by :class:`~marlow.kernel.goal_engine.GoalEngine`.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from ..goal_engine import Plan
from ..planning.parser import PlanParser
from ..planning.prompts import PLAN_SYSTEM, PLAN_USER, REPLAN_USER
from ..planning.tool_filter import ToolFilter
from .providers import LLMProvider, LLMProviderError

logger = logging.getLogger("marlow.cognition.planner")


class LLMPlanner:
    """Generate execution plans via an LLM provider.

    Used as the ``plan_generator`` callback for
    :class:`~marlow.kernel.goal_engine.GoalEngine`.

    Parameters
    ----------
    provider : LLMProvider
        Configured LLM provider instance.
    tool_filter : ToolFilter or None
        Filters 96 tools to a relevant subset for prompting.
    max_tokens : int
        Maximum tokens for plan generation.
    temperature : float
        Sampling temperature.
    """

    def __init__(
        self,
        provider: LLMProvider,
        tool_filter: ToolFilter = None,
        *,
        max_tokens: int = 4096,
        temperature: float = 0.2,
    ):
        self._provider = provider
        self._tool_filter = tool_filter or ToolFilter()
        self._parser = PlanParser()
        self._max_tokens = max_tokens
        self._temperature = temperature

    async def __call__(
        self, goal_text: str, context: dict = None,
    ) -> Plan:
        """Generate a plan for *goal_text*.

        Matches the ``plan_generator`` interface:
        ``async (goal_text, context) -> Plan``

        If ``context.get("replan")`` is truthy, uses the replan template.
        """
        context = context or {}
        is_replan = bool(context.get("replan"))

        # 1. Filter tools
        relevant_tools = self._tool_filter.filter_for_goal(goal_text)
        tools_prompt = self._tool_filter.format_for_prompt(relevant_tools)

        # 2. Build system prompt
        user_home = os.path.expanduser("~").replace("\\", "\\\\")
        system = PLAN_SYSTEM.format(
            available_tools=tools_prompt,
            app_knowledge="(none)",
            user_home=user_home,
        )

        # 3. Build user prompt
        if is_replan:
            completed = context.get("completed_steps", [])
            completed_text = "\n".join(
                f"- [{s.get('id', '?')}] {s.get('description', '?')}"
                for s in completed
            ) or "(none)"

            user_msg = REPLAN_USER.format(
                goal_text=goal_text,
                completed_steps=completed_text,
                failed_step=context.get("failed_step", "unknown"),
                error_message=context.get("error", "unknown"),
                active_window=context.get("active_window", "unknown"),
                open_windows=context.get("open_windows", "unknown"),
                available_variables=context.get("step_context", {}),
            )
        else:
            additional = f"User home directory: {os.path.expanduser('~')}\n"
            if context.get("app_framework"):
                additional += f"App framework: {context['app_framework']}\n"
            if context.get("previous_goals"):
                additional += (
                    f"Previous similar goals and results:\n"
                    f"{context['previous_goals']}\n"
                )

            user_msg = PLAN_USER.format(
                goal_text=goal_text,
                active_window=context.get("active_window", "unknown"),
                open_windows=context.get("open_windows", "unknown"),
                screen_width=context.get("screen_width", 1920),
                screen_height=context.get("screen_height", 1080),
                additional_context=additional,
            )

        # 4. Call LLM
        messages = [{"role": "user", "content": user_msg}]

        try:
            raw = await self._provider.generate(
                messages,
                system=system,
                max_tokens=self._max_tokens,
                temperature=self._temperature,
            )
        except LLMProviderError as exc:
            logger.error("LLM call failed: %s", exc)
            raise

        # 5. Parse response
        plan, errors = self._parser.parse(raw, goal_text)

        if errors:
            logger.warning("Plan parse warnings: %s", errors)

        if plan is None:
            raise LLMProviderError(
                f"Failed to parse plan from LLM response: {errors}",
            )

        return plan
