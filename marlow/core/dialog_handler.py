"""
Dialog Handler — Auto-detection and handling of system/app dialogs.

Scans windows for known dialog patterns (error, save, update, confirmation)
by analyzing their UIA tree for buttons, static text, and control layout.
Provides MCP tools to inspect dialogs and take configurable actions.

/ Detecta y maneja dialogos del sistema/apps automaticamente.
  Escanea el arbol UIA buscando botones, texto y patrones conocidos.
"""

import logging
import re
from typing import Optional

from marlow.core.uia_utils import find_window

logger = logging.getLogger("marlow.core.dialog_handler")

# ─────────────────────────────────────────────────────────────
# Known button labels (case-insensitive matching)
# ─────────────────────────────────────────────────────────────

# Buttons that dismiss/postpone dialogs
# / Botones que cierran o posponen dialogos
_DISMISS_BUTTONS = {
    "ok", "close", "cancel", "no", "later", "remind me later",
    "not now", "skip", "dismiss", "ignore", "don't save",
    "no, thanks", "maybe later",
}

# Buttons that accept/confirm
# / Botones que aceptan o confirman
_ACCEPT_BUTTONS = {
    "ok", "yes", "save", "accept", "agree", "continue",
    "allow", "confirm", "apply", "retry", "send",
}

# Update-related buttons
# / Botones relacionados a actualizaciones
_UPDATE_BUTTONS = {
    "update", "update now", "install", "install now", "restart",
    "restart now", "download", "upgrade",
}

_POSTPONE_BUTTONS = {
    "later", "remind me later", "not now", "skip", "maybe later",
    "ask me later", "remind me tomorrow",
}

# All known button labels for detection
# / Todas las etiquetas de botones conocidas para deteccion
_ALL_KNOWN_BUTTONS = {
    "ok", "cancel", "yes", "no", "save", "don't save", "close",
    "later", "remind me later", "skip", "accept", "decline",
    "allow", "deny", "retry", "abort", "ignore", "continue",
    "apply", "update", "update now", "install", "restart",
    "not now", "maybe later", "send", "discard",
}

# Error/warning indicators in dialog text
# / Indicadores de error/advertencia en texto del dialogo
_ERROR_KEYWORDS = [
    "error", "failed", "failure", "could not", "cannot", "unable to",
    "not found", "missing", "invalid", "denied", "access denied",
    "permission", "exception", "crash", "fatal",
]

_WARNING_KEYWORDS = [
    "warning", "caution", "are you sure", "confirm",
    "do you want", "would you like",
]

_UPDATE_KEYWORDS = [
    "update", "new version", "upgrade", "restart to apply",
    "restart required", "pending update", "install update",
]

_NOT_RESPONDING_KEYWORDS = [
    "not responding", "has stopped", "stopped working",
    "wait for the program", "close the program",
]

_SAVE_KEYWORDS = [
    "save", "unsaved", "do you want to save", "save changes",
    "save your", "discard changes",
]


# ─────────────────────────────────────────────────────────────
# Dialog scanner
# ─────────────────────────────────────────────────────────────

def _scan_dialog_elements(window) -> dict:
    """
    Walk the UIA tree of a window and extract buttons, text, and metadata.
    Returns structured info about the dialog's content.

    / Recorre el arbol UIA de una ventana y extrae botones, texto y metadata.
    """
    buttons: list[dict] = []
    texts: list[str] = []
    other_controls: list[dict] = []
    title = ""

    try:
        title = window.window_text() or ""
    except Exception:
        pass

    def _walk(element, depth: int) -> None:
        if depth > 8:  # Dialogs are shallow, 8 levels is plenty
            return

        try:
            info = element.element_info
            control_type = (getattr(info, "control_type", "") or "").lower()
            name = (element.window_text() or "").strip()
            auto_id = (getattr(info, "automation_id", "") or "").strip()
            class_name = (getattr(info, "class_name", "") or "").strip()

            if control_type == "button" and name:
                btn_info = {"name": name, "automation_id": auto_id}
                try:
                    rect = element.rectangle()
                    btn_info["bbox"] = {
                        "x": rect.left, "y": rect.top,
                        "width": rect.width(), "height": rect.height(),
                    }
                except Exception:
                    pass
                try:
                    btn_info["enabled"] = info.enabled
                except Exception:
                    btn_info["enabled"] = True
                buttons.append(btn_info)

            elif control_type in ("text", "edit") and name:
                # Static text or read-only edit controls contain dialog messages
                # / Texto estatico o edits readonly contienen mensajes del dialogo
                texts.append(name)

            elif control_type and name and control_type not in (
                "title bar", "menu bar", "pane", "window", "group",
                "thumb", "scroll bar",
            ):
                other_controls.append({
                    "type": control_type,
                    "name": name,
                    "automation_id": auto_id,
                })

            for child in element.children():
                _walk(child, depth + 1)

        except Exception:
            pass

    _walk(window, 0)

    return {
        "title": title,
        "buttons": buttons,
        "texts": texts,
        "other_controls": other_controls,
    }


def _classify_dialog(scan: dict) -> dict:
    """
    Classify a dialog based on its buttons and text content.

    Returns classification dict with:
    - dialog_type: error, warning, save, update, not_responding, confirmation, unknown
    - confidence: high, medium, low
    - suggested_action: report, dismiss, postpone, user_decide
    - detail: human-readable explanation

    / Clasifica un dialogo basado en sus botones y texto.
    """
    title_lower = scan["title"].lower()
    all_text = " ".join(scan["texts"]).lower()
    combined = f"{title_lower} {all_text}"
    button_names = {b["name"].lower() for b in scan["buttons"]}

    # Not Responding — highest priority
    if any(kw in combined for kw in _NOT_RESPONDING_KEYWORDS):
        return {
            "dialog_type": "not_responding",
            "confidence": "high",
            "suggested_action": "report",
            "detail": "Application not responding dialog detected",
        }

    # Error dialog
    if any(kw in combined for kw in _ERROR_KEYWORDS):
        has_ok = "ok" in button_names
        return {
            "dialog_type": "error",
            "confidence": "high" if has_ok else "medium",
            "suggested_action": "report",
            "detail": "Error dialog — read the message and report to user/LLM",
        }

    # Save dialog
    if any(kw in combined for kw in _SAVE_KEYWORDS):
        has_save = any(b in button_names for b in ("save", "save as"))
        has_dont_save = any(b in button_names for b in ("don't save", "dont save", "discard"))
        if has_save or has_dont_save:
            return {
                "dialog_type": "save",
                "confidence": "high",
                "suggested_action": "user_decide",
                "detail": "Save dialog — let user/LLM decide whether to save",
            }

    # Update dialog
    if any(kw in combined for kw in _UPDATE_KEYWORDS):
        has_postpone = button_names & {b for b in _POSTPONE_BUTTONS}
        return {
            "dialog_type": "update",
            "confidence": "high" if has_postpone else "medium",
            "suggested_action": "postpone" if has_postpone else "report",
            "detail": "Update dialog — postpone or report to user",
        }

    # Warning / Confirmation
    if any(kw in combined for kw in _WARNING_KEYWORDS):
        return {
            "dialog_type": "confirmation",
            "confidence": "medium",
            "suggested_action": "user_decide",
            "detail": "Confirmation dialog — let user/LLM decide",
        }

    # Yes/No pattern
    if {"yes", "no"} <= button_names:
        return {
            "dialog_type": "confirmation",
            "confidence": "medium",
            "suggested_action": "user_decide",
            "detail": "Yes/No dialog — let user/LLM decide",
        }

    # OK-only dialog (info/alert)
    if button_names == {"ok"} or (len(button_names) == 1 and "ok" in button_names):
        return {
            "dialog_type": "info",
            "confidence": "medium",
            "suggested_action": "dismiss",
            "detail": "Info dialog with OK button — safe to dismiss",
        }

    # Has buttons but unrecognized pattern
    if scan["buttons"]:
        return {
            "dialog_type": "unknown",
            "confidence": "low",
            "suggested_action": "report",
            "detail": "Unknown dialog with buttons — report content to user/LLM",
        }

    # No buttons at all
    return {
        "dialog_type": "unknown",
        "confidence": "low",
        "suggested_action": "report",
        "detail": "Window with no recognized dialog buttons",
    }


def _find_best_dismiss_button(buttons: list[dict]) -> Optional[dict]:
    """
    Find the best button to dismiss a dialog. Prefers Cancel > No > Close > OK.

    / Encuentra el mejor boton para cerrar un dialogo.
    """
    priority = ["cancel", "no", "close", "later", "not now", "skip", "ok"]
    name_map = {b["name"].lower(): b for b in buttons}

    for label in priority:
        if label in name_map:
            return name_map[label]

    return None


def _find_postpone_button(buttons: list[dict]) -> Optional[dict]:
    """
    Find a button that postpones an action (updates, reminders).

    / Encuentra un boton que pospone una accion.
    """
    name_map = {b["name"].lower(): b for b in buttons}

    for label in ["later", "remind me later", "not now", "maybe later",
                  "ask me later", "skip"]:
        if label in name_map:
            return name_map[label]

    return None


# ─────────────────────────────────────────────────────────────
# MCP Tool: get_dialog_info
# ─────────────────────────────────────────────────────────────

async def get_dialog_info(window_title: str) -> dict:
    """
    Get complete info about a dialog: title, text, buttons, inferred type.

    / Obtiene info completa de un dialogo: titulo, texto, botones, tipo inferido.
    """
    if not window_title:
        return {"error": "window_title is required"}

    window, err = find_window(window_title)
    if err:
        return err

    scan = _scan_dialog_elements(window)
    classification = _classify_dialog(scan)

    # Build button summary
    button_names = [b["name"] for b in scan["buttons"]]

    return {
        "success": True,
        "window_title": scan["title"],
        "dialog_type": classification["dialog_type"],
        "confidence": classification["confidence"],
        "suggested_action": classification["suggested_action"],
        "detail": classification["detail"],
        "buttons": scan["buttons"],
        "button_names": button_names,
        "texts": scan["texts"],
        "other_controls": scan["other_controls"][:10],  # Limit output
    }


# ─────────────────────────────────────────────────────────────
# MCP Tool: handle_dialog
# ─────────────────────────────────────────────────────────────

async def handle_dialog(
    action: str = "report",
    window_title: Optional[str] = None,
) -> dict:
    """
    Detect and handle active dialogs.

    Actions:
    - "report": Scan for dialogs and report info to LLM (no action taken)
    - "dismiss": Find and click the dismiss button (Cancel/Close/No/OK)
    - "auto": Handle automatically based on dialog type:
        - Error/unknown → report to LLM
        - Update → click "Later" / "Remind me later"
        - Info (OK-only) → click OK
        - Save/confirmation → report to LLM (user must decide)

    / Detecta y maneja dialogos activos.
    """
    valid_actions = ("report", "dismiss", "auto")
    if action not in valid_actions:
        return {"error": f"Invalid action '{action}'. Valid: {list(valid_actions)}"}

    # If specific window given, scan that one
    if window_title:
        window, err = find_window(window_title)
        if err:
            return err
        dialogs = [_process_single_dialog(window, action)]
        return {
            "success": True,
            "action_taken": action,
            "dialogs": dialogs,
        }

    # Otherwise, scan all top-level windows for dialog-like windows
    dialogs = await _scan_for_dialogs(action)

    if not dialogs:
        return {
            "success": True,
            "action_taken": action,
            "dialogs": [],
            "message": "No dialogs detected",
        }

    return {
        "success": True,
        "action_taken": action,
        "dialogs": dialogs,
    }


def _process_single_dialog(window, action: str) -> dict:
    """
    Process a single dialog window: scan, classify, optionally act.

    / Procesa un solo dialogo: escanear, clasificar, opcionalmente actuar.
    """
    scan = _scan_dialog_elements(window)
    classification = _classify_dialog(scan)

    result = {
        "window_title": scan["title"],
        "dialog_type": classification["dialog_type"],
        "confidence": classification["confidence"],
        "suggested_action": classification["suggested_action"],
        "texts": scan["texts"],
        "button_names": [b["name"] for b in scan["buttons"]],
        "action_performed": None,
    }

    if action == "report":
        result["action_performed"] = "reported"
        return result

    if action == "dismiss":
        btn = _find_best_dismiss_button(scan["buttons"])
        if btn:
            clicked = _click_button(window, btn["name"])
            result["action_performed"] = f"clicked '{btn['name']}'" if clicked else "click_failed"
        else:
            result["action_performed"] = "no_dismiss_button_found"
        return result

    if action == "auto":
        dtype = classification["dialog_type"]

        if dtype == "info":
            # Safe to dismiss OK-only dialogs
            btn = next(
                (b for b in scan["buttons"] if b["name"].lower() == "ok"),
                None,
            )
            if btn:
                clicked = _click_button(window, btn["name"])
                result["action_performed"] = "auto_dismissed_ok" if clicked else "click_failed"
            else:
                result["action_performed"] = "reported"

        elif dtype == "update":
            # Try to postpone
            btn = _find_postpone_button(scan["buttons"])
            if btn:
                clicked = _click_button(window, btn["name"])
                result["action_performed"] = f"auto_postponed_'{btn['name']}'" if clicked else "click_failed"
            else:
                result["action_performed"] = "reported"

        else:
            # error, save, confirmation, not_responding, unknown → report
            result["action_performed"] = "reported"

    return result


def _click_button(window, button_name: str) -> bool:
    """
    Click a button by name using UIA invoke (silent, no mouse move).

    / Click en un boton por nombre usando UIA invoke (silencioso).
    """
    try:
        from marlow.core.uia_utils import find_element_by_name
        element = find_element_by_name(window, button_name, max_depth=5)
        if element:
            try:
                iface = element.iface_invoke
                iface.Invoke()
                return True
            except Exception:
                # Fallback: click_input
                try:
                    element.click_input()
                    return True
                except Exception:
                    pass
    except Exception as e:
        logger.warning(f"Failed to click button '{button_name}': {e}")
    return False


async def _scan_for_dialogs(action: str) -> list[dict]:
    """
    Scan all top-level windows for dialog-like windows.
    Uses class_name heuristic + button analysis to filter real dialogs
    from regular app windows.

    / Escanea todas las ventanas buscando dialogos reales.
    """
    from pywinauto import Desktop

    desktop = Desktop(backend="uia")
    dialogs = []

    try:
        all_windows = desktop.windows()
    except Exception:
        return []

    for win in all_windows:
        try:
            title = win.window_text() or ""
            if not title.strip():
                continue

            info = win.element_info
            class_name = (getattr(info, "class_name", "") or "")

            # Known dialog class names on Windows
            # / Clases de ventana conocidas para dialogos en Windows
            is_dialog_class = class_name in (
                "#32770",       # Standard Windows dialog (MessageBox, etc.)
                "NUIDialog",    # Windows shell dialogs
                "TaskDialog",   # TaskDialog API
                "OperationStatusWindow",  # Copy/move progress
            )

            if not is_dialog_class:
                continue

            scan = _scan_dialog_elements(win)

            # Must have at least one button to be a dialog
            if not scan["buttons"]:
                continue

            dialogs.append(_process_single_dialog(win, action))

        except Exception:
            continue

    return dialogs
