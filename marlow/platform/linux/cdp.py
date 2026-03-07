"""Linux CDP adapter — patches platform-specific parts of cdp_manager.

The core CDPManager (WebSocket, HTTP probing, discovery, all CDP
commands) is cross-platform. This module patches only:
- _close_app_cleanly: SIGTERM instead of WM_CLOSE
- restart_confirmed: Linux process flags instead of DETACHED_PROCESS
- _find_app_process: no .exe stripping, check /proc for Electron

/ Adaptador CDP Linux — parchea las partes especificas de plataforma.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess

import psutil

logger = logging.getLogger("marlow.platform.linux.cdp")


def patch_cdp_manager():
    """Monkey-patch CDPManager with Linux-compatible methods."""
    from marlow.core.cdp_manager import CDPManager

    CDPManager._close_app_cleanly = _close_app_cleanly_linux
    CDPManager._original_restart_confirmed = CDPManager.restart_confirmed
    CDPManager.restart_confirmed = _restart_confirmed_linux
    CDPManager._find_app_process = _find_app_process_linux


def _close_app_cleanly_linux(self, pid: int) -> dict:
    """Close an app via SIGTERM, fallback to SIGKILL."""
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return {"closed": True, "detail": "Process already gone"}

    # Send SIGTERM for clean shutdown
    try:
        proc.send_signal(signal.SIGTERM)
    except (psutil.AccessDenied, psutil.NoSuchProcess) as e:
        return {"closed": False, "detail": str(e)}

    # Wait up to 5 seconds
    try:
        proc.wait(timeout=5)
        return {"closed": True, "detail": "Clean exit via SIGTERM"}
    except psutil.TimeoutExpired:
        pass

    # Force kill
    try:
        proc.kill()
        proc.wait(timeout=2)
        return {"closed": True, "detail": "Killed after SIGTERM timeout"}
    except Exception as e:
        return {"closed": False, "detail": str(e)}


async def _restart_confirmed_linux(self, app_name: str, port=None) -> dict:
    """Linux restart: SIGTERM + relaunch with start_new_session."""
    import asyncio

    app_lower = app_name.lower().strip()
    port = port or self._resolve_port(app_lower)

    loop = asyncio.get_running_loop()

    # Find process
    proc_info = await loop.run_in_executor(
        None, self._find_app_process, app_lower
    )
    if not proc_info:
        return {
            "error": f"App '{app_name}' not found running",
            "hint": "The app may have already been closed.",
        }

    exe_path = proc_info["exe"]
    original_args = proc_info["cmdline"][1:]
    pid = proc_info["pid"]

    # Close app cleanly
    close_result = await loop.run_in_executor(
        None, self._close_app_cleanly, pid
    )
    if not close_result.get("closed"):
        return {
            "error": f"Failed to close {app_name}: {close_result.get('detail', 'unknown')}",
        }

    # Build restart command
    restart_cmd = self._build_restart_command(exe_path, original_args, port)

    # Relaunch with Linux-compatible flags
    try:
        env = os.environ.copy()
        env["ELECTRON_EXTRA_LAUNCH_ARGS"] = f"--remote-debugging-port={port}"
        subprocess.Popen(
            restart_cmd,
            env=env,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        return {"error": f"Failed to relaunch: {e}"}

    logger.info(f"Relaunched {app_name} with CDP on port {port}")

    # Wait for CDP port to respond (up to 15s)
    for _ in range(30):
        await asyncio.sleep(0.5)
        probe = await loop.run_in_executor(None, self._probe_port, port)
        if probe:
            conn = await self.connect(port)
            if conn.get("success"):
                self._save_to_kb(app_lower, port, exe_path)
                return {
                    "success": True,
                    "restarted": True,
                    "app": app_name,
                    "port": port,
                    **conn,
                }

    return {
        "error": f"App relaunched but CDP did not respond on port {port} within 15s",
        "hint": "The app may need more time to start, or may not support CDP.",
    }


def _find_app_process_linux(self, app_lower: str):
    """Find a running process by app name on Linux."""
    if not app_lower:
        return None

    for proc in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        try:
            pname = (proc.info["name"] or "").lower()
            exe = proc.info["exe"] or ""
            cmdline = proc.info["cmdline"] or []

            if not pname and not exe:
                continue

            # Match by process name
            if pname and (app_lower in pname or pname in app_lower):
                return {
                    "pid": proc.info["pid"],
                    "name": proc.info["name"],
                    "exe": exe,
                    "cmdline": cmdline if cmdline else [exe],
                }

            # Match by exe path
            if exe and app_lower in exe.lower():
                return {
                    "pid": proc.info["pid"],
                    "name": proc.info["name"],
                    "exe": exe,
                    "cmdline": cmdline if cmdline else [exe],
                }

            # Match Electron apps by cmdline
            cmdline_str = " ".join(cmdline).lower()
            if app_lower in cmdline_str and (
                "electron" in cmdline_str or "--type=" in cmdline_str
            ):
                return {
                    "pid": proc.info["pid"],
                    "name": proc.info["name"],
                    "exe": exe,
                    "cmdline": cmdline if cmdline else [exe],
                }

        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue

    return None


def is_electron_app(pid: int) -> bool:
    """Check if a process is an Electron app by inspecting cmdline/maps."""
    try:
        proc = psutil.Process(pid)
        cmdline = " ".join(proc.cmdline()).lower()
        if "electron" in cmdline or "--type=renderer" in cmdline:
            return True

        # Check loaded libraries
        maps_path = f"/proc/{pid}/maps"
        if os.path.exists(maps_path):
            with open(maps_path) as f:
                for line in f:
                    if "electron" in line.lower() or "libnode" in line.lower():
                        return True
    except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
        pass
    return False


def discover_electron_apps() -> list[dict]:
    """Find running Electron apps on the system."""
    apps = []
    seen_pids = set()

    for proc in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        try:
            pid = proc.info["pid"]
            if pid in seen_pids:
                continue

            cmdline = proc.info["cmdline"] or []
            cmdline_str = " ".join(cmdline).lower()

            # Quick filter: likely Electron process
            if not ("electron" in cmdline_str
                    or "--type=" in cmdline_str
                    or "chrome" in cmdline_str):
                continue

            # Main process only (no --type=renderer/gpu/etc)
            if any(a.startswith("--type=") for a in cmdline):
                continue

            seen_pids.add(pid)
            apps.append({
                "pid": pid,
                "name": proc.info["name"],
                "exe": proc.info["exe"] or "",
                "is_electron": is_electron_app(pid),
            })

        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue

    return apps
