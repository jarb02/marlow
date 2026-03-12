"""Gemini Live API voice bridge for Marlow OS.

Bidirectional audio streaming via WebSocket to Gemini.
Gemini handles VAD, speech recognition, conversation, and voice output.
Tool calls executed via callback to daemon HTTP API.

Flow:
    mic → Gemini WebSocket → speaker (audio)
    Gemini → tool call → execute → result back to Gemini

/ Bridge de voz con Gemini Live API — audio bidireccional + function calling.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Callable

logger = logging.getLogger("marlow.bridges.voice.gemini_live")

# Audio format constants
CHANNELS = 1
SEND_SAMPLE_RATE = 16000    # Gemini expects 16kHz input
RECEIVE_SAMPLE_RATE = 24000  # Gemini outputs 24kHz
CHUNK_SIZE = 1024

# Session timeout: close if no response from Gemini for this long
SESSION_TIMEOUT_S = 30.0

from marlow.bridges.tools_schema import (
    build_tool_declarations,
    build_system_prompt,
    resolve_tool_call,
)


# ─────────────────────────────────────────────────────────────
# Result compaction (prevents WebSocket 1007 errors)
# ─────────────────────────────────────────────────────────────

_LIVE_MAX_RESULT_BYTES = 4096

_BINARY_TOOLS = {"take_screenshot", "get_annotated_screenshot", "visual_diff",
                 "visual_diff_compare", "cdp_screenshot", "cdp_get_dom"}


def _compact_result_for_live(tool_name: str, result: dict) -> dict:
    """Compact a tool result for Gemini Live WebSocket transport.

    Keeps results under _LIVE_MAX_RESULT_BYTES to prevent error 1007.
    """
    import json

    # Binary/large tools: return minimal summary
    if tool_name in _BINARY_TOOLS:
        compact = {"success": result.get("success", False)}
        for key in ("window_id", "screenshot_taken", "size_bytes", "note",
                     "error", "differences_found"):
            if key in result:
                compact[key] = result[key]
        if compact.get("success") and tool_name in ("take_screenshot",
                "get_annotated_screenshot", "cdp_screenshot"):
            compact["note"] = (
                "Screenshot captured successfully. "
                "Use ocr_region to read its text content, "
                "or describe what you found based on context."
            )
        return compact

    # Other tools: keep key fields, truncate large strings
    compact = {"success": result.get("success", False)}
    for key in ("error", "pid", "output", "windows", "result", "window_id",
                "launched", "note", "text", "count", "app_id", "title",
                "screenshot_path", "status"):
        if key in result:
            val = result[key]
            if isinstance(val, str) and len(val) > 500:
                val = val[:500] + "..."
            compact[key] = val

    if "windows" in compact and isinstance(compact["windows"], list):
        compact["windows"] = [
            {"id": w.get("id"), "title": (w.get("title") or "")[:80],
             "app": w.get("app_id", "")}
            for w in compact["windows"][:20]
        ]

    # Final size check
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


# ─────────────────────────────────────────────────────────────
# Gemini Live Voice Bridge
# ─────────────────────────────────────────────────────────────


class GeminiLiveVoiceBridge:
    """Bidirectional audio bridge to Gemini Live API.

    Handles mic → Gemini → speaker streaming with echo suppression
    and function calling for desktop actions.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "",
        voice: str = "",
        user_name: str = "",
        language: str = "es",
    ):
        self._api_key = api_key
        self._model = model or "gemini-2.5-flash-native-audio-preview-12-2025"
        self._voice = voice
        self._user_name = user_name or "amigo"
        self._language = language

        self._session = None
        self._is_active = False
        self._audio_out_queue: asyncio.Queue = asyncio.Queue()
        self._is_outputting = False       # Echo suppression flag
        self._output_end_time = 0.0       # Echo suppression buffer end
        self._stop_audio = threading.Event()
        self._last_response_time = 0.0    # Session timeout tracker

    def _build_config(self) -> dict:
        from google.genai import types

        config = {
            "response_modalities": ["AUDIO"],
            "system_instruction": build_system_prompt(self._user_name, self._language),
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
        """Run a complete conversation session. Blocks until session ends."""
        from google import genai

        client = genai.Client(api_key=self._api_key)
        config = self._build_config()

        logger.info("Session opened (model=%s)", self._model)

        async with client.aio.live.connect(
            model=self._model, config=config,
        ) as session:
            self._session = session
            self._is_active = True
            self._stop_audio.clear()
            self._last_response_time = time.monotonic()

            try:
                await self._stream_audio(session, tool_executor)
            except asyncio.CancelledError:
                logger.info("Session cancelled")
            except Exception as e:
                logger.error("Session error: %s", e)
            finally:
                self._is_active = False
                self._stop_audio.set()
                try:
                    await asyncio.wait_for(session.close(), timeout=3.0)
                except Exception:
                    pass
                self._session = None
                while not self._audio_out_queue.empty():
                    self._audio_out_queue.get_nowait()
                logger.info("Session closed")

    async def _stream_audio(self, session, tool_executor: Callable):
        """Core audio loop: send mic, receive audio + tool calls, play audio."""
        import pyaudio

        pya = pyaudio.PyAudio()

        mic_info = pya.get_default_input_device_info()
        mic_stream = pya.open(
            format=pyaudio.paInt16,
            channels=CHANNELS,
            rate=SEND_SAMPLE_RATE,
            input=True,
            input_device_index=int(mic_info["index"]),
            frames_per_buffer=CHUNK_SIZE,
        )

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
                    # Echo suppression: skip while Gemini audio is playing
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
            """Receive audio + function calls from Gemini."""
            while self._is_active:
                try:
                    turn = session.receive()
                    turn_iter = turn.__aiter__()
                    while self._is_active:
                        try:
                            response = await asyncio.wait_for(
                                turn_iter.__anext__(), timeout=2.0,
                            )
                        except asyncio.TimeoutError:
                            # Check session timeout
                            if time.monotonic() - self._last_response_time > SESSION_TIMEOUT_S:
                                logger.info(
                                    "Session timeout (%ds no response)",
                                    int(SESSION_TIMEOUT_S),
                                )
                                self._is_active = False
                                return
                            continue
                        except StopAsyncIteration:
                            break

                        if not self._is_active:
                            break

                        self._last_response_time = time.monotonic()

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

                        # Interruption — clear playback queue
                        if (
                            response.server_content
                            and response.server_content.interrupted
                        ):
                            while not self._audio_out_queue.empty():
                                self._audio_out_queue.get_nowait()

                        # Function calls from Gemini
                        if response.tool_call:
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
            """Play audio from Gemini through speakers (echo suppression flag)."""
            while self._is_active:
                try:
                    data = await asyncio.wait_for(
                        self._audio_out_queue.get(), timeout=0.5,
                    )
                    self._is_outputting = True
                    await asyncio.to_thread(speaker_stream.write, data)
                    if self._audio_out_queue.empty():
                        self._is_outputting = False
                        self._output_end_time = time.monotonic() + 0.3
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
            self._is_active = False
            self._stop_audio.set()
            for t in pending:
                t.cancel()
            try:
                await asyncio.wait_for(
                    asyncio.gather(*pending, return_exceptions=True),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                logger.warning("Audio tasks did not finish within 5s")
        finally:
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
        """Thread-safe mic read — returns None when stop is signaled."""
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
            logger.info("Tool call: %s(%s)", fc.name, fc.args)

            real_name, real_args = resolve_tool_call(fc.name, fc.args or {})
            try:
                result = await tool_executor(real_name, real_args)
                compact = _compact_result_for_live(real_name, result)
                function_responses.append(
                    types.FunctionResponse(
                        id=fc.id,
                        name=fc.name,
                        response={"result": compact},
                    ),
                )
                logger.info(
                    "Tool result: %s -> success=%s",
                    fc.name, result.get("success", "?"),
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

    def stop(self):
        """Signal the session to end and close the WebSocket."""
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
