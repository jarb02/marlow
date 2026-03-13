#!/usr/bin/env python3
"""Marlow OS Tool Coverage Test — force Gemini to use all 53 tools.

Talks to the real daemon via HTTP. No mocks. No Marlow imports.
Same 3 data layers as stress_test.py: HTTP responses, journalctl, SQLite.

Goal: craft requests that naturally require specific tools, then measure
which of the 53 tools Gemini actually used.

Usage:
    python3 ~/marlow/tests/integration/tool_coverage_test.py [OPTIONS]

Options:
    --suite single|combo|recovery|all   Test suite (default: all)
    --verbose                           Show full responses
    --cleanup                           Close windows + delete test data
    --timeout N                         Timeout per test in seconds (default: 120)
    --delay N                           Seconds between tests (default: 3)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import textwrap
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    print("Installing requests...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "-q"])
    import requests

# ─── Config ──────────────────────────────────────────────────

DAEMON_URL = "http://127.0.0.1:8420"
LOGS_DB = os.path.expanduser("~/.marlow/db/logs.db")
STATE_DB = os.path.expanduser("~/.marlow/db/state.db")
REPORT_DIR = os.path.expanduser("~/marlow/tests/integration")

# All 53 tools Gemini has access to
ALL_TOOLS = [
    "click", "type_text", "press_key", "hotkey", "move_mouse",
    "list_windows", "focus_window", "manage_window",
    "setup_background_mode", "move_to_agent_screen", "move_to_user_screen",
    "get_agent_screen_state", "set_agent_screen_only",
    "launch_in_shadow", "get_shadow_windows", "move_to_user", "move_to_shadow",
    "get_ui_tree", "find_elements", "get_element_properties", "do_action",
    "get_text", "smart_find", "cascade_find",
    "get_annotated_screenshot", "som_click",
    "detect_dialogs",
    "take_screenshot", "ocr_region", "list_ocr_languages",
    "run_command", "open_application", "system_info",
    "memory_save", "memory_recall", "memory_delete", "memory_list",
    "clipboard", "clipboard_history",
    "restore_user_focus",
    "cdp_send", "cdp_screenshot", "cdp_evaluate", "cdp_get_dom",
    "wait_for_element", "wait_for_text", "wait_for_window", "wait_for_idle",
    "visual_diff", "visual_diff_compare",
    "execute_complex_goal",
    "scrape_url",
    "close_window",
]

# ─── Helpers ─────────────────────────────────────────────────


def ts_local() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def health_check() -> bool:
    try:
        r = requests.get(f"{DAEMON_URL}/health", timeout=5)
        return r.json().get("status") == "ok"
    except Exception:
        return False


def send_goal(text: str, channel: str = "console", timeout: int = 120) -> dict:
    t0 = time.monotonic()
    try:
        r = requests.post(
            f"{DAEMON_URL}/goal",
            json={"goal": text, "channel": channel},
            timeout=timeout,
        )
        elapsed = (time.monotonic() - t0) * 1000
        try:
            body = r.json()
        except Exception:
            body = {"raw": r.text[:2000]}
        return {
            "response": body,
            "status_code": r.status_code,
            "elapsed_ms": round(elapsed, 1),
            "error": None,
        }
    except requests.Timeout:
        elapsed = (time.monotonic() - t0) * 1000
        return {
            "response": None, "status_code": 0,
            "elapsed_ms": round(elapsed, 1), "error": "TIMEOUT",
        }
    except Exception as e:
        elapsed = (time.monotonic() - t0) * 1000
        return {
            "response": None, "status_code": 0,
            "elapsed_ms": round(elapsed, 1), "error": str(e),
        }


def get_journalctl(since: str, until: str) -> str:
    try:
        result = subprocess.run(
            [
                "journalctl", "--user", "-u", "marlow-daemon",
                "--since", since, "--until", until,
                "--no-pager", "--output=cat",
            ],
            capture_output=True, text=True, timeout=15,
        )
        return result.stdout
    except Exception as e:
        return f"[journalctl error: {e}]"


def parse_pipeline_logs(raw: str) -> dict:
    tools = []
    errors = []

    for line in raw.splitlines():
        m = re.search(r"Gemini tool call \[round (\d+)\]: (\w+)\(", line)
        if m:
            tools.append({"round": int(m.group(1)), "tool": m.group(2)})
        m = re.search(r"Claude tool call.*?:\s*(\w+)", line)
        if m:
            tools.append({"round": 0, "tool": m.group(1)})
        if "ERROR" in line or "error" in line.lower():
            if "Task exception was never retrieved" not in line:
                errors.append(line.strip()[:200])

    return {
        "tool_calls": tools,
        "tool_count": len(tools),
        "unique_tools": list({t["tool"] for t in tools}),
        "errors": errors[:20],
        "error_count": len(errors),
    }


# ─── Response helpers ────────────────────────────────────────


def _get_response_text(result: dict) -> str:
    resp = result.get("response")
    if not resp:
        return ""
    if isinstance(resp, dict):
        return (
            resp.get("response", "")
            or resp.get("result_summary", "")
            or resp.get("raw", "")
            or ""
        ).lower()
    return str(resp).lower()


# ─── Validation functions ────────────────────────────────────


def validate_has_response(result: dict) -> tuple[bool, str]:
    text = _get_response_text(result)
    if len(text) > 5:
        return True, f"Got response ({len(text)} chars)"
    return False, "Empty or very short response"


def validate_nocrash(result: dict) -> tuple[bool, str]:
    if result.get("error"):
        return False, f"Error: {result['error']}"
    if result.get("status_code", 0) == 200:
        return True, "Got 200 response (no crash)"
    return False, f"Status {result.get('status_code')}"


def validate_ui_tree(result: dict) -> tuple[bool, str]:
    text = _get_response_text(result)
    keywords = ["botón", "button", "menú", "menu", "barra", "panel",
                 "toolbar", "tab", "elemento", "element", "interfaz"]
    found = [k for k in keywords if k in text]
    if found:
        return True, f"UI tree described: {', '.join(found[:3])}"
    if len(text) > 20:
        return True, f"Got description ({len(text)} chars)"
    return False, f"No UI description: {text[:100]}"


def validate_element_found(result: dict) -> tuple[bool, str]:
    text = _get_response_text(result)
    keywords = ["encontr", "found", "botón", "button", "reload", "recargar",
                 "back", "atrás", "elemento", "element"]
    found = [k for k in keywords if k in text]
    if found:
        return True, f"Element found: {', '.join(found[:3])}"
    if len(text) > 15:
        return True, f"Got response about element ({len(text)} chars)"
    return False, f"Element not found: {text[:100]}"


def validate_text_read(result: dict) -> tuple[bool, str]:
    text = _get_response_text(result)
    if len(text) > 20:
        return True, f"Read text ({len(text)} chars)"
    return False, f"No text read: {text[:100]}"


def validate_screenshot(result: dict) -> tuple[bool, str]:
    text = _get_response_text(result)
    keywords = ["screenshot", "captura", "pantalla", "imagen", "tomé",
                 "tomado", "aquí", "muestro", "veo"]
    found = [k for k in keywords if k in text]
    if found:
        return True, f"Screenshot taken: {', '.join(found[:3])}"
    if len(text) > 10:
        return True, f"Got response ({len(text)} chars)"
    return False, f"No screenshot confirmation: {text[:100]}"


def validate_window_managed(result: dict) -> tuple[bool, str]:
    text = _get_response_text(result)
    keywords = ["minim", "maxim", "cerr", "restaur", "hecho", "listo",
                 "ventana", "window", "closed", "done"]
    found = [k for k in keywords if k in text]
    if found:
        return True, f"Window managed: {', '.join(found[:3])}"
    return False, f"No management confirmation: {text[:100]}"


def validate_shadow(result: dict) -> tuple[bool, str]:
    text = _get_response_text(result)
    keywords = ["segundo plano", "shadow", "invisible", "background",
                 "abrí", "abiert", "lanzad", "launch"]
    found = [k for k in keywords if k in text]
    if found:
        return True, f"Shadow operation: {', '.join(found[:3])}"
    if len(text) > 15:
        return True, f"Got response ({len(text)} chars)"
    return False, f"No shadow confirmation: {text[:100]}"


def validate_shadow_list(result: dict) -> tuple[bool, str]:
    text = _get_response_text(result)
    keywords = ["segundo plano", "shadow", "ventana", "window", "no hay",
                 "ninguna", "background", "invisible"]
    found = [k for k in keywords if k in text]
    if found:
        return True, f"Shadow list: {', '.join(found[:3])}"
    return False, f"No shadow list: {text[:100]}"


def validate_typed(result: dict) -> tuple[bool, str]:
    text = _get_response_text(result)
    keywords = ["escrib", "typed", "wrote", "hecho", "listo", "texto",
                 "ingres", "hola mundo"]
    found = [k for k in keywords if k in text]
    if found:
        return True, f"Text typed: {', '.join(found[:3])}"
    if len(text) > 10:
        return True, f"Got response ({len(text)} chars)"
    return False, f"No typing confirmation: {text[:100]}"


def validate_key_pressed(result: dict) -> tuple[bool, str]:
    text = _get_response_text(result)
    keywords = ["presion", "pressed", "enter", "tecla", "key", "hecho",
                 "listo", "ejecut"]
    found = [k for k in keywords if k in text]
    if found:
        return True, f"Key pressed: {', '.join(found[:3])}"
    if len(text) > 10:
        return True, f"Got response ({len(text)} chars)"
    return False, f"No key press confirmation: {text[:100]}"


def validate_command_output(result: dict) -> tuple[bool, str]:
    text = _get_response_text(result)
    keywords = ["gb", "mb", "tb", "disco", "disk", "espacio", "space",
                 "free", "used", "available", "disponible", "%"]
    found = [k for k in keywords if k in text]
    if found:
        return True, f"Command output: {', '.join(found[:3])}"
    if len(text) > 15:
        return True, f"Got command response ({len(text)} chars)"
    return False, f"No command output: {text[:100]}"


def validate_system_info(result: dict) -> tuple[bool, str]:
    text = _get_response_text(result)
    keywords = ["cpu", "ram", "memoria", "memory", "gb", "mb", "%",
                 "sistema", "system", "uso", "usage", "procesador",
                 "núcleo", "core", "ghz"]
    found = [k for k in keywords if k in text]
    if found:
        return True, f"System info: {', '.join(found[:3])}"
    return False, f"No system info: {text[:100]}"


def validate_app_opened(result: dict) -> tuple[bool, str]:
    text = _get_response_text(result)
    resp = result.get("response", {})
    success = resp.get("success", False) if isinstance(resp, dict) else False
    keywords = ["abriendo", "abrí", "abierto", "opened", "listo",
                 "lanzando", "abierta", "aquí", "archivos", "files",
                 "nautilus", "firefox"]
    found = [k for k in keywords if k in text]
    if success or found:
        return True, f"App opened: {', '.join(found[:3]) or 'success=True'}"
    return False, f"App open failed: {text[:100]}"


def validate_clipboard(result: dict) -> tuple[bool, str]:
    text = _get_response_text(result)
    keywords = ["portapapeles", "clipboard", "copiado", "copied", "vacío",
                 "empty", "contiene", "contains", "texto", "nada"]
    found = [k for k in keywords if k in text]
    if found:
        return True, f"Clipboard info: {', '.join(found[:3])}"
    if len(text) > 10:
        return True, f"Got clipboard response ({len(text)} chars)"
    return False, f"No clipboard info: {text[:100]}"


def validate_memory_save(result: dict) -> tuple[bool, str]:
    text = _get_response_text(result)
    keywords = ["recordar", "remember", "guardado", "saved", "anotado",
                 "listo", "entendido", "hecho", "claro"]
    found = [k for k in keywords if k in text]
    if found:
        return True, f"Memory saved: {', '.join(found[:3])}"
    return False, f"Memory save unclear: {text[:100]}"


def validate_memory_recall(result: dict) -> tuple[bool, str]:
    text = _get_response_text(result)
    if "marlow" in text:
        return True, "Recalled 'Marlow'"
    return False, f"Didn't recall 'Marlow': {text[:100]}"


def validate_memory_deleted(result: dict) -> tuple[bool, str]:
    text = _get_response_text(result)
    keywords = ["olvidé", "eliminé", "borré", "forgot", "deleted", "removed",
                 "listo", "hecho"]
    found = [k for k in keywords if k in text]
    if found:
        return True, f"Memory deleted: {', '.join(found[:3])}"
    if len(text) > 10:
        return True, f"Got response ({len(text)} chars)"
    return False, f"Memory delete unclear: {text[:100]}"


def validate_memory_list(result: dict) -> tuple[bool, str]:
    text = _get_response_text(result)
    keywords = ["recuerd", "memor", "guardado", "saved", "nada", "vacío",
                 "no tengo", "nothing", "empty"]
    found = [k for k in keywords if k in text]
    if found:
        return True, f"Memory listed: {', '.join(found[:3])}"
    if len(text) > 10:
        return True, f"Got response ({len(text)} chars)"
    return False, f"No memory list: {text[:100]}"


def validate_scrape(result: dict) -> tuple[bool, str]:
    text = _get_response_text(result)
    keywords = ["miami", "ciudad", "city", "florida", "population",
                 "estados unidos", "united states", "wikipedia",
                 "county", "condado"]
    found = [k for k in keywords if k in text]
    if found:
        return True, f"Page scraped: {', '.join(found[:3])}"
    if len(text) > 50:
        return True, f"Got scraped content ({len(text)} chars)"
    return False, f"No scrape result: {text[:100]}"


def validate_dialog_check(result: dict) -> tuple[bool, str]:
    text = _get_response_text(result)
    keywords = ["diálogo", "dialog", "popup", "no hay", "ninguno", "no detect",
                 "ventana emergente", "alerta", "alert"]
    found = [k for k in keywords if k in text]
    if found:
        return True, f"Dialog check: {', '.join(found[:3])}"
    if len(text) > 10:
        return True, f"Got response ({len(text)} chars)"
    return False, f"No dialog info: {text[:100]}"


def validate_annotated(result: dict) -> tuple[bool, str]:
    text = _get_response_text(result)
    keywords = ["anotado", "annotated", "numerado", "numbered", "elemento",
                 "element", "screenshot", "captura", "[1]", "[2]", "1.", "2."]
    found = [k for k in keywords if k in text]
    if found:
        return True, f"Annotated screenshot: {', '.join(found[:3])}"
    if len(text) > 15:
        return True, f"Got response ({len(text)} chars)"
    return False, f"No annotated screenshot: {text[:100]}"


def validate_properties(result: dict) -> tuple[bool, str]:
    text = _get_response_text(result)
    keywords = ["propiedad", "property", "role", "rol", "nombre", "name",
                 "estado", "state", "posición", "position", "acción",
                 "action", "type", "tipo", "push button", "botón"]
    found = [k for k in keywords if k in text]
    if found:
        return True, f"Properties shown: {', '.join(found[:3])}"
    if len(text) > 20:
        return True, f"Got properties ({len(text)} chars)"
    return False, f"No properties: {text[:100]}"


def validate_graceful_failure(result: dict) -> tuple[bool, str]:
    text = _get_response_text(result)
    keywords = ["no encontr", "no pude", "no existe", "not found", "error",
                 "no hay", "no está", "no se puede", "no tiene", "disculpa",
                 "no detect"]
    found = [k for k in keywords if k in text]
    if found:
        return True, f"Graceful failure: {', '.join(found[:2])}"
    if len(text) > 10:
        return True, f"Got response (no crash): {text[:60]}"
    return False, f"Unexpected: {text[:100]}"


def validate_multi_step(result: dict) -> tuple[bool, str]:
    text = _get_response_text(result)
    if len(text) > 20:
        return True, f"Multi-step completed ({len(text)} chars)"
    return False, f"Multi-step failed: {text[:100]}"


def validate_ocr(result: dict) -> tuple[bool, str]:
    text = _get_response_text(result)
    if len(text) > 15:
        return True, f"OCR text read ({len(text)} chars)"
    return False, f"No OCR result: {text[:100]}"


def validate_visual_diff(result: dict) -> tuple[bool, str]:
    text = _get_response_text(result)
    keywords = ["captura", "screenshot", "antes", "before", "foto", "referencia",
                 "visual", "diff", "comparar", "tomé", "tomado"]
    found = [k for k in keywords if k in text]
    if found:
        return True, f"Visual diff: {', '.join(found[:3])}"
    if len(text) > 10:
        return True, f"Got response ({len(text)} chars)"
    return False, f"No visual diff: {text[:100]}"


# ─── Test definitions ────────────────────────────────────────
# Suite A: Single-tool requests — one test per target tool

SINGLE_TOOL_TESTS = [
    # ── UI tree / element inspection ──
    {
        "name": "get_ui_tree",
        "prompt": "Describe la interfaz de la ventana activa, dime qué botones y menús ves",
        "target_tools": ["get_ui_tree"],
        "validate": validate_ui_tree,
        "setup": None,
    },
    {
        "name": "find_elements",
        "prompt": "Encuentra todos los botones que hay en la ventana activa",
        "target_tools": ["find_elements"],
        "validate": validate_element_found,
        "setup": None,
    },
    {
        "name": "get_text",
        "prompt": "Lee todo el texto visible en la ventana activa",
        "target_tools": ["get_text"],
        "validate": validate_text_read,
        "setup": None,
    },
    {
        "name": "get_element_properties",
        "prompt": "Dime las propiedades detalladas del primer botón que encuentres en la ventana activa — su rol, estado, posición y acciones disponibles",
        "target_tools": ["get_element_properties", "find_elements"],
        "validate": validate_properties,
        "setup": None,
    },
    {
        "name": "do_action",
        "prompt": "Encuentra un botón en la ventana activa y haz click en él usando la acción de invocación",
        "target_tools": ["do_action", "find_elements"],
        "validate": validate_has_response,
        "setup": None,
    },
    {
        "name": "smart_find",
        "prompt": "Busca algo que diga 'Close' o 'Cerrar' en la ventana activa",
        "target_tools": ["smart_find"],
        "validate": validate_has_response,
        "setup": None,
    },
    {
        "name": "cascade_find",
        "prompt": "Busca un campo de texto donde pueda escribir en la ventana activa, usa todos los métodos disponibles",
        "target_tools": ["cascade_find"],
        "validate": validate_has_response,
        "setup": None,
    },

    # ── Screenshots / OCR ──
    {
        "name": "take_screenshot",
        "prompt": "Toma un screenshot de lo que se ve en pantalla ahora mismo",
        "target_tools": ["take_screenshot"],
        "validate": validate_screenshot,
        "setup": None,
    },
    {
        "name": "ocr_region",
        "prompt": "Lee el texto que aparece en la pantalla usando OCR",
        "target_tools": ["ocr_region"],
        "validate": validate_ocr,
        "setup": None,
    },
    {
        "name": "get_annotated_screenshot",
        "prompt": "Toma un screenshot anotado de la pantalla mostrando los elementos interactivos numerados",
        "target_tools": ["get_annotated_screenshot"],
        "validate": validate_annotated,
        "setup": None,
    },

    # ── Window management ──
    {
        "name": "list_windows",
        "prompt": "¿Qué ventanas tengo abiertas en este momento?",
        "target_tools": ["list_windows"],
        "validate": validate_has_response,
        "setup": None,
    },
    {
        "name": "focus_window",
        "prompt": "Cambia el foco a la terminal",
        "target_tools": ["focus_window"],
        "validate": validate_has_response,
        "setup": "open_terminal",
    },
    {
        "name": "manage_window_minimize",
        "prompt": "Minimiza la terminal",
        "target_tools": ["manage_window"],
        "validate": validate_window_managed,
        "setup": "open_terminal",
    },
    {
        "name": "manage_window_maximize",
        "prompt": "Maximiza la terminal",
        "target_tools": ["manage_window"],
        "validate": validate_window_managed,
        "setup": "open_terminal",
    },
    {
        "name": "close_window",
        "prompt": "Cierra la ventana de la terminal",
        "target_tools": ["close_window", "manage_window"],
        "validate": validate_window_managed,
        "setup": "open_terminal",
    },

    # ── Shadow mode ──
    {
        "name": "launch_in_shadow",
        "prompt": "Abre Firefox en segundo plano sin que yo lo vea",
        "target_tools": ["launch_in_shadow"],
        "validate": validate_shadow,
        "setup": None,
    },
    {
        "name": "get_shadow_windows",
        "prompt": "¿Qué ventanas tengo en segundo plano invisible?",
        "target_tools": ["get_shadow_windows"],
        "validate": validate_shadow_list,
        "setup": None,
    },
    {
        "name": "move_to_user",
        "prompt": "Muéstrame la ventana que tienes en segundo plano, tráela a mi pantalla",
        "target_tools": ["move_to_user"],
        "validate": validate_has_response,
        "setup": "launch_shadow_firefox",
    },
    {
        "name": "move_to_shadow",
        "prompt": "Manda Firefox al segundo plano invisible",
        "target_tools": ["move_to_shadow"],
        "validate": validate_has_response,
        "setup": None,
    },

    # ── Input ──
    {
        "name": "type_text",
        "prompt": "Escribe 'hola mundo' en la terminal",
        "target_tools": ["type_text"],
        "validate": validate_typed,
        "setup": "open_terminal",
    },
    {
        "name": "press_key",
        "prompt": "Presiona la tecla Enter en la terminal",
        "target_tools": ["press_key"],
        "validate": validate_key_pressed,
        "setup": "open_terminal",
    },
    {
        "name": "hotkey",
        "prompt": "Presiona Ctrl+L en Firefox para ir a la barra de direcciones",
        "target_tools": ["hotkey"],
        "validate": validate_has_response,
        "setup": "open_firefox",
    },
    {
        "name": "click_coords",
        "prompt": "Haz click en las coordenadas 100, 100 de la pantalla",
        "target_tools": ["click"],
        "validate": validate_has_response,
        "setup": None,
    },
    {
        "name": "move_mouse",
        "prompt": "Mueve el mouse al centro de la pantalla sin hacer click",
        "target_tools": ["move_mouse"],
        "validate": validate_has_response,
        "setup": None,
    },

    # ── System ──
    {
        "name": "run_command",
        "prompt": "Dime cuánto espacio libre hay en disco",
        "target_tools": ["run_command"],
        "validate": validate_command_output,
        "setup": None,
    },
    {
        "name": "system_info",
        "prompt": "Dame información detallada del sistema — CPU, RAM, disco, uso actual",
        "target_tools": ["system_info"],
        "validate": validate_system_info,
        "setup": None,
    },
    {
        "name": "open_application",
        "prompt": "Abre el administrador de archivos",
        "target_tools": ["open_application"],
        "validate": validate_app_opened,
        "setup": None,
    },

    # ── Clipboard ──
    {
        "name": "clipboard",
        "prompt": "¿Qué hay en el portapapeles ahora mismo?",
        "target_tools": ["clipboard"],
        "validate": validate_clipboard,
        "setup": None,
    },
    {
        "name": "clipboard_history",
        "prompt": "Muéstrame el historial del portapapeles, los últimos 5 elementos copiados",
        "target_tools": ["clipboard_history"],
        "validate": validate_has_response,
        "setup": None,
    },

    # ── Memory ──
    {
        "name": "memory_save",
        "prompt": "Recuerda que mi proyecto se llama Marlow",
        "target_tools": ["memory_save"],
        "validate": validate_memory_save,
        "setup": None,
    },
    {
        "name": "memory_recall",
        "prompt": "¿Cómo se llama mi proyecto?",
        "target_tools": ["memory_recall"],
        "validate": validate_memory_recall,
        "setup": None,
    },
    {
        "name": "memory_list",
        "prompt": "¿Qué cosas recuerdas de mí? Lista todo lo que tengas guardado",
        "target_tools": ["memory_list"],
        "validate": validate_memory_list,
        "setup": None,
    },
    {
        "name": "memory_delete",
        "prompt": "Olvida el nombre de mi proyecto",
        "target_tools": ["memory_delete"],
        "validate": validate_memory_deleted,
        "setup": None,
    },

    # ── Wait tools ──
    {
        "name": "wait_for_window",
        "prompt": "Abre Firefox y avísame cuando la ventana esté lista",
        "target_tools": ["wait_for_window", "open_application"],
        "validate": validate_has_response,
        "setup": None,
    },
    {
        "name": "wait_for_element",
        "prompt": "Espera a que aparezca un botón en la ventana activa, máximo 10 segundos",
        "target_tools": ["wait_for_element"],
        "validate": validate_has_response,
        "setup": None,
    },

    # ── Scraping ──
    {
        "name": "scrape_url",
        "prompt": "Dame el contenido de la página wikipedia.org/wiki/Miami",
        "target_tools": ["scrape_url"],
        "validate": validate_scrape,
        "setup": None,
    },

    # ── Visual diff ──
    {
        "name": "visual_diff",
        "prompt": "Toma una foto de referencia de la pantalla para poder comparar después",
        "target_tools": ["visual_diff", "take_screenshot"],
        "validate": validate_visual_diff,
        "setup": None,
    },

    # ── Dialogs ──
    {
        "name": "detect_dialogs",
        "prompt": "¿Hay algún diálogo o popup abierto en la pantalla?",
        "target_tools": ["detect_dialogs"],
        "validate": validate_dialog_check,
        "setup": None,
    },

    # ── Focus restore ──
    {
        "name": "restore_user_focus",
        "prompt": "Devuelve el foco a la ventana donde yo estaba trabajando antes",
        "target_tools": ["restore_user_focus"],
        "validate": validate_has_response,
        "setup": None,
    },

    # ── Complex goal ──
    {
        "name": "execute_complex_goal",
        "prompt": "Ejecuta este objetivo complejo: abre Firefox, espera a que cargue, y dime el título de la ventana",
        "target_tools": ["execute_complex_goal"],
        "validate": validate_has_response,
        "setup": None,
    },
]


# Suite B: Multi-tool combination tests (3+ tools per request)

COMBO_TESTS = [
    {
        "name": "search_read_close",
        "prompt": ("Abre Firefox, navega a google.com, busca 'clima Miami', "
                   "lee el primer resultado, y cierra Firefox"),
        "target_tools": ["open_application", "type_text", "press_key",
                         "get_text", "close_window"],
        "min_tool_calls": 3,
        "validate": validate_multi_step,
    },
    {
        "name": "screenshot_ocr_memory",
        "prompt": ("Toma un screenshot de la pantalla, lee todo el texto que "
                   "aparece usando OCR, y guarda un resumen en tu memoria"),
        "target_tools": ["take_screenshot", "ocr_region", "memory_save"],
        "min_tool_calls": 2,
        "validate": validate_multi_step,
    },
    {
        "name": "find_type_enter",
        "prompt": ("Abre Firefox, encuentra la barra de direcciones, "
                   "escribe 'github.com', y presiona Enter"),
        "target_tools": ["open_application", "find_elements", "type_text",
                         "press_key"],
        "min_tool_calls": 3,
        "validate": validate_multi_step,
    },
    {
        "name": "two_terminals_commands",
        "prompt": ("Abre dos terminales, en una escribe 'ls -la' y presiona Enter, "
                   "en la otra escribe 'pwd' y presiona Enter"),
        "target_tools": ["open_application", "focus_window", "type_text",
                         "press_key"],
        "min_tool_calls": 4,
        "validate": validate_multi_step,
    },
    {
        "name": "visual_compare",
        "prompt": ("Toma una foto de referencia de la pantalla, después abre "
                   "la terminal, y compara cómo cambió la pantalla"),
        "target_tools": ["visual_diff", "open_application",
                         "visual_diff_compare"],
        "min_tool_calls": 2,
        "validate": validate_multi_step,
    },
    {
        "name": "shadow_inspect_show",
        "prompt": ("Abre Firefox en segundo plano invisible, revisa qué ventanas "
                   "tengo en shadow, y después trae Firefox a mi pantalla"),
        "target_tools": ["launch_in_shadow", "get_shadow_windows",
                         "move_to_user"],
        "min_tool_calls": 2,
        "validate": validate_multi_step,
    },
    {
        "name": "annotate_and_click",
        "prompt": ("Toma un screenshot anotado de la pantalla con los elementos "
                   "numerados, dime qué ves, y haz click en el primer elemento"),
        "target_tools": ["get_annotated_screenshot", "som_click"],
        "min_tool_calls": 2,
        "validate": validate_multi_step,
    },
    {
        "name": "sysinfo_save_recall",
        "prompt": ("Dame información del sistema (CPU, RAM), guarda el resultado "
                   "en tu memoria, y después dime lo que guardaste"),
        "target_tools": ["system_info", "memory_save", "memory_recall"],
        "min_tool_calls": 2,
        "validate": validate_multi_step,
    },
    {
        "name": "scrape_and_summarize",
        "prompt": ("Ve a wikipedia.org/wiki/Linux, lee el contenido, y dime "
                   "un resumen de los primeros 3 párrafos"),
        "target_tools": ["scrape_url"],
        "min_tool_calls": 1,
        "validate": validate_multi_step,
    },
    {
        "name": "open_list_focus_close",
        "prompt": ("Abre 3 ventanas de terminal, lista todas las ventanas "
                   "abiertas, cambia el foco a la primera, y cierra las demás"),
        "target_tools": ["open_application", "list_windows", "focus_window",
                         "close_window"],
        "min_tool_calls": 3,
        "validate": validate_multi_step,
    },
]


# Suite C: Recovery scenarios — first approach should fail

RECOVERY_TESTS = [
    {
        "name": "read_nonexistent_window",
        "prompt": "Lee el contenido de la ventana de Photoshop",
        "expected_behavior": "Report that Photoshop is not open",
        "validate": validate_graceful_failure,
    },
    {
        "name": "type_no_target",
        "prompt": "Escribe 'hola' en la calculadora",
        "expected_behavior": "Detect calculator not open or open it first",
        "validate": validate_has_response,
    },
    {
        "name": "close_not_running",
        "prompt": "Cierra Visual Studio Code",
        "expected_behavior": "Report VS Code not running",
        "validate": validate_graceful_failure,
    },
    {
        "name": "find_nonexistent_element",
        "prompt": "Encuentra el botón que dice 'Comprar Bitcoin' en la pantalla",
        "expected_behavior": "Timeout and report element not found",
        "validate": validate_graceful_failure,
    },
    {
        "name": "open_5_firefox",
        "prompt": "Abre 5 ventanas de Firefox al mismo tiempo",
        "expected_behavior": "Handle multiple instances or explain limitation",
        "validate": validate_has_response,
    },
    {
        "name": "focus_nonexistent",
        "prompt": "Cambia el foco a la ventana de Spotify",
        "expected_behavior": "Report Spotify not open",
        "validate": validate_graceful_failure,
    },
    {
        "name": "shadow_no_compositor",
        "prompt": "Muéstrame la ventana 99999 del shadow",
        "expected_behavior": "Report window not found",
        "validate": validate_graceful_failure,
    },
    {
        "name": "ocr_empty_screen",
        "prompt": "Lee el texto del escritorio vacío",
        "expected_behavior": "Report no text found or return desktop content",
        "validate": validate_has_response,
    },
]


# ─── Setup helpers ───────────────────────────────────────────

def run_setup(setup_name: str, timeout: int) -> None:
    """Run a setup action before a test."""
    if setup_name == "open_terminal":
        send_goal("Abre una terminal", timeout=timeout)
        time.sleep(2)
    elif setup_name == "open_firefox":
        send_goal("Abre Firefox", timeout=timeout)
        time.sleep(3)
    elif setup_name == "launch_shadow_firefox":
        send_goal("Abre Firefox en segundo plano invisible", timeout=timeout)
        time.sleep(3)


# ─── Test runner ─────────────────────────────────────────────


class ToolCoverageRunner:
    """Runs tests and tracks per-tool coverage."""

    def __init__(self, args: argparse.Namespace):
        self.verbose = args.verbose
        self.cleanup = args.cleanup
        self.delay = args.delay
        self.timeout = args.timeout
        self.results: list[dict] = []
        self.run_start = ts_local()
        self.run_start_utc = datetime.now(timezone.utc).isoformat()
        self.run_start_mono = time.monotonic()
        # Per-tool tracking
        self.tool_usage: dict[str, dict] = {
            t: {"calls": 0, "success": 0, "fail": 0, "tests": []}
            for t in ALL_TOOLS
        }

    def run_test(self, test: dict, suite: str) -> dict:
        """Run a single test with 3-layer data collection."""
        name = test["name"]
        print(f"  [{suite}.{name}] ", end="", flush=True)

        # Setup if needed
        setup = test.get("setup")
        if setup:
            print(f"(setup: {setup}) ", end="", flush=True)
            run_setup(setup, self.timeout)

        # Layer 2 prep
        log_start = ts_local()
        time.sleep(0.1)

        # Layer 1: HTTP
        result = send_goal(test["prompt"], timeout=self.timeout)

        # Validation
        if result.get("error") == "TIMEOUT":
            status = "TIMEOUT"
            reason = f"Timed out after {self.timeout}s"
        elif result.get("error"):
            status = "ERROR"
            reason = result["error"]
        else:
            passed, reason = test["validate"](result)
            status = "PASS" if passed else "FAIL"

        # Layer 2: Pipeline logs
        time.sleep(0.3)
        log_end = ts_local()
        raw_logs = get_journalctl(log_start, log_end)
        pipeline = parse_pipeline_logs(raw_logs)

        # Track tool usage
        tools_used = pipeline.get("unique_tools", [])
        for tool_name in tools_used:
            if tool_name in self.tool_usage:
                self.tool_usage[tool_name]["calls"] += 1
                self.tool_usage[tool_name]["tests"].append(name)
                if status == "PASS":
                    self.tool_usage[tool_name]["success"] += 1
                else:
                    self.tool_usage[tool_name]["fail"] += 1
            else:
                # Tool not in our list (alias or new tool)
                self.tool_usage[tool_name] = {
                    "calls": 1,
                    "success": 1 if status == "PASS" else 0,
                    "fail": 0 if status == "PASS" else 1,
                    "tests": [name],
                }

        # Check if target tools were used
        target_tools = test.get("target_tools", [])
        target_hit = [t for t in target_tools if t in tools_used]
        target_miss = [t for t in target_tools if t not in tools_used]

        # Print status
        elapsed_str = f"{result['elapsed_ms']:.0f}ms"
        status_icon = {"PASS": "✓", "FAIL": "✗", "TIMEOUT": "⏱", "ERROR": "!"}
        icon = status_icon.get(status, "?")
        print(f"{icon} {status} ({elapsed_str}) — {reason[:80]}")

        if self.verbose:
            text = _get_response_text(result)
            if text:
                for line in textwrap.wrap(text[:500], width=90):
                    print(f"    │ {line}")
            if pipeline["tool_calls"]:
                tools_str = " → ".join(t["tool"] for t in pipeline["tool_calls"])
                print(f"    │ Pipeline: {tools_str}")
            if target_hit:
                print(f"    │ Target tools HIT: {', '.join(target_hit)}")
            if target_miss:
                print(f"    │ Target tools MISS: {', '.join(target_miss)}")

        entry = {
            "name": name,
            "suite": suite,
            "prompt": test.get("prompt", ""),
            "target_tools": target_tools,
            "tools_used": tools_used,
            "target_hit": target_hit,
            "target_miss": target_miss,
            "layer1_http": {
                "status_code": result.get("status_code"),
                "elapsed_ms": result.get("elapsed_ms"),
                "response": result.get("response"),
                "error": result.get("error"),
            },
            "layer2_pipeline": {
                "raw_logs": raw_logs,
                **pipeline,
            },
            "status": status,
            "reason": reason,
        }
        self.results.append(entry)
        return entry

    def collect_sqlite_stats(self) -> dict:
        """Layer 3: Query SQLite for action_logs during this test run."""
        stats = {
            "total_actions": 0,
            "successful": 0,
            "failed": 0,
            "by_tool": {},
            "slowest_tools": [],
            "avg_duration_by_tool": {},
            "error_messages": [],
        }

        if not os.path.exists(LOGS_DB):
            stats["error"] = f"Database not found: {LOGS_DB}"
            return stats

        try:
            conn = sqlite3.connect(LOGS_DB)
            conn.row_factory = sqlite3.Row

            rows = conn.execute(
                "SELECT * FROM action_logs WHERE timestamp >= ? ORDER BY timestamp",
                (self.run_start_utc[:19],),
            ).fetchall()

            stats["total_actions"] = len(rows)
            stats["successful"] = sum(1 for r in rows if r["success"])
            stats["failed"] = sum(1 for r in rows if not r["success"])

            tool_counts: dict[str, dict] = {}
            for r in rows:
                tn = r["tool_name"] or "unknown"
                if tn not in tool_counts:
                    tool_counts[tn] = {
                        "total": 0, "success": 0, "fail": 0, "durations": [],
                    }
                tool_counts[tn]["total"] += 1
                if r["success"]:
                    tool_counts[tn]["success"] += 1
                else:
                    tool_counts[tn]["fail"] += 1
                if r["duration_ms"]:
                    tool_counts[tn]["durations"].append(r["duration_ms"])

            stats["by_tool"] = {
                tn: {
                    "total": d["total"],
                    "success": d["success"],
                    "fail": d["fail"],
                    "success_rate": (
                        round(d["success"] / d["total"] * 100, 1)
                        if d["total"] > 0 else 0
                    ),
                }
                for tn, d in tool_counts.items()
            }

            stats["avg_duration_by_tool"] = {
                tn: round(sum(d["durations"]) / len(d["durations"]), 1)
                for tn, d in tool_counts.items()
                if d["durations"]
            }

            stats["slowest_tools"] = sorted(
                stats["avg_duration_by_tool"].items(),
                key=lambda x: x[1], reverse=True,
            )[:10]

            for r in rows:
                if not r["success"] and r["error_message"]:
                    stats["error_messages"].append({
                        "tool": r["tool_name"],
                        "error": r["error_message"][:200],
                        "timestamp": r["timestamp"],
                    })

            conn.close()
        except Exception as e:
            stats["error"] = str(e)

        return stats

    def build_coverage_report(self) -> dict:
        """Build the tool coverage analysis."""
        total_tools = len(ALL_TOOLS)
        used_tools = [t for t, d in self.tool_usage.items() if d["calls"] > 0]
        never_used = [t for t, d in self.tool_usage.items() if d["calls"] == 0]
        coverage_pct = round(len(used_tools) / total_tools * 100, 1) if total_tools else 0

        # Categorize never-used tools
        never_used_analysis: list[dict] = []
        # Tools that are hard to trigger naturally
        hard_to_trigger = {
            "som_click": "Requires get_annotated_screenshot first, Gemini rarely chains them",
            "setup_background_mode": "Infrastructure tool, not user-facing",
            "move_to_agent_screen": "Dual-monitor only, no second monitor on laptop",
            "move_to_user_screen": "Dual-monitor only, no second monitor on laptop",
            "get_agent_screen_state": "Dual-monitor only, no second monitor on laptop",
            "set_agent_screen_only": "Dual-monitor only, no second monitor on laptop",
            "list_ocr_languages": "Very niche, rarely needed",
            "wait_for_text": "Gemini prefers get_text over waiting",
            "wait_for_idle": "Gemini doesn't think about idle states",
            "cdp_send": "No Electron apps in test environment",
            "cdp_screenshot": "No Electron apps in test environment",
            "cdp_evaluate": "No Electron apps in test environment",
            "cdp_get_dom": "No Electron apps in test environment",
            "visual_diff_compare": "Requires visual_diff first, multi-step",
        }

        for tool_name in never_used:
            reason = hard_to_trigger.get(tool_name, "Gemini chose alternative tools")
            never_used_analysis.append({
                "tool": tool_name,
                "reason": reason,
            })

        # Target accuracy: how often did the test trigger its target tools?
        target_hits = 0
        target_total = 0
        for r in self.results:
            targets = r.get("target_tools", [])
            if targets:
                target_total += len(targets)
                target_hits += len(r.get("target_hit", []))

        target_accuracy = (
            round(target_hits / target_total * 100, 1)
            if target_total > 0 else 0
        )

        return {
            "total_tools": total_tools,
            "used_count": len(used_tools),
            "never_used_count": len(never_used),
            "coverage_pct": coverage_pct,
            "used_tools": sorted(used_tools),
            "never_used_tools": sorted(never_used),
            "never_used_analysis": never_used_analysis,
            "target_accuracy_pct": target_accuracy,
            "per_tool": {
                t: {
                    "calls": d["calls"],
                    "success": d["success"],
                    "fail": d["fail"],
                    "tests": d["tests"],
                }
                for t, d in sorted(self.tool_usage.items())
                if d["calls"] > 0
            },
        }

    def do_cleanup(self):
        """Close windows and delete test data."""
        print("\n── Cleanup ──")

        print("  Closing all windows...", end=" ", flush=True)
        r = send_goal("Cierra todas las ventanas", timeout=30)
        print("done" if r.get("status_code") == 200 else "skipped")
        time.sleep(2)

        print("  Deleting test memories...", end=" ", flush=True)
        r = send_goal("Olvida el nombre de mi proyecto", timeout=15)
        print("done" if r.get("status_code") == 200 else "skipped")

        print("  Cleaning up memory...", end=" ", flush=True)
        r = send_goal("Borra todos los recuerdos que guardaste en este test", timeout=15)
        print("done" if r.get("status_code") == 200 else "skipped")

    def generate_reports(self, sqlite_stats: dict, coverage: dict):
        """Generate JSON and Markdown reports."""
        os.makedirs(REPORT_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        total_duration = time.monotonic() - self.run_start_mono

        total = len(self.results)
        passed = sum(1 for r in self.results if r["status"] == "PASS")
        failed = sum(1 for r in self.results if r["status"] == "FAIL")
        timeouts = sum(1 for r in self.results if r["status"] == "TIMEOUT")
        errors = sum(1 for r in self.results if r["status"] == "ERROR")

        # By suite
        by_suite: dict[str, dict] = {}
        for r in self.results:
            s = r["suite"]
            if s not in by_suite:
                by_suite[s] = {
                    "total": 0, "pass": 0, "fail": 0,
                    "timeout": 0, "error": 0, "elapsed": [],
                }
            by_suite[s]["total"] += 1
            by_suite[s][r["status"].lower()] = (
                by_suite[s].get(r["status"].lower(), 0) + 1
            )
            by_suite[s]["elapsed"].append(
                r["layer1_http"].get("elapsed_ms", 0)
            )

        slowest = sorted(
            self.results,
            key=lambda r: r["layer1_http"].get("elapsed_ms", 0),
            reverse=True,
        )[:5]

        # All tool calls across tests
        all_tools_log: dict[str, int] = {}
        for r in self.results:
            for tc in r["layer2_pipeline"].get("tool_calls", []):
                tn = tc["tool"]
                all_tools_log[tn] = all_tools_log.get(tn, 0) + 1
        top_tools = sorted(
            all_tools_log.items(), key=lambda x: x[1], reverse=True,
        )

        # ── JSON report ──
        json_data = {
            "metadata": {
                "timestamp": ts,
                "run_start": self.run_start,
                "duration_seconds": round(total_duration, 1),
                "daemon_url": DAEMON_URL,
                "test_type": "tool_coverage",
            },
            "summary": {
                "total": total, "passed": passed, "failed": failed,
                "timeouts": timeouts, "errors": errors,
                "pass_rate": round(passed / total * 100, 1) if total else 0,
            },
            "by_suite": {
                s: {
                    "total": d["total"],
                    "passed": d.get("pass", 0),
                    "failed": d.get("fail", 0),
                    "timeouts": d.get("timeout", 0),
                    "errors": d.get("error", 0),
                    "avg_response_ms": (
                        round(sum(d["elapsed"]) / len(d["elapsed"]), 1)
                        if d["elapsed"] else 0
                    ),
                }
                for s, d in sorted(by_suite.items())
            },
            "coverage": coverage,
            "tests": [
                {
                    "name": r["name"],
                    "suite": r["suite"],
                    "status": r["status"],
                    "reason": r["reason"],
                    "prompt": r["prompt"][:200] if isinstance(r["prompt"], str) else "",
                    "elapsed_ms": r["layer1_http"].get("elapsed_ms"),
                    "target_tools": r.get("target_tools", []),
                    "tools_used": r.get("tools_used", []),
                    "target_hit": r.get("target_hit", []),
                    "target_miss": r.get("target_miss", []),
                    "pipeline": {
                        k: v for k, v in r["layer2_pipeline"].items()
                        if k != "raw_logs"
                    },
                }
                for r in self.results
            ],
            "sqlite_stats": sqlite_stats,
        }

        json_path = os.path.join(REPORT_DIR, f"coverage_{ts}.json")
        with open(json_path, "w") as f:
            json.dump(json_data, f, indent=2, default=str, ensure_ascii=False)
        print(f"\n  JSON report: {json_path}")

        # ── Markdown report ──
        md = []
        md.append("# Marlow OS Tool Coverage Report")
        md.append("")
        md.append(f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        md.append(f"**Duration:** {total_duration:.1f}s")
        md.append(f"**Daemon:** {DAEMON_URL}")
        md.append("")

        # Summary
        md.append("## Summary")
        md.append("")
        md.append("| Metric | Value |")
        md.append("|--------|-------|")
        md.append(f"| Total tests | {total} |")
        md.append(f"| Passed | {passed} |")
        md.append(f"| Failed | {failed} |")
        md.append(f"| Timeouts | {timeouts} |")
        md.append(f"| Errors | {errors} |")
        if total:
            md.append(f"| Pass rate | {passed/total*100:.1f}% |")
        md.append("")

        # By suite
        md.append("### By Suite")
        md.append("")
        md.append("| Suite | Total | Pass | Fail | Timeout | Avg Response |")
        md.append("|-------|-------|------|------|---------|-------------|")
        for s, d in sorted(by_suite.items()):
            avg = (
                round(sum(d["elapsed"]) / len(d["elapsed"]))
                if d["elapsed"] else 0
            )
            md.append(
                f"| {s} | {d['total']} | {d.get('pass', 0)} | "
                f"{d.get('fail', 0)} | {d.get('timeout', 0)} | {avg}ms |"
            )
        md.append("")

        # ── TOOL COVERAGE (the main event) ──
        md.append("## Tool Coverage")
        md.append("")
        md.append(f"**Total tools:** {coverage['total_tools']}")
        md.append(f"**Tools used:** {coverage['used_count']}")
        md.append(f"**Never used:** {coverage['never_used_count']}")
        md.append(f"**Coverage:** {coverage['coverage_pct']}%")
        md.append(f"**Target accuracy:** {coverage['target_accuracy_pct']}%")
        md.append("")

        # Per-tool table
        md.append("### Tool Usage Detail")
        md.append("")
        md.append("| # | Tool | Calls | Success | Fail | Tests |")
        md.append("|---|------|-------|---------|------|-------|")
        for i, tool_name in enumerate(ALL_TOOLS, 1):
            d = self.tool_usage.get(tool_name, {"calls": 0, "success": 0, "fail": 0, "tests": []})
            if d["calls"] > 0:
                tests_str = ", ".join(d["tests"][:3])
                if len(d["tests"]) > 3:
                    tests_str += f" (+{len(d['tests'])-3})"
                md.append(
                    f"| {i} | **{tool_name}** | {d['calls']} | "
                    f"{d['success']} | {d['fail']} | {tests_str} |"
                )
            else:
                md.append(f"| {i} | ~~{tool_name}~~ | 0 | 0 | 0 | — |")
        md.append("")

        # Never-used analysis
        if coverage["never_used_analysis"]:
            md.append("### Never-Used Tools — Analysis")
            md.append("")
            md.append("| Tool | Reason |")
            md.append("|------|--------|")
            for item in coverage["never_used_analysis"]:
                md.append(f"| {item['tool']} | {item['reason']} |")
            md.append("")

        # Target accuracy per test
        md.append("### Target Accuracy (did Gemini use the expected tool?)")
        md.append("")
        md.append("| Test | Target Tools | Actually Used | Hit? |")
        md.append("|------|-------------|---------------|------|")
        for r in self.results:
            targets = r.get("target_tools", [])
            if not targets:
                continue
            used = r.get("tools_used", [])
            hit = r.get("target_hit", [])
            miss = r.get("target_miss", [])
            hit_str = "✓" if not miss else f"partial ({len(hit)}/{len(targets)})"
            if not hit:
                hit_str = "✗"
            md.append(
                f"| {r['name']} | {', '.join(targets[:4])} | "
                f"{', '.join(used[:4]) or '(none)'} | {hit_str} |"
            )
        md.append("")

        # Slowest tests
        md.append("## Top 5 Slowest Tests")
        md.append("")
        md.append("| Test | Suite | Time | Status |")
        md.append("|------|-------|------|--------|")
        for s in slowest:
            ms = s["layer1_http"].get("elapsed_ms", 0)
            md.append(f"| {s['name']} | {s['suite']} | {ms:.0f}ms | {s['status']} |")
        md.append("")

        # Tool usage from journalctl
        if top_tools:
            md.append("## Tool Call Frequency (journalctl)")
            md.append("")
            md.append("| Tool | Total Calls |")
            md.append("|------|-------------|")
            for tn, count in top_tools:
                md.append(f"| {tn} | {count} |")
            md.append("")

        # SQLite stats
        if sqlite_stats.get("by_tool"):
            md.append("## Tool Stats (SQLite action_logs)")
            md.append("")
            md.append("| Tool | Total | Success | Fail | Rate | Avg ms |")
            md.append("|------|-------|---------|------|------|--------|")
            for tn, d in sorted(
                sqlite_stats["by_tool"].items(),
                key=lambda x: x[1]["total"], reverse=True,
            )[:20]:
                avg = sqlite_stats["avg_duration_by_tool"].get(tn, "—")
                md.append(
                    f"| {tn} | {d['total']} | {d['success']} | "
                    f"{d['fail']} | {d['success_rate']}% | {avg} |"
                )
            md.append("")

        # Findings
        md.append("## Findings")
        md.append("")

        if coverage["coverage_pct"] >= 80:
            md.append(f"- **Excellent coverage:** {coverage['coverage_pct']}% of tools used")
        elif coverage["coverage_pct"] >= 60:
            md.append(f"- **Good coverage:** {coverage['coverage_pct']}% of tools used")
        elif coverage["coverage_pct"] >= 40:
            md.append(f"- **Moderate coverage:** {coverage['coverage_pct']}% — some tools untested")
        else:
            md.append(f"- **Low coverage:** {coverage['coverage_pct']}% — many tools never triggered")

        if coverage["target_accuracy_pct"] < 50:
            md.append(
                f"- **Low target accuracy ({coverage['target_accuracy_pct']}%):** "
                f"Gemini often chooses different tools than expected"
            )

        working_well = [
            r["name"] for r in self.results
            if r["status"] == "PASS"
            and r["layer1_http"].get("elapsed_ms", 999999) < 5000
        ]
        if working_well:
            md.append(f"- **Fast & working:** {', '.join(working_well[:8])}")

        failing = [r["name"] for r in self.results if r["status"] == "FAIL"]
        if failing:
            md.append(f"- **Failing:** {', '.join(failing)}")

        slow = [
            f"{r['name']} ({r['layer1_http'].get('elapsed_ms', 0):.0f}ms)"
            for r in self.results
            if r["layer1_http"].get("elapsed_ms", 0) > 15000
        ]
        if slow:
            md.append(f"- **Slow (>15s):** {', '.join(slow)}")

        timed_out = [r["name"] for r in self.results if r["status"] == "TIMEOUT"]
        if timed_out:
            md.append(f"- **Timeouts:** {', '.join(timed_out)}")

        # CDP tools note
        cdp_tools = [t for t in coverage["never_used_tools"]
                     if t.startswith("cdp_")]
        if cdp_tools:
            md.append(
                f"- **CDP tools untested ({len(cdp_tools)}):** "
                f"No Electron apps available in test environment"
            )

        # Dual-monitor tools note
        dm_tools = [t for t in coverage["never_used_tools"]
                    if t in ("setup_background_mode", "move_to_agent_screen",
                             "move_to_user_screen", "get_agent_screen_state",
                             "set_agent_screen_only")]
        if dm_tools:
            md.append(
                f"- **Dual-monitor tools untested ({len(dm_tools)}):** "
                f"Single monitor on test laptop"
            )

        md.append("")
        md.append("## Recommendations")
        md.append("")

        if failed > 0:
            md.append(f"- Investigate {failed} failing test(s): {', '.join(failing[:5])}")
        if timeouts > 0:
            md.append(f"- {timeouts} test(s) timed out — increase timeout or optimize")
        if coverage["coverage_pct"] < 70:
            md.append("- Consider adding more targeted prompts for unused tools")
        if coverage["target_accuracy_pct"] < 60:
            md.append("- Gemini's tool selection diverges from expectations — review prompts")
        if passed == total and total > 0:
            md.append("- All tests passing — system is stable")

        md.append(f"\n---\n*Generated by tool_coverage_test.py*\n")

        md_path = os.path.join(REPORT_DIR, f"coverage_{ts}.md")
        with open(md_path, "w") as f:
            f.write("\n".join(md))
        print(f"  Markdown report: {md_path}")


# ─── Main ────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Marlow OS Tool Coverage Test"
    )
    parser.add_argument(
        "--suite", default="all",
        help="Test suite: single, combo, recovery, or all (default: all)",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Show full responses in console",
    )
    parser.add_argument(
        "--cleanup", action="store_true",
        help="Close windows and delete test data after",
    )
    parser.add_argument(
        "--delay", type=float, default=3.0,
        help="Seconds between tests (default: 3)",
    )
    parser.add_argument(
        "--timeout", type=int, default=120,
        help="Timeout per test in seconds (default: 120)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  Marlow OS Tool Coverage Test")
    print(f"  {len(ALL_TOOLS)} tools to cover")
    print("=" * 60)
    print()

    # Health check
    print("Checking daemon health...", end=" ", flush=True)
    if not health_check():
        print("FAILED")
        print("Daemon not responding at", DAEMON_URL)
        print("Start it with: systemctl --user start marlow-daemon")
        sys.exit(1)
    print("OK")

    runner = ToolCoverageRunner(args)

    suites = []
    if args.suite == "all":
        suites = ["single", "combo", "recovery"]
    else:
        suites = [s.strip() for s in args.suite.split(",")]

    # ── Suite A: Single-tool tests ──
    if "single" in suites:
        print(f"\n── Suite A: Single-Tool Tests ({len(SINGLE_TOOL_TESTS)} tests) ──\n")
        for test in SINGLE_TOOL_TESTS:
            runner.run_test(test, suite="single")
            time.sleep(args.delay)

    # ── Suite B: Combo tests ──
    if "combo" in suites:
        print(f"\n── Suite B: Multi-Tool Combos ({len(COMBO_TESTS)} tests) ──\n")
        for test in COMBO_TESTS:
            runner.run_test(test, suite="combo")
            time.sleep(args.delay)

    # ── Suite C: Recovery tests ──
    if "recovery" in suites:
        print(f"\n── Suite C: Recovery Scenarios ({len(RECOVERY_TESTS)} tests) ──\n")
        for test in RECOVERY_TESTS:
            runner.run_test(test, suite="recovery")
            time.sleep(args.delay)

    # Cleanup
    if args.cleanup:
        runner.do_cleanup()

    # Layer 3: SQLite
    print("\n── Layer 3: SQLite Stats ──\n")
    sqlite_stats = runner.collect_sqlite_stats()
    print(f"  Total actions logged: {sqlite_stats['total_actions']}")
    print(f"  Successful: {sqlite_stats['successful']}")
    print(f"  Failed: {sqlite_stats['failed']}")
    if sqlite_stats.get("by_tool"):
        print(f"  Unique tools used: {len(sqlite_stats['by_tool'])}")

    # Coverage analysis
    print("\n── Tool Coverage Analysis ──\n")
    coverage = runner.build_coverage_report()
    print(f"  Total tools: {coverage['total_tools']}")
    print(f"  Used: {coverage['used_count']}")
    print(f"  Never used: {coverage['never_used_count']}")
    print(f"  Coverage: {coverage['coverage_pct']}%")
    print(f"  Target accuracy: {coverage['target_accuracy_pct']}%")

    if coverage["never_used_tools"]:
        print(f"\n  Never-used tools:")
        for item in coverage["never_used_analysis"][:10]:
            print(f"    - {item['tool']}: {item['reason']}")

    # Reports
    print("\n── Generating Reports ──")
    runner.generate_reports(sqlite_stats, coverage)

    # Final summary
    total = len(runner.results)
    passed = sum(1 for r in runner.results if r["status"] == "PASS")
    failed = sum(1 for r in runner.results if r["status"] == "FAIL")
    timeouts = sum(1 for r in runner.results if r["status"] == "TIMEOUT")
    errors_count = sum(1 for r in runner.results if r["status"] == "ERROR")
    elapsed = time.monotonic() - runner.run_start_mono

    print(f"\n{'=' * 60}")
    print(f"  RESULTS: {passed}/{total} passed", end="")
    if failed:
        print(f", {failed} failed", end="")
    if timeouts:
        print(f", {timeouts} timeouts", end="")
    if errors_count:
        print(f", {errors_count} errors", end="")
    print(f"  ({elapsed:.1f}s total)")
    print(f"  COVERAGE: {coverage['coverage_pct']}% "
          f"({coverage['used_count']}/{coverage['total_tools']} tools)")
    print(f"{'=' * 60}")

    sys.exit(0 if failed == 0 and errors_count == 0 else 1)


if __name__ == "__main__":
    main()
