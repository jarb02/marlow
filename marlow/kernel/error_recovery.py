"""ErrorRecoveryPolicy — 4-level error recovery for ReactiveGoalLoop.

Levels:
    RETRY  — same action, same target (transient errors, timing)
    ADAPT  — same intent, different method (LLM suggests alternative)
    REPLAN — new plan from current state (preserves completed steps)
    ABORT  — stop and explain (security, permissions, unrecoverable)

/ Politica de recuperacion de errores en 4 niveles.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger("marlow.kernel.error_recovery")


class RecoveryLevel(Enum):
    RETRY = "retry"
    ADAPT = "adapt"
    REPLAN = "replan"
    ABORT = "abort"


@dataclass
class RecoveryDecision:
    level: RecoveryLevel
    reason: str
    suggestion: str = ""
    new_plan: list = field(default_factory=list)


class ErrorRecoveryPolicy:
    """4-level error recovery for ReactiveGoalLoop."""

    MAX_RETRIES_PER_STEP = 2
    MAX_ADAPTS_PER_STEP = 1
    MAX_REPLANS_PER_SESSION = 2
    RETRY_DELAY_MS = 500

    def __init__(self, llm_generate=None):
        self._llm_generate = llm_generate
        self._replan_count = 0

    def classify(
        self, error_result: dict, session: dict, observation=None,
    ) -> RecoveryDecision:
        """Classify an error and recommend a recovery level."""
        error_str = ""
        if isinstance(error_result, dict):
            error_str = str(error_result.get("error", "")).lower()

        current_step = session.get("current_step", 0)
        step_errors = [
            e for e in session.get("errors", [])
            if e.get("step") == current_step
        ]
        retry_count = len(step_errors)

        # ABORT for security/permission errors
        if any(w in error_str for w in (
            "permission denied", "security", "blocked", "not allowed",
            "outside home", "forbidden", "access denied",
        )):
            return RecoveryDecision(
                level=RecoveryLevel.ABORT,
                reason=f"Security/permission error: {error_str[:100]}",
            )

        # RETRY for transient errors
        if retry_count < self.MAX_RETRIES_PER_STEP:
            if any(w in error_str for w in (
                "timeout", "timed out", "connection", "temporary",
                "rate limit", "busy", "try again", "not ready",
            )):
                return RecoveryDecision(
                    level=RecoveryLevel.RETRY,
                    reason=f"Transient error, retry {retry_count+1}/{self.MAX_RETRIES_PER_STEP}",
                )

        # RETRY for element/file not found (might be timing)
        if retry_count < self.MAX_RETRIES_PER_STEP:
            if any(w in error_str for w in (
                "not found", "no element", "no such", "does not exist",
                "element not found", "window not found",
            )):
                return RecoveryDecision(
                    level=RecoveryLevel.RETRY,
                    reason=f"Not found, retry {retry_count+1}/{self.MAX_RETRIES_PER_STEP}",
                )

        # ADAPT if retries exhausted on actionable errors
        adapt_count = len([
            e for e in step_errors if e.get("recovery") == "adapt"
        ])
        if adapt_count < self.MAX_ADAPTS_PER_STEP:
            if any(w in error_str for w in (
                "not found", "no element", "no such", "failed",
                "could not", "unable to",
            )):
                return RecoveryDecision(
                    level=RecoveryLevel.ADAPT,
                    reason="Action failed after retries, trying alternative",
                )

        # REPLAN if adapts exhausted
        if self._replan_count < self.MAX_REPLANS_PER_SESSION:
            if any(w in error_str for w in (
                "unexpected", "state", "changed", "different",
            )):
                return RecoveryDecision(
                    level=RecoveryLevel.REPLAN,
                    reason="Unexpected state, replanning",
                )
            if (retry_count >= self.MAX_RETRIES_PER_STEP
                    and adapt_count >= self.MAX_ADAPTS_PER_STEP):
                return RecoveryDecision(
                    level=RecoveryLevel.REPLAN,
                    reason="Retries and adapts exhausted, replanning",
                )

        # Unknown error with retries left
        if retry_count < self.MAX_RETRIES_PER_STEP:
            return RecoveryDecision(
                level=RecoveryLevel.RETRY,
                reason=f"Unknown error, retry {retry_count+1}/{self.MAX_RETRIES_PER_STEP}",
            )

        # All options exhausted
        return RecoveryDecision(
            level=RecoveryLevel.ABORT,
            reason=f"All recovery exhausted after {retry_count} retries",
        )

    async def execute_recovery(
        self, decision: RecoveryDecision, session: dict,
        action: dict, observation=None,
    ) -> bool:
        """Execute recovery. Returns True if loop should continue."""
        logger.info(
            "  Recovery: %s — %s", decision.level.value, decision.reason,
        )

        if decision.level == RecoveryLevel.RETRY:
            await asyncio.sleep(self.RETRY_DELAY_MS / 1000)
            return True

        if decision.level == RecoveryLevel.ADAPT:
            if self._llm_generate:
                alternative = await self._ask_llm_alternative(
                    action, session, observation,
                )
                if alternative:
                    decision.suggestion = alternative
                    step_idx = session.get("current_step", 0)
                    if step_idx < len(session.get("plan", [])):
                        session["plan"][step_idx] = f"(ADAPTED) {alternative}"
                    return True
            # No alternative found — skip step
            logger.warning("  ADAPT: No alternative, skipping step")
            session["current_step"] = session.get("current_step", 0) + 1
            return True

        if decision.level == RecoveryLevel.REPLAN:
            if self._llm_generate:
                new_plan = await self._generate_new_plan(session, observation)
                if new_plan:
                    self._replan_count += 1
                    completed_count = session.get("current_step", 0)
                    old_completed = session.get("plan", [])[:completed_count]
                    session["plan"] = old_completed + new_plan
                    session["max_iterations"] = min(
                        int(len(session["plan"]) * 1.5) + 1, 12,
                    )
                    logger.info(
                        "  REPLAN: %d new steps (replan %d/%d)",
                        len(new_plan), self._replan_count,
                        self.MAX_REPLANS_PER_SESSION,
                    )
                    return True
            logger.warning("  REPLAN: Failed to generate new plan")
            return False

        if decision.level == RecoveryLevel.ABORT:
            logger.warning("  ABORT: %s", decision.reason)
            return False

        return False

    async def _ask_llm_alternative(
        self, action: dict, session: dict, observation=None,
    ) -> Optional[str]:
        """Ask LLM for an alternative approach."""
        if not self._llm_generate:
            return None
        try:
            obs_text = ""
            if observation and hasattr(observation, "summary"):
                obs_text = f"\nObservation: {observation.summary}"

            last_error = ""
            errors = session.get("errors", [])
            if errors:
                last_error = errors[-1].get("error", "unknown")

            prompt = (
                "The following action failed during a multi-step task:\n\n"
                f"Task: {session.get('goal_text', '?')}\n"
                f"Failed action: {action.get('tool', '?')} "
                f"with params {action.get('parameters', {})}\n"
                f"Error: {last_error}{obs_text}\n\n"
                "Suggest ONE alternative approach. Name the tool and key params.\n"
                "If no alternative exists, respond with NONE."
            )
            response = await self._llm_generate(prompt)
            response = response.strip()
            if response.upper() == "NONE" or not response:
                return None
            return response
        except Exception as e:
            logger.warning("LLM alternative request failed: %s", e)
            return None

    async def _generate_new_plan(
        self, session: dict, observation=None,
    ) -> Optional[list]:
        """Generate a new plan from the current state."""
        if not self._llm_generate:
            return None
        try:
            completed_text = "\n".join(
                f"- Step {s['step']+1}: {s['result_summary']}"
                for s in session.get("completed_steps", [])
            ) or "(nothing yet)"

            errors_text = "\n".join(
                f"- {e.get('tool', '?')}: {e.get('error', '?')}"
                for e in session.get("errors", [])[-3:]
            ) or "(none)"

            key_facts_text = "\n".join(
                f"- {f}" for f in session.get("key_facts", [])
            ) or "(none)"

            obs_text = ""
            if observation and hasattr(observation, "summary"):
                obs_text = observation.summary

            prompt = (
                "You are Marlow. The current plan failed and needs replanning.\n\n"
                f"Task: {session.get('goal_text', '?')}\n\n"
                f"Completed:\n{completed_text}\n\n"
                f"Key facts:\n{key_facts_text}\n\n"
                f"Recent errors:\n{errors_text}\n\n"
                f"Observation: {obs_text or '(none)'}\n\n"
                "Generate a NEW plan for the REMAINING work.\n"
                "Respond ONLY with a JSON array. Use different approaches. 2-5 steps."
            )
            response = await self._llm_generate(prompt)

            text = response.strip()
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()

            result = json.loads(text)
            if isinstance(result, list) and len(result) > 0:
                return result
            return None
        except Exception as e:
            logger.warning("Replan generation failed: %s", e)
            return None

    def reset(self):
        """Reset counters for a new session."""
        self._replan_count = 0
