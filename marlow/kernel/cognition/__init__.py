"""Cognition subsystem — LLM-backed planning for GoalEngine.

Re-exports public names for convenient imports::

    from marlow.kernel.cognition import create_provider, LLMPlanner
"""

from .planner import LLMPlanner
from .providers import (
    AnthropicProvider,
    GeminiProvider,
    LLMProvider,
    LLMProviderError,
    OllamaProvider,
    OpenAIProvider,
    ProviderConfig,
    create_provider,
)

__all__ = [
    "AnthropicProvider",
    "GeminiProvider",
    "LLMPlanner",
    "LLMProvider",
    "LLMProviderError",
    "OllamaProvider",
    "OpenAIProvider",
    "ProviderConfig",
    "create_provider",
]
