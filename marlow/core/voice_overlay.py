"""
Marlow Voice Overlay

Floating tkinter window that shows voice control status:
- Status indicator (idle/listening/processing/ready)
- Transcribed user text
- Marlow's response/action
- Mini-log of last 5 conversation lines

Runs in a separate daemon thread. Zero external dependencies (tkinter
ships with Python). Always-on-top, semi-transparent, bottom-right corner.

/ Ventana flotante tkinter para control de voz.
/ Muestra estado, texto transcrito, y mini-log de conversacion.
"""

import threading
import logging
import queue
from typing import Optional

logger = logging.getLogger("marlow.core.voice_overlay")

# ── Module state ──
_overlay: Optional["VoiceOverlay"] = None
_overlay_lock = threading.Lock()


# ── Status constants ──
STATUS_IDLE = "idle"
STATUS_LISTENING = "listening"
STATUS_PROCESSING = "processing"
STATUS_READY = "ready"

_STATUS_CONFIG = {
    STATUS_IDLE: {"color": "#808080", "text": "Idle", "text_es": "Inactivo"},
    STATUS_LISTENING: {"color": "#FF4444", "text": "Listening...", "text_es": "Escuchando..."},
    STATUS_PROCESSING: {"color": "#FFAA00", "text": "Processing...", "text_es": "Procesando..."},
    STATUS_READY: {"color": "#44CC44", "text": "Ready", "text_es": "Listo"},
}


class VoiceOverlay:
    """
    Floating overlay window for voice control feedback.

    Runs tkinter mainloop in a daemon thread. Uses a queue for
    thread-safe communication from the voice hotkey pipeline.

    / Ventana flotante para feedback de control de voz.
    """

    def __init__(self, user_monitor: Optional[dict] = None):
        self._queue: queue.Queue = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._root = None
        self._running = False
        self._user_monitor = user_monitor or {"left": 0, "top": 0, "width": 1920, "height": 1080}
        self._log_lines: list[str] = []

    def show(self) -> None:
        """
        Show the overlay window. Starts the tkinter thread if not running.

        / Muestra la ventana overlay. Inicia el thread de tkinter si no esta corriendo.
        """
        if self._running and self._root:
            self._queue.put(("show", None))
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_tk, daemon=True)
        self._thread.start()

    def hide(self) -> None:
        """
        Hide the overlay window (without destroying it).

        / Oculta la ventana overlay sin destruirla.
        """
        if self._running:
            self._queue.put(("hide", None))

    def close(self) -> None:
        """
        Close and destroy the overlay window.

        / Cierra y destruye la ventana overlay.
        """
        self._running = False
        self._queue.put(("close", None))

    def update_status(self, status: str) -> None:
        """
        Update the status indicator.

        Args:
            status: One of STATUS_IDLE, STATUS_LISTENING, STATUS_PROCESSING, STATUS_READY.

        / Actualiza el indicador de estado.
        """
        if self._running:
            self._queue.put(("status", status))

    def update_text(self, text: str, source: str = "user") -> None:
        """
        Update displayed text and add to mini-log.

        Args:
            text: Text to display.
            source: "user" for transcribed speech, "marlow" for responses.

        / Actualiza el texto y lo agrega al mini-log.
        """
        if self._running:
            self._queue.put(("text", (text, source)))

    def _run_tk(self) -> None:
        """
        Tkinter mainloop in daemon thread.

        / Mainloop de tkinter en thread daemon.
        """
        try:
            import tkinter as tk
        except ImportError:
            logger.error("tkinter not available")
            self._running = False
            return

        try:
            root = tk.Tk()
            self._root = root

            # ── Window setup ──
            root.title("Marlow Voice")
            root.overrideredirect(True)  # No title bar
            root.attributes("-topmost", True)  # Always on top
            root.attributes("-alpha", 0.85)  # Semi-transparent
            root.configure(bg="#1a1a2e")

            # Size and position: bottom-right of user monitor
            win_w, win_h = 300, 200
            mon = self._user_monitor
            pos_x = mon["left"] + mon["width"] - win_w - 20
            pos_y = mon["top"] + mon["height"] - win_h - 60
            root.geometry(f"{win_w}x{win_h}+{pos_x}+{pos_y}")

            # ── Header with status indicator ──
            header_frame = tk.Frame(root, bg="#1a1a2e")
            header_frame.pack(fill="x", padx=8, pady=(6, 2))

            self._status_dot = tk.Canvas(
                header_frame, width=14, height=14,
                bg="#1a1a2e", highlightthickness=0,
            )
            self._status_dot.pack(side="left", padx=(0, 6))
            self._dot_id = self._status_dot.create_oval(2, 2, 12, 12, fill="#808080", outline="")

            self._status_label = tk.Label(
                header_frame, text="Idle", fg="#cccccc",
                bg="#1a1a2e", font=("Segoe UI", 10, "bold"),
            )
            self._status_label.pack(side="left")

            # Title on right
            tk.Label(
                header_frame, text="Marlow", fg="#555577",
                bg="#1a1a2e", font=("Segoe UI", 8),
            ).pack(side="right")

            # ── Separator ──
            tk.Frame(root, bg="#333355", height=1).pack(fill="x", padx=8, pady=2)

            # ── Current text display ──
            self._text_label = tk.Label(
                root, text="", fg="#eeeeee", bg="#1a1a2e",
                font=("Segoe UI", 9), wraplength=280, justify="left",
                anchor="w",
            )
            self._text_label.pack(fill="x", padx=8, pady=(2, 0))

            # ── Mini-log (scrollable text) ──
            log_frame = tk.Frame(root, bg="#12122a")
            log_frame.pack(fill="both", expand=True, padx=8, pady=(4, 8))

            self._log_text = tk.Text(
                log_frame, bg="#12122a", fg="#999999",
                font=("Consolas", 8), wrap="word",
                height=5, width=36, borderwidth=0,
                highlightthickness=0, state="disabled",
            )
            self._log_text.pack(fill="both", expand=True)

            # ── Keybindings ──
            root.bind("<Escape>", lambda e: self._on_escape())

            # ── Pulse animation state ──
            self._pulse_active = False
            self._pulse_on = True

            # ── Process queue periodically ──
            self._process_queue(root)

            root.mainloop()

        except Exception as e:
            logger.error(f"Voice overlay error: {e}")
        finally:
            self._running = False
            self._root = None

    def _process_queue(self, root) -> None:
        """
        Process pending messages from the queue. Called every 100ms.

        / Procesa mensajes pendientes de la cola. Se llama cada 100ms.
        """
        if not self._running:
            try:
                root.destroy()
            except Exception:
                pass
            return

        try:
            while True:
                msg_type, data = self._queue.get_nowait()

                if msg_type == "status":
                    self._apply_status(data)
                elif msg_type == "text":
                    text, source = data
                    self._apply_text(text, source)
                elif msg_type == "show":
                    root.deiconify()
                elif msg_type == "hide":
                    root.withdraw()
                elif msg_type == "close":
                    self._running = False
                    root.destroy()
                    return
        except queue.Empty:
            pass
        except Exception as e:
            logger.debug(f"Queue processing error: {e}")

        # Schedule next check
        try:
            root.after(100, lambda: self._process_queue(root))
        except Exception:
            pass

    def _apply_status(self, status: str) -> None:
        """Apply a status change to the UI."""
        cfg = _STATUS_CONFIG.get(status, _STATUS_CONFIG[STATUS_IDLE])

        try:
            self._status_dot.itemconfig(self._dot_id, fill=cfg["color"])
            self._status_label.config(text=cfg["text"])

            # Pulse animation for listening
            if status == STATUS_LISTENING:
                self._pulse_active = True
                self._pulse_on = True
                self._animate_pulse()
            else:
                self._pulse_active = False
                self._status_dot.itemconfig(self._dot_id, fill=cfg["color"])
        except Exception:
            pass

    def _animate_pulse(self) -> None:
        """Pulse the status dot red/dark for listening state."""
        if not self._pulse_active or not self._running or not self._root:
            return
        try:
            color = "#FF4444" if self._pulse_on else "#661111"
            self._status_dot.itemconfig(self._dot_id, fill=color)
            self._pulse_on = not self._pulse_on
            self._root.after(500, self._animate_pulse)
        except Exception:
            pass

    def _apply_text(self, text: str, source: str) -> None:
        """Update text display and add to log."""
        try:
            # Update main text
            prefix = "You" if source == "user" else "Marlow"
            display = f"{prefix}: {text}"
            self._text_label.config(
                text=display[:120],
                fg="#eeeeee" if source == "user" else "#88ccff",
            )

            # Add to log
            log_line = f"[{prefix}] {text}"
            self._log_lines.append(log_line)
            if len(self._log_lines) > 5:
                self._log_lines = self._log_lines[-5:]

            # Update log widget
            self._log_text.config(state="normal")
            self._log_text.delete("1.0", "end")
            self._log_text.insert("1.0", "\n".join(self._log_lines))
            self._log_text.config(state="disabled")
            self._log_text.see("end")
        except Exception:
            pass

    def _on_escape(self) -> None:
        """Handle Escape key — close overlay."""
        self.close()


# ── Public API ──

def show_overlay(user_monitor: Optional[dict] = None) -> dict:
    """
    Show the voice overlay window.

    Args:
        user_monitor: Monitor info dict with left, top, width, height.

    / Muestra la ventana overlay de voz.
    """
    global _overlay

    with _overlay_lock:
        if _overlay is None or not _overlay._running:
            _overlay = VoiceOverlay(user_monitor=user_monitor)

        _overlay.show()

    return {"success": True, "visible": True}


def hide_overlay() -> dict:
    """
    Hide the voice overlay window.

    / Oculta la ventana overlay de voz.
    """
    with _overlay_lock:
        if _overlay and _overlay._running:
            _overlay.hide()
            return {"success": True, "visible": False}

    return {"success": True, "visible": False, "note": "Overlay not active"}


def close_overlay() -> dict:
    """
    Close and destroy the overlay.

    / Cierra y destruye la ventana overlay.
    """
    global _overlay

    with _overlay_lock:
        if _overlay and _overlay._running:
            _overlay.close()
        _overlay = None

    return {"success": True, "closed": True}


def update_status(status: str) -> None:
    """
    Update overlay status indicator (thread-safe).

    / Actualiza el indicador de estado del overlay.
    """
    with _overlay_lock:
        if _overlay and _overlay._running:
            _overlay.update_status(status)


def update_text(text: str, source: str = "user") -> None:
    """
    Update overlay text display (thread-safe).

    / Actualiza el texto del overlay.
    """
    with _overlay_lock:
        if _overlay and _overlay._running:
            _overlay.update_text(text, source)


def is_visible() -> bool:
    """Check if overlay is currently visible."""
    with _overlay_lock:
        return _overlay is not None and _overlay._running


async def toggle_voice_overlay(visible: bool) -> dict:
    """
    MCP tool: Show or hide the voice overlay window.

    Args:
        visible: True to show, False to hide.

    Returns:
        Dictionary with overlay state.

    / Herramienta MCP: Mostrar u ocultar la ventana overlay de voz.
    """
    if visible:
        # Get user monitor info from background manager
        monitor = None
        try:
            from marlow.tools.background import _manager
            if _manager.primary_monitor:
                monitor = _manager.primary_monitor
        except Exception:
            pass
        return show_overlay(user_monitor=monitor)
    else:
        return hide_overlay()
