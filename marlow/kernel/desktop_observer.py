"""DesktopObserver — Continuous desktop state from compositor IPC events.

Subscribes to the compositor's push event stream (WindowCreated,
WindowDestroyed, WindowFocused, WindowMovedToShadow, WindowMovedToUser,
ConflictDetected) and maintains a live model of the desktop.

Feeds WindowTracker, DesktopWeather, and EventBus continuously —
not just during GoalEngine steps.

/ Observador continuo del escritorio via IPC del compositor.
"""

import asyncio
import logging
import os
import struct
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

try:
    import msgpack
except ImportError:
    msgpack = None

logger = logging.getLogger("marlow.kernel.desktop_observer")


# ── Data types ───────────────────────────────────────────────


@dataclass
class WindowInfo:
    """Lightweight window representation from compositor events."""
    id: int
    title: str
    app_id: str
    space: str = "user"  # "user" or "shadow"

    def to_tracker_dict(self) -> dict:
        """Format for WindowTracker.record_snapshot()."""
        return {
            "title": self.title,
            "hwnd": self.id,
            "pid": 0,
            "rect": {"left": 0, "top": 0, "right": 0, "bottom": 0},
            "is_active": False,
        }


@dataclass
class DesktopState:
    """Snapshot of the current desktop model."""
    windows: dict  # int -> WindowInfo
    focused_window: Optional[WindowInfo]
    focus_history: list  # last N focused windows
    last_change: Optional[datetime]
    desktop_state: str  # ESTABLE/OCUPADO/INESTABLE/TORMENTA
    connected: bool
    last_user_activity: Optional[datetime]
    user_idle: bool


# ── Observer ─────────────────────────────────────────────────

# Default idle threshold: 5 minutes
DEFAULT_IDLE_MINUTES = 5

# Reconnect delay after disconnect
RECONNECT_DELAY = 3.0
MAX_RECONNECT_DELAY = 30.0


class DesktopObserver:
    """Subscribes to compositor IPC events and maintains live desktop model.

    Uses its own dedicated IPC connection (separate from
    CompositorWindowManager) to avoid interference.
    """

    def __init__(
        self,
        event_bus: Any = None,
        window_tracker: Any = None,
        desktop_weather: Any = None,
        socket_path: Optional[str] = None,
        idle_minutes: float = DEFAULT_IDLE_MINUTES,
    ):
        # External integrations (all Optional for testability)
        self._event_bus = event_bus
        self._window_tracker = window_tracker
        self._weather = desktop_weather

        # IPC connection
        self._socket_path = socket_path or self._default_socket_path()
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._connected = False

        # Desktop model
        self._windows: dict[int, WindowInfo] = {}
        self._focused_window: Optional[WindowInfo] = None
        self._focus_history: deque[WindowInfo] = deque(maxlen=20)
        self._last_change: Optional[datetime] = None

        # Idle detection
        self._idle_threshold = idle_minutes * 60.0  # seconds
        self._last_user_activity: Optional[float] = None
        self._user_idle = False

        # Lifecycle
        self._task: Optional[asyncio.Task] = None
        self._stopping = False

    @staticmethod
    def _default_socket_path() -> str:
        runtime_dir = os.environ.get(
            "XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"
        )
        return os.path.join(runtime_dir, "marlow-compositor.sock")

    # ── Public API ───────────────────────────────────────────

    def get_state(self) -> DesktopState:
        """Return current desktop model snapshot (sync, safe anytime)."""
        climate = "ESTABLE"
        if self._weather:
            try:
                report = self._weather.get_report()
                climate = report.climate.value
            except Exception:
                pass

        return DesktopState(
            windows=dict(self._windows),
            focused_window=self._focused_window,
            focus_history=list(self._focus_history),
            last_change=self._last_change,
            desktop_state=climate,
            connected=self._connected,
            last_user_activity=(
                datetime.fromtimestamp(self._last_user_activity)
                if self._last_user_activity else None
            ),
            user_idle=self._user_idle,
        )

    async def run(self):
        """Main loop: connect, subscribe, process events. Auto-reconnects."""
        logger.info("DesktopObserver starting (socket=%s)", self._socket_path)
        delay = RECONNECT_DELAY

        while not self._stopping:
            try:
                await self._connect()
                if not self._connected:
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, MAX_RECONNECT_DELAY)
                    continue

                # Connected — reset backoff
                delay = RECONNECT_DELAY

                # Seed model with initial window list
                await self._seed_windows()

                # Subscribe to all push events
                await self._subscribe()

                # Process events until disconnect
                await self._event_loop()

            except asyncio.CancelledError:
                logger.info("DesktopObserver cancelled")
                break
            except Exception as e:
                logger.warning("DesktopObserver error: %s", e)
                self._connected = False
                if not self._stopping:
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, MAX_RECONNECT_DELAY)

        await self._disconnect()
        logger.info("DesktopObserver stopped")

    def stop(self):
        """Signal the observer to shut down."""
        self._stopping = True
        if self._task and not self._task.done():
            self._task.cancel()

    # ── IPC connection ───────────────────────────────────────

    async def _connect(self):
        """Open Unix socket connection to compositor."""
        if not os.path.exists(self._socket_path):
            logger.debug("Compositor socket not found: %s", self._socket_path)
            self._connected = False
            return

        try:
            self._reader, self._writer = await asyncio.open_unix_connection(
                self._socket_path
            )
            self._connected = True
            logger.info("DesktopObserver connected to compositor")
        except (ConnectionRefusedError, OSError) as e:
            logger.debug("Compositor connection failed: %s", e)
            self._connected = False

    async def _disconnect(self):
        """Close IPC connection."""
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None
        self._connected = False

    async def _send_request(self, request: dict) -> dict:
        """Send a MessagePack request and read the response."""
        if not msgpack or not self._writer or not self._reader:
            return {"status": "error", "message": "not connected"}

        payload = msgpack.packb(request, use_bin_type=True)
        self._writer.write(struct.pack("<I", len(payload)))
        self._writer.write(payload)
        await self._writer.drain()

        len_buf = await asyncio.wait_for(
            self._reader.readexactly(4), timeout=10.0
        )
        msg_len = struct.unpack("<I", len_buf)[0]
        data = await asyncio.wait_for(
            self._reader.readexactly(msg_len), timeout=10.0
        )
        return msgpack.unpackb(data, raw=False)

    async def _read_event(self, timeout: float = 5.0) -> Optional[dict]:
        """Read a single pushed event from the compositor."""
        if not msgpack or not self._reader:
            return None
        try:
            len_buf = await asyncio.wait_for(
                self._reader.readexactly(4), timeout=timeout
            )
            msg_len = struct.unpack("<I", len_buf)[0]
            data = await self._reader.readexactly(msg_len)
            return msgpack.unpackb(data, raw=False)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError):
            return None

    # ── Setup ────────────────────────────────────────────────

    async def _seed_windows(self):
        """Fetch initial window list to populate the model."""
        try:
            resp = await self._send_request({"type": "ListWindows"})
            if resp.get("status") == "ok":
                data = resp.get("data", {})
                # Handle both formats: {"windows": [...]} and [...]
                if isinstance(data, list):
                    windows_data = data
                elif isinstance(data, dict):
                    windows_data = data.get("windows", [])
                else:
                    windows_data = []
                self._windows.clear()
                for w in windows_data:
                    wid = w.get("window_id", 0)
                    space = w.get("space", "user")
                    if isinstance(space, bool):
                        space = "shadow" if space else "user"
                    info = WindowInfo(
                        id=wid,
                        title=w.get("title", ""),
                        app_id=w.get("app_id", ""),
                        space=space,
                    )
                    self._windows[wid] = info
                    if w.get("focused"):
                        self._focused_window = info

                if self._weather:
                    self._weather.update_window_count(len(self._windows))

                logger.info(
                    "Seeded %d windows, focused=%s",
                    len(self._windows),
                    self._focused_window.title if self._focused_window else "none",
                )
        except Exception as e:
            logger.warning("Failed to seed windows: %s", e)

    async def _subscribe(self):
        """Subscribe to all compositor push events."""
        try:
            resp = await self._send_request({
                "type": "Subscribe",
                "events": ["all"],
            })
            if resp.get("status") == "ok":
                logger.info("Subscribed to compositor events")
            else:
                logger.warning("Subscribe failed: %s", resp)
        except Exception as e:
            logger.warning("Subscribe error: %s", e)

    # ── Event processing ─────────────────────────────────────

    async def _event_loop(self):
        """Read and dispatch compositor events until disconnect."""
        idle_check_interval = 30.0  # check idle every 30s
        last_idle_check = time.monotonic()

        while self._connected and not self._stopping:
            event = await self._read_event(timeout=2.0)

            if event is not None:
                try:
                    await self._dispatch_event(event)
                except Exception as e:
                    logger.debug("Event dispatch error: %s", e)

            # Periodic idle check
            now = time.monotonic()
            if now - last_idle_check >= idle_check_interval:
                last_idle_check = now
                await self._check_idle()

    async def _dispatch_event(self, event: dict):
        """Route a compositor event to the appropriate handler."""
        event_type = event.get("event", "")

        if event_type == "WindowCreated":
            await self._on_window_created(event)
        elif event_type == "WindowDestroyed":
            await self._on_window_destroyed(event)
        elif event_type == "WindowFocused":
            await self._on_window_focused(event)
        elif event_type == "WindowMovedToShadow":
            await self._on_window_moved_shadow(event)
        elif event_type == "WindowMovedToUser":
            await self._on_window_moved_user(event)
        elif event_type == "ConflictDetected":
            await self._on_conflict(event)
        elif event_type == "ProactivityToggle":
            await self._on_proactivity_toggle(event)
        elif event_type == "Pong":
            pass  # keepalive
        else:
            logger.debug("Unknown compositor event: %s", event_type)

    async def _on_window_created(self, event: dict):
        """Handle WindowCreated — new window appeared."""
        wid = event.get("window_id", 0)
        title = event.get("title", "")
        app_id = event.get("app_id", "")

        info = WindowInfo(id=wid, title=title, app_id=app_id, space="user")
        self._windows[wid] = info
        self._last_change = datetime.now()
        self._record_user_activity()

        logger.info("Window created: %s (%s) id=%d", title, app_id, wid)

        # Feed subsystems (fault-tolerant)
        self._feed_weather(window_change=True)
        self._feed_window_tracker()

        # Publish to EventBus
        await self._publish_event(
            "world.window_changed",
            change_type="appeared",
            window_title=title,
            data={"window_id": wid, "app_id": app_id},
        )

    async def _on_window_destroyed(self, event: dict):
        """Handle WindowDestroyed — window closed."""
        wid = event.get("window_id", 0)
        old_info = self._windows.pop(wid, None)
        title = old_info.title if old_info else f"id={wid}"
        self._last_change = datetime.now()

        if self._focused_window and self._focused_window.id == wid:
            self._focused_window = None

        logger.info("Window destroyed: %s id=%d", title, wid)

        self._feed_weather(window_change=True)

        self._feed_window_tracker()

        await self._publish_event(
            "world.window_changed",
            change_type="disappeared",
            window_title=title,
            data={"window_id": wid},
        )

    async def _on_window_focused(self, event: dict):
        """Handle WindowFocused — focus changed."""
        wid = event.get("window_id", 0)
        title = event.get("title", "")

        # Update model
        if wid in self._windows:
            info = self._windows[wid]
            # Update title if compositor sends newer one
            if title and title != info.title:
                self._windows[wid] = WindowInfo(
                    id=info.id, title=title, app_id=info.app_id, space=info.space,
                )
                info = self._windows[wid]
        else:
            # Window we didn't know about — add it
            info = WindowInfo(id=wid, title=title, app_id="", space="user")
            self._windows[wid] = info

        old_focused = self._focused_window
        self._focused_window = info
        self._focus_history.append(info)
        self._last_change = datetime.now()
        self._record_user_activity()

        self._feed_weather(window_change=True)
        self._feed_window_tracker()

        # Publish focus change event
        await self._publish_event(
            "world.focus_changed",
            window_title=title,
            data={
                "window_id": wid,
                "previous": old_focused.title if old_focused else "",
            },
        )

    async def _on_window_moved_shadow(self, event: dict):
        """Handle WindowMovedToShadow — window moved to shadow space."""
        wid = event.get("window_id", 0)
        if wid in self._windows:
            old = self._windows[wid]
            self._windows[wid] = WindowInfo(
                id=old.id, title=old.title, app_id=old.app_id, space="shadow",
            )
        self._last_change = datetime.now()

        await self._publish_event(
            "world.window_moved_shadow",
            data={"window_id": wid},
        )

    async def _on_window_moved_user(self, event: dict):
        """Handle WindowMovedToUser — window returned to user space."""
        wid = event.get("window_id", 0)
        if wid in self._windows:
            old = self._windows[wid]
            self._windows[wid] = WindowInfo(
                id=old.id, title=old.title, app_id=old.app_id, space="user",
            )
        self._last_change = datetime.now()

        await self._publish_event(
            "world.window_moved_user",
            data={"window_id": wid},
        )

    async def _on_conflict(self, event: dict):
        """Handle ConflictDetected — user interacted with agent-focused window."""
        wid = event.get("window_id", 0)
        reason = event.get("reason", "")
        self._record_user_activity()

        logger.warning("Conflict detected: window=%d reason=%s", wid, reason)

        window_title = ""
        if wid in self._windows:
            window_title = self._windows[wid].title

        await self._publish_event(
            "world.focus_lost",
            expected_app="agent",
            actual_app=window_title,
            data={"window_id": wid, "reason": reason},
        )

    async def _on_proactivity_toggle(self, event: dict):
        """Handle ProactivityToggle — Super+Escape kill switch."""
        logger.info("ProactivityToggle received from compositor")
        await self._publish_event("system.proactivity_toggle")

    # ── Idle detection ───────────────────────────────────────

    def _record_user_activity(self):
        """Mark that user activity was detected."""
        now = time.time()
        was_idle = self._user_idle

        self._last_user_activity = now
        self._user_idle = False

        if was_idle:
            # Transition: idle -> active
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._publish_event("system.user_active"))
            except RuntimeError:
                pass  # no event loop — skip publish (testing or shutdown)
            logger.info("User became active")

    async def _check_idle(self):
        """Check if user has been idle long enough to emit idle event."""
        if self._last_user_activity is None:
            return

        elapsed = time.time() - self._last_user_activity
        if elapsed >= self._idle_threshold and not self._user_idle:
            self._user_idle = True
            await self._publish_event(
                "system.user_idle",
                data={"idle_seconds": round(elapsed)},
            )
            logger.info("User idle for %.0f seconds", elapsed)

    # ── Subsystem feeds ──────────────────────────────────────

    def _feed_weather(self, window_change: bool = False):
        """Update DesktopWeather (fault-tolerant)."""
        if not self._weather:
            return
        try:
            if window_change:
                self._weather.record_window_change()
            self._weather.update_window_count(len(self._windows))
        except Exception as e:
            logger.debug("DesktopWeather feed error: %s", e)

    def _feed_window_tracker(self):
        """Push current window model to WindowTracker."""
        if not self._window_tracker:
            return
        try:
            window_dicts = []
            for winfo in self._windows.values():
                d = winfo.to_tracker_dict()
                d["is_active"] = (
                    self._focused_window is not None
                    and self._focused_window.id == winfo.id
                )
                window_dicts.append(d)
            self._window_tracker.record_snapshot(window_dicts)
        except Exception as e:
            logger.debug("WindowTracker feed error: %s", e)

    async def _publish_event(self, event_type: str, **kwargs):
        """Publish an event to the EventBus (fire-and-forget safe)."""
        if not self._event_bus:
            return
        try:
            from marlow.kernel.events import Event, WindowChanged, FocusLost

            # Use typed events when available
            if event_type == "world.window_changed":
                evt = WindowChanged(
                    source="desktop_observer",
                    change_type=kwargs.get("change_type", ""),
                    window_title=kwargs.get("window_title", ""),
                    data=kwargs.get("data", {}),
                )
            elif event_type == "world.focus_lost":
                evt = FocusLost(
                    source="desktop_observer",
                    expected_app=kwargs.get("expected_app", ""),
                    actual_app=kwargs.get("actual_app", ""),
                    data=kwargs.get("data", {}),
                )
            else:
                evt = Event(
                    event_type=event_type,
                    source="desktop_observer",
                    data=kwargs.get("data", {}),
                )

            await self._event_bus.publish(evt)
        except Exception as e:
            logger.debug("EventBus publish error: %s", e)
