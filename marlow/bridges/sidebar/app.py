"""Marlow sidebar — GTK4 + WebKit6 chat window.

Always-visible sidebar on the right side of the screen. Shows chat
history, text input, mic toggle, and visual state indicator.
Connects to the daemon via WebSocket for real-time updates.

Usage:
    python3 ~/marlow/marlow/bridges/sidebar/app.py

/ Sidebar Marlow — ventana GTK4 + WebKit6 de chat.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

import gi

# IMPORTANT: Gtk4LayerShell MUST be imported BEFORE Gtk4.
# It hooks into libwayland during library load; if Gtk loads first,
# the Wayland connection is already established and layer-shell fails.
try:
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell
    HAS_LAYER_SHELL = True
except (ValueError, ImportError):
    HAS_LAYER_SHELL = False

gi.require_version("Gtk", "4.0")
gi.require_version("WebKit", "6.0")
from gi.repository import Gtk, WebKit, GLib, Gdk

logger = logging.getLogger("marlow.sidebar")

# Lazy imports for onboarding
def _check_onboarding():
    try:
        sys.path.insert(0, os.path.expanduser("~/marlow"))
        from marlow.bridges.sidebar.onboarding import is_onboarding_needed
        return is_onboarding_needed()
    except Exception:
        return False

SIDEBAR_WIDTH = 380
DAEMON_URL = "http://localhost:8420"


# ─────────────────────────────────────────────────────────────
# HTML template for the chat UI
# ─────────────────────────────────────────────────────────────

CHAT_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    font-family: 'Inter', 'Segoe UI', system-ui, sans-serif;
    background: #0f1320;
    color: #e0e0e0;
    height: 100vh;
    border-left: 1px solid #2a3a5a;
    display: flex;
    flex-direction: column;
    overflow: hidden;
}

#header {
    display: flex;
    align-items: center;
    padding: 12px 16px;
    background: #16213e;
    border-bottom: 1px solid #2a2a4a;
    gap: 10px;
}

#status-dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: #666;
    transition: background 0.3s;
}
#status-dot.idle { background: #666; }
#status-dot.listening { background: #4dabf7; animation: pulse 1.5s infinite; }
#status-dot.processing { background: #ffc107; }
#status-dot.responding { background: #51cf66; }
#status-dot.error { background: #ff6b6b; }

@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
}

#header-title {
    font-size: 15px;
    font-weight: 600;
    flex: 1;
    color: #c4c4e0;
}

#mic-btn {
    width: 32px;
    height: 32px;
    border-radius: 50%;
    border: none;
    background: #2a2a4a;
    color: #888;
    font-size: 16px;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.2s;
}
#mic-btn:hover { background: #3a3a5a; }
#mic-btn.active { background: #4dabf7; color: white; }

#messages {
    flex: 1;
    overflow-y: auto;
    padding: 12px 16px;
    display: flex;
    flex-direction: column;
    gap: 8px;
}

.message {
    max-width: 95%;
    padding: 8px 12px;
    border-radius: 12px;
    font-size: 13px;
    line-height: 1.5;
    word-wrap: break-word;
    white-space: pre-wrap;
}
.message.user {
    align-self: flex-end;
    background: #2a4a7a;
    color: #e0e0ff;
    border-bottom-right-radius: 4px;
}
.message.marlow {
    align-self: flex-start;
    background: #2a2a4a;
    color: #d0d0e0;
    border-bottom-left-radius: 4px;
}
.message .time {
    font-size: 10px;
    color: #666;
    margin-top: 4px;
}
.message .channel-tag {
    font-size: 9px;
    color: #888;
    margin-left: 4px;
}
.message.system {
    align-self: center;
    background: transparent;
    color: #666;
    font-size: 11px;
    font-style: italic;
}

#input-area {
    display: flex;
    padding: 10px 12px;
    background: #16213e;
    border-top: 1px solid #2a2a4a;
    gap: 8px;
}

#text-input {
    flex: 1;
    background: #0a0e18;
    border: 1px solid #2a2a4a;
    border-radius: 20px;
    padding: 8px 16px;
    color: #e0e0e0;
    font-size: 13px;
    outline: none;
    font-family: inherit;
}
#text-input:focus { border-color: #4dabf7; }
#text-input::placeholder { color: #555; }

#send-btn {
    width: 36px;
    height: 36px;
    border-radius: 50%;
    border: none;
    background: #4dabf7;
    color: white;
    font-size: 16px;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
}
#send-btn:hover { background: #339af0; }

::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #2a2a4a; border-radius: 3px; }
</style>
</head>
<body>
    <div id="header">
        <div id="status-dot" class="idle"></div>
        <div id="header-title">Marlow</div>
        <button id="mic-btn" onclick="toggleMic()">&#x1F3A4;</button>
    </div>

    <div id="messages"></div>

    <div id="input-area">
        <input id="text-input" type="text" placeholder="Escribe un mensaje..."
               onkeydown="if(event.key==='Enter')sendMessage()">
        <button id="send-btn" onclick="sendMessage()">&#x27A4;</button>
    </div>

<script>
function getTime() {
    const now = new Date();
    return now.getHours().toString().padStart(2,'0') + ':' +
           now.getMinutes().toString().padStart(2,'0');
}

function addMessage(text, role, channel) {
    const container = document.getElementById('messages');
    const div = document.createElement('div');
    div.className = 'message ' + role;
    let html = text;
    const timeStr = getTime();
    html += '<div class="time">' + timeStr;
    if (channel && channel !== 'sidebar') {
        html += '<span class="channel-tag">via ' + channel + '</span>';
    }
    html += '</div>';
    div.innerHTML = html;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

function addSystemMessage(text) {
    const container = document.getElementById('messages');
    const div = document.createElement('div');
    div.className = 'message system';
    div.textContent = text;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

function setStatus(state) {
    const dot = document.getElementById('status-dot');
    dot.className = 'idle';
    if (state) dot.className = state;
}

function sendMessage() {
    const input = document.getElementById('text-input');
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    addMessage(text, 'user');
    // Notify Python via title change (simple bridge)
    document.title = 'MSG:' + text;
}

function toggleMic() {
    const btn = document.getElementById('mic-btn');
    const isActive = btn.classList.contains('active');
    document.title = 'MIC:' + (isActive ? 'off' : 'on');
    btn.classList.toggle('active');
}

function updateMicState(isActive) {
    const btn = document.getElementById('mic-btn');
    if (isActive) btn.classList.add('active');
    else btn.classList.remove('active');
}

addSystemMessage('Marlow listo');
</script>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────
# GTK4 Application
# ─────────────────────────────────────────────────────────────

class MarlowSidebar(Gtk.Application):
    """GTK4 sidebar application with WebKit6 chat view."""

    def __init__(self):
        super().__init__(application_id="com.marlow.sidebar")
        self._webview = None
        self._window = None
        self._daemon_poller = None
        self._last_status = "idle"
        self._last_transcript_time = 0.0

    def do_activate(self):
        # Create window
        self._window = Gtk.ApplicationWindow(application=self)
        self._window.set_title("Marlow")
        self._window.set_default_size(SIDEBAR_WIDTH, 600)
        self._window.set_resizable(True)

        # Anchor sidebar to right edge as a layer surface
        if HAS_LAYER_SHELL:
            Gtk4LayerShell.init_for_window(self._window)
            Gtk4LayerShell.set_layer(self._window, Gtk4LayerShell.Layer.TOP)
            Gtk4LayerShell.set_anchor(self._window, Gtk4LayerShell.Edge.RIGHT, True)
            Gtk4LayerShell.set_anchor(self._window, Gtk4LayerShell.Edge.TOP, True)
            Gtk4LayerShell.set_anchor(self._window, Gtk4LayerShell.Edge.BOTTOM, True)
            Gtk4LayerShell.set_exclusive_zone(self._window, SIDEBAR_WIDTH)
            Gtk4LayerShell.set_margin(self._window, Gtk4LayerShell.Edge.TOP, 0)
            Gtk4LayerShell.set_keyboard_mode(self._window, Gtk4LayerShell.KeyboardMode.ON_DEMAND)
            logger.info("Sidebar using layer-shell (anchored right, %dpx, keyboard=on_demand)", SIDEBAR_WIDTH)
        else:
            logger.warning("gtk4-layer-shell not available, sidebar will be a regular window")

        # WebKit webview
        self._webview = WebKit.WebView()
        # Check if onboarding is needed
        if _check_onboarding():
            from marlow.bridges.sidebar.onboarding import get_onboarding_html
            self._webview.load_html(get_onboarding_html(), "file:///")
            self._onboarding_mode = True
        else:
            self._webview.load_html(CHAT_HTML, "file:///")
            self._onboarding_mode = False

        # Monitor title changes for message bridge
        self._webview.connect("notify::title", self._on_title_change)

        self._window.set_child(self._webview)
        self._window.present()

        # Start polling daemon status + voice transcripts
        GLib.timeout_add(2000, self._poll_daemon_status)
        GLib.timeout_add(1500, self._poll_transcripts)

    def _on_title_change(self, webview, pspec):
        """Handle messages from JavaScript via title changes."""
        title = webview.get_title()
        if not title:
            return

        if title.startswith("ONBOARD:"):
            self._handle_onboarding(title[8:])
            return

        if title.startswith("MSG:"):
            text = title[4:]
            self._send_goal(text)
        elif title.startswith("MIC:"):
            mic_state = title[4:]
            self._toggle_mic(mic_state)

    def _handle_onboarding(self, data: str):
        """Handle onboarding events from the wizard."""
        parts = data.split(":", 1)
        if len(parts) != 2:
            return
        event_type, value = parts

        try:
            from marlow.bridges.sidebar.onboarding import process_onboarding_event
            process_onboarding_event(event_type, value)
        except Exception as e:
            logger.error("Onboarding error: %s", e)

        # When done, switch to normal chat mode
        if event_type == "done":
            self._onboarding_mode = False
            self._webview.load_html(CHAT_HTML, "file:///")

    def _send_goal(self, text: str):
        """Send goal to daemon via HTTP in a thread."""
        def _do():
            import urllib.request
            try:
                data = json.dumps({"goal": text, "channel": "sidebar"}).encode()
                req = urllib.request.Request(
                    f"{DAEMON_URL}/goal",
                    data=data,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=120) as resp:
                    result = json.loads(resp.read())
                    success = result.get("success", False)
                    summary = result.get("result_summary", "")
                    status = result.get("status", "unknown")

                    # Prefer 'response' field (LLM-formatted)
                    response_text = result.get("response", "")
                    if not response_text:
                        if success and summary:
                            response_text = summary
                        elif success:
                            response_text = "OK"
                        else:
                            errors = result.get("errors", [])
                            response_text = errors[0] if errors else status

                    GLib.idle_add(self._add_marlow_message, response_text)
            except Exception as e:
                logger.error("Goal request failed: %s", e)
                GLib.idle_add(
                    self._add_marlow_message,
                    "No pude procesar tu solicitud. \xbfPodr\xedas intentarlo de nuevo?",
                )

        threading.Thread(target=_do, daemon=True).start()

        # Show processing state
        self._run_js("setStatus('processing')")

    def _toggle_mic(self, state: str):
        """Toggle mic via trigger file, with state validation."""
        trigger = "/tmp/marlow-voice-trigger"
        try:
            if state == "on":
                voice_state = self._read_voice_state()
                if voice_state == "gemini-active":
                    return  # Already active
                with open(trigger, "w") as f:
                    f.write("press")
            else:
                with open(trigger, "w") as f:
                    f.write("release")
        except Exception as e:
            logger.warning("Mic toggle failed: %s", e)

    @staticmethod
    def _read_voice_state() -> str:
        """Read current voice daemon state from state file."""
        try:
            with open("/tmp/marlow-voice-state") as f:
                return f.read().strip()
        except FileNotFoundError:
            return "idle"

    def _add_marlow_message(self, text: str):
        """Add a Marlow response message (called from GLib main thread)."""
        safe_text = text.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
        self._run_js(f"addMessage('{safe_text}', 'marlow')")
        self._run_js("setStatus('idle')")
        return False  # Don't repeat GLib.idle_add

    def _poll_daemon_status(self) -> bool:
        """Poll daemon /status every 2s to update state indicator."""
        def _do():
            import urllib.request
            try:
                with urllib.request.urlopen(f"{DAEMON_URL}/status", timeout=3) as resp:
                    data = json.loads(resp.read())
                    state = data.get("state", "idle")
                    if state != self._last_status:
                        self._last_status = state
                        state_map = {
                            "idle": "idle",
                            "executing": "processing",
                            "planning": "processing",
                            "starting": "idle",
                        }
                        css_state = state_map.get(state, "idle")
                        GLib.idle_add(self._run_js, f"setStatus('{css_state}')")
            except Exception:
                pass  # Daemon not running — don't spam errors

        threading.Thread(target=_do, daemon=True).start()
        self._check_voice_liveness()
        voice_active = self._read_voice_state() == "gemini-active"
        self._run_js(f"updateMicState({str(voice_active).lower()})")
        return True  # Continue polling

    def _poll_transcripts(self) -> bool:
        """Poll daemon /transcripts for voice conversation updates."""
        since = self._last_transcript_time

        def _do():
            import urllib.request
            try:
                url = f"{DAEMON_URL}/transcripts?since={since}"
                with urllib.request.urlopen(url, timeout=3) as resp:
                    data = json.loads(resp.read())
                    transcripts = data.get("transcripts", [])
                    for t in transcripts:
                        role = t.get("role", "user")
                        text = t.get("text", "")
                        ts = t.get("time", 0)
                        if ts > self._last_transcript_time:
                            self._last_transcript_time = ts
                        if text:
                            css_role = "marlow" if role == "marlow" else "user"
                            GLib.idle_add(
                                self._add_voice_transcript, text, css_role,
                            )
            except Exception:
                pass

        threading.Thread(target=_do, daemon=True).start()
        return True  # Continue polling

    def _add_voice_transcript(self, text: str, role: str):
        """Add a voice transcript message to the chat (via voice channel tag)."""
        safe = text.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
        self._run_js(f"addMessage('{safe}', '{role}', 'voice')")
        return False

    def _check_voice_liveness(self):
        """Detect dead voice daemon: if state is gemini-active but stale, reset."""
        state_file = "/tmp/marlow-voice-state"
        trigger_file = "/tmp/marlow-voice-trigger"
        try:
            if not os.path.exists(state_file):
                return
            mtime = os.path.getmtime(state_file)
            with open(state_file) as f:
                state = f.read().strip()
            if state == "gemini-active" and (time.time() - mtime) > 60:
                logger.warning("Voice daemon appears dead (stale state), resetting")
                with open(state_file, "w") as f:
                    f.write("idle")
                try:
                    os.unlink(trigger_file)
                except FileNotFoundError:
                    pass
                GLib.idle_add(
                    self._run_js,
                    "document.getElementById('mic-btn').classList.remove('active')",
                )
        except Exception:
            pass

    def _run_js(self, js: str):
        """Execute JavaScript in the webview."""
        if self._webview:
            self._webview.evaluate_javascript(js, -1)
        return False


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    app = MarlowSidebar()
    app.run(None)


if __name__ == "__main__":
    main()
