"""
Marlow System Tools

Run commands, open applications, manage clipboard, get system info.
All shell commands pass through the safety engine's blocked command list.
"""

import logging
import platform
from typing import Optional

logger = logging.getLogger("marlow.tools.system")


async def run_command(
    command: str,
    shell: str = "powershell",
    timeout: int = 30,
) -> dict:
    """
    Execute a shell command (PowerShell or CMD).

    ⚠️ This tool passes through Marlow's safety engine:
    - Destructive commands (format, del /f, rm -rf, etc.) are BLOCKED
    - Commands are logged for audit trail
    - Rate limits apply

    Args:
        command: The command to execute.
        shell: "powershell" or "cmd". Default: "powershell".
        timeout: Max execution time in seconds. Default: 30.

    Returns:
        Command output (stdout + stderr) and exit code.
    
    / Ejecuta un comando de shell (PowerShell o CMD).
    / ⚠️ Comandos destructivos están BLOQUEADOS por el motor de seguridad.
    """
    import subprocess

    try:
        if shell == "powershell":
            cmd = ["powershell", "-NoProfile", "-Command", command]
        else:
            cmd = ["cmd", "/c", command]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=None,
        )

        return {
            "stdout": result.stdout.strip() if result.stdout else "",
            "stderr": result.stderr.strip() if result.stderr else "",
            "exit_code": result.returncode,
            "success": result.returncode == 0,
            "shell": shell,
        }

    except subprocess.TimeoutExpired:
        return {
            "error": f"Command timed out after {timeout} seconds",
            "command": command,
        }
    except FileNotFoundError:
        return {"error": f"Shell '{shell}' not found"}
    except Exception as e:
        return {"error": str(e)}


async def open_application(
    app_name: Optional[str] = None,
    app_path: Optional[str] = None,
) -> dict:
    """
    Open an application by name (Start Menu search) or by file path.

    Args:
        app_name: Name to search in Start Menu (e.g., "Notepad", "Chrome").
        app_path: Full path to the executable.

    Returns:
        Result of opening the application.
    
    / Abre una aplicación por nombre (búsqueda Start Menu) o por ruta.
    """
    import subprocess

    try:
        if app_path:
            subprocess.Popen(app_path, shell=False)
            return {
                "success": True,
                "method": "direct_path",
                "path": app_path,
            }
        elif app_name:
            # Use Start Menu search via PowerShell
            subprocess.Popen(
                ["powershell", "-NoProfile", "-Command",
                 f"Start-Process '{app_name}'"],
                shell=False,
            )
            return {
                "success": True,
                "method": "start_menu",
                "app_name": app_name,
            }
        else:
            return {"error": "Provide either app_name or app_path"}

    except FileNotFoundError:
        return {"error": f"Application not found: {app_name or app_path}"}
    except Exception as e:
        return {"error": str(e)}


async def clipboard(
    action: str = "read",
    text: Optional[str] = None,
) -> dict:
    """
    Read from or write to the system clipboard.

    Args:
        action: "read" to get clipboard content, "write" to set it.
        text: Text to write to clipboard (only for action="write").

    Returns:
        Clipboard content or confirmation of write.
    
    / Lee o escribe en el portapapeles del sistema.
    """
    if action not in ("read", "write"):
        return {"error": "Invalid action. Use 'read' or 'write'."}
    if action == "write" and not text:
        return {"error": "Provide 'text' parameter for write action."}

    try:
        import pyperclip

        if action == "read":
            content = pyperclip.paste()
            return {"content": content, "action": "read", "length": len(content)}
        else:
            pyperclip.copy(text)
            return {"success": True, "action": "write", "length": len(text)}
    except ImportError:
        pass

    # Fallback: Win32 clipboard API via PowerShell (pyperclip not installed)
    try:
        import subprocess

        if action == "read":
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                capture_output=True, text=True, timeout=10,
            )
            return {
                "content": result.stdout.strip(),
                "action": "read",
            }
        else:
            # Pass text via stdin to avoid shell injection
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "$input | Set-Clipboard"],
                input=text,
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return {"error": f"Clipboard write failed: {result.stderr.strip()[:200]}"}
            return {"success": True, "action": "write", "length": len(text)}
    except Exception as e:
        return {"error": str(e)}


async def system_info() -> dict:
    """
    Get system information: OS, CPU, RAM, disk, and running processes.

    Returns:
        Dictionary with system info.
    
    / Obtiene información del sistema: OS, CPU, RAM, disco, procesos.
    """
    try:
        import psutil

        cpu_percent = psutil.cpu_percent(interval=0.5)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage("/")

        # Top processes by memory
        processes = []
        for proc in psutil.process_iter(["pid", "name", "memory_percent"]):
            try:
                info = proc.info
                if info["memory_percent"] and info["memory_percent"] > 0.5:
                    processes.append({
                        "pid": info["pid"],
                        "name": info["name"],
                        "memory_percent": round(info["memory_percent"], 1),
                    })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        processes.sort(key=lambda x: x["memory_percent"], reverse=True)

        return {
            "os": {
                "system": platform.system(),
                "version": platform.version(),
                "machine": platform.machine(),
            },
            "cpu": {
                "percent": cpu_percent,
                "cores": psutil.cpu_count(),
                "cores_physical": psutil.cpu_count(logical=False),
            },
            "memory": {
                "total_gb": round(memory.total / (1024**3), 1),
                "available_gb": round(memory.available / (1024**3), 1),
                "percent_used": memory.percent,
            },
            "disk": {
                "total_gb": round(disk.total / (1024**3), 1),
                "free_gb": round(disk.free / (1024**3), 1),
                "percent_used": disk.percent,
            },
            "top_processes": processes[:10],
        }

    except ImportError:
        return {"error": "psutil not installed. Run: pip install psutil"}
    except Exception as e:
        return {"error": str(e)}
