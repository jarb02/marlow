"""Linux SystemProvider — bash + xdg-open.

Runs shell commands and launches applications on Linux.

/ SystemProvider Linux — bash + xdg-open.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess

from marlow.platform.base import SystemProvider

logger = logging.getLogger("marlow.platform.linux.system")


class LinuxSystemProvider(SystemProvider):
    """System operations on Linux."""

    def run_command(self, command: str, timeout: int = 30) -> dict:
        """Execute a shell command via bash."""
        try:
            r = subprocess.run(
                ["bash", "-c", command],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return {
                "stdout": r.stdout,
                "stderr": r.stderr,
                "exit_code": r.returncode,
                "success": r.returncode == 0,
            }
        except subprocess.TimeoutExpired:
            return {
                "stdout": "",
                "stderr": f"Command timed out after {timeout}s",
                "exit_code": -1,
                "success": False,
            }
        except Exception as e:
            return {
                "stdout": "",
                "stderr": str(e),
                "exit_code": -1,
                "success": False,
            }

    def open_application(self, name_or_path: str) -> dict:
        """Launch an application by name or path."""
        try:
            # If it's a full path, launch directly
            if os.path.isfile(name_or_path):
                proc = subprocess.Popen(
                    [name_or_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return {"success": True, "pid": proc.pid}

            # If it looks like a desktop app name, try common launchers
            # Try direct execution first (e.g. "firefox", "nautilus")
            resolved = shutil.which(name_or_path)
            if resolved:
                proc = subprocess.Popen(
                    [resolved],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return {"success": True, "pid": proc.pid}

            # Fallback to xdg-open (for file associations / URLs)
            r = subprocess.run(
                ["xdg-open", name_or_path],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                return {"success": True, "pid": 0}
            return {
                "success": False,
                "error": f"xdg-open failed: {r.stderr.strip()}",
            }

        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_system_info(self) -> dict:
        """Gather system information."""
        info: dict = {
            "os": {
                "system": platform.system(),
                "release": platform.release(),
                "version": platform.version(),
                "machine": platform.machine(),
                "distro": self._get_distro(),
            },
            "cpu": self._get_cpu_info(),
            "memory": self._get_memory_info(),
            "display": self._get_display_info(),
        }
        return info

    # ── Helpers ──

    @staticmethod
    def _get_distro() -> str:
        """Get Linux distro name from os-release."""
        try:
            with open("/etc/os-release") as f:
                for line in f:
                    if line.startswith("PRETTY_NAME="):
                        return line.split("=", 1)[1].strip().strip('"')
        except OSError:
            pass
        return "Unknown Linux"

    @staticmethod
    def _get_cpu_info() -> dict:
        """Basic CPU info from /proc/cpuinfo."""
        info = {"cores": os.cpu_count() or 0, "model": "Unknown"}
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        info["model"] = line.split(":", 1)[1].strip()
                        break
        except OSError:
            pass
        return info

    @staticmethod
    def _get_memory_info() -> dict:
        """Memory info from /proc/meminfo."""
        info = {"total_mb": 0, "available_mb": 0}
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        kb = int(line.split()[1])
                        info["total_mb"] = kb // 1024
                    elif line.startswith("MemAvailable:"):
                        kb = int(line.split()[1])
                        info["available_mb"] = kb // 1024
        except (OSError, ValueError):
            pass
        return info

    @staticmethod
    def _get_display_info() -> dict:
        """Display info from Sway IPC."""
        try:
            import i3ipc
            conn = i3ipc.Connection()
            outputs = conn.get_outputs()
            displays = []
            for o in outputs:
                if o.active:
                    displays.append({
                        "name": o.name,
                        "resolution": f"{o.rect.width}x{o.rect.height}",
                        "position": f"{o.rect.x},{o.rect.y}",
                    })
            return {"displays": displays, "count": len(displays)}
        except Exception:
            return {"displays": [], "count": 0}


if __name__ == "__main__":
    sys_provider = LinuxSystemProvider()

    print("=== LinuxSystemProvider self-test ===")

    print("\n--- run_command('echo hello') ---")
    r = sys_provider.run_command("echo hello")
    print(f"  stdout: {r['stdout'].strip()}")
    print(f"  success: {r['success']}")
    assert r["success"] and r["stdout"].strip() == "hello"

    print("\n--- run_command('uname -r') ---")
    r = sys_provider.run_command("uname -r")
    print(f"  kernel: {r['stdout'].strip()}")

    print("\n--- get_system_info ---")
    info = sys_provider.get_system_info()
    print(f"  distro: {info['os']['distro']}")
    print(f"  cpu: {info['cpu']['model']} ({info['cpu']['cores']} cores)")
    print(f"  memory: {info['memory']['total_mb']}MB total, "
          f"{info['memory']['available_mb']}MB available")
    displays = info.get("display", {}).get("displays", [])
    for d in displays:
        print(f"  display: {d['name']} {d['resolution']}")

    print("\nPASS: LinuxSystemProvider self-test complete")
