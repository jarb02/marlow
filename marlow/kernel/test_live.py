"""Live desktop integration test — runs real tools on real desktop.

NOT a pytest file. Run manually:

    python -m marlow.kernel.test_live

Tests:
1. system_info (read-only, always works)
2. list_windows (read-only, shows open windows)
3. take_screenshot (captures current screen)
4. Template goal: "open Notepad" (template planner + real execution)
5. Manual plan: open Calculator
6. clipboard read (read-only)
7. Non-matching goal (verifies graceful failure)

Each test prints PASS/FAIL. Failures don't stop subsequent tests.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time

# Configure logging before imports
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("marlow.test_live")


async def run_tests():
    """Run all live integration tests."""
    from marlow.kernel.integration import AutonomousMarlow

    print("\n" + "=" * 60)
    print("  Marlow Integration Test — Live Desktop")
    print("=" * 60 + "\n")

    # Setup
    marlow = AutonomousMarlow(timeout=30.0, auto_confirm=True)
    setup_result = marlow.setup()

    print(f"Setup: {setup_result['total_tools']} tools registered")
    if setup_result["failed"]:
        print(f"  Failed: {setup_result['failed']}")
    print()

    results = []
    start = time.monotonic()

    # ── Test 1: system_info ──
    results.append(await _test_direct_tool(
        marlow, "system_info", "system_info", {},
    ))

    # ── Test 2: list_windows ──
    results.append(await _test_direct_tool(
        marlow, "list_windows", "list_windows", {},
    ))

    # ── Test 3: take_screenshot ──
    results.append(await _test_direct_tool(
        marlow, "take_screenshot", "take_screenshot", {},
    ))

    # ── Test 4: clipboard read ──
    results.append(await _test_direct_tool(
        marlow, "clipboard_read", "clipboard", {"action": "read"},
    ))

    # ── Test 5: Template goal — "take a screenshot" ──
    results.append(await _test_goal(
        marlow, "template_screenshot", "take a screenshot",
    ))

    # ── Test 6: Template goal — "open Notepad" ──
    results.append(await _test_goal(
        marlow, "template_open_notepad", "open Notepad",
    ))

    # ── Test 7: Manual plan — list windows + system info ──
    results.append(await _test_manual_plan(
        marlow,
        "manual_readonly",
        "Get system state",
        [
            {
                "tool_name": "list_windows",
                "params": {},
                "description": "List all windows",
            },
            {
                "tool_name": "system_info",
                "params": {},
                "description": "Get system info",
            },
        ],
    ))

    # ── Test 8: Non-matching goal ──
    results.append(await _test_no_match(
        marlow, "no_match", "analyze quarterly financial reports",
    ))

    # Teardown
    marlow.teardown()

    # Summary
    elapsed = time.monotonic() - start
    passed = sum(1 for r in results if r["passed"])
    total = len(results)

    print("\n" + "=" * 60)
    print(f"  Results: {passed}/{total} passed  ({elapsed:.1f}s)")
    print("=" * 60)

    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        print(f"  [{status}] {r['name']}: {r['detail']}")

    print()

    if passed < total:
        print("Some tests failed. This may be expected if:")
        print("  - Notepad was already open or failed to launch")
        print("  - Desktop state was unexpected")
        print("  - Optional dependencies are missing")

    return passed == total


async def _test_direct_tool(
    marlow, name: str, tool_name: str, params: dict,
) -> dict:
    """Test a single tool call directly via executor."""
    print(f"[TEST] {name}: calling {tool_name}...")
    try:
        result = await marlow._executor.execute(tool_name, params)
        if result.success:
            # Show a brief summary of the result
            data = result.data
            detail = _summarize(data)
            print(f"  PASS: {detail}")
            return {"name": name, "passed": True, "detail": detail}
        else:
            print(f"  FAIL: {result.error}")
            return {"name": name, "passed": False, "detail": result.error}
    except Exception as e:
        print(f"  FAIL: {type(e).__name__}: {e}")
        return {"name": name, "passed": False, "detail": str(e)}


async def _test_goal(marlow, name: str, goal_text: str) -> dict:
    """Test a goal via AutonomousMarlow.execute()."""
    print(f"[TEST] {name}: \"{goal_text}\"...")
    try:
        result = await marlow.execute(goal_text)
        detail = (
            f"steps={result.steps_completed}/{result.steps_total}, "
            f"score={result.avg_score}"
        )
        if result.success:
            print(f"  PASS: {detail}")
            return {"name": name, "passed": True, "detail": detail}
        else:
            errors = "; ".join(result.errors) if result.errors else "unknown"
            print(f"  FAIL: {detail} — {errors}")
            return {"name": name, "passed": False, "detail": errors}
    except Exception as e:
        print(f"  FAIL: {type(e).__name__}: {e}")
        return {"name": name, "passed": False, "detail": str(e)}


async def _test_manual_plan(
    marlow, name: str, goal_text: str, steps: list[dict],
) -> dict:
    """Test a manually constructed plan."""
    print(f"[TEST] {name}: \"{goal_text}\" ({len(steps)} steps)...")
    try:
        result = await marlow.execute_plan(goal_text, steps)
        detail = (
            f"steps={result.steps_completed}/{result.steps_total}, "
            f"score={result.avg_score}"
        )
        if result.success:
            print(f"  PASS: {detail}")
            return {"name": name, "passed": True, "detail": detail}
        else:
            errors = "; ".join(result.errors) if result.errors else "unknown"
            print(f"  FAIL: {detail} — {errors}")
            return {"name": name, "passed": False, "detail": errors}
    except Exception as e:
        print(f"  FAIL: {type(e).__name__}: {e}")
        return {"name": name, "passed": False, "detail": str(e)}


async def _test_no_match(marlow, name: str, goal_text: str) -> dict:
    """Test that a non-matching goal fails gracefully."""
    print(f"[TEST] {name}: \"{goal_text}\"...")
    try:
        result = await marlow.execute(goal_text)
        if not result.success:
            print(f"  PASS: correctly returned failure")
            return {
                "name": name,
                "passed": True,
                "detail": "No match — correct",
            }
        else:
            print(f"  FAIL: should have failed but succeeded")
            return {
                "name": name,
                "passed": False,
                "detail": "Unexpected success",
            }
    except Exception as e:
        print(f"  FAIL: {type(e).__name__}: {e}")
        return {"name": name, "passed": False, "detail": str(e)}


def _summarize(data) -> str:
    """Create a brief human-readable summary of tool output."""
    if isinstance(data, dict):
        if "windows" in data:
            count = len(data["windows"])
            return f"{count} windows found"
        if "os" in data:
            return f"{data.get('os', '?')} — {data.get('cpu', '?')}"
        if "image_base64" in data:
            w = data.get("width", "?")
            h = data.get("height", "?")
            return f"Screenshot {w}x{h}"
        if "text" in data:
            text = str(data["text"])[:60]
            return f"clipboard: {text!r}"
        if "success" in data:
            return f"success={data['success']}"
        # Generic: show keys
        keys = list(data.keys())[:5]
        return f"keys: {keys}"
    if isinstance(data, str):
        return data[:80]
    return str(type(data).__name__)


if __name__ == "__main__":
    success = asyncio.run(run_tests())
    sys.exit(0 if success else 1)
