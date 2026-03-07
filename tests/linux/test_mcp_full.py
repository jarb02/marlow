"""Full MCP integration test for Marlow Linux server.

Connects as an MCP client and runs 8 tool calls in sequence.
Run on the Fedora laptop with Sway env vars exported.
"""

import asyncio
import json
import base64
import os
import sys
import time


SCREENSHOT_DIR = os.path.expanduser("~/marlow/test_screenshots")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

ENV = {}
for key in ("PATH", "HOME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
            "SWAYSOCK", "WAYLAND_DISPLAY"):
    if key in os.environ:
        ENV[key] = os.environ[key]
ENV["PYTHONPATH"] = os.path.expanduser("~/marlow")
if "PATH" not in ENV:
    ENV["PATH"] = "/usr/local/bin:/usr/bin:/bin"

# Auto-detect SWAYSOCK if not in env (common when running via SSH)
if "SWAYSOCK" not in ENV:
    import glob
    socks = glob.glob("/run/user/1000/sway-ipc.*.sock")
    if socks:
        ENV["SWAYSOCK"] = socks[0]

# Default WAYLAND_DISPLAY
if "WAYLAND_DISPLAY" not in ENV:
    ENV["WAYLAND_DISPLAY"] = "wayland-1"


async def main():
    from mcp.client.stdio import stdio_client, StdioServerParameters
    from mcp.client.session import ClientSession

    params = StdioServerParameters(
        command="python3",
        args=["-m", "marlow"],
        cwd=os.path.expanduser("~/marlow"),
        env=ENV,
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("=" * 60)
            print("MARLOW LINUX MCP SERVER — FULL INTEGRATION TEST")
            print("=" * 60)

            # List tools
            tools_resp = await session.list_tools()
            tool_names = [t.name for t in tools_resp.tools]
            print(f"\nTools available: {len(tool_names)}")
            print(f"  {', '.join(tool_names[:10])}...")
            print()

            results = {}

            # ── Test 1: system_info ──
            print("--- Test 1: system_info ---")
            r = await session.call_tool("system_info", {})
            info = json.loads(r.content[0].text)
            os_info = info.get("os", {})
            cpu = info.get("cpu", {})
            mem = info.get("memory", {})
            print(f"  OS: {os_info.get('system')} -- {os_info.get('distro')}")
            print(f"  CPU: {cpu.get('model')} ({cpu.get('cores')} cores)")
            print(f"  RAM: {mem.get('total_mb')} MB total, {mem.get('available_mb')} MB free")
            ok = "Fedora" in os_info.get("distro", "")
            print(f"  {'PASS' if ok else 'FAIL'}: Fedora detected")
            results["system_info"] = ok

            # ── Test 2: list_windows ──
            print("\n--- Test 2: list_windows ---")
            r = await session.call_tool("list_windows", {})
            win_data = json.loads(r.content[0].text)
            windows = win_data.get("windows", [])
            count = win_data.get("count", 0)
            print(f"  Windows found: {count}")
            for w in windows:
                focused = " [FOCUSED]" if w.get("is_focused") else ""
                print(f"    {w.get('title', '?')} ({w.get('app_name', '?')}) "
                      f"{w.get('width')}x{w.get('height')}{focused}")
            ok = count >= 1
            print(f"  {'PASS' if ok else 'FAIL'}: {count} windows")
            results["list_windows"] = ok

            # ── Test 3: take_screenshot (before) ──
            print("\n--- Test 3: take_screenshot (desktop) ---")
            r = await session.call_tool("take_screenshot", {})
            has_image = False
            img_size = 0
            for c in r.content:
                if hasattr(c, "data") and c.type == "image":
                    has_image = True
                    raw = base64.b64decode(c.data)
                    img_size = len(raw)
                    path = os.path.join(SCREENSHOT_DIR, "01_desktop.png")
                    with open(path, "wb") as f:
                        f.write(raw)
                    print(f"  Saved: {path} ({img_size:,} bytes)")
                elif hasattr(c, "text"):
                    print(f"  Info: {c.text}")
            ok = has_image and img_size > 1000
            print(f"  {'PASS' if ok else 'FAIL'}: screenshot captured")
            results["screenshot_before"] = ok

            # ── Test 4: get_ui_tree max_depth=3 ──
            print("\n--- Test 4: get_ui_tree (max_depth=3) ---")
            r = await session.call_tool("get_ui_tree", {"max_depth": 3})
            tree_data = json.loads(r.content[0].text)
            success = tree_data.get("success", False)
            elem_count = tree_data.get("element_count", 0)
            depth = tree_data.get("depth_used", 0)
            if success:
                tree = tree_data.get("tree", {})
                children = tree.get("children", [])
                app_names = []
                for child in children:
                    name = child.get("name", "?")
                    role = child.get("role", "?")
                    app_names.append(f"{name} ({role})")
                print(f"  Elements: {elem_count}, Depth: {depth}")
                print(f"  Top-level apps: {', '.join(app_names[:5])}")
            else:
                print(f"  Error: {tree_data.get('error', 'unknown')}")
            ok = success and elem_count > 0
            print(f"  {'PASS' if ok else 'FAIL'}: {elem_count} elements")
            results["get_ui_tree"] = ok

            # ── Test 5: find_elements role=button ──
            print("\n--- Test 5: find_elements (role='button') ---")
            r = await session.call_tool("find_elements", {"role": "button"})
            elem_data = json.loads(r.content[0].text)
            if isinstance(elem_data, list):
                elements = elem_data
            else:
                elements = elem_data.get("elements", elem_data.get("results", []))
            print(f"  Buttons found: {len(elements)}")
            for e in elements[:8]:
                name = e.get("name", "?")
                score = e.get("score", "")
                score_str = f" score={score:.2f}" if isinstance(score, float) else ""
                print(f"    [{e.get('role', '?')}] {name}{score_str}")
            ok = len(elements) >= 1
            print(f"  {'PASS' if ok else 'FAIL'}: {len(elements)} buttons")
            results["find_elements"] = ok

            # ── Test 6: focus_window Firefox ──
            print("\n--- Test 6: focus_window (Firefox) ---")
            r = await session.call_tool("focus_window", {"window_title": "Firefox"})
            focus_data = json.loads(r.content[0].text)
            if isinstance(focus_data, dict):
                ok = focus_data.get("success", False)
            else:
                ok = bool(focus_data)
            print(f"  Result: {focus_data}")
            print(f"  {'PASS' if ok else 'FAIL'}: focus Firefox")
            results["focus_window"] = ok

            time.sleep(0.3)

            # ── Test 7: hotkey Ctrl+L then type_text ──
            print("\n--- Test 7: hotkey(Ctrl+L) + type_text ---")
            r = await session.call_tool("hotkey", {"keys": ["ctrl", "l"]})
            hotkey_data = json.loads(r.content[0].text)
            if isinstance(hotkey_data, bool):
                hotkey_ok = hotkey_data
            else:
                hotkey_ok = hotkey_data.get("success", hotkey_data)
            print(f"  hotkey(ctrl+l): {hotkey_ok}")

            time.sleep(0.3)

            r = await session.call_tool("type_text", {"text": "Marlow Linux MVP test"})
            type_data = json.loads(r.content[0].text)
            if isinstance(type_data, bool):
                type_ok = type_data
            else:
                type_ok = type_data.get("success", type_data)
            print(f"  type_text: {type_ok}")

            ok = bool(hotkey_ok) and bool(type_ok)
            print(f"  {'PASS' if ok else 'FAIL'}: input sequence")
            results["input_sequence"] = ok

            time.sleep(0.5)

            # ── Test 8: take_screenshot (after) ──
            print("\n--- Test 8: take_screenshot (result) ---")
            r = await session.call_tool("take_screenshot", {})
            has_image = False
            img_size = 0
            for c in r.content:
                if hasattr(c, "data") and c.type == "image":
                    has_image = True
                    raw = base64.b64decode(c.data)
                    img_size = len(raw)
                    path = os.path.join(SCREENSHOT_DIR, "02_after_type.png")
                    with open(path, "wb") as f:
                        f.write(raw)
                    print(f"  Saved: {path} ({img_size:,} bytes)")
                elif hasattr(c, "text"):
                    print(f"  Info: {c.text}")
            ok = has_image and img_size > 1000
            print(f"  {'PASS' if ok else 'FAIL'}: final screenshot")
            results["screenshot_after"] = ok

            # ── Summary ──
            print("\n" + "=" * 60)
            print("RESULTS SUMMARY")
            print("=" * 60)
            passed = sum(1 for v in results.values() if v)
            total = len(results)
            for name, ok in results.items():
                print(f"  {'PASS' if ok else 'FAIL'}: {name}")
            print(f"\n  {passed}/{total} tests passed")
            if passed == total:
                print("  ALL TESTS PASSED")
            else:
                print(f"  {total - passed} FAILED")
            print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
