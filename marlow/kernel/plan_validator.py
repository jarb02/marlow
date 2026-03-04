"""Validates plans structurally and for safety patterns.

Three validation layers:
1. **Structural** -- tool exists, params present, step limits, IDs unique.
2. **Pattern matching** -- dangerous commands and exfiltration patterns in
   params trigger safety enforcement (mark as ``requires_confirmation``).
3. **Token budget** -- estimates token usage and warns if plan is expensive.

/ Validacion de planes con 3 capas: estructura, patrones, presupuesto.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    (r"rm\s+-rf", "Recursive force delete"),
    (r"del\s+/[sf]", "Force delete command"),
    (r"format\s+[a-z]:", "Disk format"),
    (r"rmdir\s+/s", "Recursive directory removal"),
    (r"DROP\s+TABLE", "SQL table drop"),
    (r"powershell.*-enc", "Encoded PowerShell"),
    (r"iex\s*\(", "PowerShell invoke-expression"),
    (r"reg\s+delete", "Registry deletion"),
    (r"reg\s+add", "Registry modification"),
    (r"netsh", "Network configuration change"),
    (r"schtasks\s+/create", "Scheduled task creation"),
    (r"bitsadmin", "Background transfer manipulation"),
    (r"certutil.*-decode", "Certificate utility decode (download)"),
    (r"curl.*\|.*sh", "Remote script execution"),
    (r"wget.*\|.*sh", "Remote script execution"),
]

EXFILTRATION_PATTERNS: list[tuple[str, str]] = [
    (r"curl\s+.*-d\s+@", "Data exfiltration via curl POST"),
    (r"Invoke-WebRequest.*-Body", "Data exfiltration via PowerShell"),
    (r"nslookup\s+\S+\.\S+\.\S+", "DNS exfiltration"),
    (r"certutil.*-encode", "Data encoding for exfiltration"),
    (r"tar\s+.*\|.*curl", "Archive and upload"),
    (r"type.*>.*\\\\", "File copy to network share"),
    (r"copy.*\\\\", "File copy to network share"),
    (r"xcopy.*\\\\", "File copy to network share"),
]


@dataclass
class ValidationResult:
    """Result of plan validation."""

    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    enforced_plan: Optional[object] = None  # Plan with safety overrides


class PlanValidator:
    """Validates execution plans before they run.

    Parameters
    ----------
    * **available_tools** (list of str):
        Known tool names. If empty, tool-existence check is skipped.
    * **max_steps** (int):
        Maximum allowed steps per plan.
    * **max_duration_ms** (float):
        Maximum estimated duration before warning.
    """

    TOKEN_BUDGET_PER_GOAL = 50_000

    def __init__(
        self,
        available_tools: list[str] = None,
        max_steps: int = 20,
        max_duration_ms: float = 300_000,
    ):
        self._tools = set(available_tools or [])
        self._max_steps = max_steps
        self._max_duration_ms = max_duration_ms

    def validate(self, plan) -> ValidationResult:
        """Validate a plan through 3 layers.

        Layer 1: Structural (tool exists, params present, limits).
        Layer 2: Pattern matching (dangerous commands, exfiltration).
        Layer 3: Token budget estimation.

        Also enforces safety: marks dangerous steps as
        ``requires_confirmation``.
        """
        errors: list[str] = []
        warnings: list[str] = []

        if not plan or not plan.steps:
            errors.append("Plan has no steps")
            return ValidationResult(is_valid=False, errors=errors)

        # Layer 1: Structural
        if len(plan.steps) > self._max_steps:
            errors.append(
                f"Plan has {len(plan.steps)} steps (max {self._max_steps})",
            )

        total_duration = sum(s.estimated_duration_ms for s in plan.steps)
        if total_duration > self._max_duration_ms:
            warnings.append(
                f"Estimated duration {total_duration}ms "
                f"exceeds {self._max_duration_ms}ms",
            )

        seen_ids: set[str] = set()
        for i, step in enumerate(plan.steps):
            if not step.tool_name:
                errors.append(f"Step {i}: missing tool_name")
            elif self._tools and step.tool_name not in self._tools:
                errors.append(
                    f"Step {i}: unknown tool '{step.tool_name}'",
                )

            if step.id in seen_ids:
                errors.append(f"Step {i}: duplicate id '{step.id}'")
            seen_ids.add(step.id)

        # Layer 2: Pattern matching (dangerous commands in params)
        for step in plan.steps:
            params_str = json.dumps(step.params)
            for pattern, description in DANGEROUS_PATTERNS:
                if re.search(pattern, params_str, re.IGNORECASE):
                    step.risk = "critical"
                    step.requires_confirmation = True
                    warnings.append(
                        f"Step '{step.id}': dangerous pattern "
                        f"detected: {description}",
                    )
            for pattern, description in EXFILTRATION_PATTERNS:
                if re.search(pattern, params_str, re.IGNORECASE):
                    step.risk = "critical"
                    step.requires_confirmation = True
                    warnings.append(
                        f"Step '{step.id}': exfiltration pattern "
                        f"detected: {description}",
                    )

        # Layer 3: Token budget estimation (1 token ≈ 4 chars)
        total_chars = sum(len(str(step.params)) for step in plan.steps)
        estimated_tokens = total_chars // 4
        if estimated_tokens > self.TOKEN_BUDGET_PER_GOAL:
            warnings.append(
                f"Plan estimated at {estimated_tokens} tokens, "
                f"exceeds budget of {self.TOKEN_BUDGET_PER_GOAL}",
            )

        # Mark plan as requiring confirmation if any step does
        if any(s.requires_confirmation for s in plan.steps):
            plan.requires_confirmation = True

        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            enforced_plan=plan,
        )
