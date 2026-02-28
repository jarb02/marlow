"""
Marlow App Framework Detector

Detects the UI framework of running applications by analyzing loaded DLLs.
Used to determine the best automation strategy for each app:
  - Electron/CEF → CDP recommended
  - WPF/WinForms → UIA works well
  - WinUI/UWP → UIA works well but some custom controls need OCR
  - Win32 → UIA + SendMessage

/ Detecta el framework UI de aplicaciones analizando DLLs cargadas.
/ Usado para determinar la mejor estrategia de automatizacion por app.
"""

import os
import logging
import ctypes
from ctypes import wintypes
from typing import Optional

import psutil

logger = logging.getLogger("marlow.core.app_detector")

# ── Framework detection rules ──
# Order matters: more specific checks first
# / Orden importa: checks mas especificos primero

_FRAMEWORK_RULES = [
    # (marker_dlls, framework_name, confidence, cdp_recommended, details_template)
    ({"electron.dll"}, "electron", "high", True,
     "electron.dll loaded — Electron app"),
    ({"libcef.dll"}, "cef", "high", True,
     "libcef.dll loaded — Chromium Embedded Framework"),
    ({"msedge_elf.dll"}, "edge_webview2", "high", False,
     "msedge_elf.dll loaded — Edge WebView2"),
    ({"chrome_elf.dll"}, "chromium", "high", False,
     "chrome_elf.dll loaded — Chromium-based browser"),
    ({"microsoft.ui.xaml.dll"}, "winui3", "high", False,
     "Microsoft.UI.Xaml.dll loaded — WinUI 3 app"),
    ({"windows.ui.xaml.dll"}, "uwp", "medium", False,
     "Windows.UI.Xaml.dll loaded — UWP/XAML app"),
    ({"wpfgfx_cor3.dll"}, "wpf", "high", False,
     "wpfgfx_cor3.dll loaded — WPF (.NET Core) app"),
    ({"wpfgfx_v0400.dll"}, "wpf", "high", False,
     "wpfgfx_v0400.dll loaded — WPF (.NET Framework) app"),
    ({"presentationframework.dll"}, "wpf", "high", False,
     "PresentationFramework.dll loaded — WPF app"),
    ({"clrjit.dll"}, "winforms", "medium", False,
     "clrjit.dll loaded — .NET app (likely WinForms)"),
    ({"mscorlib.dll"}, "winforms", "medium", False,
     "mscorlib.dll loaded — .NET Framework app (likely WinForms)"),
]

# Cache: pid → detection result
# / Cache: pid → resultado de deteccion
_cache: dict[int, dict] = {}


def _get_loaded_dlls(pid: int) -> Optional[set[str]]:
    """
    Get set of loaded DLL/EXE basenames for a process.
    Uses psutil.Process.memory_maps().

    / Obtener set de DLLs cargadas por un proceso via psutil.
    """
    try:
        proc = psutil.Process(pid)
        maps = proc.memory_maps()
        dlls = set()
        for m in maps:
            base = os.path.basename(m.path).lower()
            if base.endswith(".dll") or base.endswith(".exe"):
                dlls.add(base)
        return dlls
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        return None
    except Exception as e:
        logger.debug(f"Failed to get DLLs for PID {pid}: {e}")
        return None


def _check_electron_by_exe(pid: int) -> bool:
    """
    Check if the process executable path contains 'electron'.
    Some Electron apps don't load electron.dll but run from
    an Electron-based executable.

    / Verificar si el path del ejecutable contiene 'electron'.
    """
    try:
        proc = psutil.Process(pid)
        exe = proc.exe().lower()
        return "electron" in exe
    except Exception:
        return False


def _check_electron_by_cmdline(pid: int) -> bool:
    """
    Check if command line args indicate Electron (--type= flag).

    / Verificar si los args de linea de comandos indican Electron.
    """
    try:
        proc = psutil.Process(pid)
        cmdline = " ".join(proc.cmdline()).lower()
        return "--type=" in cmdline and ("electron" in cmdline or "app" in cmdline)
    except Exception:
        return False


def detect_framework(pid: int, use_cache: bool = True) -> dict:
    """
    Detect the UI framework of a process by analyzing loaded DLLs.

    Args:
        pid: Process ID to analyze.
        use_cache: Use cached results if available.

    Returns:
        Dictionary with framework info:
        {framework, confidence, details, cdp_recommended, pid, process_name}

    / Detecta el framework UI de un proceso analizando DLLs cargadas.
    """
    if use_cache and pid in _cache:
        return _cache[pid]

    # Get process name
    # / Obtener nombre del proceso
    try:
        proc = psutil.Process(pid)
        proc_name = proc.name()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return {"error": f"Cannot access process PID {pid}"}

    # Get loaded DLLs
    dlls = _get_loaded_dlls(pid)
    if dlls is None:
        result = {
            "framework": "unknown",
            "confidence": "none",
            "details": "Cannot read process DLLs (access denied or process exited)",
            "cdp_recommended": False,
            "pid": pid,
            "process_name": proc_name,
        }
        _cache[pid] = result
        return result

    # Check against framework rules
    # / Verificar contra reglas de framework
    for markers, framework, confidence, cdp_rec, details in _FRAMEWORK_RULES:
        if markers.issubset(dlls):
            # Special case: chrome_elf without electron → pure Chromium browser
            # But if electron markers also present, it's Electron
            if framework == "chromium" and ("electron.dll" in dlls or _check_electron_by_exe(pid)):
                continue  # Let the electron rule match instead

            result = {
                "framework": framework,
                "confidence": confidence,
                "details": details,
                "cdp_recommended": cdp_rec,
                "pid": pid,
                "process_name": proc_name,
            }
            _cache[pid] = result
            return result

    # Fallback: check Electron by exe path and command line
    # / Fallback: verificar Electron por path del exe y args
    if _check_electron_by_exe(pid) or _check_electron_by_cmdline(pid):
        result = {
            "framework": "electron",
            "confidence": "medium",
            "details": "Electron detected via executable path or command line args",
            "cdp_recommended": True,
            "pid": pid,
            "process_name": proc_name,
        }
        _cache[pid] = result
        return result

    # Default: Win32
    result = {
        "framework": "win32",
        "confidence": "low",
        "details": "No known framework DLLs found — native Win32 app",
        "cdp_recommended": False,
        "pid": pid,
        "process_name": proc_name,
    }
    _cache[pid] = result
    return result


def is_electron(pid: int) -> bool:
    """
    Check if a process is an Electron app.

    / Verifica si un proceso es una app Electron.
    """
    info = detect_framework(pid)
    return info.get("framework") in ("electron", "cef")


def _get_pid_from_hwnd(hwnd: int) -> int:
    """Get PID from a window handle via Win32 API."""
    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def detect_all_windows() -> list[dict]:
    """
    Scan all visible windows and detect the framework of each.
    Results are cached per PID to avoid re-scanning.

    Returns:
        List of dicts with window_title, framework info for each window.

    / Escanea todas las ventanas visibles y detecta el framework de cada una.
    """
    from pywinauto import Desktop

    desktop = Desktop(backend="uia")
    results = []

    for win in desktop.windows():
        title = win.window_text()
        if not title or not title.strip():
            continue

        try:
            hwnd = win.handle
            pid = _get_pid_from_hwnd(hwnd)
            if pid == 0:
                continue

            fw = detect_framework(pid)
            results.append({
                "window_title": title,
                "pid": pid,
                **{k: v for k, v in fw.items() if k != "pid"},
            })
        except Exception:
            continue

    return results


async def detect_app_framework(
    window_title: Optional[str] = None,
) -> dict:
    """
    Detect the UI framework of a window or all visible windows.

    Args:
        window_title: Window to analyze. If None, scans all windows.

    Returns:
        Dictionary with framework detection results.

    / Detecta el framework UI de una ventana o de todas las ventanas visibles.
    """
    try:
        if window_title:
            from marlow.core.uia_utils import find_window

            win, err = find_window(window_title, list_available=True)
            if err:
                return err

            hwnd = win.handle
            pid = _get_pid_from_hwnd(hwnd)
            fw = detect_framework(pid)

            return {
                "success": True,
                "window_title": win.window_text(),
                **fw,
            }

        else:
            windows = detect_all_windows()
            return {
                "success": True,
                "windows": windows,
                "count": len(windows),
            }

    except Exception as e:
        return {"error": str(e)}


def get_framework_hint(pid: int) -> Optional[str]:
    """
    Get a hint about the framework for smart_find results.
    Returns None if no special hint is needed.

    / Obtener hint sobre el framework para resultados de smart_find.
    """
    fw = detect_framework(pid)
    framework = fw.get("framework", "unknown")

    if framework == "electron":
        return (
            "This app is Electron. UIA has limited coverage (~40-60%). "
            "Consider connecting CDP for full access to the DOM."
        )
    if framework == "cef":
        return (
            "This app uses Chromium Embedded Framework. UIA has limited coverage. "
            "Consider connecting CDP for full access."
        )
    return None
