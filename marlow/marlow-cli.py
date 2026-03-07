#!/usr/bin/env python3
"""Marlow CLI — talk to the Marlow daemon from any terminal.

Usage:
    marlow "open firefox and search for cats"
    marlow --status
    marlow --history
    marlow --stop
    marlow --health

/ CLI simple para enviar goals al daemon Marlow en localhost:8420.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

DAEMON_URL = "http://127.0.0.1:8420"


def _request(method: str, path: str, data: dict = None) -> dict:
    """Make an HTTP request to the daemon."""
    url = f"{DAEMON_URL}{path}"
    body = json.dumps(data).encode() if data else None
    headers = {"Content-Type": "application/json"} if data else {}

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=660) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.URLError as e:
        print(f"Error: Cannot connect to Marlow daemon at {DAEMON_URL}")
        print(f"  Is the daemon running? Start it with: python3 -m marlow.daemon_linux")
        print(f"  Detail: {e}")
        sys.exit(1)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            print(f"HTTP {e.code}: {body}")
            sys.exit(1)


def _print_json(data: dict):
    """Pretty-print JSON response."""
    print(json.dumps(data, indent=2, ensure_ascii=False))


def cmd_goal(goal_text: str):
    """Submit a goal to the daemon."""
    print(f"Sending goal: {goal_text}")
    print("Waiting for execution...")
    result = _request("POST", "/goal", {"goal": goal_text})
    _print_json(result)


def cmd_status():
    """Show daemon status."""
    result = _request("GET", "/status")
    state = result.get("state", "unknown")
    uptime = result.get("uptime_s", 0)
    tools = result.get("tools_registered", 0)
    current = result.get("current_goal")
    queue = result.get("queue_size", 0)

    print(f"State:    {state}")
    print(f"Uptime:   {uptime:.0f}s")
    print(f"Tools:    {tools}")
    if current:
        print(f"Current:  {current}")
    if queue > 0:
        print(f"Queue:    {queue} pending")

    recent = result.get("recent_goals", [])
    if recent:
        print(f"\nRecent goals ({len(recent)}):")
        for r in recent:
            status = "OK" if r.get("success") else r.get("status", "?")
            print(f"  [{status}] {r['goal'][:70]} ({r.get('duration_s', 0):.1f}s)")


def cmd_history():
    """Show goal history."""
    result = _request("GET", "/history")
    history = result.get("history", [])
    if not history:
        print("No goals executed yet.")
        return

    print(f"Goal history ({len(history)} entries):\n")
    for i, r in enumerate(history, 1):
        status = "OK" if r.get("success") else r.get("status", "failed").upper()
        steps = f"{r.get('steps_completed', 0)}/{r.get('steps_total', 0)}"
        dur = r.get("duration_s", 0)
        print(f"  {i}. [{status}] {r['goal'][:65]}")
        print(f"     Steps: {steps} | Score: {r.get('avg_score', 0):.2f} | Time: {dur:.1f}s")
        errors = r.get("errors", [])
        if errors:
            print(f"     Errors: {'; '.join(errors[:3])}")
        print()


def cmd_stop():
    """Stop the currently executing goal."""
    result = _request("POST", "/stop")
    _print_json(result)


def cmd_health():
    """Health check."""
    result = _request("GET", "/health")
    _print_json(result)


def main():
    args = sys.argv[1:]

    if not args:
        print("Usage:")
        print('  marlow-cli.py "goal text here"')
        print("  marlow-cli.py --status")
        print("  marlow-cli.py --history")
        print("  marlow-cli.py --stop")
        print("  marlow-cli.py --health")
        sys.exit(0)

    if args[0] == "--status":
        cmd_status()
    elif args[0] == "--history":
        cmd_history()
    elif args[0] == "--stop":
        cmd_stop()
    elif args[0] == "--health":
        cmd_health()
    elif args[0].startswith("--"):
        print(f"Unknown option: {args[0]}")
        sys.exit(1)
    else:
        goal = " ".join(args)
        cmd_goal(goal)


if __name__ == "__main__":
    main()
