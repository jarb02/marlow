"""Linux diagnostics — verify system readiness for Marlow.

Checks Python, Sway, AT-SPI2, CLI tools, Tesseract, PipeWire,
disk, RAM, GPU, and pip dependencies.

/ Diagnosticos Linux — verifica que el sistema esta listo para Marlow.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import sys

logger = logging.getLogger("marlow.platform.linux.diagnostics")


def run_diagnostics() -> dict:
    """Run all system checks and return results."""
    checks = {}

    # 1. Python version
    py_ver = platform.python_version()
    checks["python"] = {
        "status": "PASS",
        "detail": f"Python {py_ver}",
        "version": py_ver,
    }

    # 2. Sway running + version
    checks["sway"] = _check_sway()

    # 3. AT-SPI2 accessible
    checks["atspi2"] = _check_atspi()

    # 4. CLI tools: wtype, ydotool, grim, wl-copy, wl-paste
    for tool in ("wtype", "ydotool", "grim", "wl-copy", "wl-paste"):
        checks[tool] = _check_tool(tool)

    # 5. Tesseract + languages
    checks["tesseract"] = _check_tesseract()

    # 6. PipeWire status
    checks["pipewire"] = _check_pipewire()

    # 7. Disk space
    checks["disk"] = _check_disk()

    # 8. RAM
    checks["ram"] = _check_ram()

    # 9. GPU info
    checks["gpu"] = _check_gpu()

    # 10. Pip dependencies
    checks["pip_deps"] = _check_pip_deps()

    # Summary
    passed = sum(1 for v in checks.values() if v["status"] == "PASS")
    total = len(checks)

    return {
        "success": True,
        "checks": checks,
        "passed": passed,
        "total": total,
        "all_pass": passed == total,
        "platform": "linux",
        "python": py_ver,
    }


def _check_sway() -> dict:
    try:
        r = subprocess.run(
            ["swaymsg", "-t", "get_version"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            import json
            data = json.loads(r.stdout)
            ver = data.get("human_readable", "unknown")
            return {"status": "PASS", "detail": f"Sway {ver}"}
        return {"status": "FAIL", "detail": f"swaymsg returned {r.returncode}"}
    except FileNotFoundError:
        return {"status": "FAIL", "detail": "swaymsg not found"}
    except Exception as e:
        return {"status": "FAIL", "detail": str(e)}


def _check_atspi() -> dict:
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
        rc = Atspi.init()
        desktop = Atspi.get_desktop(0)
        n_apps = desktop.get_child_count()
        return {
            "status": "PASS",
            "detail": f"AT-SPI2 OK, {n_apps} apps on desktop",
        }
    except Exception as e:
        return {"status": "FAIL", "detail": str(e)}


def _check_tool(name: str) -> dict:
    path = shutil.which(name)
    if path:
        return {"status": "PASS", "detail": f"Found at {path}"}
    return {"status": "FAIL", "detail": f"{name} not found in PATH"}


def _check_tesseract() -> dict:
    path = shutil.which("tesseract")
    if not path:
        return {"status": "FAIL", "detail": "tesseract not found in PATH"}
    try:
        r = subprocess.run(
            ["tesseract", "--list-langs"],
            capture_output=True, text=True, timeout=5,
        )
        langs = [
            l.strip() for l in r.stdout.strip().splitlines()[1:]
            if l.strip()
        ]
        return {
            "status": "PASS",
            "detail": f"Tesseract found, {len(langs)} languages",
            "languages": langs,
        }
    except Exception as e:
        return {"status": "FAIL", "detail": str(e)}


def _check_pipewire() -> dict:
    try:
        r = subprocess.run(
            ["pw-cli", "info", "0"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return {"status": "PASS", "detail": "PipeWire running"}
        return {"status": "FAIL", "detail": f"pw-cli returned {r.returncode}"}
    except FileNotFoundError:
        return {"status": "FAIL", "detail": "pw-cli not found"}
    except Exception as e:
        return {"status": "FAIL", "detail": str(e)}


def _check_disk() -> dict:
    try:
        st = os.statvfs(os.path.expanduser("~"))
        free_gb = round((st.f_bavail * st.f_frsize) / (1024 ** 3), 1)
        total_gb = round((st.f_blocks * st.f_frsize) / (1024 ** 3), 1)
        status = "PASS" if free_gb > 1.0 else "FAIL"
        return {
            "status": status,
            "detail": f"{free_gb} GB free / {total_gb} GB total",
        }
    except Exception as e:
        return {"status": "FAIL", "detail": str(e)}


def _check_ram() -> dict:
    try:
        with open("/proc/meminfo") as f:
            lines = f.readlines()
        info = {}
        for line in lines:
            parts = line.split(":")
            if len(parts) == 2:
                key = parts[0].strip()
                val = parts[1].strip().split()[0]  # kB
                info[key] = int(val)
        total_gb = round(info.get("MemTotal", 0) / (1024 ** 2), 1)
        avail_gb = round(info.get("MemAvailable", 0) / (1024 ** 2), 1)
        status = "PASS" if avail_gb > 0.5 else "FAIL"
        return {
            "status": status,
            "detail": f"{avail_gb} GB available / {total_gb} GB total",
        }
    except Exception as e:
        return {"status": "FAIL", "detail": str(e)}


def _check_gpu() -> dict:
    # Try lspci first
    try:
        r = subprocess.run(
            ["lspci"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            gpus = [
                l.strip() for l in r.stdout.splitlines()
                if "VGA" in l or "3D" in l or "Display" in l
            ]
            if gpus:
                return {"status": "PASS", "detail": gpus[0]}
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fallback: check /proc
    try:
        with open("/proc/driver/nvidia/version") as f:
            return {"status": "PASS", "detail": f.readline().strip()[:80]}
    except FileNotFoundError:
        pass

    return {"status": "PASS", "detail": "No dedicated GPU detected (integrated likely)"}


def _check_pip_deps() -> dict:
    # Map pip package names to importable module names
    required = {
        "mcp": "mcp",
        "Pillow": "PIL",
        "pytesseract": "pytesseract",
        "psutil": "psutil",
        "i3ipc": "i3ipc",
        "websocket-client": "websocket",
        "httpx": "httpx",
        "beautifulsoup4": "bs4",
    }
    missing = []
    for pkg, mod in required.items():
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)

    if not missing:
        return {
            "status": "PASS",
            "detail": f"All {len(required)} core dependencies installed",
        }
    return {
        "status": "FAIL",
        "detail": f"Missing: {', '.join(missing)}",
        "missing": missing,
    }
