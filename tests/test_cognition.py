"""Tests for marlow.kernel.cognition (providers, planner, factory).

All tests mock httpx.AsyncClient — zero real API calls.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from marlow.kernel.cognition.providers import (
    AnthropicProvider,
    GeminiProvider,
    LLMProviderError,
    OllamaProvider,
    OpenAIProvider,
    ProviderConfig,
    create_provider,
)
from marlow.kernel.cognition.planner import LLMPlanner
from marlow.kernel.planning.tool_filter import ToolFilter


# ── Helpers ──


def _mock_response(status_code: int = 200, json_data: dict = None):
    """Build a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = json.dumps(json_data or {})
    resp.json.return_value = json_data or {}
    return resp


def _anthropic_success():
    return {
        "content": [{"type": "text", "text": '{"steps": []}'}],
    }


def _openai_success():
    return {
        "choices": [{"message": {"content": '{"steps": []}'}}],
    }


def _gemini_success():
    return {
        "candidates": [{
            "content": {
                "parts": [{"text": '{"steps": []}'}],
            },
        }],
    }


def _ollama_success():
    return {
        "message": {"content": '{"steps": []}'},
    }


def _valid_plan_json():
    """Return a valid plan JSON string that PlanParser can parse."""
    return json.dumps({
        "steps": [
            {
                "id": "step_1",
                "tool_name": "open_application",
                "params": {"app_name": "notepad"},
                "description": "Open Notepad",
                "risk": "low",
                "estimated_duration_ms": 3000,
            },
        ],
        "context": {"target_app": "notepad.exe"},
    })


# ── TestAnthropicProvider ──


class TestAnthropicProvider:
    """Anthropic Messages API provider tests."""

    @pytest.mark.asyncio
    async def test_success(self):
        provider = AnthropicProvider(
            ProviderConfig(api_key="test-key-123"),
        )
        mock_resp = _mock_response(200, _anthropic_success())

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await provider.generate(
                [{"role": "user", "content": "Hello"}],
                system="You are helpful.",
            )

        assert result == '{"steps": []}'
        call_args = mock_client.request.call_args
        assert call_args[0][0] == "POST"
        body = call_args[1]["json"]
        assert body["system"] == "You are helpful."
        assert body["model"] == "claude-sonnet-4-20250514"
        headers = call_args[1]["headers"]
        assert headers["x-api-key"] == "test-key-123"
        assert headers["anthropic-version"] == "2023-06-01"

    @pytest.mark.asyncio
    async def test_missing_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        provider = AnthropicProvider(ProviderConfig(api_key=""))
        with pytest.raises(LLMProviderError, match="ANTHROPIC_API_KEY"):
            await provider.generate([{"role": "user", "content": "Hi"}])

    @pytest.mark.asyncio
    async def test_rate_limit_retry(self):
        """429 triggers retry, then succeeds on second attempt."""
        provider = AnthropicProvider(
            ProviderConfig(api_key="key", max_retries=1),
        )
        resp_429 = _mock_response(429, {"error": "rate limited"})
        resp_ok = _mock_response(200, _anthropic_success())

        with patch("httpx.AsyncClient") as mock_cls, \
             patch("asyncio.sleep", new_callable=AsyncMock):
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(
                side_effect=[resp_429, resp_ok],
            )
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await provider.generate(
                [{"role": "user", "content": "Hi"}],
            )
        assert result == '{"steps": []}'

    @pytest.mark.asyncio
    async def test_timeout_retry(self):
        """Timeout triggers retry, then succeeds."""
        provider = AnthropicProvider(
            ProviderConfig(api_key="key", timeout=1.0, max_retries=1),
        )
        resp_ok = _mock_response(200, _anthropic_success())

        with patch("httpx.AsyncClient") as mock_cls, \
             patch("asyncio.sleep", new_callable=AsyncMock):
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(
                side_effect=[httpx.TimeoutException("timeout"), resp_ok],
            )
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await provider.generate(
                [{"role": "user", "content": "Hi"}],
            )
        assert result == '{"steps": []}'


# ── TestOpenAIProvider ──


class TestOpenAIProvider:
    """OpenAI Chat Completions API provider tests."""

    @pytest.mark.asyncio
    async def test_success(self):
        provider = OpenAIProvider(
            ProviderConfig(api_key="sk-test"),
        )
        mock_resp = _mock_response(200, _openai_success())

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await provider.generate(
                [{"role": "user", "content": "Hello"}],
                system="Be concise.",
            )

        assert result == '{"steps": []}'
        call_args = mock_client.request.call_args
        body = call_args[1]["json"]
        # System message should be first
        assert body["messages"][0] == {
            "role": "system", "content": "Be concise.",
        }
        assert body["model"] == "gpt-4o"
        headers = call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer sk-test"

    @pytest.mark.asyncio
    async def test_missing_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        provider = OpenAIProvider(ProviderConfig(api_key=""))
        with pytest.raises(LLMProviderError, match="OPENAI_API_KEY"):
            await provider.generate([{"role": "user", "content": "Hi"}])


# ── TestGeminiProvider ──


class TestGeminiProvider:
    """Google Gemini generateContent API provider tests."""

    @pytest.mark.asyncio
    async def test_success(self):
        provider = GeminiProvider(
            ProviderConfig(api_key="gemini-key"),
        )
        mock_resp = _mock_response(200, _gemini_success())

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await provider.generate(
                [{"role": "user", "content": "Hello"}],
            )

        assert result == '{"steps": []}'
        call_args = mock_client.request.call_args
        url = call_args[0][1]
        assert "gemini-2.0-flash:generateContent" in url
        assert call_args[1]["params"] == {"key": "gemini-key"}

    @pytest.mark.asyncio
    async def test_missing_key(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        provider = GeminiProvider(ProviderConfig(api_key=""))
        with pytest.raises(LLMProviderError, match="GEMINI_API_KEY"):
            await provider.generate([{"role": "user", "content": "Hi"}])

    @pytest.mark.asyncio
    async def test_system_instruction(self):
        """System prompt sent as systemInstruction field."""
        provider = GeminiProvider(
            ProviderConfig(api_key="key"),
        )
        mock_resp = _mock_response(200, _gemini_success())

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            await provider.generate(
                [{"role": "user", "content": "Hi"}],
                system="Be helpful.",
            )

        body = mock_client.request.call_args[1]["json"]
        assert body["systemInstruction"] == {
            "parts": [{"text": "Be helpful."}],
        }


# ── TestOllamaProvider ──


class TestOllamaProvider:
    """Ollama local API provider tests."""

    @pytest.mark.asyncio
    async def test_success(self):
        provider = OllamaProvider()
        mock_resp = _mock_response(200, _ollama_success())

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await provider.generate(
                [{"role": "user", "content": "Hello"}],
                system="You are Marlow.",
            )

        assert result == '{"steps": []}'
        body = mock_client.request.call_args[1]["json"]
        assert body["model"] == "llama3.1"
        assert body["stream"] is False
        # System as first message
        assert body["messages"][0] == {
            "role": "system", "content": "You are Marlow.",
        }

    @pytest.mark.asyncio
    async def test_no_key_needed(self):
        """Ollama doesn't require an API key."""
        provider = OllamaProvider()
        assert provider._config.api_key == ""
        # Should not raise on missing key
        mock_resp = _mock_response(200, _ollama_success())

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await provider.generate(
                [{"role": "user", "content": "Test"}],
            )
        assert isinstance(result, str)


# ── TestCreateProvider ──


class TestCreateProvider:
    """Factory function tests."""

    def test_anthropic(self):
        p = create_provider("anthropic", api_key="k")
        assert isinstance(p, AnthropicProvider)
        assert p.name == "anthropic"

    def test_openai(self):
        p = create_provider("openai", api_key="k")
        assert isinstance(p, OpenAIProvider)
        assert p.name == "openai"

    def test_gemini(self):
        p = create_provider("gemini", api_key="k")
        assert isinstance(p, GeminiProvider)
        assert p.name == "gemini"

    def test_ollama(self):
        p = create_provider("ollama")
        assert isinstance(p, OllamaProvider)
        assert p.name == "ollama"

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown provider"):
            create_provider("grok")


# ── TestLLMPlanner ──


class TestLLMPlanner:
    """LLMPlanner integration tests (mocked LLM)."""

    @pytest.mark.asyncio
    async def test_plan_generation(self):
        """Full pipeline: provider → parse → Plan."""
        mock_provider = AsyncMock(spec=AnthropicProvider)
        mock_provider.name = "anthropic"
        mock_provider.generate = AsyncMock(return_value=_valid_plan_json())

        planner = LLMPlanner(
            provider=mock_provider,
            tool_filter=ToolFilter(),
        )

        plan = await planner("Open Notepad", {})

        assert plan is not None
        assert len(plan.steps) == 1
        assert plan.steps[0].tool_name == "open_application"
        assert plan.steps[0].params == {"app_name": "notepad"}
        mock_provider.generate.assert_called_once()

    @pytest.mark.asyncio
    async def test_replan_template(self):
        """Replan context switches to REPLAN_USER template."""
        mock_provider = AsyncMock(spec=AnthropicProvider)
        mock_provider.name = "anthropic"
        mock_provider.generate = AsyncMock(return_value=_valid_plan_json())

        planner = LLMPlanner(
            provider=mock_provider,
            tool_filter=ToolFilter(),
        )

        context = {
            "replan": True,
            "completed_steps": [
                {"id": "step_1", "description": "Opened Notepad"},
            ],
            "failed_step": "Type hello",
            "error": "Element not found",
        }

        plan = await planner("Open Notepad and type hello", context)

        assert plan is not None
        # Verify the user message contains replan-specific content
        call_args = mock_provider.generate.call_args
        user_msg = call_args[0][0][0]["content"]
        assert "COMPLETED STEPS" in user_msg
        assert "FAILED STEP" in user_msg
        assert "Element not found" in user_msg

    @pytest.mark.asyncio
    async def test_invalid_response(self):
        """LLM returns garbage → LLMProviderError."""
        mock_provider = AsyncMock(spec=AnthropicProvider)
        mock_provider.name = "anthropic"
        mock_provider.generate = AsyncMock(
            return_value="This is not JSON at all",
        )

        planner = LLMPlanner(
            provider=mock_provider,
            tool_filter=ToolFilter(),
        )

        with pytest.raises(LLMProviderError, match="Failed to parse"):
            await planner("Do something", {})

    @pytest.mark.asyncio
    async def test_tool_filter_applied(self):
        """ToolFilter narrows tools in system prompt."""
        mock_provider = AsyncMock(spec=AnthropicProvider)
        mock_provider.name = "anthropic"
        mock_provider.generate = AsyncMock(return_value=_valid_plan_json())

        # Only register a few tools
        tool_filter = ToolFilter(all_tools=["click", "type_text", "open_application"])

        planner = LLMPlanner(
            provider=mock_provider,
            tool_filter=tool_filter,
        )

        await planner("Open Notepad", {})

        # System prompt should contain filtered tool list
        call_args = mock_provider.generate.call_args
        system = call_args[1]["system"]
        assert "click" in system
        assert "type_text" in system
        # Tools not in the filter should be absent
        assert "cdp_evaluate" not in system
