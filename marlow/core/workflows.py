"""
Marlow Workflow Manager — Record, Save, and Replay Tool Sequences

Allows recording a sequence of MCP tool calls, saving them as named
workflows, and replaying them with safety checks at each step.

/ Graba secuencias de herramientas MCP, las guarda como workflows,
/ y las reproduce con checks de seguridad en cada paso.
"""

import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from marlow.core.config import CONFIG_DIR

logger = logging.getLogger("marlow.core.workflows")

WORKFLOWS_DIR = CONFIG_DIR / "workflows"
WORKFLOWS_FILE = WORKFLOWS_DIR / "workflows.json"

# Tools that should never be recorded in workflows
_META_TOOLS = {
    "kill_switch", "workflow_record", "workflow_stop", "workflow_run",
    "workflow_list", "workflow_delete", "get_suggestions",
    "accept_suggestion", "dismiss_suggestion", "get_capabilities", "get_version",
}


class WorkflowManager:
    """
    Records and replays sequences of MCP tool calls.

    / Graba y reproduce secuencias de llamadas a herramientas MCP.
    """

    def __init__(self):
        self._recording: bool = False
        self._current_name: str = ""
        self._current_steps: list[dict] = []
        self._last_step_time: float = 0.0

    @property
    def is_recording(self) -> bool:
        return self._recording

    # ── Recording ──────────────────────────────────────────────

    def record_step(self, tool: str, params: dict, success: bool) -> None:
        """
        Record a tool step during workflow recording.
        Only records successful calls to non-meta tools.

        / Registra un paso durante la grabacion de un workflow.
        """
        if not self._recording:
            return
        if tool in _META_TOOLS:
            return
        if not success:
            return

        now = time.monotonic()
        delay_ms = 0
        if self._last_step_time > 0:
            delay_ms = int((now - self._last_step_time) * 1000)
        self._last_step_time = now

        self._current_steps.append({
            "tool": tool,
            "params": params,
            "delay_ms": delay_ms,
        })

    # ── Persistence ────────────────────────────────────────────

    def _ensure_dir(self) -> None:
        """Create workflows directory if needed."""
        WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)

    def _load_workflows(self) -> dict:
        """Load all workflows from disk."""
        if WORKFLOWS_FILE.exists():
            try:
                return json.loads(WORKFLOWS_FILE.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to load workflows: {e}")
        return {}

    def _save_workflows(self, data: dict) -> None:
        """Save workflows to disk."""
        self._ensure_dir()
        WORKFLOWS_FILE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


# Module-level singleton
_manager = WorkflowManager()


# ─────────────────────────────────────────────────────────────
# MCP Tools
# ─────────────────────────────────────────────────────────────

async def workflow_record(name: str) -> dict:
    """
    Start recording a new workflow.

    / Comienza a grabar un nuevo workflow.
    """
    try:
        if _manager._recording:
            return {
                "error": f"Already recording workflow '{_manager._current_name}'. "
                         "Call workflow_stop first.",
            }

        # Check name doesn't already exist
        existing = _manager._load_workflows()
        if name in existing:
            return {"error": f"Workflow '{name}' already exists. Delete it first or use a different name."}

        _manager._recording = True
        _manager._current_name = name
        _manager._current_steps = []
        _manager._last_step_time = 0.0

        return {
            "success": True,
            "recording": True,
            "workflow_name": name,
            "message": f"Recording workflow '{name}'. Perform actions, then call workflow_stop.",
        }
    except Exception as e:
        logger.error(f"workflow_record error: {e}")
        return {"error": str(e)}


async def workflow_stop() -> dict:
    """
    Stop recording and save the workflow.

    / Detiene la grabacion y guarda el workflow.
    """
    try:
        if not _manager._recording:
            return {"error": "Not currently recording any workflow."}

        name = _manager._current_name
        steps = _manager._current_steps

        _manager._recording = False
        _manager._current_name = ""
        _manager._current_steps = []
        _manager._last_step_time = 0.0

        if not steps:
            return {
                "success": True,
                "workflow_name": name,
                "steps": 0,
                "message": "No steps recorded — workflow not saved.",
            }

        # Save
        workflows = _manager._load_workflows()
        workflows[name] = {
            "steps": steps,
            "created": datetime.now().isoformat(),
            "step_count": len(steps),
        }
        _manager._save_workflows(workflows)

        return {
            "success": True,
            "workflow_name": name,
            "steps": len(steps),
            "message": f"Workflow '{name}' saved with {len(steps)} steps.",
        }
    except Exception as e:
        logger.error(f"workflow_stop error: {e}")
        # Reset state on error
        _manager._recording = False
        _manager._current_name = ""
        _manager._current_steps = []
        return {"error": str(e)}


async def workflow_run(
    name: str,
    safety_engine: object,
    dispatch_fn: Callable,
) -> dict:
    """
    Load and replay a saved workflow, executing each step.

    Checks kill switch and safety approval before each step.
    Stops on first failure, returns partial results.

    / Carga y reproduce un workflow guardado, ejecutando cada paso.
    """
    try:
        workflows = _manager._load_workflows()
        if name not in workflows:
            available = list(workflows.keys()) if workflows else []
            return {"error": f"Workflow '{name}' not found. Available: {available}"}

        workflow = workflows[name]
        steps = workflow["steps"]
        results = []

        for i, step in enumerate(steps):
            # Check kill switch before each step
            if safety_engine.is_killed:  # type: ignore[attr-defined]
                results.append({
                    "step": i + 1,
                    "tool": step["tool"],
                    "status": "skipped",
                    "reason": "kill_switch_active",
                })
                return {
                    "success": False,
                    "workflow_name": name,
                    "completed_steps": i,
                    "total_steps": len(steps),
                    "results": results,
                    "stopped_reason": "kill_switch",
                }

            # Safety approval per step
            approved, reason = await safety_engine.approve_action(  # type: ignore[attr-defined]
                step["tool"], step["tool"], step["params"],
            )
            if not approved:
                results.append({
                    "step": i + 1,
                    "tool": step["tool"],
                    "status": "blocked",
                    "reason": reason,
                })
                return {
                    "success": False,
                    "workflow_name": name,
                    "completed_steps": i,
                    "total_steps": len(steps),
                    "results": results,
                    "stopped_reason": "safety_blocked",
                }

            # Execute the step
            try:
                result = await dispatch_fn(step["tool"], step["params"])
                success = isinstance(result, dict) and "error" not in result
                results.append({
                    "step": i + 1,
                    "tool": step["tool"],
                    "status": "ok" if success else "error",
                    "result": result,
                })
                if not success:
                    return {
                        "success": False,
                        "workflow_name": name,
                        "completed_steps": i,
                        "total_steps": len(steps),
                        "results": results,
                        "stopped_reason": "step_failed",
                    }
            except Exception as step_err:
                results.append({
                    "step": i + 1,
                    "tool": step["tool"],
                    "status": "error",
                    "error": str(step_err),
                })
                return {
                    "success": False,
                    "workflow_name": name,
                    "completed_steps": i,
                    "total_steps": len(steps),
                    "results": results,
                    "stopped_reason": "exception",
                }

            # Delay between steps (clamp between 100ms and 5s)
            delay_ms = step.get("delay_ms", 500)
            delay_s = max(0.1, min(5.0, delay_ms / 1000))
            await asyncio.sleep(delay_s)

        return {
            "success": True,
            "workflow_name": name,
            "completed_steps": len(steps),
            "total_steps": len(steps),
            "results": results,
        }
    except Exception as e:
        logger.error(f"workflow_run error: {e}")
        return {"error": str(e)}


async def workflow_list() -> dict:
    """
    List all saved workflows with metadata.

    / Lista todos los workflows guardados con metadatos.
    """
    try:
        workflows = _manager._load_workflows()
        items = []
        for name, data in workflows.items():
            items.append({
                "name": name,
                "step_count": data.get("step_count", len(data.get("steps", []))),
                "created": data.get("created", "unknown"),
                "tools": [s["tool"] for s in data.get("steps", [])],
            })
        return {
            "success": True,
            "workflows": items,
            "total": len(items),
        }
    except Exception as e:
        logger.error(f"workflow_list error: {e}")
        return {"error": str(e)}


async def workflow_delete(name: str) -> dict:
    """
    Delete a saved workflow by name.

    / Elimina un workflow guardado por nombre.
    """
    try:
        workflows = _manager._load_workflows()
        if name not in workflows:
            return {"error": f"Workflow '{name}' not found."}

        del workflows[name]
        _manager._save_workflows(workflows)

        return {
            "success": True,
            "deleted": name,
            "remaining": len(workflows),
        }
    except Exception as e:
        logger.error(f"workflow_delete error: {e}")
        return {"error": str(e)}
