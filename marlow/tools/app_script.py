"""
Marlow COM Automation Tool

Run Python scripts that control Office and Adobe apps via COM.
Scripts execute in a sandbox with restricted builtins — no imports,
no file access, no eval/exec. Validated via AST analysis.

Supported apps: Word, Excel, PowerPoint, Outlook, Photoshop, Access.

/ Ejecuta scripts Python que controlan apps de Office y Adobe via COM.
/ Los scripts se ejecutan en un sandbox con builtins restringidos.
/ Validacion via analisis AST (no regex).
"""

import ast
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

# Forbidden AST node types — blocks imports, exec, eval at the syntax level
_FORBIDDEN_NODE_TYPES = (
    ast.Import,
    ast.ImportFrom,
)

# Forbidden function/attribute names accessed in the script
_FORBIDDEN_NAMES = frozenset({
    "eval", "exec", "compile", "execfile",
    "__import__", "open", "input",
    "globals", "locals", "vars", "dir",
    "getattr", "setattr", "delattr", "hasattr",
    "type", "super", "classmethod", "staticmethod",
    "property", "memoryview", "bytearray",
    "breakpoint", "exit", "quit", "help",
})

# Forbidden dunder attribute access — blocks sandbox escape
_FORBIDDEN_ATTRS = frozenset({
    "__class__", "__bases__", "__subclasses__", "__mro__",
    "__builtins__", "__globals__", "__code__", "__func__",
    "__self__", "__dict__", "__init_subclass__",
    "__import__", "__loader__", "__spec__",
    "__reduce__", "__reduce_ex__",
})

# Forbidden module-level attribute access (e.g., os.system)
_FORBIDDEN_MODULE_PREFIXES = frozenset({
    "os", "sys", "subprocess", "shutil", "pathlib",
    "importlib", "ctypes", "socket", "http", "urllib",
    "pickle", "shelve", "tempfile", "glob", "signal",
})


class _ScriptValidator(ast.NodeVisitor):
    """AST visitor that rejects dangerous code patterns."""

    def __init__(self):
        self.errors: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        self.errors.append(f"Line {node.lineno}: import statements are forbidden")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.errors.append(f"Line {node.lineno}: from...import statements are forbidden")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # Check direct calls: eval(...), exec(...), etc.
        if isinstance(node.func, ast.Name):
            if node.func.id in _FORBIDDEN_NAMES:
                self.errors.append(
                    f"Line {node.lineno}: calling '{node.func.id}()' is forbidden"
                )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # Block dunder attribute access: obj.__class__, obj.__subclasses__
        if node.attr in _FORBIDDEN_ATTRS:
            self.errors.append(
                f"Line {node.lineno}: accessing '{node.attr}' is forbidden"
            )
        # Block dangerous module access: os.system, sys.exit
        if isinstance(node.value, ast.Name) and node.value.id in _FORBIDDEN_MODULE_PREFIXES:
            self.errors.append(
                f"Line {node.lineno}: accessing '{node.value.id}.{node.attr}' is forbidden"
            )
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        # Block direct reference to forbidden names (not just calls)
        if node.id in _FORBIDDEN_MODULE_PREFIXES:
            self.errors.append(
                f"Line {node.lineno}: referencing '{node.id}' is forbidden"
            )
        self.generic_visit(node)


def _validate_script(script: str) -> Optional[str]:
    """
    Validate script using AST analysis.
    Returns error message if invalid, None if OK.

    / Valida el script usando analisis AST — mas seguro que regex.
    """
    # Step 1: Parse into AST
    try:
        tree = ast.parse(script)
    except SyntaxError as e:
        return f"Script has a syntax error: {e}"

    # Step 2: Walk the AST checking for forbidden patterns
    validator = _ScriptValidator()
    validator.visit(tree)

    if validator.errors:
        return (
            f"Script validation failed ({len(validator.errors)} issue(s)):\n"
            + "\n".join(f"  - {e}" for e in validator.errors[:5])
        )

    return None


async def run_app_script(
    app_name: str,
    script: str,
    timeout: int = 30,
    visible: bool = False,
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
        visible: Whether to show the app window (default: False).
                 When False, new instances run invisibly in the background.
                 Existing instances keep their current visibility.

    Returns:
        Dictionary with script result or error.

    / Ejecuta un script Python que controla una aplicacion Windows via COM.
    / visible=False por default — instancias nuevas corren invisible en background.
    """
    # Validate app name
    app_lower = app_name.lower().strip()
    if app_lower not in SUPPORTED_APPS:
        return {
            "error": f"Unsupported application: '{app_name}'",
            "supported_apps": list(SUPPORTED_APPS.keys()),
        }

    # Validate script via AST analysis
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
                    app.Visible = visible
                    connection_method = f"Dispatch (new instance, visible={visible})"
                except Exception as e:
                    return {
                        "error": f"Failed to connect to {app_name}: {e}",
                        "hint": f"Make sure {app_name.title()} is installed.",
                    }

            # Execute script in restricted sandbox
            # Only 'app' and 'result' are exposed; builtins limited to
            # safe types needed for basic data manipulation
            safe_builtins = {
                "True": True, "False": False, "None": None,
                "int": int, "float": float, "str": str,
                "bool": bool, "list": list, "dict": dict,
                "tuple": tuple, "set": set,
                "len": len, "range": range, "enumerate": enumerate,
                "zip": zip, "map": map, "filter": filter,
                "sorted": sorted, "reversed": reversed,
                "min": min, "max": max, "sum": sum, "abs": abs,
                "round": round, "isinstance": isinstance,
                "print": lambda *a, **kw: None,  # Silenced print
            }
            sandbox = {
                "app": app,
                "result": None,
                "__builtins__": safe_builtins,
            }

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
        loop = asyncio.get_running_loop()
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
