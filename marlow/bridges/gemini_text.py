"""Gemini text bridge for Marlow OS.

Handles non-voice text interaction with Gemini API (regular, not Live).
Uses the SAME tools and system prompt as the Gemini Live voice bridge.
Maintains multi-turn conversation history with 30-minute inactivity timeout.

Architecture:
    sidebar/telegram/console text -> GeminiTextBridge -> Gemini API
    -> function calls for desktop actions -> natural language response

/ Bridge de texto con Gemini API — mismos tools que voz.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Callable, Awaitable, Optional

logger = logging.getLogger("marlow.bridges.gemini_text")

# Maximum function call rounds per message (prevents infinite loops)
_MAX_TOOL_ROUNDS = 8

# Chat session timeout (30 minutes of inactivity)
_SESSION_TIMEOUT = 1800


class GeminiTextBridge:
    """Text interaction bridge using Gemini API with function calling.

    Same tools and personality as GeminiLiveVoiceBridge, but for text.
    Gemini decides everything: greetings, questions, desktop actions.
    """

    def __init__(
        self,
        api_key: str,
        tool_executor: Callable[[str, dict], Awaitable[dict]],
        user_name: str = "",
        language: str = "es",
        model: str = "",
    ):
        """
        Parameters
        ----------
        api_key : str
            Gemini API key.
        tool_executor : callable
            async callable(tool_name, args) -> dict. Executes desktop tools.
        user_name : str
            User name for system prompt.
        language : str
            ISO language code (es, en, etc.).
        model : str
            Gemini model ID. Default: gemini-2.5-flash.
        """
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self._tool_executor = tool_executor
        self._model = model or "gemini-2.5-flash"
        self._user_name = user_name
        self._language = language
        self._chat = None
        self._last_activity: float = 0.0

    def _create_chat(self):
        """Create a new chat session with tools and system prompt."""
        from google.genai import types
        from marlow.bridges.tools_schema import (
            build_system_prompt,
            build_tool_declarations,
        )

        config = types.GenerateContentConfig(
            system_instruction=build_system_prompt(
                self._user_name, self._language,
            ),
            tools=build_tool_declarations(),
            temperature=0.7,
            max_output_tokens=500,
        )

        self._chat = self._client.aio.chats.create(
            model=self._model,
            config=config,
        )
        self._last_activity = time.time()
        logger.info("New Gemini chat session (model=%s)", self._model)

    def _ensure_chat(self):
        """Ensure a chat session exists, reset if timed out."""
        now = time.time()
        if self._chat is None or (now - self._last_activity > _SESSION_TIMEOUT):
            if self._chat is not None:
                logger.info("Chat session timed out (%.0fm idle), resetting",
                            (now - self._last_activity) / 60)
            self._create_chat()
        self._last_activity = now

    async def send_message(self, text: str) -> str:
        """Send a text message to Gemini and return the response.

        Gemini may call tools (function calling) to execute desktop actions.
        The tool call loop runs until Gemini returns a text response.

        Returns the final text response from Gemini.
        """
        from marlow.bridges.tools_schema import resolve_tool_call

        self._ensure_chat()

        try:
            response = await self._chat.send_message(text)
        except Exception as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                # Rate limit — retry once after delay (do not reset chat)
                logger.warning("Gemini rate limited, retrying in 5s...")
                await asyncio.sleep(5)
                try:
                    response = await self._chat.send_message(text)
                except Exception as e2:
                    logger.error("Gemini retry also failed: %s", e2)
                    raise
            else:
                logger.error("Gemini send_message error: %s", e)
                self._chat = None
                raise

        # Process function calls in a loop
        for round_num in range(_MAX_TOOL_ROUNDS):
            # Extract function calls from response
            function_calls = self._extract_function_calls(response)
            if not function_calls:
                break

            # Execute each tool call
            from google.genai import types
            function_responses = []

            for fc in function_calls:
                tool_name = fc.name
                tool_args = dict(fc.args) if fc.args else {}

                # Resolve aliases (close_window -> manage_window, etc.)
                real_name, real_args = resolve_tool_call(tool_name, tool_args)

                logger.info(
                    "Gemini tool call [round %d]: %s(%s)%s",
                    round_num + 1, tool_name, tool_args,
                    f" -> {real_name}({real_args})" if real_name != tool_name else "",
                )

                try:
                    result = await self._tool_executor(real_name, real_args)
                except Exception as e:
                    logger.error("Tool execution error (%s): %s", real_name, e)
                    result = {"success": False, "error": str(e)}

                logger.info(
                    "Tool result: %s -> success=%s",
                    tool_name, result.get("success", "?"),
                )

                # Build function response
                # Compact the result for Gemini (avoid huge payloads)
                compact = self._compact_result(result)

                function_responses.append(
                    types.Part.from_function_response(
                        name=tool_name,  # Use original name, not alias
                        response=compact,
                    )
                )

            # Send tool results back to Gemini
            try:
                response = await self._chat.send_message(function_responses)
            except Exception as e:
                if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                    logger.warning("Gemini rate limited on tool response, retrying in 5s...")
                    await asyncio.sleep(5)
                    try:
                        response = await self._chat.send_message(function_responses)
                    except Exception as e2:
                        logger.error("Gemini retry also failed: %s", e2)
                        raise
                else:
                    logger.error("Gemini tool response error: %s", e)
                    self._chat = None
                    raise

        # Extract text response
        response_text = self._extract_text(response)
        if not response_text:
            response_text = "Listo." if self._language == "es" else "Done."

        return response_text

    def _extract_function_calls(self, response) -> list:
        """Extract function call parts from a Gemini response."""
        calls = []
        try:
            if not response.candidates:
                return calls
            for part in response.candidates[0].content.parts:
                if hasattr(part, "function_call") and part.function_call:
                    calls.append(part.function_call)
        except (AttributeError, IndexError, TypeError):
            pass
        return calls

    def _extract_text(self, response) -> str:
        """Extract text from a Gemini response."""
        try:
            return response.text or ""
        except (AttributeError, ValueError):
            # .text raises ValueError if response has no text parts
            try:
                parts = response.candidates[0].content.parts
                texts = [p.text for p in parts if hasattr(p, "text") and p.text]
                return " ".join(texts)
            except (AttributeError, IndexError, TypeError):
                return ""

    def _compact_result(self, result: dict) -> dict:
        """Compact a tool result to avoid huge payloads to Gemini."""
        compact = {"success": result.get("success", False)}

        # Include key fields
        for key in ("error", "pid", "output", "windows", "result",
                     "screenshot_path", "text"):
            if key in result:
                val = result[key]
                # Truncate long strings
                if isinstance(val, str) and len(val) > 500:
                    val = val[:500] + "..."
                compact[key] = val

        # For list_windows, keep window list compact
        if "windows" in compact and isinstance(compact["windows"], list):
            compact["windows"] = [
                {"id": w.get("id"), "title": w.get("title", "")[:80],
                 "app": w.get("app_id", "")}
                for w in compact["windows"][:20]
            ]

        return compact

    def reset_chat(self):
        """Force reset the chat session (e.g. sidebar disconnect)."""
        self._chat = None
        logger.info("Chat session reset")

    @property
    def is_ready(self) -> bool:
        """True if the bridge is initialized and ready."""
        return self._client is not None
