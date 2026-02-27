"""
Marlow COM Automation Tool

Run Python scripts that control Office and Adobe apps via COM.
Scripts execute in a sandbox with restricted builtins — no imports,
no file access, no eval/exec.

Supported apps: Word, Excel, PowerPoint, Outlook, Photoshop, Access.

/ Ejecuta scripts Python que controlan apps de Office y Adobe via COM.
/ Los scripts se ejecutan en un sandbox con builtins restringidos.
"""

import re
import asyncio
import logging
from typing import Optional

logger = logging.getLogger("marlow.tools.app_script")

# Supported applications and their COM ProgIDs
SUPPORTED_APPS = {
    "word": "Word.Application",
    "excel": "Excel.Application",
    "powerpoint": "PowerPoint.Application",
    "outlook": "Outlook.Application",
    "photoshop": "Photoshop.Application",
    "access": "Access.Application",
}

# Forbidden patterns in scripts — blocks dangerous code
FORBIDDEN_PATTERNS = [
    r"\bimport\s",
    r"__import__",
    r"\beval\s*\(",
    r"\bexec\s*\(",
    r"\bopen\s*\(",
    r"\bos\.",
    r"\bsys\.",
    r"\bsubprocess",
    r"__builtins__",
    r"__class__",
    r"__subclasses__",
    r"\bglobals\s*\(",
    r"\blocals\s*\(",
    r"\bgetattr\s*\(",
    r"\bsetattr\s*\(",
    r"\bdelattr\s*\(",
    r"\bcompile\s*\(",
]


def _validate_script(script: str) -> Optional[str]:
    """
    Validate script for forbidden patterns.
    Returns error message if invalid, None if OK.
    """
    for pattern in FORBIDDEN_PATTERNS:
        match = re.search(pattern, script)
        if match:
            return (
                f"Forbidden pattern detected: '{match.group()}'. "
                "Scripts cannot use imports, eval, exec, open, os, sys, "
                "subprocess, or access dunder attributes."
            )
    return None


async def run_app_script(
    app_name: str,
    script: str,
    timeout: int = 30,
) -> dict:
    """
    Run a Python script that controls a Windows application via COM.

    The script has access to a single variable 'app' (the COM object)
    and should store its result in a variable called 'result'.

    Example for Excel:
        app_name: "excel"
        script: |
            wb = app.ActiveWorkbook
            ws = wb.ActiveSheet
            result = ws.Range("A1").Value

    Args:
        app_name: Application name (word, excel, powerpoint, outlook,
                  photoshop, access).
        script: Python script to execute. Has access to 'app' variable.
                Store output in 'result' variable.
        timeout: Maximum execution time in seconds (default: 30).

    Returns:
        Dictionary with script result or error.

    / Ejecuta un script Python que controla una aplicación Windows via COM.
    """
    # Validate app name
    app_lower = app_name.lower().strip()
    if app_lower not in SUPPORTED_APPS:
        return {
            "error": f"Unsupported application: '{app_name}'",
            "supported_apps": list(SUPPORTED_APPS.keys()),
        }

    # Validate script
    validation_error = _validate_script(script)
    if validation_error:
        return {"error": validation_error}

    prog_id = SUPPORTED_APPS[app_lower]

    def _execute():
        import pythoncom
        pythoncom.CoInitialize()

        try:
            import win32com.client

            # Try to connect to running instance first
            app = None
            try:
                app = win32com.client.GetActiveObject(prog_id)
                connection_method = "GetActiveObject (existing instance)"
            except Exception:
                pass

            if app is None:
                try:
                    app = win32com.client.Dispatch(prog_id)
                    app.Visible = True
                    connection_method = "Dispatch (new instance)"
                except Exception as e:
                    return {
                        "error": f"Failed to connect to {app_name}: {e}",
                        "hint": f"Make sure {app_name.title()} is installed.",
                    }

            # Execute script in sandbox
            sandbox = {"app": app, "result": None, "__builtins__": {}}

            try:
                exec(script, sandbox)
            except Exception as e:
                return {
                    "error": f"Script execution error: {e}",
                    "script_preview": script[:200],
                }

            # Get result
            result = sandbox.get("result")

            # Convert COM objects to strings for serialization
            if result is not None:
                try:
                    # Try to serialize — if it fails, convert to string
                    import json
                    json.dumps(result, default=str)
                except (TypeError, ValueError):
                    result = str(result)

            return {
                "success": True,
                "app": app_name,
                "connection": connection_method,
                "result": result,
            }

        except ImportError:
            return {
                "error": "pywin32 not installed. Run: pip install pywin32",
            }
        except Exception as e:
            return {"error": str(e)}
        finally:
            pythoncom.CoUninitialize()

    try:
        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(None, _execute),
            timeout=timeout,
        )
        return result
    except asyncio.TimeoutError:
        return {
            "error": f"Script timed out after {timeout} seconds.",
            "hint": "Increase timeout or simplify the script.",
        }
    except Exception as e:
        logger.error(f"app_script error: {e}")
        return {"error": str(e)}
