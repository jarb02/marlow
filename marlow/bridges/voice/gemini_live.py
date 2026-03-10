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
                await self._stream_audio(session, tool_executor)
            except asyncio.CancelledError:
                logger.info("Gemini session cancelled")
            except Exception as e:
                logger.error("Gemini session error: %s", e)
            finally:
                self._is_active = False
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
            """Receive audio + function calls from Gemini."""
            while self._is_active:
                try:
                    turn = session.receive()
                    async for response in turn:
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
                                # Text transcript from model
                                if part.text and self._on_transcript:
                                    try:
                                        await self._on_transcript(
                                            "marlow", part.text,
                                        )
                                    except Exception:
                                        pass

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
            await asyncio.gather(*pending, return_exceptions=True)
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
                function_responses.append(
                    types.FunctionResponse(
                        id=fc.id,
                        name=fc.name,
                        response={"result": result},
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

    def stop(self):
        """Signal the session to end."""
        self._is_active = False
        self._stop_audio.set()

    @property
    def is_active(self) -> bool:
        """True if a Gemini session is currently active."""
        return self._is_active
