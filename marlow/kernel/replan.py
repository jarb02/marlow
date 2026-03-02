"""Decision logic for handling step failures + pre-generated handlers.

Provides the ReplanDecision class that determines what to do when a
plan step fails: retry, apply a known handler, skip, replan via LLM,
or abort entirely.
"""

from __future__ import annotations

TRANSIENT_ERRORS = [
    "element not found",
    "timeout",
    "window not ready",
    "stale element",
    "not responding",
    "busy",
]

COMMON_FAILURE_HANDLERS: dict[str, list[dict]] = {
    "element not found": [
        {"tool": "wait_for_idle", "params": {"timeout_ms": 2000}},
    ],
    "timeout": [
        {"tool": "wait_for_idle", "params": {"timeout_ms": 3000}},
    ],
    "not responding": [
        {"tool": "wait_for_idle", "params": {"timeout_ms": 5000}},
    ],
    "dialog": [
        {"tool": "handle_dialog", "params": {"action": "dismiss"}},
    ],
    "wrong window": [
        {"tool": "press_key", "params": {"key": "escape"}},
        {"tool": "wait_for_idle", "params": {"timeout_ms": 500}},
    ],
    "popup": [
        {"tool": "press_key", "params": {"key": "escape"}},
        {"tool": "wait_for_idle", "params": {"timeout_ms": 500}},
    ],
}


class ReplanDecision:
    """Decides how to handle a failed step.

    Decision priority:
    1. Known handler exists for the error → ``"handle_known"``
    2. Transient error + retries left → ``"retry"``
    3. Verification passed despite tool error → ``"skip"``
    4. Step is skippable and low risk → ``"skip"``
    5. Critical risk → ``"abort"``
    6. Default → ``"replan"``
    """

    @staticmethod
    def decide(
        step,
        error: str,
        retry_count: int,
        max_retries: int,
        check_passed: bool = None,
    ) -> str:
        """Determine how to handle a failed step.

        Returns
        -------
        str
            One of: ``"retry"``, ``"handle_known"``, ``"skip"``,
            ``"replan"``, ``"abort"``.
        """
        error_lower = error.lower() if error else ""

        # 1. Known handler exists?
        for pattern in COMMON_FAILURE_HANDLERS:
            if pattern in error_lower:
                if retry_count < max_retries:
                    return "handle_known"

        # 2. Transient error + retries left?
        if any(t in error_lower for t in TRANSIENT_ERRORS):
            if retry_count < max_retries:
                return "retry"

        # 3. Check passed despite tool error? Probably fine.
        if check_passed:
            return "skip"

        # 4. Step is skippable and low risk?
        if (
            hasattr(step, "skippable")
            and step.skippable
            and hasattr(step, "risk")
            and step.risk == "low"
        ):
            return "skip"

        # 5. Critical risk? Don't replan, abort.
        if hasattr(step, "risk") and step.risk == "critical":
            return "abort"

        # 6. Default: try to replan
        return "replan"

    @staticmethod
    def get_handler(error: str) -> list[dict]:
        """Get pre-generated failure handler steps for a known error."""
        error_lower = error.lower() if error else ""
        for pattern, handler in COMMON_FAILURE_HANDLERS.items():
            if pattern in error_lower:
                return handler
        return []
