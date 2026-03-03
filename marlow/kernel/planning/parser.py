"""Parse LLM JSON output into Plan and PlanStep objects.

Handles malformed JSON, missing fields, and normalizes data.
Tolerates common LLM mistakes: markdown fences, trailing commas,
``"tool"`` instead of ``"tool_name"``, etc.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Optional

from ..goal_engine import Plan, PlanStep


class PlanParser:
    """Parse raw LLM output into a Plan object."""

    def parse(
        self, raw_output: str, goal_text: str = "",
    ) -> tuple[Optional[Plan], list[str]]:
        """Parse LLM output into a Plan.

        Returns
        -------
        tuple[Plan or None, list[str]]
            ``(plan, errors)``. If errors is non-empty, plan may be
            partial or None.
        """
        errors: list[str] = []

        # 1. Extract JSON from LLM output
        json_str = self._extract_json(raw_output)
        if not json_str:
            return None, ["No valid JSON found in output"]

        # 2. Parse JSON
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            fixed = self._fix_json(json_str)
            if fixed:
                try:
                    data = json.loads(fixed)
                except json.JSONDecodeError:
                    return None, [f"Invalid JSON: {e}"]
            else:
                return None, [f"Invalid JSON: {e}"]

        # 3. Extract steps
        steps_data = data.get("steps", [])
        if not steps_data:
            return None, ["No steps found in plan"]

        # 4. Build PlanSteps
        steps: list[PlanStep] = []
        for i, s in enumerate(steps_data):
            step_id = s.get("id", f"step_{i + 1}")
            tool = s.get("tool_name", s.get("tool", ""))

            if not tool:
                errors.append(f"Step {step_id}: missing tool_name")
                continue

            # Normalize success_check
            check = s.get("success_check")
            if isinstance(check, str):
                check = {"type": check, "params": {}}

            steps.append(PlanStep(
                id=step_id,
                tool_name=tool,
                params=s.get("params", s.get("parameters", {})),
                description=s.get("description", f"Execute {tool}"),
                expected_app=s.get("expected_app", ""),
                risk=s.get("risk", "low"),
                requires_confirmation=s.get("requires_confirmation", False),
                success_check=check,
                estimated_duration_ms=float(
                    s.get("estimated_duration_ms", 3000),
                ),
                skippable=s.get("skippable", False),
                max_retries=int(s.get("max_retries", 2)),
            ))

        if not steps:
            return None, errors + ["No valid steps could be parsed"]

        # 5. Build Plan
        context = data.get("context", {})
        plan = Plan(
            goal_id=uuid.uuid4().hex[:12],
            goal_text=goal_text,
            steps=steps,
            context=context,
            estimated_total_ms=sum(s.estimated_duration_ms for s in steps),
            requires_confirmation=any(
                s.requires_confirmation for s in steps
            ),
        )

        return plan, errors

    def _extract_json(self, text: str) -> Optional[str]:
        """Extract JSON from text that may have markdown fences."""
        # Try: ```json ... ``` block
        match = re.search(
            r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL,
        )
        if match:
            return match.group(1).strip()

        # Try: find first { ... } balanced block
        depth = 0
        start = None
        for i, c in enumerate(text):
            if c == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0 and start is not None:
                    return text[start : i + 1]

        return None

    def _fix_json(self, json_str: str) -> Optional[str]:
        """Try to fix common LLM JSON mistakes."""
        fixed = json_str
        # Remove trailing commas before } or ]
        fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
        # Replace single quotes with double quotes (common LLM mistake)
        if "'" in fixed and '"' not in fixed:
            fixed = fixed.replace("'", '"')
        return fixed
