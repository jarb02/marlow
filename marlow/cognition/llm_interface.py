"""Abstract LLM interface for the cognition layer.

Defines the contract that any LLM provider must implement. The kernel
calls these methods to plan, reason, parse, and evaluate — it never
talks to an LLM directly.

Concrete implementations will be added in Tier 6 (Cognition).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LLMInterface(ABC):
    """Abstract base class for LLM providers.

    All methods are async to support both local models (faster-whisper,
    Ollama) and remote LLM APIs without blocking the
    kernel's event loop.
    """

    @abstractmethod
    async def plan(self, goal: str, context: dict[str, Any]) -> dict:
        """Generate an action plan to achieve a goal.

        Parameters
        ----------
        * **goal** (str):
            Natural language description of what to accomplish.
        * **context** (dict):
            Current state: active window, recent actions, screen info.

        Returns
        -------
        dict
            Plan with keys: steps (list of dicts), estimated_actions (int),
            confidence (float 0-1).
        """
        raise NotImplementedError("Implement in Tier 6")

    @abstractmethod
    async def reason(self, situation: str, options: list[str]) -> str:
        """Choose between options given a situation.

        Parameters
        ----------
        * **situation** (str):
            Description of the current situation or problem.
        * **options** (list of str):
            Available choices to pick from.

        Returns
        -------
        str
            The selected option (must be one of the provided options).
        """
        raise NotImplementedError("Implement in Tier 6")

    @abstractmethod
    async def parse_intent(self, user_input: str) -> dict:
        """Parse a natural language command into structured intent.

        Parameters
        ----------
        * **user_input** (str):
            Raw user command (e.g. "open notepad and type hello").

        Returns
        -------
        dict
            Parsed intent with keys: action (str), target (str),
            parameters (dict), confidence (float 0-1).
        """
        raise NotImplementedError("Implement in Tier 6")

    @abstractmethod
    async def evaluate(self, action: dict, result: dict) -> float:
        """Score how well an action achieved its intended effect.

        Parameters
        ----------
        * **action** (dict):
            The action that was executed (tool name, parameters).
        * **result** (dict):
            The result returned by the tool.

        Returns
        -------
        float
            Score from 0.0 (complete failure) to 1.0 (perfect success).
        """
        raise NotImplementedError("Implement in Tier 6")
