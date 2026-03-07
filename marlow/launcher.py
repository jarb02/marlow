#!/usr/bin/env python3
"""Marlow Launcher — wofi/rofi input → daemon goal → notification.

Bound to Super+M in Sway. Opens a text input, sends the goal to the
Marlow daemon on localhost:8420, shows result via notify-send.

/ Launcher Sway — input de wofi, envia goal al daemon, notifica resultado.
"""

import json
import subprocess
import sys
import urllib.error
import urllib.request

DAEMON_URL = "http://127.0.0.1:8420"


def _find_launcher() -> list[str]:
    """Return command to open a text input prompt."""
    for cmd, args in [
        ("wofi", ["wofi", "--dmenu", "--prompt", "Marlow>",
                   "--lines", "0", "--width", "500"]),
        ("rofi", ["rofi", "-dmenu", "-p", "Marlow>",
                   "-theme-str", "listview { lines: 0; }"]),
    ]:
        try:
            subprocess.run(["which", cmd], capture_output=True, check=True)
            return args
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
    return []


def _notify(title: str, body: str, urgency: str = "normal"):
    """Send a desktop notification."""
    try:
        subprocess.run(
            ["notify-send", "-u", urgency, "-t", "5000",
             "-a", "Marlow", title, body],
            timeout=5,
        )
    except Exception:
        pass


def _send_goal(goal: str) -> dict:
    """Send goal to daemon and return response."""
    data = json.dumps({"goal": goal}).encode()
    req = urllib.request.Request(
        f"{DAEMON_URL}/goal",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=660) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.URLError:
        return {"error": "Cannot connect to Marlow daemon. Is it running?"}
    except Exception as e:
        return {"error": str(e)}


def main():
    # 1. Open launcher input
    launcher_cmd = _find_launcher()
    if not launcher_cmd:
        _notify("Marlow Error", "No launcher found (wofi/rofi)", "critical")
        sys.exit(1)

    try:
        result = subprocess.run(
            launcher_cmd, capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        sys.exit(0)

    goal = result.stdout.strip()
    if not goal:
        sys.exit(0)  # User cancelled

    # 2. Notify that we're executing
    _notify("Marlow", f"Executing: {goal}")

    # 3. Send to daemon
    response = _send_goal(goal)

    # 4. Show result notification
    if "error" in response:
        _notify("Marlow Error", response["error"], "critical")
    elif response.get("success"):
        steps = f"{response.get('steps_completed', 0)}/{response.get('steps_total', 0)}"
        dur = response.get("duration_s", 0)
        summary = response.get("result_summary", "")
        body = f"{goal}\nSteps: {steps} | {dur:.1f}s"
        if summary:
            # Truncate for notification readability
            summary_lines = summary.strip().split("\n")[:6]
            body += "\n" + "\n".join(summary_lines)
        _notify("Marlow Done", body)
    else:
        errors = response.get("errors", [])
        err_msg = errors[0] if errors else response.get("status", "unknown")
        _notify("Marlow Failed", f"{goal}\n{err_msg}", "critical")


if __name__ == "__main__":
    main()
