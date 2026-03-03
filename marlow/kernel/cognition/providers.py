"""Multi-provider LLM interface using httpx — no vendor SDKs.

Supported providers:
- **Anthropic** (Messages API)
- **OpenAI** (Chat Completions)
- **Gemini** (generateContent)
- **Ollama** (local, no key required)

All providers share the same ABC and retry logic (429 / 5xx / timeout).
"""

from __future__ import annotations

import abc
import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

import httpx

logger = logging.getLogger("marlow.cognition.providers")

# ── Configuration ──


@dataclass
class ProviderConfig:
    """Configuration for an LLM provider.

    Parameters
    ----------
    api_key : str
        API key (empty string for keyless providers like Ollama).
    base_url : str
        Base URL for the API.
    model : str
        Model identifier.
    timeout : float
        Request timeout in seconds.
    max_retries : int
        Maximum retry attempts for transient errors.
    """

    api_key: str = ""
    base_url: str = ""
    model: str = ""
    timeout: float = 60.0
    max_retries: int = 2


# ── Abstract Base ──


class LLMProvider(abc.ABC):
    """Abstract LLM provider.

    Subclasses implement :meth:`generate` to call a specific API.
    Retry logic for 429 / 5xx / timeout is handled by :meth:`_request`.
    """

    def __init__(self, config: ProviderConfig):
        self._config = config

    @abc.abstractmethod
    async def generate(
        self,
        messages: list[dict],
        *,
        system: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> str:
        """Generate a completion from *messages*.

        Parameters
        ----------
        messages : list[dict]
            ``[{"role": "user", "content": "..."}]``
        system : str
            System prompt (provider-specific placement).
        max_tokens : int
            Maximum tokens to generate.
        temperature : float
            Sampling temperature.

        Returns
        -------
        str
            Raw text response from the LLM.
        """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Provider name (e.g. ``"anthropic"``)."""

    # ── Shared retry helper ──

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict = None,
        json_body: dict = None,
        params: dict = None,
    ) -> dict:
        """HTTP request with exponential-backoff retry on 429 / 5xx / timeout.

        Returns parsed JSON response dict.
        Raises ``LLMProviderError`` on exhausted retries or client errors.
        """
        last_exc: Optional[Exception] = None
        attempts = 1 + self._config.max_retries

        for attempt in range(attempts):
            try:
                async with httpx.AsyncClient(
                    timeout=self._config.timeout,
                ) as client:
                    resp = await client.request(
                        method,
                        url,
                        headers=headers,
                        json=json_body,
                        params=params,
                    )

                if resp.status_code == 429 or resp.status_code >= 500:
                    # Transient — retry with backoff
                    wait = min(2 ** attempt, 30)
                    logger.warning(
                        "%s: HTTP %d, retry %d/%d in %ds",
                        self.name, resp.status_code,
                        attempt + 1, attempts, wait,
                    )
                    last_exc = LLMProviderError(
                        f"HTTP {resp.status_code}: {resp.text[:200]}",
                    )
                    await asyncio.sleep(wait)
                    continue

                if resp.status_code >= 400:
                    raise LLMProviderError(
                        f"HTTP {resp.status_code}: {resp.text[:500]}",
                    )

                return resp.json()

            except httpx.TimeoutException as exc:
                wait = min(2 ** attempt, 30)
                logger.warning(
                    "%s: timeout, retry %d/%d in %ds",
                    self.name, attempt + 1, attempts, wait,
                )
                last_exc = LLMProviderError(f"Timeout: {exc}")
                if attempt < attempts - 1:
                    await asyncio.sleep(wait)

        raise last_exc or LLMProviderError("Request failed after retries")


# ── Error ──


class LLMProviderError(Exception):
    """Raised when an LLM provider call fails."""


# ── Anthropic ──


class AnthropicProvider(LLMProvider):
    """Anthropic Messages API provider.

    Env: ``ANTHROPIC_API_KEY``
    """

    DEFAULT_MODEL = "claude-sonnet-4-20250514"
    API_URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, config: ProviderConfig = None):
        config = config or ProviderConfig()
        if not config.api_key:
            config.api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not config.model:
            config.model = self.DEFAULT_MODEL
        if not config.base_url:
            config.base_url = self.API_URL
        super().__init__(config)

    @property
    def name(self) -> str:
        return "anthropic"

    async def generate(
        self,
        messages: list[dict],
        *,
        system: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> str:
        if not self._config.api_key:
            raise LLMProviderError(
                "ANTHROPIC_API_KEY not set in environment or config",
            )

        headers = {
            "x-api-key": self._config.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        body = {
            "model": self._config.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }
        if system:
            body["system"] = system

        data = await self._request(
            "POST", self._config.base_url, headers=headers, json_body=body,
        )

        # response: {"content": [{"type": "text", "text": "..."}]}
        content = data.get("content", [])
        parts = [c["text"] for c in content if c.get("type") == "text"]
        return "".join(parts)


# ── OpenAI ──


class OpenAIProvider(LLMProvider):
    """OpenAI Chat Completions API provider.

    Env: ``OPENAI_API_KEY``
    """

    DEFAULT_MODEL = "gpt-4o"
    API_URL = "https://api.openai.com/v1/chat/completions"

    def __init__(self, config: ProviderConfig = None):
        config = config or ProviderConfig()
        if not config.api_key:
            config.api_key = os.environ.get("OPENAI_API_KEY", "")
        if not config.model:
            config.model = self.DEFAULT_MODEL
        if not config.base_url:
            config.base_url = self.API_URL
        super().__init__(config)

    @property
    def name(self) -> str:
        return "openai"

    async def generate(
        self,
        messages: list[dict],
        *,
        system: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> str:
        if not self._config.api_key:
            raise LLMProviderError(
                "OPENAI_API_KEY not set in environment or config",
            )

        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
        }

        # OpenAI: system prompt as first message
        api_messages = []
        if system:
            api_messages.append({"role": "system", "content": system})
        api_messages.extend(messages)

        body = {
            "model": self._config.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": api_messages,
        }

        data = await self._request(
            "POST", self._config.base_url, headers=headers, json_body=body,
        )

        # response: {"choices": [{"message": {"content": "..."}}]}
        choices = data.get("choices", [])
        if not choices:
            raise LLMProviderError("Empty response from OpenAI")
        return choices[0]["message"]["content"]


# ── Gemini ──


class GeminiProvider(LLMProvider):
    """Google Gemini generateContent API provider.

    Env: ``GEMINI_API_KEY``
    """

    DEFAULT_MODEL = "gemini-2.0-flash"
    API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self, config: ProviderConfig = None):
        config = config or ProviderConfig()
        if not config.api_key:
            config.api_key = os.environ.get("GEMINI_API_KEY", "")
        if not config.model:
            config.model = self.DEFAULT_MODEL
        if not config.base_url:
            config.base_url = self.API_BASE
        super().__init__(config)

    @property
    def name(self) -> str:
        return "gemini"

    async def generate(
        self,
        messages: list[dict],
        *,
        system: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> str:
        if not self._config.api_key:
            raise LLMProviderError(
                "GEMINI_API_KEY not set in environment or config",
            )

        url = (
            f"{self._config.base_url}/{self._config.model}:generateContent"
        )

        # Convert messages to Gemini format
        contents = []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            contents.append({
                "role": role,
                "parts": [{"text": msg["content"]}],
            })

        body: dict = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": temperature,
            },
        }
        if system:
            body["systemInstruction"] = {
                "parts": [{"text": system}],
            }

        data = await self._request(
            "POST", url,
            headers={"Content-Type": "application/json"},
            json_body=body,
            params={"key": self._config.api_key},
        )

        # response: {"candidates": [{"content": {"parts": [{"text": "..."}]}}]}
        candidates = data.get("candidates", [])
        if not candidates:
            raise LLMProviderError("Empty response from Gemini")
        parts = candidates[0].get("content", {}).get("parts", [])
        return "".join(p.get("text", "") for p in parts)


# ── Ollama ──


class OllamaProvider(LLMProvider):
    """Ollama local API provider (no API key needed).

    Default: ``localhost:11434``
    """

    DEFAULT_MODEL = "llama3.1"
    API_URL = "http://localhost:11434/api/chat"

    def __init__(self, config: ProviderConfig = None):
        config = config or ProviderConfig()
        if not config.model:
            config.model = self.DEFAULT_MODEL
        if not config.base_url:
            config.base_url = self.API_URL
        if config.timeout < 120.0:
            config.timeout = 120.0  # Local models need more time
        super().__init__(config)

    @property
    def name(self) -> str:
        return "ollama"

    async def generate(
        self,
        messages: list[dict],
        *,
        system: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> str:
        # Ollama: system as first message
        api_messages = []
        if system:
            api_messages.append({"role": "system", "content": system})
        api_messages.extend(messages)

        body = {
            "model": self._config.model,
            "messages": api_messages,
            "stream": False,
            "options": {
                "num_predict": max_tokens,
                "temperature": temperature,
            },
        }

        data = await self._request(
            "POST", self._config.base_url,
            headers={"Content-Type": "application/json"},
            json_body=body,
        )

        # response: {"message": {"content": "..."}}
        return data.get("message", {}).get("content", "")


# ── Factory ──

_PROVIDERS: dict[str, type[LLMProvider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
    "ollama": OllamaProvider,
}


def create_provider(
    provider_name: str,
    *,
    api_key: str = "",
    base_url: str = "",
    model: str = "",
    timeout: float = 60.0,
    max_retries: int = 2,
) -> LLMProvider:
    """Create an LLM provider by name.

    Parameters
    ----------
    provider_name : str
        One of ``"anthropic"``, ``"openai"``, ``"gemini"``, ``"ollama"``.
    api_key : str
        Override API key (otherwise read from env).
    base_url : str
        Override base URL.
    model : str
        Override model name.
    timeout : float
        Request timeout in seconds.
    max_retries : int
        Maximum retry attempts.

    Returns
    -------
    LLMProvider
        Configured provider instance.

    Raises
    ------
    ValueError
        If *provider_name* is not recognized.
    """
    cls = _PROVIDERS.get(provider_name.lower())
    if cls is None:
        raise ValueError(
            f"Unknown provider: {provider_name!r}. "
            f"Available: {', '.join(sorted(_PROVIDERS))}",
        )

    config = ProviderConfig(
        api_key=api_key,
        base_url=base_url,
        model=model,
        timeout=timeout,
        max_retries=max_retries,
    )
    return cls(config)
