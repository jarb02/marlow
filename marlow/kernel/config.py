"""Kernel configuration — governance limits for the decision loop.

These defaults are conservative. The kernel will not exceed these limits
without explicit user override.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class KernelConfig:
    """Configuration for the Marlow kernel decision loop.

    Parameters
    ----------
    * **max_iterations** (int):
        Maximum iterations per goal before the kernel stops.
    * **max_actions_per_minute** (int):
        Rate limit for tool executions (prevents runaway loops).
    * **max_llm_calls_per_hour** (int):
        Budget cap for LLM API calls (prevents cost overruns).
    * **loop_frequency_idle** (float):
        Seconds between loop ticks when idle (no active goal).
    * **loop_frequency_executing** (float):
        Seconds between loop ticks during active execution.
    * **scoring_threshold_success** (float):
        Minimum score to consider a goal fully achieved.
    * **scoring_threshold_partial** (float):
        Minimum score for partial success (may retry or adjust).
    * **scoring_threshold_no_retry** (float):
        Below this score, the kernel will not retry the same approach.
    """

    # Iteration limits
    max_iterations: int = 25
    max_actions_per_minute: int = 30
    max_llm_calls_per_hour: int = 50

    # Loop timing
    loop_frequency_idle: float = 2.0
    loop_frequency_executing: float = 0.01

    # Scoring thresholds
    scoring_threshold_success: float = 0.80
    scoring_threshold_partial: float = 0.50
    scoring_threshold_no_retry: float = 0.30
