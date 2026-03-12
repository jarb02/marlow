"""Gemini Live API voice bridge for Marlow OS.

Streams audio bidirectionally via WebSocket to Gemini 2.5 Flash.
Gemini handles VAD, speech recognition, conversation, and voice output.
Marlow handles desktop actions via function calls.

Architecture:
    mic -> PipeWire -> Gemini Live WebSocket -> function calls for actions -> speaker
    Wake word detection stays local (OpenWakeWord).

/ Bridge de voz con Gemini Live API — audio bidireccional + function calling.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Optional, Callable, Awaitable

logger = logging.getLogger("marlow.bridges.voice.gemini_live")

# Audio format constants
CHANNELS = 1
SEND_SAMPLE_RATE = 16000    # Gemini expects 16kHz input
RECEIVE_SAMPLE_RATE = 24000  # Gemini outputs 24kHz
CHUNK_SIZE = 1024


# Tools and system prompt imported from shared schema
from marlow.bridges.tools_schema import (
    build_tool_declarations,
    build_system_prompt,
    resolve_tool_call,
)


# ─────────────────────────────────────────────────────────────
# Gemini Live Voice Bridge
# ─────────────────────────────────────────────────────────────


# Max payload size for Gemini Live WebSocket function responses.
# Larger results (screenshots, DOM dumps, etc.) crash the connection
# with error 1007 "invalid frame payload data".
_LIVE_MAX_RESULT_BYTES = 4096

# Tools whose output is binary/large and should be replaced with a summary
_BINARY_TOOLS = {"take_screenshot", "get_annotated_screenshot", "visual_diff",
                 "visual_diff_compare", "cdp_screenshot", "cdp_get_dom"}


def _compact_result_for_live(tool_name: str, result: dict) -> dict:
    """Compact a tool result for Gemini Live WebSocket transport.

    Gemini Live has strict WebSocket frame limits. Screenshots (base64 PNG)
    and other large payloads cause error 1007. This function:
    1. Replaces binary/large tool results with a short summary
    2. Truncates any remaining oversized string fields
    3. Keeps the result under _LIVE_MAX_RESULT_BYTES
    """
    import json

    # For known binary tools, return a minimal summary
    if tool_name in _BINARY_TOOLS:
        compact = {"success": result.get("success", False)}
        # Keep useful metadata
        for key in ("window_id", "screenshot_taken", "size_bytes", "note",
                     "error", "differences_found"):
            if key in result:
                compact[key] = result[key]
        # Add guidance for the model
        if compact.get("success") and tool_name in ("take_screenshot",
                "get_annotated_screenshot", "cdp_screenshot"):
            compact["note"] = (
                "Screenshot captured successfully. "
                "Use ocr_region to read its text content, "
                "or describe what you found based on context."
            )
        return compact

    # For all other tools, check total size
    compact = {"success": result.get("success", False)}
    for key in ("error", "pid", "output", "windows", "result", "window_id",
                "launched", "note", "text", "count", "app_id", "title",
                "screenshot_path", "status"):
        if key in result:
            val = result[key]
            if isinstance(val, str) and len(val) > 500:
                val = val[:500] + "..."
            compact[key] = val

    # Compact window lists
    if "windows" in compact and isinstance(compact["windows"], list):
        compact["windows"] = [
            {"id": w.get("id"), "title": (w.get("title") or "")[:80],
             "app": w.get("app_id", "")}
            for w in compact["windows"][:20]
        ]

    # Final size check — if still too large, truncate aggressively
    try:
        serialized = json.dumps(compact, default=str)
        if len(serialized) > _LIVE_MAX_RESULT_BYTES:
            compact = {
                "success": result.get("success", False),
                "note": "Result too large for voice. Ask user to check screen.",
            }
            if result.get("error"):
                compact["error"] = str(result["error"])[:200]
    except Exception:
        compact = {"success": result.get("success", False),
                   "note": "Result could not be serialized for voice channel."}

    return compact


class GeminiLiveVoiceBridge:
    """Voice bridge using Gemini Live API for natural streaming conversation.

    Handles:
    - Bidirectional audio streaming (mic <-> Gemini)
    - Automatic VAD and turn-taking (Gemini-side)
    - Function calling for desktop actions
    - Transcript broadcasting to sidebar
    """

    def __init__(
        self,
        api_key: str,
        model: str = "",
        voice: str = "",
        user_name: str = "",
        language: str = "es",
        on_transcript: Optional[Callable[[str, str], Awaitable[None]]] = None,
        context_builder: Optional[Callable[[], str]] = None,
    ):
        """
        Parameters
        ----------
        api_key : str
            Gemini API key.
        model : str
            Model ID. Default: gemini-2.5-flash-native-audio-preview.
        voice : str
            Voice name (Puck, Kore, Charon, etc.). Empty = default.
        user_name : str
            User's name for the system prompt.
        language : str
            ISO language code (es, en, pt, etc.).
        on_transcript : callable
            async callback(role, text) for transcript updates.
        context_builder : callable or None
            A ``() -> str`` that returns dynamic context. Injected before tool calls.
        """
        self._api_key = api_key
        self._model = model or "gemini-2.5-flash-native-audio-preview-12-2025"
        self._voice = voice
        self._user_name = user_name or "amigo"
        self._language = language
        self._on_transcript = on_transcript

        self._session = None
        self._is_active = False
        self._audio_out_queue: asyncio.Queue = asyncio.Queue()
        self._is_outputting = False  # True while playing audio (echo suppression)
        self._output_end_time = 0.0  # timestamp when output stopped + buffer
        self._stop_audio = threading.Event()
        self._context_builder = context_builder

    def _build_system_prompt(self) -> str:
        return build_system_prompt(self._user_name, self._language)

    def _build_config(self) -> dict:
        from google.genai import types

        config: dict = {
            "response_modalities": ["AUDIO"],
            "system_instruction": self._build_system_prompt(),
            "tools": build_tool_declarations(),
            "context_window_compression": types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow(),
            ),
        }

        if self._voice:
            config["speech_config"] = types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=self._voice,
                    ),
                ),
            )

        return config

    # ── Session lifecycle ──

    async def run_session(self, tool_executor: Callable):
        """Run a complete conversation session.

        Blocks until the session ends (goodbye, timeout, error, or stop()).

        Parameters
        ----------
        tool_executor : callable
            async callable(tool_name: str, args: dict) -> dict
            Executes Marlow desktop tools and returns result dict.
        """
        from google import genai

        client = genai.Client(api_key=self._api_key)
        config = self._build_config()

        logger.info("Opening Gemini Live session (model=%s)", self._model)

        async with client.aio.live.connect(
            model=self._model, config=config,
        ) as session:
            self._session = session
            self._is_active = True
            self._stop_audio.clear()

            try:
                # Inject dynamic context once at session start
                if self._context_builder:
                    try:
                        ctx = self._context_builder()
                        if ctx:
                            from marlow.kernel.adapters import inject_context_gemini_live
                            turns = inject_context_gemini_live(ctx)
                            await session.send_client_content(
                                turns=turns, turn_complete=True,
                            )
                            logger.debug("Initial context injected at session start")
                    except Exception as e:
                        logger.debug("Initial context injection error: %s", e)

                await self._stream_audio(session, tool_executor)
            except asyncio.CancelledError:
                logger.info("Gemini session cancelled")
            except Exception as e:
                logger.error("Gemini session error: %s", e)
            finally:
                self._is_active = False
                self._stop_audio.set()
                # Ensure WebSocket is closed (session will exit context manager,
                # but close explicitly first for immediate cleanup)
                try:
                    await asyncio.wait_for(session.close(), timeout=3.0)
                except Exception:
                    pass
                self._session = None
                # Drain playback queue
                while not self._audio_out_queue.empty():
                    self._audio_out_queue.get_nowait()
                logger.info("Gemini Live session ended")

    async def _stream_audio(self, session, tool_executor: Callable):
        """Core audio streaming loop — sends mic, receives audio + tool calls."""
        import pyaudio

        pya = pyaudio.PyAudio()

        # Open mic input
        mic_info = pya.get_default_input_device_info()
        mic_stream = pya.open(
            format=pyaudio.paInt16,
            channels=CHANNELS,
            rate=SEND_SAMPLE_RATE,
            input=True,
            input_device_index=int(mic_info["index"]),
            frames_per_buffer=CHUNK_SIZE,
        )

        # Open speaker output
        speaker_stream = pya.open(
            format=pyaudio.paInt16,
            channels=CHANNELS,
            rate=RECEIVE_SAMPLE_RATE,
            output=True,
        )

        async def send_audio():
            """Stream mic audio to Gemini (with echo suppression)."""
            while self._is_active:
                try:
                    data = await asyncio.to_thread(
                        self._read_mic_safe, mic_stream,
                    )
                    if data is None:
                        break
                    # Echo suppression: skip sending while Gemini audio is playing
                    # (plus 300ms buffer after output stops)
                    if self._is_outputting or time.monotonic() < self._output_end_time:
                        continue
                    await session.send_realtime_input(
                        audio={"data": data, "mime_type": "audio/pcm"},
                    )
                except Exception as e:
                    if self._is_active:
                        logger.error("Send audio error: %s", e)
                    break

        async def receive_responses():
            """Receive audio + function calls from Gemini.

            Uses anext() with timeout instead of bare async-for so that
            stop() / session.close() can unblock the iterator promptly.
            """
            while self._is_active:
                try:
                    turn = session.receive()
                    turn_iter = turn.__aiter__()
                    turn_has_tool_calls = False
                    turn_text_parts = []  # Buffer text until we know if turn has tools
                    while self._is_active:
                        try:
                            response = await asyncio.wait_for(
                                turn_iter.__anext__(), timeout=2.0,
                            )
                        except asyncio.TimeoutError:
                            # Re-check _is_active on timeout
                            continue
                        except StopAsyncIteration:
                            # Turn ended — flush text if conversational (no tool calls)
                            if turn_text_parts and not turn_has_tool_calls and self._on_transcript:
                                combined = " ".join(turn_text_parts)
                                if combined.strip():
                                    try:
                                        await self._on_transcript("marlow", combined)
                                    except Exception:
                                        pass
                            turn_has_tool_calls = False
                            turn_text_parts = []
                            break

                        if not self._is_active:
                            break

                        # Audio output from model
                        if (
                            response.server_content
                            and response.server_content.model_turn
                        ):
                            for part in response.server_content.model_turn.parts:
                                if (
                                    part.inline_data
                                    and isinstance(part.inline_data.data, bytes)
                                ):
                                    self._audio_out_queue.put_nowait(
                                        part.inline_data.data,
                                    )
                                # Buffer text for end-of-turn decision
                                # (don't emit CoT reasoning during tool-call turns)
                                if part.text:
                                    turn_text_parts.append(part.text)

                        # Interruption — clear playback queue
                        if (
                            response.server_content
                            and response.server_content.interrupted
                        ):
                            while not self._audio_out_queue.empty():
                                self._audio_out_queue.get_nowait()

                        # Function calls from Gemini
                        if response.tool_call:
                            turn_has_tool_calls = True
                            await self._handle_tool_calls(
                                session, response.tool_call, tool_executor,
                            )

                        # Session ending signal
                        if response.go_away is not None:
                            logger.info("Gemini go_away received")
                            self._is_active = False
                            break

                except Exception as e:
                    if self._is_active:
                        logger.error("Receive error: %s", e)
                    break

        async def play_audio():
            """Play audio from Gemini through speakers (sets echo suppression flag)."""
            while self._is_active:
                try:
                    data = await asyncio.wait_for(
                        self._audio_out_queue.get(), timeout=0.5,
                    )
                    self._is_outputting = True
                    await asyncio.to_thread(speaker_stream.write, data)
                    # If queue is empty after this chunk, mark output as stopped
                    if self._audio_out_queue.empty():
                        self._is_outputting = False
                        self._output_end_time = time.monotonic() + 0.3  # 300ms buffer
                except asyncio.TimeoutError:
                    if self._is_outputting:
                        self._is_outputting = False
                        self._output_end_time = time.monotonic() + 0.3
                    continue
                except Exception as e:
                    self._is_outputting = False
                    if self._is_active:
                        logger.error("Play audio error: %s", e)
                    break

        tasks = [
            asyncio.create_task(send_audio()),
            asyncio.create_task(receive_responses()),
            asyncio.create_task(play_audio()),
        ]

        try:
            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED,
            )
            # If receive ended (session closed), stop everything
            self._is_active = False
            self._stop_audio.set()
            for t in pending:
                t.cancel()
            # Timeout: don't wait forever for tasks to finish
            try:
                await asyncio.wait_for(
                    asyncio.gather(*pending, return_exceptions=True),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                logger.warning("Audio tasks did not finish within 5s, forcing cleanup")
        finally:
            # Wait for mic reader thread to exit before closing stream
            await asyncio.sleep(0.2)
            try:
                mic_stream.stop_stream()
                mic_stream.close()
            except Exception:
                pass
            try:
                speaker_stream.stop_stream()
                speaker_stream.close()
            except Exception:
                pass
            try:
                pya.terminate()
            except Exception:
                pass

    def _read_mic_safe(self, stream):
        """Thread-safe mic read -- returns None when stop is signaled."""
        if self._stop_audio.is_set():
            return None
        try:
            return stream.read(CHUNK_SIZE, exception_on_overflow=False)
        except Exception:
            return None

    async def _handle_tool_calls(self, session, tool_call, tool_executor: Callable):
        """Execute function calls and return results to Gemini."""
        from google.genai import types

        function_responses = []
        for fc in tool_call.function_calls:
            logger.info("Gemini tool call: %s(%s)", fc.name, fc.args)

            # Resolve aliases (close_window -> manage_window, etc.)
            real_name, real_args = resolve_tool_call(fc.name, fc.args or {})
            try:
                result = await tool_executor(real_name, real_args)
                # Compact result for WebSocket transport (prevents 1007 errors)
                compact = _compact_result_for_live(real_name, result)
                function_responses.append(
                    types.FunctionResponse(
                        id=fc.id,
                        name=fc.name,
                        response={"result": compact},
                    ),
                )
                logger.info(
                    "Tool result: %s -> success=%s (compact %d bytes)",
                    fc.name, result.get("success", "?"),
                    len(str(compact)),
                )
            except Exception as e:
                logger.error("Tool execution error: %s: %s", fc.name, e)
                function_responses.append(
                    types.FunctionResponse(
                        id=fc.id,
                        name=fc.name,
                        response={
                            "success": False,
                            "error": "The operation could not be completed. Try again.",
                        },
                    ),
                )

        if function_responses:
            await session.send_tool_response(
                function_responses=function_responses,
            )

    # ── External control ──

    async def send_text(self, text: str) -> bool:
        """Send text input to the active session (sidebar integration).

        Returns True if sent successfully.
        """
        if not self._session or not self._is_active:
            return False
        try:
            await self._session.send_client_content(
                turns={"role": "user", "parts": [{"text": text}]},
                turn_complete=True,
            )
            if self._on_transcript:
                await self._on_transcript("user", text)
            return True
        except Exception as e:
            logger.error("Send text error: %s", e)
            return False

    async def stop_async(self):
        """Signal the session to end and close the WebSocket."""
        self._is_active = False
        self._stop_audio.set()
        session = self._session
        if session:
            try:
                await session.close()
            except Exception:
                pass

    def stop(self):
        """Signal the session to end (sync wrapper).

        Closes the WebSocket to unblock receive_responses().
        """
        self._is_active = False
        self._stop_audio.set()
        session = self._session
        if session:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self._close_session(session))
                else:
                    loop.run_until_complete(self._close_session(session))
            except Exception:
                pass

    async def _close_session(self, session):
        """Close session with timeout."""
        try:
            await asyncio.wait_for(session.close(), timeout=3.0)
        except Exception:
            pass

    @property
    def is_active(self) -> bool:
        """True if a Gemini session is currently active."""
        return self._is_active
