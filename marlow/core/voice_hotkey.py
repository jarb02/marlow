"""
Marlow Voice Hotkey

Background process that listens for:
- Ctrl+Shift+M → start recording speech
- Ctrl+Shift+N → stop recording manually (skip silence detection)

Records speech with chunk-based VAD (voice activity detection),
transcribes via faster-whisper, and types the result into the
active MCP client window. Opens voice overlay automatically.

Not an MCP tool itself (except get_voice_hotkey_status for querying state).
Started automatically from server.py main().

/ Proceso background con Ctrl+Shift+M (grabar) y Ctrl+Shift+N (parar).
/ Graba voz con VAD, transcribe, escribe en cliente MCP. Abre overlay.
"""

import time
import asyncio
import logging
import ctypes
import threading
import winsound
import numpy as np
from typing import Optional, Callable
from pathlib import Path

logger = logging.getLogger("marlow.core.voice_hotkey")

# ── Module state ──
_hotkey_active: bool = False
_recording: bool = False
_manual_stop: bool = False  # set by Ctrl+Shift+N to stop recording early
_hotkey_combo: str = "ctrl+shift+m"
_stop_combo: str = "ctrl+shift+n"
_last_text: Optional[str] = None
_last_error: Optional[str] = None
_kill_switch_check: Optional[Callable] = None
_hotkey_handle: Optional[object] = None
_stop_handle: Optional[object] = None
_saved_hwnd: Optional[int] = None  # foreground window when hotkey pressed

# ── Recording config ──
CHUNK_DURATION = 0.5        # seconds per chunk
SAMPLE_RATE = 16000          # 16kHz mono (optimal for whisper)
SILENCE_RMS_THRESHOLD = 500  # RMS below this = silence
SILENCE_CHUNKS_TO_STOP = 4   # 2 seconds of silence after speech
MAX_RECORDING_SECONDS = 30   # absolute max


def start_voice_hotkey(
    hotkey: str = "ctrl+shift+m",
    kill_check: Optional[Callable] = None,
) -> dict:
    """
    Register global voice hotkey via keyboard module.
    Same pattern as kill switch in safety.py.

    Args:
        hotkey: Hotkey combination (default: ctrl+shift+m).
        kill_check: Callable returning True if kill switch is active.

    Returns:
        Status dict.

    / Registra hotkey global de voz via modulo keyboard.
    """
    global _hotkey_active, _hotkey_combo, _kill_switch_check
    global _hotkey_handle, _stop_handle

    if _hotkey_active:
        return {"success": True, "status": "already_active", "hotkey": _hotkey_combo}

    _hotkey_combo = hotkey
    _kill_switch_check = kill_check

    try:
        import keyboard
        _hotkey_handle = keyboard.add_hotkey(hotkey, _on_hotkey_pressed)
        _stop_handle = keyboard.add_hotkey(_stop_combo, _on_stop_pressed)
        _hotkey_active = True
        logger.info(f"Voice hotkeys active: {hotkey} (record), {_stop_combo} (stop)")
        return {"success": True, "hotkey": hotkey, "stop_hotkey": _stop_combo}
    except ImportError:
        logger.warning("keyboard module not available. Voice hotkey disabled.")
        return {"error": "keyboard module not installed"}
    except Exception as e:
        logger.error(f"Failed to register voice hotkey: {e}")
        return {"error": str(e)}


def stop_voice_hotkey() -> dict:
    """
    Unregister both voice hotkeys (record + stop).

    / Desregistra ambos hotkeys de voz (grabar + parar).
    """
    global _hotkey_active, _hotkey_handle, _stop_handle

    if not _hotkey_active:
        return {"success": True, "status": "already_inactive"}

    try:
        import keyboard
        if _hotkey_handle is not None:
            keyboard.remove_hotkey(_hotkey_handle)
            _hotkey_handle = None
        if _stop_handle is not None:
            keyboard.remove_hotkey(_stop_handle)
            _stop_handle = None
        _hotkey_active = False
        logger.info("Voice hotkeys deactivated")
        return {"success": True, "status": "deactivated"}
    except Exception as e:
        logger.error(f"Failed to remove voice hotkeys: {e}")
        return {"error": str(e)}


async def get_voice_hotkey_status() -> dict:
    """
    Get current voice hotkey status (MCP tool).

    Returns:
        Dict with hotkey_active, currently_recording, last_transcribed_text, last_error.

    / Obtiene el estado actual del hotkey de voz.
    """
    return {
        "success": True,
        "hotkey_active": _hotkey_active,
        "hotkey": _hotkey_combo,
        "currently_recording": _recording,
        "last_transcribed_text": _last_text,
        "last_error": _last_error,
    }


def _on_hotkey_pressed():
    """
    Callback when Ctrl+Shift+M is pressed.
    Saves foreground HWND, opens overlay, checks kill switch, starts recording.

    / Callback cuando se presiona Ctrl+Shift+M.
    / Guarda HWND, abre overlay, verifica kill switch, inicia grabacion.
    """
    global _recording, _saved_hwnd, _manual_stop

    # Check kill switch
    if _kill_switch_check and _kill_switch_check():
        logger.warning("Voice hotkey pressed but kill switch is active")
        winsound.Beep(400, 300)
        return

    # Don't start if already recording
    if _recording:
        logger.debug("Voice hotkey pressed but already recording")
        return

    _manual_stop = False

    # Save the foreground window HWND (the MCP client where user pressed hotkey)
    try:
        _saved_hwnd = ctypes.windll.user32.GetForegroundWindow()
    except Exception:
        _saved_hwnd = None

    # Open overlay and set listening state
    try:
        from marlow.core import voice_overlay
        voice_overlay.show_overlay()
        voice_overlay.update_status(voice_overlay.STATUS_LISTENING)
    except Exception:
        pass

    # Start recording in daemon thread (non-blocking)
    thread = threading.Thread(target=_record_and_transcribe, daemon=True)
    thread.start()


def _on_stop_pressed():
    """
    Callback when Ctrl+Shift+N is pressed.
    Signals the recording loop to stop immediately.

    / Callback cuando se presiona Ctrl+Shift+N.
    / Señala al loop de grabacion que pare inmediatamente.
    """
    global _manual_stop

    if _recording:
        _manual_stop = True
        logger.debug("Manual stop requested via Ctrl+Shift+N")


def _record_and_transcribe():
    """
    Core pipeline: beep -> record with VAD -> save -> transcribe -> type into MCP client.
    Updates overlay status throughout the pipeline.

    / Pipeline principal: beep -> grabar con VAD -> guardar -> transcribir -> escribir en cliente MCP.
    / Actualiza el overlay durante todo el pipeline.
    """
    global _recording, _last_text, _last_error

    _recording = True
    _last_error = None

    try:
        # Start beep
        winsound.Beep(800, 200)

        # Record with voice activity detection
        audio_data = _record_with_vad()
        if audio_data is None or len(audio_data) == 0:
            _last_error = "No audio recorded"
            _overlay_status("idle")
            winsound.Beep(400, 300)
            return

        # Update overlay: processing
        _overlay_status("processing")

        # Save to WAV
        audio_path = _save_chunks_to_wav(audio_data)
        if audio_path is None:
            _last_error = "Failed to save audio"
            _overlay_status("idle")
            winsound.Beep(400, 300)
            return

        # Transcribe
        text = _transcribe_sync(str(audio_path))
        if text is None or not text.strip():
            _last_error = "Transcription returned empty text"
            _overlay_status("idle")
            winsound.Beep(400, 300)
            return

        _last_text = text.strip()

        # Update overlay with transcribed text
        _overlay_text(_last_text, "user")

        # Type into the MCP client window (saved on hotkey press)
        success = _type_into_active_window(_last_text)
        if not success:
            _last_error = "Failed to type into MCP client"
            _overlay_status("idle")
            winsound.Beep(400, 300)
            return

        # Success
        _overlay_status("ready")
        _overlay_text("Sent to MCP client", "marlow")
        winsound.Beep(1200, 200)
        logger.info(f"Voice command transcribed: {_last_text[:50]}...")

    except Exception as e:
        _last_error = str(e)
        logger.error(f"Voice hotkey pipeline error: {e}")
        _overlay_status("idle")
        try:
            winsound.Beep(400, 300)
        except Exception:
            pass
    finally:
        _recording = False


def _record_with_vad() -> Optional[np.ndarray]:
    """
    Record audio in 0.5s chunks with voice activity detection.
    Stops after 2s of continuous silence AFTER speech is detected.
    Max 30 seconds total.

    Returns:
        Concatenated numpy array of int16 samples, or None on error.

    / Graba audio en chunks de 0.5s con deteccion de actividad de voz.
    """
    try:
        import sounddevice as sd
    except ImportError:
        logger.error("sounddevice not installed")
        return None

    chunk_samples = int(CHUNK_DURATION * SAMPLE_RATE)
    max_chunks = int(MAX_RECORDING_SECONDS / CHUNK_DURATION)

    chunks = []
    has_speech = False
    silence_count = 0

    for i in range(max_chunks):
        # Check kill switch between chunks
        if _kill_switch_check and _kill_switch_check():
            logger.warning("Kill switch activated during recording")
            break

        # Check manual stop (Ctrl+Shift+N)
        if _manual_stop:
            logger.debug(f"Manual stop at chunk {i+1}")
            break

        # Record one chunk
        try:
            chunk = sd.rec(
                chunk_samples,
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="int16",
            )
            sd.wait()
            chunks.append(chunk.flatten())
        except Exception as e:
            logger.error(f"Recording chunk error: {e}")
            break

        # Compute RMS for this chunk
        rms = _compute_rms_from_array(chunks[-1])

        if rms >= SILENCE_RMS_THRESHOLD:
            has_speech = True
            silence_count = 0
        elif has_speech:
            silence_count += 1
            if silence_count >= SILENCE_CHUNKS_TO_STOP:
                logger.debug(f"Silence detected after speech, stopping (chunk {i+1})")
                break

    if not chunks:
        return None

    return np.concatenate(chunks)


def _compute_rms_from_array(audio_array: np.ndarray) -> float:
    """
    Compute RMS (root mean square) of a numpy int16 audio array.
    Adapted from voice._compute_rms but works directly on arrays
    without WAV file I/O.

    / Calcula RMS de un array numpy int16 de audio.
    """
    if len(audio_array) == 0:
        return 0.0
    samples = audio_array.astype(np.float64)
    rms = np.sqrt(np.mean(samples ** 2))
    return float(rms)


def _save_chunks_to_wav(audio_data: np.ndarray) -> Optional[Path]:
    """
    Save numpy int16 audio array to WAV file in AUDIO_DIR.

    / Guarda array numpy de audio int16 en archivo WAV.
    """
    try:
        import soundfile as sf
        from marlow.tools.audio import AUDIO_DIR

        ts = time.strftime("%Y%m%d_%H%M%S")
        output_path = AUDIO_DIR / f"voice_hotkey_{ts}.wav"
        sf.write(str(output_path), audio_data, SAMPLE_RATE)
        return output_path
    except Exception as e:
        logger.error(f"Failed to save audio: {e}")
        return None


def _transcribe_sync(audio_path: str) -> Optional[str]:
    """
    Transcribe audio file synchronously (we're in a thread, not async context).
    Creates a new event loop to call the async transcribe_audio().

    / Transcribe archivo de audio sincronamente usando nuevo event loop.
    """
    try:
        from marlow.tools.audio import transcribe_audio

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                transcribe_audio(audio_path=audio_path, language="auto")
            )
        finally:
            loop.close()

        if "error" in result:
            logger.error(f"Transcription error: {result['error']}")
            return None

        return result.get("text")
    except Exception as e:
        logger.error(f"Transcription sync error: {e}")
        return None


def _restore_saved_window() -> bool:
    """
    Restore focus to the window that was active when hotkey was pressed.
    Uses AttachThreadInput trick for reliable SetForegroundWindow.

    / Restaura foco a la ventana que estaba activa al presionar el hotkey.
    """
    if _saved_hwnd is None or _saved_hwnd == 0:
        return False

    try:
        user32 = ctypes.windll.user32
        # Verify the window still exists
        if not user32.IsWindow(_saved_hwnd):
            return False

        # AttachThreadInput trick for reliable SetForegroundWindow
        fg_thread = user32.GetWindowThreadProcessId(
            user32.GetForegroundWindow(), None
        )
        my_thread = ctypes.windll.kernel32.GetCurrentThreadId()

        if fg_thread != my_thread:
            user32.AttachThreadInput(my_thread, fg_thread, True)
            user32.SetForegroundWindow(_saved_hwnd)
            user32.AttachThreadInput(my_thread, fg_thread, False)
        else:
            user32.SetForegroundWindow(_saved_hwnd)

        return True
    except Exception as e:
        logger.debug(f"Restore saved window failed: {e}")
        return False


def _type_into_active_window(text: str) -> bool:
    """
    Restore focus to the saved MCP client window and type transcribed text.
    Uses UIA silent methods first, falls back to clipboard paste.

    / Restaura foco a la ventana del cliente MCP y escribe el texto transcrito.
    """
    try:
        from marlow.tools.keyboard import _find_editable_element, _set_text_silent

        # Restore focus to the saved window
        if not _restore_saved_window():
            logger.warning("Could not restore saved window, using fallback")
            return _type_fallback(text)

        time.sleep(0.3)

        # Try to find the window wrapper via pywinauto for UIA methods
        try:
            from pywinauto import Desktop
            desktop = Desktop(backend="uia")
            # Find window by HWND
            target = None
            for w in desktop.windows():
                try:
                    if w.handle == _saved_hwnd:
                        target = w
                        break
                except Exception:
                    continue

            if target is not None:
                editor = _find_editable_element(target)
                if editor is not None:
                    result = _set_text_silent(editor, text, clear_first=False)
                    if result is not None:
                        import pyautogui
                        time.sleep(0.1)
                        pyautogui.press("enter")
                        return True
        except Exception as e:
            logger.debug(f"UIA method failed: {e}")

        # UIA failed, use clipboard fallback
        return _type_fallback(text)

    except Exception as e:
        logger.error(f"Type into active window error: {e}")
        return _type_fallback(text)


def _overlay_status(status: str) -> None:
    """Update overlay status indicator (safe to call from any thread)."""
    try:
        from marlow.core import voice_overlay
        voice_overlay.update_status(status)
    except Exception:
        pass


def _overlay_text(text: str, source: str = "user") -> None:
    """Update overlay text display (safe to call from any thread)."""
    try:
        from marlow.core import voice_overlay
        voice_overlay.update_text(text, source)
    except Exception:
        pass


def _type_fallback(text: str) -> bool:
    """
    Clipboard-based fallback for typing into the MCP client window.
    Works for all text including Unicode/Spanish characters.

    / Fallback basado en clipboard para escribir en la ventana del cliente MCP.
    """
    try:
        import pyautogui
        import subprocess

        # Restore focus if not already done
        _restore_saved_window()
        time.sleep(0.2)

        # Use PowerShell to set clipboard (handles Unicode properly)
        # Input via stdin to avoid injection
        proc = subprocess.run(
            ["powershell", "-Command", "$input | Set-Clipboard"],
            input=text,
            capture_output=True,
            text=True,
            timeout=5,
        )

        if proc.returncode != 0:
            logger.error(f"Clipboard set failed: {proc.stderr}")
            return False

        time.sleep(0.1)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.1)
        pyautogui.press("enter")
        return True

    except Exception as e:
        logger.error(f"Type fallback error: {e}")
        return False
