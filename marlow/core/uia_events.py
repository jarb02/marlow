"""
UIA Event Handlers — Real-time UI monitoring via Windows UI Automation COM events.

Detects window opens/closes, focus changes, and structure changes without polling.
Uses comtypes (already installed via pywinauto) to implement COM event handlers
running in a dedicated STA daemon thread with a Win32 message pump.

/ Monitoreo en tiempo real de eventos UI via COM. Detecta ventanas, foco, cambios
  de estructura sin polling. Thread STA dedicado con message pump Win32.
"""

import ctypes
import ctypes.wintypes
import logging
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import psutil

logger = logging.getLogger("marlow.uia_events")

# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

UIA_Window_WindowOpenedEventId = 20016
UIA_Window_WindowClosedEventId = 20017

# TreeScope values
TreeScope_Element = 0x1
TreeScope_Children = 0x2
TreeScope_Subtree = 0x7  # Element + Children + Descendants

MAX_EVENTS = 500

# StructureChangeType names
_STRUCTURE_CHANGE_NAMES = {
    0: "ChildAdded",
    1: "ChildRemoved",
    2: "ChildrenInvalidated",
    3: "ChildrenBulkAdded",
    4: "ChildrenBulkRemoved",
    5: "ChildrenReordered",
}

# PM_REMOVE for PeekMessage
_PM_REMOVE = 0x0001

# ─────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────

_manager: Optional["UIAEventManager"] = None
_lock = threading.Lock()


def get_manager() -> "UIAEventManager":
    """Double-checked locking singleton (same pattern as cdp_manager.py)."""
    global _manager
    if _manager is None:
        with _lock:
            if _manager is None:
                _manager = UIAEventManager()
    return _manager


# ─────────────────────────────────────────────────────────────
# COM Type Library (lazy loaded on daemon thread)
# ─────────────────────────────────────────────────────────────

# These will be populated by _load_com_types() on the daemon thread
_com_loaded = False
_com_load_error: Optional[str] = None

# Placeholders for COM interfaces — assigned after GetModule
IUIAutomationEventHandler = None
IUIAutomationFocusChangedEventHandler = None
IUIAutomationStructureChangedEventHandler = None
CUIAutomation = None


def _load_com_types() -> Optional[str]:
    """
    Load UIA COM type library. Must be called from STA thread.
    Returns error string on failure, None on success.

    / Carga la type library COM de UIA. Debe ejecutarse en thread STA.
    """
    global _com_loaded, _com_load_error
    global IUIAutomationEventHandler, IUIAutomationFocusChangedEventHandler
    global IUIAutomationStructureChangedEventHandler, CUIAutomation

    if _com_loaded:
        return None

    try:
        import comtypes
        import comtypes.client

        comtypes.client.GetModule("UIAutomationCore.dll")

        from comtypes.gen.UIAutomationClient import (
            CUIAutomation as _CUIAutomation,
            IUIAutomationEventHandler as _IUIAutomationEventHandler,
            IUIAutomationFocusChangedEventHandler as _IUIAutomationFocusChangedEventHandler,
            IUIAutomationStructureChangedEventHandler as _IUIAutomationStructureChangedEventHandler,
        )

        IUIAutomationEventHandler = _IUIAutomationEventHandler
        IUIAutomationFocusChangedEventHandler = _IUIAutomationFocusChangedEventHandler
        IUIAutomationStructureChangedEventHandler = _IUIAutomationStructureChangedEventHandler
        CUIAutomation = _CUIAutomation

        _com_loaded = True
        return None

    except Exception as e:
        _com_load_error = f"Failed to load UIA COM types: {e}"
        logger.warning(_com_load_error)
        return _com_load_error


# ─────────────────────────────────────────────────────────────
# Helper: extract element info from IUIAutomationElement
# ─────────────────────────────────────────────────────────────

def _extract_element_info(element) -> dict:
    """
    Extract basic info from IUIAutomationElement. Must be fast.
    COM callbacks must never raise, so every access is wrapped.

    / Extrae info basica de un IUIAutomationElement. Rapido y seguro.
    """
    info = {}
    try:
        info["element_name"] = element.CurrentName or ""
    except Exception:
        info["element_name"] = ""
    try:
        pid = element.CurrentProcessId
        info["process_id"] = pid
        try:
            info["process_name"] = psutil.Process(pid).name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            info["process_name"] = ""
    except Exception:
        info["process_id"] = 0
        info["process_name"] = ""
    try:
        info["control_type"] = element.CurrentLocalizedControlType or ""
    except Exception:
        info["control_type"] = ""
    try:
        info["class_name"] = element.CurrentClassName or ""
    except Exception:
        info["class_name"] = ""
    return info


# ─────────────────────────────────────────────────────────────
# COM Event Handler Classes
# ─────────────────────────────────────────────────────────────

def _create_handler_classes():
    """
    Create COM handler classes after type library is loaded.
    Must be called after _load_com_types() succeeds.

    / Crea clases de handlers COM despues de cargar la type library.
    """
    import comtypes

    class WindowEventHandler(comtypes.COMObject):
        """Handles WindowOpened and WindowClosed events."""
        _com_interfaces_ = [IUIAutomationEventHandler]

        def __init__(self, event_callback):
            super().__init__()
            self._callback = event_callback

        def HandleAutomationEvent(self, sender, eventId):
            try:
                info = _extract_element_info(sender)
                if eventId == UIA_Window_WindowOpenedEventId:
                    event_type = "window_opened"
                elif eventId == UIA_Window_WindowClosedEventId:
                    event_type = "window_closed"
                else:
                    event_type = f"unknown_{eventId}"
                self._callback(event_type, info)
            except Exception:
                pass  # COM callbacks must not raise
            return 0  # S_OK

    class FocusEventHandler(comtypes.COMObject):
        """Handles FocusChanged events."""
        _com_interfaces_ = [IUIAutomationFocusChangedEventHandler]

        def __init__(self, event_callback):
            super().__init__()
            self._callback = event_callback

        def HandleFocusChangedEvent(self, sender):
            try:
                info = _extract_element_info(sender)
                self._callback("focus_changed", info)
            except Exception:
                pass
            return 0  # S_OK

    class StructureEventHandler(comtypes.COMObject):
        """Handles StructureChanged events (per-window)."""
        _com_interfaces_ = [IUIAutomationStructureChangedEventHandler]

        def __init__(self, event_callback):
            super().__init__()
            self._callback = event_callback

        def HandleStructureChangedEvent(self, sender, changeType, runtimeId):
            try:
                info = _extract_element_info(sender)
                info["change_type"] = _STRUCTURE_CHANGE_NAMES.get(changeType, str(changeType))
                self._callback("structure_changed", info)
            except Exception:
                pass
            return 0  # S_OK

    return WindowEventHandler, FocusEventHandler, StructureEventHandler


# ─────────────────────────────────────────────────────────────
# UIAEventManager
# ─────────────────────────────────────────────────────────────

class UIAEventManager:
    """
    Manages UIA COM event handlers in a dedicated STA daemon thread.

    Architecture:
    - Daemon thread runs CoInitialize() + Win32 message pump
    - All COM objects created and accessed on this thread
    - Event callbacks fire synchronously during PeekMessage/DispatchMessage
    - Commands sent to daemon via queue.Queue (watch/unwatch requests)
    - Stop via threading.Event flag checked in pump loop

    / Maneja handlers de eventos UIA COM en un thread STA daemon dedicado.
    """

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._events: list[dict] = []
        self._event_lock = threading.Lock()
        self._command_queue: queue.Queue = queue.Queue()
        self._running = False
        self._start_error: Optional[str] = None

        # COM objects — created on daemon thread only
        self._automation = None
        self._root = None
        self._handlers: list = []  # registered handler instances (prevent GC)
        self._window_opened_handler = None
        self._window_closed_handler = None
        self._focus_handler = None

    def is_running(self) -> bool:
        """Check if the event monitor is running."""
        return self._running and self._thread is not None and self._thread.is_alive()

    def start(self) -> Optional[str]:
        """
        Start the UIA event monitor daemon thread.
        Returns error string on failure, None on success.

        / Inicia el thread daemon del monitor de eventos UIA.
        """
        if self.is_running():
            return None  # Already running

        self._stop_event.clear()
        self._ready_event.clear()
        self._start_error = None
        self._events.clear()

        self._thread = threading.Thread(
            target=self._run_event_loop,
            name="marlow-uia-events",
            daemon=True,
        )
        self._thread.start()

        # Wait for daemon thread to signal ready (or fail)
        self._ready_event.wait(timeout=10.0)

        if self._start_error:
            self._running = False
            return self._start_error

        self._running = True
        logger.info("UIA event monitor started")
        return None

    def stop(self) -> None:
        """
        Stop the UIA event monitor.

        / Detiene el monitor de eventos UIA.
        """
        if not self._running:
            return

        self._stop_event.set()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

        self._running = False
        self._thread = None
        self._handlers.clear()
        self._window_opened_handler = None
        self._window_closed_handler = None
        self._focus_handler = None
        logger.info("UIA event monitor stopped")

    def get_events(
        self,
        since: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict]:
        """
        Get recent events, optionally filtered.

        / Obtiene eventos recientes, opcionalmente filtrados.
        """
        limit = max(1, min(limit, MAX_EVENTS))

        with self._event_lock:
            filtered = list(self._events)

        # Filter by event_type
        if event_type:
            filtered = [e for e in filtered if e.get("type") == event_type]

        # Filter by timestamp
        if since:
            try:
                since_dt = datetime.fromisoformat(since)
                filtered = [
                    e for e in filtered
                    if datetime.fromisoformat(e["timestamp"]) >= since_dt
                ]
            except (ValueError, KeyError):
                pass  # Invalid since format, skip filter

        # Return newest first, limited
        return filtered[-limit:][::-1]

    def _on_event(self, event_type: str, info: dict) -> None:
        """
        Callback passed to handler constructors. Thread-safe event storage.
        Skips events with no identifying info (e.g. closed child windows
        whose process is already gone).

        / Callback para constructores de handlers. Almacena eventos thread-safe.
          Omite eventos sin info identificable.
        """
        # Skip empty events — no name, no process, no class
        if (
            not info.get("element_name")
            and not info.get("process_name")
            and not info.get("class_name")
        ):
            return

        event = {
            "type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **info,
        }
        with self._event_lock:
            self._events.append(event)
            if len(self._events) > MAX_EVENTS:
                self._events = self._events[-MAX_EVENTS:]

    def _run_event_loop(self) -> None:
        """
        Daemon thread: STA COM initialization, handler registration, message pump.

        / Thread daemon: inicializacion COM STA, registro de handlers, message pump.
        """
        try:
            # Initialize COM in STA mode
            ctypes.windll.ole32.CoInitialize(None)
        except Exception as e:
            self._start_error = f"CoInitialize failed: {e}"
            self._ready_event.set()
            return

        try:
            # Load COM type library
            err = _load_com_types()
            if err:
                self._start_error = err
                self._ready_event.set()
                return

            # Create handler classes (needs loaded type library)
            WindowEventHandler, FocusEventHandler, _ = _create_handler_classes()

            # Create IUIAutomation instance
            import comtypes.client
            self._automation = comtypes.client.CreateObject(CUIAutomation)
            self._root = self._automation.GetRootElement()

            # Register global event handlers
            self._register_global_handlers(WindowEventHandler, FocusEventHandler)

            # Signal ready
            self._ready_event.set()

            # Run message pump
            msg = ctypes.wintypes.MSG()
            while not self._stop_event.is_set():
                # Process Windows messages (delivers COM callbacks)
                while ctypes.windll.user32.PeekMessageW(
                    ctypes.byref(msg), 0, 0, 0, _PM_REMOVE
                ):
                    ctypes.windll.user32.TranslateMessage(ctypes.byref(msg))
                    ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg))

                # Brief sleep to avoid busy-wait (~50ms between pump cycles)
                self._stop_event.wait(0.05)

        except Exception as e:
            logger.error(f"UIA event loop error: {e}")
            if not self._ready_event.is_set():
                self._start_error = f"Event loop failed: {e}"
                self._ready_event.set()
        finally:
            self._unregister_all()
            try:
                ctypes.windll.ole32.CoUninitialize()
            except Exception:
                pass

    def _register_global_handlers(self, WindowEventHandler, FocusEventHandler) -> None:
        """
        Register WindowOpened, WindowClosed, and FocusChanged handlers
        on the root element (desktop).

        / Registra handlers de WindowOpened, WindowClosed y FocusChanged
          en el elemento raiz (desktop).
        """
        try:
            # WindowOpened — on root element, subtree scope
            handler_opened = WindowEventHandler(self._on_event)
            self._automation.AddAutomationEventHandler(
                UIA_Window_WindowOpenedEventId,
                self._root,
                TreeScope_Subtree,
                None,  # cacheRequest
                handler_opened,
            )
            self._window_opened_handler = handler_opened
            self._handlers.append(handler_opened)
            logger.debug("Registered WindowOpened handler")
        except Exception as e:
            logger.warning(f"Failed to register WindowOpened handler: {e}")

        try:
            # WindowClosed — on root element, subtree scope
            handler_closed = WindowEventHandler(self._on_event)
            self._automation.AddAutomationEventHandler(
                UIA_Window_WindowClosedEventId,
                self._root,
                TreeScope_Subtree,
                None,
                handler_closed,
            )
            self._window_closed_handler = handler_closed
            self._handlers.append(handler_closed)
            logger.debug("Registered WindowClosed handler")
        except Exception as e:
            logger.warning(f"Failed to register WindowClosed handler: {e}")

        try:
            # FocusChanged — global, no element needed
            handler_focus = FocusEventHandler(self._on_event)
            self._automation.AddFocusChangedEventHandler(
                None,  # cacheRequest
                handler_focus,
            )
            self._focus_handler = handler_focus
            self._handlers.append(handler_focus)
            logger.debug("Registered FocusChanged handler")
        except Exception as e:
            logger.warning(f"Failed to register FocusChanged handler: {e}")

    def _unregister_all(self) -> None:
        """
        Unregister all event handlers. Called during cleanup.

        / Desregistra todos los handlers de eventos. Se llama al limpiar.
        """
        if not self._automation:
            return

        try:
            self._automation.RemoveAllEventHandlers()
            logger.debug("Removed all UIA event handlers")
        except Exception as e:
            logger.warning(f"Error removing event handlers: {e}")

        self._handlers.clear()
        self._window_opened_handler = None
        self._window_closed_handler = None
        self._focus_handler = None
        self._automation = None
        self._root = None


# ─────────────────────────────────────────────────────────────
# MCP Tool Wrappers
# ─────────────────────────────────────────────────────────────

async def start_ui_monitor() -> dict:
    """
    Start real-time UI event monitoring (window open/close, focus changes).

    / Iniciar monitoreo de eventos UI en tiempo real (ventanas, foco).
    """
    mgr = get_manager()
    if mgr.is_running():
        return {"success": True, "status": "already_running"}

    err = mgr.start()
    if err:
        return {"error": err}

    return {
        "success": True,
        "status": "started",
        "events_monitored": [
            "window_opened",
            "window_closed",
            "focus_changed",
        ],
    }


async def stop_ui_monitor() -> dict:
    """
    Stop UI event monitoring.

    / Detener monitoreo de eventos UI.
    """
    mgr = get_manager()
    if not mgr.is_running():
        return {"success": True, "status": "not_running"}

    mgr.stop()
    return {"success": True, "status": "stopped"}


async def get_ui_events(
    event_type: Optional[str] = None,
    limit: int = 20,
    since: Optional[str] = None,
) -> dict:
    """
    Get recent UI events from the monitor.

    / Obtener eventos UI recientes del monitor.
    """
    mgr = get_manager()
    if not mgr.is_running():
        return {"error": "UI monitor not running. Call start_ui_monitor first."}

    events = mgr.get_events(since=since, event_type=event_type, limit=limit)
    return {
        "success": True,
        "count": len(events),
        "events": events,
    }
