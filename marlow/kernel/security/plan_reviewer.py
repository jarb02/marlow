"""PlanReviewer — dual LLM safety review for high-risk plans.

When a plan contains dangerous patterns, a second review evaluates it
before execution. Critical plans are blocked; high-risk plans are flagged.

Only activates for plans flagged as high-risk by PlanValidator.
Low/medium risk plans skip review for performance.

Based on OWASP Agentic Top 10 defense: dual-agent verification.

/ Revision de seguridad dual para planes de alto riesgo.
"""

import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger("marlow.kernel.security.plan_reviewer")


class ReviewVerdict(Enum):
    """Result of a safety review."""

    APPROVED = "approved"
    FLAGGED = "flagged"      # concerns but can proceed with confirmation
    REJECTED = "rejected"    # too dangerous, block execution


@dataclass(frozen=True)
class PlanReview:
    """Result of dual LLM safety review."""

    verdict: ReviewVerdict
    concerns: tuple = ()        # list of safety concerns found
    risk_level: str = "low"     # low, medium, high, critical
    reviewer: str = ""          # which model reviewed
    should_block: bool = False  # hard block (vs soft warning)

    @property
    def is_safe(self) -> bool:
        return self.verdict == ReviewVerdict.APPROVED


class PlanReviewer:
    """Dual LLM safety review for high-risk plans.

    When a plan contains dangerous patterns, send it to a second reviewer
    for independent safety evaluation before execution.

    Currently uses rule-based review. Future: second LLM for independent
    evaluation.
    """

    # Risk indicators that trigger review
    REVIEW_TRIGGERS = {
        "run_command",      # arbitrary command execution
        "run_app_script",   # arbitrary script execution
        "cdp_evaluate",     # arbitrary JS execution
    }

    # Params that are extra risky
    RISKY_PARAM_PATTERNS = [
        "delete", "remove", "drop", "format", "shutdown",
        "reboot", "registry", "netsh", "schtasks",
        "powershell -enc", "iex(", "invoke-expression",
    ]

    def __init__(self, llm_planner=None):
        self._llm_planner = llm_planner  # Optional: for actual LLM review
        self._review_count = 0
        self._blocked_count = 0

    def needs_review(self, steps: list) -> bool:
        """Determine if a plan needs dual LLM review.

        Reviews are triggered when:
        1. Plan contains high-risk tools (run_command, run_app_script, cdp_evaluate)
        2. Plan params contain risky patterns
        3. Plan has steps marked requires_confirmation
        """
        for step in steps:
            tool = step.tool_name if hasattr(step, "tool_name") else str(step)
            params_str = str(step.params if hasattr(step, "params") else "").lower()

            if tool in self.REVIEW_TRIGGERS:
                return True

            if any(p in params_str for p in self.RISKY_PARAM_PATTERNS):
                return True

            if hasattr(step, "requires_confirmation") and step.requires_confirmation:
                return True

        return False

    def review_plan(self, goal_text: str, steps: list) -> PlanReview:
        """Review a plan for safety concerns.

        Currently uses rule-based review (no LLM call).
        Future: send to second LLM for independent evaluation.
        """
        self._review_count += 1
        concerns: list[str] = []
        max_risk = "low"

        for step in steps:
            tool = step.tool_name if hasattr(step, "tool_name") else str(step)
            params = step.params if hasattr(step, "params") else {}
            params_str = str(params).lower()

            # Check 1: Dangerous tools
            if tool == "run_command":
                cmd = params.get("command", params.get("cmd", ""))
                if any(p in cmd.lower() for p in self.RISKY_PARAM_PATTERNS):
                    concerns.append(f"Dangerous command: {cmd[:100]}")
                    max_risk = "critical"
                else:
                    concerns.append(f"Shell command execution: {cmd[:100]}")
                    if max_risk not in ("critical", "high"):
                        max_risk = "medium"

            elif tool == "run_app_script":
                concerns.append("Script execution via COM automation")
                if max_risk != "critical":
                    max_risk = "high"

            elif tool == "cdp_evaluate":
                js = params.get("expression", params.get("script", ""))
                concerns.append(f"JavaScript execution: {js[:100]}")
                if max_risk != "critical":
                    max_risk = "high"

            # Check 2: Data exfiltration patterns
            if any(w in params_str for w in ("http://", "https://", "ftp://", "\\\\")):
                if tool in ("run_command", "run_app_script", "cdp_evaluate"):
                    concerns.append(f"Potential data exfiltration in {tool}")
                    max_risk = "critical"

        # Check 3: Multiple dangerous tools in one plan
        dangerous_count = sum(
            1 for s in steps
            if (s.tool_name if hasattr(s, "tool_name") else "") in self.REVIEW_TRIGGERS
        )
        if dangerous_count >= 3 and max_risk not in ("critical",):
            max_risk = "high"

        # Determine verdict
        if max_risk == "critical":
            verdict = ReviewVerdict.REJECTED
            self._blocked_count += 1
        elif max_risk == "high":
            verdict = ReviewVerdict.FLAGGED
        else:
            verdict = ReviewVerdict.APPROVED

        review = PlanReview(
            verdict=verdict,
            concerns=tuple(concerns),
            risk_level=max_risk,
            reviewer="rule_based_v1",
            should_block=(verdict == ReviewVerdict.REJECTED),
        )

        if concerns:
            logger.info(
                "Plan review [%s]: %d concerns, risk=%s, goal='%s'",
                verdict.value, len(concerns), max_risk, goal_text[:50],
            )

        return review

    @property
    def stats(self) -> dict:
        return {
            "reviews": self._review_count,
            "blocked": self._blocked_count,
        }
