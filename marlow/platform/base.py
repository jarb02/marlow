"""Platform Abstraction Layer — Abstract Base Classes.

Defines the interfaces that each platform backend (Windows, Linux)
must implement. Tools in marlow/tools/ delegate to these interfaces
instead of calling platform-specific APIs directly.

/ Capa de abstraccion de plataforma — clases base abstractas.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


# ── Data types ──


@dataclass
class WindowInfo:
    """Platform-agnostic window information.

    Args:
        identifier: Opaque handle — hwnd (Windows), con_id (Sway), window id (X11).
        title: Window title text.
        app_name: Application name or process name.
        pid: Process ID (0 if unknown).
        is_focused: Whether the window currently has focus.
        is_visible: Whether the window is visible (not minimized to tray).
        x: Left edge of the window in pixels.
        y: Top edge of the window in pixels.
        width: Window width in pixels.
        height: Window height in pixels.
        extra: Backend-specific metadata (app_id for Sway, class_name for Win32).
    """

    identifier: str
    title: str
    app_name: str
    pid: int = 0
    is_focused: bool = False
    is_visible: bool = True
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    extra: dict = field(default_factory=dict)


@dataclass
class FocusSnapshot:
    """Saved focus state that can be restored later.

    Args:
        identifier: Opaque window handle to restore focus to.
        title: Window title at the time of the snapshot (for logging).
    """

    identifier: str
    title: str


# ── Abstract interfaces ──


class WindowManager(ABC):
    """Enumerate, focus, and manage desktop windows."""

    @abstractmethod
    def list_windows(self, include_minimized: bool = True) -> list[WindowInfo]:
        """Return a list of all open windows.

        Args:
            include_minimized: If True, include minimized/hidden windows.

        Returns:
            List of WindowInfo for each window found.
        """

    @abstractmethod
    def focus_window(self, identifier: str) -> bool:
        """Focus a window by title substring or backend-specific identifier.

        Args:
            identifier: Window title substring (fuzzy match) or opaque id.

        Returns:
            True if the window was focused successfully.
        """

    @abstractmethod
    def get_focused_window(self) -> Optional[WindowInfo]:
        """Get the currently focused window.

        Returns:
            WindowInfo for the focused window, or None if not determinable.
        """

    @abstractmethod
    def manage_window(self, identifier: str, action: str, **kwargs) -> bool:
        """Perform a management action on a window.

        Args:
            identifier: Window title substring or opaque id.
            action: One of 'minimize', 'maximize', 'restore', 'close',
                    'move', 'resize', 'fullscreen'.
            **kwargs: Action-specific parameters:
                      move: x=int, y=int
                      resize: width=int, height=int

        Returns:
            True if the action was performed successfully.
        """


class InputProvider(ABC):
    """Send keyboard and mouse input to the desktop."""

    @abstractmethod
    def type_text(self, text: str) -> bool:
        """Type a string of text into the currently focused window.

        Args:
            text: The text to type.

        Returns:
            True if the text was sent successfully.
        """

    @abstractmethod
    def press_key(self, key: str) -> bool:
        """Press and release a single key.

        Args:
            key: Key name — e.g. 'Return', 'Tab', 'Escape', 'BackSpace',
                 'Up', 'Down', 'Left', 'Right', 'F1'...'F12'.

        Returns:
            True if the key was pressed successfully.
        """

    @abstractmethod
    def hotkey(self, *keys: str) -> bool:
        """Press a key combination (modifier + key).

        Args:
            *keys: Keys to press simultaneously — e.g. hotkey('ctrl', 'c').
                   Modifiers: 'ctrl', 'alt', 'shift', 'super'.

        Returns:
            True if the hotkey was sent successfully.
        """

    @abstractmethod
    def click(self, x: int, y: int, button: str = "left") -> bool:
        """Click at absolute screen coordinates.

        Args:
            x: X coordinate in pixels.
            y: Y coordinate in pixels.
            button: 'left', 'right', or 'middle'.

        Returns:
            True if the click was sent successfully.
        """

    @abstractmethod
    def move_mouse(self, x: int, y: int) -> bool:
        """Move the mouse to absolute screen coordinates.

        Args:
            x: X coordinate in pixels.
            y: Y coordinate in pixels.

        Returns:
            True if the mouse was moved successfully.
        """


class ScreenCapture(ABC):
    """Capture screenshots of the desktop or individual windows."""

    @abstractmethod
    def screenshot(
        self,
        window_title: Optional[str] = None,
        region: Optional[tuple[int, int, int, int]] = None,
    ) -> bytes:
        """Capture a screenshot and return PNG-encoded bytes.

        Args:
            window_title: If provided, capture only this window.
                         If None, capture the full screen.
            region: If provided, a (x, y, width, height) tuple to capture.
                    Mutually exclusive with window_title.

        Returns:
            PNG image data as bytes.

        Raises:
            RuntimeError: If capture fails (tool not installed, etc.).
        """


class FocusGuard(ABC):
    """Save and restore the user's window focus.

    Used by the MCP server to wrap tool calls so the user's active
    window is preserved even when tools need to interact with other
    windows.
    """

    @abstractmethod
    def save_user_focus(self) -> Optional[FocusSnapshot]:
        """Save the currently focused window.

        Returns:
            A FocusSnapshot that can be passed to restore_user_focus(),
            or None if the current focus could not be determined.
        """

    @abstractmethod
    def restore_user_focus(self, snapshot: Optional[FocusSnapshot] = None) -> bool:
        """Restore focus to a previously saved window.

        Args:
            snapshot: The FocusSnapshot from save_user_focus().
                     If None, restores the last automatically saved snapshot.

        Returns:
            True if focus was restored successfully.
        """


class UITreeProvider(ABC):
    """Read the desktop accessibility tree and interact with elements.

    This is Marlow's primary "vision" — it reads the structure of any
    window without needing screenshots. Cost: 0 tokens.

    On Windows this uses UI Automation (pywinauto).
    On Linux this uses AT-SPI2 (gi.repository.Atspi).
    """

    @abstractmethod
    def get_tree(
        self,
        window_title: Optional[str] = None,
        max_depth: Optional[int] = None,
    ) -> dict:
        """Build the accessibility tree for a window or the desktop.

        Args:
            window_title: Title substring of the target window. If None,
                         returns the tree for all applications.
            max_depth: Maximum recursion depth. None means backend default
                      (typically 8-15 depending on app framework).

        Returns:
            Dict with structure::

                {
                    "success": True,
                    "window": {"title": str, "app": str, "pid": int, ...},
                    "tree": {
                        "role": str,
                        "name": str,
                        "description": str,
                        "states": [str, ...],
                        "bounds": {"x": int, "y": int, "w": int, "h": int},
                        "path": "0",
                        "children": [<same structure>],
                    },
                    "element_count": int,
                    "depth_used": int,
                }

            On error: ``{"success": False, "error": str}``.
        """

    @abstractmethod
    def find_elements(
        self,
        name: Optional[str] = None,
        role: Optional[str] = None,
        states: Optional[list[str]] = None,
        window_title: Optional[str] = None,
    ) -> list[dict]:
        """Search the tree for elements matching criteria.

        Args:
            name: Element name to match (case-insensitive substring;
                  fuzzy Levenshtein if no exact match found).
            role: Role/control type filter (e.g. 'push button', 'text',
                  'menu item', 'check box').
            states: Required states (e.g. ['focused'], ['enabled', 'visible']).
            window_title: Limit search to this window.

        Returns:
            List of dicts, each with: 'role', 'name', 'description',
            'bounds', 'path', 'score' (match quality 0.0-1.0),
            'actions' (available action names).
        """

    @abstractmethod
    def get_element_properties(self, path: str, window_title: Optional[str] = None) -> dict:
        """Get detailed properties for an element identified by tree path.

        Args:
            path: Dot-separated index path from get_tree() (e.g. "0.2.1").
            window_title: Window context (needed to rebuild tree).

        Returns:
            Dict with: 'role', 'name', 'description', 'states', 'bounds',
            'interfaces' (list of supported interface names),
            'actions' (list of action names), 'text' (if Text interface),
            'value' (if Value interface), 'children_count'.
        """

    @abstractmethod
    def do_action(self, path: str, action_name: str, window_title: Optional[str] = None) -> bool:
        """Execute an action on an element via the Action interface.

        Args:
            path: Dot-separated index path (e.g. "0.2.1").
            action_name: Action to perform (e.g. 'click', 'activate', 'press').
            window_title: Window context.

        Returns:
            True if the action was performed successfully.
        """

    @abstractmethod
    def get_text(self, path: str, window_title: Optional[str] = None) -> Optional[str]:
        """Get text content from an element via the Text interface.

        Args:
            path: Dot-separated index path (e.g. "0.2.1").
            window_title: Window context.

        Returns:
            The text content, or None if the element has no Text interface.
        """


class AccessibilityProvider(ABC):
    """Monitor desktop accessibility events and detect dialogs.

    On Windows this uses UIA COM event handlers (comtypes).
    On Linux this uses AT-SPI2 event listeners (GLib + D-Bus).
    """

    @abstractmethod
    def register_event(self, event_type: str, callback) -> bool:
        """Register a callback for an accessibility event.

        Args:
            event_type: AT-SPI2 / UIA event name. Common values:
                        'window:create', 'window:destroy', 'window:activate',
                        'object:state-changed:focused',
                        'object:state-changed:visible',
                        'object:text-changed'.
            callback: Callable(event_dict) where event_dict has keys:
                     'type', 'source_name', 'source_role', 'app_name',
                     'detail', 'timestamp'.

        Returns:
            True if registration succeeded.
        """

    @abstractmethod
    def unregister_event(self, event_type: str) -> bool:
        """Remove a previously registered event listener.

        Args:
            event_type: The same event type string used in register_event().

        Returns:
            True if the listener was found and removed.
        """

    @abstractmethod
    def start_listening(self) -> bool:
        """Start the event processing loop in a background thread.

        Returns:
            True if the loop was started (or was already running).
        """

    @abstractmethod
    def stop_listening(self) -> bool:
        """Stop the event processing loop and clean up.

        Returns:
            True if the loop was stopped.
        """

    @abstractmethod
    def detect_dialogs(self) -> list[dict]:
        """Scan the accessibility tree for active dialog windows.

        Returns:
            List of dicts, each with:
            'title' (str), 'message' (str or None),
            'dialog_type' (str: 'dialog', 'alert', 'file-chooser', 'message-dialog'),
            'buttons' (list of {'name': str, 'actions': [str]}),
            'app_name' (str), 'pid' (int).
        """


class AudioProvider(ABC):
    """Capture audio from system output and microphone.

    On Windows this uses WASAPI loopback (PyAudioWPatch).
    On Linux this uses PipeWire (pw-record / pw-cat).
    """

    @abstractmethod
    def capture_system_audio(
        self, duration_seconds: int = 5, output_path: Optional[str] = None,
    ) -> dict:
        """Record system/desktop audio output.

        Args:
            duration_seconds: How long to record (seconds).
            output_path: File path for the WAV output. If None, a temp
                        path under ~/.marlow/audio/ is generated.

        Returns:
            Dict with: 'success', 'path' (output file), 'duration',
            'sample_rate', 'channels', 'size_bytes', 'error' (on failure).
        """

    @abstractmethod
    def capture_mic_audio(
        self, duration_seconds: int = 5, output_path: Optional[str] = None,
    ) -> dict:
        """Record audio from the default microphone.

        Args:
            duration_seconds: How long to record (seconds).
            output_path: File path for the WAV output. If None, generated.

        Returns:
            Dict with: 'success', 'path', 'duration', 'sample_rate',
            'channels', 'size_bytes', 'error'.
        """

    @abstractmethod
    def list_audio_sources(self) -> list[dict]:
        """List available audio sources (sinks, sources, monitors).

        Returns:
            List of dicts with: 'name', 'description', 'type'
            ('sink', 'source', 'monitor'), 'is_default' (bool).
        """

    @abstractmethod
    def get_audio_status(self) -> dict:
        """Get audio subsystem status.

        Returns:
            Dict with: 'running' (bool), 'server' ('pipewire'/'pulseaudio'/...),
            'version' (str), 'default_sink' (str), 'default_source' (str).
        """


class SystemProvider(ABC):
    """Run commands, launch applications, and query system info."""

    @abstractmethod
    def run_command(self, command: str, timeout: int = 30) -> dict:
        """Execute a shell command.

        Args:
            command: The command string to execute.
            timeout: Maximum seconds to wait for completion.

        Returns:
            Dict with keys: 'stdout', 'stderr', 'exit_code', 'success'.
        """

    @abstractmethod
    def open_application(self, name_or_path: str) -> dict:
        """Launch an application.

        Args:
            name_or_path: Application name (resolved by system) or full path.

        Returns:
            Dict with keys: 'success', 'pid' (if available), 'error' (if failed).
        """

    @abstractmethod
    def get_system_info(self) -> dict:
        """Gather system information.

        Returns:
            Dict with keys: 'os', 'cpu', 'memory', 'disk', 'display'.
        """
