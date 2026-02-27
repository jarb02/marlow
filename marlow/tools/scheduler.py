"""
Marlow Task Scheduler

Schedules recurring tasks (commands) at regular intervals.
Tasks run in daemon threads with output captured.

/ Programador de tareas recurrentes con intervalos configurables.
"""

import logging
import subprocess
import threading
import time
from datetime import datetime
from typing import Optional

logger = logging.getLogger("marlow.tools.scheduler")

# Module-level state
_tasks: dict[str, dict] = {}
_task_history: list[dict] = []
_max_history = 200

# Kill switch callback — set by server.py at startup
_kill_switch_check: Optional[callable] = None


def set_kill_switch_check(fn: callable) -> None:
    """Register a callback that returns True if the kill switch is active."""
    global _kill_switch_check
    _kill_switch_check = fn


class TaskRunner(threading.Thread):
    """Daemon thread that executes a command at regular intervals."""

    def __init__(self, name: str, command: str, interval: int,
                 shell: str, max_runs: Optional[int]):
        super().__init__(daemon=True)
        self.task_name = name
        self.command = command
        self.interval = interval
        self.shell = shell
        self.max_runs = max_runs
        self.run_count = 0
        self.active = True

    def run(self) -> None:
        while self.active:
            if self.max_runs and self.run_count >= self.max_runs:
                self.active = False
                break

            # Wait the interval (check active flag every second)
            for _ in range(self.interval):
                if not self.active:
                    return
                time.sleep(1)

            if not self.active:
                return

            # Check kill switch before every execution
            if _kill_switch_check and _kill_switch_check():
                _task_history.append({
                    "task": self.task_name,
                    "error": "kill switch active — execution skipped",
                    "timestamp": datetime.now().isoformat(),
                })
                continue

            # Execute the command
            try:
                if self.shell == "powershell":
                    cmd = ["powershell", "-NoProfile", "-Command", self.command]
                else:
                    cmd = ["cmd", "/c", self.command]

                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=60,
                )

                self.run_count += 1

                _task_history.append({
                    "task": self.task_name,
                    "command": self.command,
                    "exit_code": result.returncode,
                    "stdout": result.stdout.strip()[:500],
                    "stderr": result.stderr.strip()[:200],
                    "success": result.returncode == 0,
                    "run_number": self.run_count,
                    "timestamp": datetime.now().isoformat(),
                })

                # Enforce max history limit
                while len(_task_history) > _max_history:
                    _task_history.pop(0)

            except subprocess.TimeoutExpired:
                _task_history.append({
                    "task": self.task_name,
                    "error": "timeout after 60s",
                    "timestamp": datetime.now().isoformat(),
                })
            except Exception as e:
                _task_history.append({
                    "task": self.task_name,
                    "error": str(e),
                    "timestamp": datetime.now().isoformat(),
                })

    def stop(self):
        self.active = False


async def schedule_task(
    name: str,
    command: str,
    interval_seconds: int = 300,
    shell: str = "powershell",
    max_runs: int = None,
) -> dict:
    """
    Schedule a recurring task.

    Args:
        name: Unique name for this task.
        command: Shell command to execute.
        interval_seconds: Run every N seconds (minimum 10).
        shell: 'powershell' or 'cmd'.
        max_runs: Stop after N executions (None = unlimited).

    Returns:
        Dict with task details on success, or error.
    """
    if name in _tasks:
        return {"error": f"Task '{name}' already exists. Remove it first."}

    if interval_seconds < 10:
        return {"error": "Minimum interval is 10 seconds."}

    # NOTE: Security check (blocked commands) is enforced in server.py
    # via safety.approve_action() before this function is called.

    runner = TaskRunner(name, command, interval_seconds, shell, max_runs)
    runner.start()

    _tasks[name] = {
        "command": command,
        "interval_seconds": interval_seconds,
        "shell": shell,
        "max_runs": max_runs,
        "runner": runner,
        "created": datetime.now().isoformat(),
    }

    logger.info(f"Scheduled task '{name}': '{command}' every {interval_seconds}s")

    return {
        "success": True,
        "task": name,
        "command": command,
        "interval_seconds": interval_seconds,
        "max_runs": max_runs or "unlimited",
        "next_run_in": f"{interval_seconds} seconds",
    }


async def list_scheduled_tasks() -> dict:
    """
    List all scheduled tasks with their status.

    Returns:
        Dict with tasks list and count.
    """
    tasks = []
    for name, t in _tasks.items():
        runner = t["runner"]
        tasks.append({
            "name": name,
            "command": t["command"],
            "interval_seconds": t["interval_seconds"],
            "shell": t["shell"],
            "active": runner.active,
            "run_count": runner.run_count,
            "max_runs": t["max_runs"],
            "created": t["created"],
        })

    return {"tasks": tasks, "count": len(tasks)}


async def remove_task(task_name: str) -> dict:
    """
    Remove a scheduled task.

    Args:
        task_name: Name of the task to remove.

    Returns:
        Dict with success or error.
    """
    if task_name not in _tasks:
        return {"error": f"Task '{task_name}' not found"}

    _tasks[task_name]["runner"].stop()
    del _tasks[task_name]

    logger.info(f"Removed scheduled task: {task_name}")

    return {
        "success": True,
        "task": task_name,
        "action": "removed",
    }


async def get_task_history(task_name: str = None, limit: int = 20) -> dict:
    """
    Get execution history for scheduled tasks.

    Args:
        task_name: Filter to a specific task name.
        limit: Max entries to return.

    Returns:
        Dict with history list and total count.
    """
    filtered = _task_history

    if task_name:
        filtered = [h for h in filtered if h.get("task") == task_name]

    return {
        "history": filtered[-limit:],
        "total": len(filtered),
    }
