"""
marlow/kernel/test_diag.py
Diagnostic: compare direct tool calls vs SmartExecutor dispatch.

Usage: python -m marlow.kernel.test_diag

Checks whether tools actually execute real desktop actions or
return fake success without doing anything.
"""

import asyncio
import inspect
import io
import json
import logging
import sys
import time

# Fix Windows console encoding
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("diag")


def pp(label: str, obj):
    """Pretty-print a result."""
    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print(f"{'=' * 60}")
    if hasattr(obj, "__dataclass_fields__"):
        for k in obj.__dataclass_fields__:
            print(f"  {k}: {getattr(obj, k)!r}")
    elif isinstance(obj, dict):
        print(json.dumps(obj, indent=2, default=str))
    else:
        print(repr(obj))
    print()


async def main():
    print("\n" + "#" * 60)
    print("#  MARLOW TOOL DIAGNOSTIC")
    print("#" * 60)

    # ── Test 1: Direct import + call ──
    print("\n>>> TEST 1: Direct call to list_windows")
    from marlow.tools import windows
    print(f"    windows.list_windows is async: "
          f"{inspect.iscoroutinefunction(windows.list_windows)}")
    result = await windows.list_windows(include_minimized=False)
    pp("Direct list_windows result", result)
    window_count = len(result.get("windows", []))
    print(f"    Found {window_count} windows")

    # ── Test 2: Direct call to take_screenshot ──
    print("\n>>> TEST 2: Direct call to take_screenshot")
    from marlow.tools import screenshot
    result = await screenshot.take_screenshot(quality=50)
    pp("Direct take_screenshot result (truncated image)",
       {k: (v[:80] + "..." if isinstance(v, str) and len(v) > 80 else v)
        for k, v in result.items()})

    # ── Test 3: Direct call to open_application ──
    # We DON'T actually open — just inspect what would happen
    print("\n>>> TEST 3: Inspect open_application")
    from marlow.tools import system
    print(f"    system.open_application is async: "
          f"{inspect.iscoroutinefunction(system.open_application)}")
    print(f"    Signature: {inspect.signature(system.open_application)}")

    # ── Test 4: Same call through SmartExecutor ──
    print("\n>>> TEST 4: list_windows via SmartExecutor")
    from marlow.kernel.executor import SmartExecutor

    executor = SmartExecutor(default_timeout=30.0)

    # Register the tool THE SAME WAY integration.py does it
    executor.register_tool(
        "list_windows",
        lambda **kw: windows.list_windows(
            include_minimized=kw.get("include_minimized", True),
        ),
    )

    # Check what type the registered function is
    func = executor._tools["list_windows"]
    print(f"    Registered func type: {type(func)}")
    print(f"    iscoroutinefunction: {inspect.iscoroutinefunction(func)}")
    print(f"    Calling func() returns: ", end="")
    test_ret = func(include_minimized=False)
    print(f"{type(test_ret)} — iscoroutine: {inspect.iscoroutine(test_ret)}")
    # Must await it or close it to avoid warning
    if inspect.iscoroutine(test_ret):
        test_data = await test_ret
        print(f"    After await: {type(test_data)}, "
              f"success={test_data.get('success', '???')}")

    # Now do it through executor.execute()
    tool_result = await executor.execute("list_windows", {})
    pp("SmartExecutor list_windows ToolResult", tool_result)

    # ── Test 5: Check lambda wrapping behavior for hotkey ──
    print("\n>>> TEST 5: Inspect hotkey lambda dispatch")
    from marlow.tools import keyboard

    # This is how integration.py registers hotkey:
    hotkey_lambda = lambda **kw: keyboard.hotkey(*kw.get("keys", []))

    print(f"    hotkey_lambda is coroutinefunction: "
          f"{inspect.iscoroutinefunction(hotkey_lambda)}")

    # Simulate what executor does with params from LLM plan
    # Case A: LLM sends {"keys": ["ctrl", "s"]} (correct)
    print("\n    Case A: params={'keys': ['ctrl', 's']}")
    ret_a = hotkey_lambda(keys=["ctrl", "s"])
    print(f"      returns: {type(ret_a)}, iscoroutine: {inspect.iscoroutine(ret_a)}")
    if inspect.iscoroutine(ret_a):
        ret_a.close()  # Don't actually press keys
        print("      (closed coroutine — would have called pyautogui.hotkey('ctrl', 's'))")

    # Case B: LLM sends {"keys": "ctrl+s"} (string, not list!)
    print("\n    Case B: params={'keys': 'ctrl+s'}")
    ret_b = hotkey_lambda(keys="ctrl+s")
    print(f"      returns: {type(ret_b)}, iscoroutine: {inspect.iscoroutine(ret_b)}")
    if inspect.iscoroutine(ret_b):
        ret_b.close()
        print("      (closed coroutine — would have called "
              "pyautogui.hotkey('c','t','r','l','+','s') — WRONG!)")

    # Case C: LLM sends {} (no keys param at all)
    print("\n    Case C: params={} (no keys)")
    ret_c = hotkey_lambda()
    print(f"      returns: {type(ret_c)}, iscoroutine: {inspect.iscoroutine(ret_c)}")
    if inspect.iscoroutine(ret_c):
        ret_c_data = await ret_c
        print(f"      After await: {ret_c_data}")
        print("      ^ hotkey() with no args — does nothing, returns success!")

    # ── Test 6: Check param mapping mismatch ──
    print("\n>>> TEST 6: Param mapping — integration.py vs server.py")

    # integration.py's open_application lambda:
    int_lambda = lambda **kw: system.open_application(
        app_name=kw.get("app_name") or kw.get("name"),
        app_path=kw.get("app_path"),
    )

    # LLM might generate: {"app_name": "notepad"} or {"name": "notepad"}
    for params in [
        {"app_name": "notepad"},
        {"name": "notepad"},
        {"app_name": None, "name": None},
        {},
    ]:
        coro = int_lambda(**params)
        result = await coro
        app_val = params.get("app_name") or params.get("name")
        print(f"    params={params} → app resolved to '{app_val}' → "
              f"result={result}")

    # ── Test 7: Check _raw_to_result normalization ──
    print("\n>>> TEST 7: _raw_to_result behavior")
    from marlow.kernel.executor import _raw_to_result

    for raw in [
        {"success": True, "method": "start_menu"},
        {"error": "not found"},
        {"success": True},
        None,
        "some string",
    ]:
        tr = _raw_to_result("test_tool", raw, 100.0)
        print(f"    raw={raw!r:50s} → success={tr.success}, error={tr.error}")

    # ── Summary ──
    print("\n" + "#" * 60)
    print("#  DIAGNOSTIC SUMMARY")
    print("#" * 60)
    print("""
    Key findings to check:
    1. Are tool functions actually async? (Test 1-3)
    2. Does SmartExecutor properly await coroutines? (Test 4)
    3. Does hotkey fail with wrong param format? (Test 5)
    4. Do empty/null params cause silent success? (Test 6)
    5. Does _raw_to_result hide real errors? (Test 7)
    """)

    executor.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
