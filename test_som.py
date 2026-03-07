"""Test Set-of-Mark (SoM) annotation + click via MCP on Linux.

Requires Firefox open on example.com.
"""

import asyncio
import base64
import json
import os


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
        print(f"Auto-detected SWAYSOCK: {socks[0]}")

# Default WAYLAND_DISPLAY
if "WAYLAND_DISPLAY" not in ENV:
    ENV["WAYLAND_DISPLAY"] = "wayland-1"


SCREENSHOT_DIR = os.path.expanduser("~/marlow/test_screenshots")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


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
            print("SET-OF-MARK (SoM) TEST")
            print("=" * 60)

            # Verify tools registered
            tools_resp = await session.list_tools()
            tool_names = [t.name for t in tools_resp.tools]
            print(f"\nTools: {len(tool_names)}")
            for needed in ["get_annotated_screenshot", "som_click"]:
                status = "OK" if needed in tool_names else "MISSING"
                print(f"  {needed}: {status}")

            results = {}

            # ── Test 1: get_annotated_screenshot on Firefox ──
            print("\n--- Test 1: get_annotated_screenshot(window_title='Firefox') ---")
            r = await session.call_tool(
                "get_annotated_screenshot",
                {"window_title": "Firefox"},
            )

            # Should return ImageContent + TextContent
            has_image = False
            data = {}
            for c in r.content:
                if hasattr(c, "data"):
                    has_image = True
                    # Save annotated image for inspection
                    img_bytes = base64.b64decode(c.data)
                    img_path = os.path.join(SCREENSHOT_DIR, "som_firefox.png")
                    with open(img_path, "wb") as f:
                        f.write(img_bytes)
                    print(f"  Annotated image saved: {img_path} ({len(img_bytes):,} bytes)")
                elif hasattr(c, "text"):
                    data = json.loads(c.text)

            success = data.get("success", False)
            elements = data.get("elements", [])
            elem_count = data.get("element_count", 0)
            win_title = data.get("window_title", "?")
            print(f"  success={success} has_image={has_image}")
            print(f"  window_title={win_title}")
            print(f"  elements: {elem_count}")

            # Print first 10 elements
            for e in elements[:10]:
                print(f"    [{e['index']}] {e['name']!r} ({e['role']}) "
                      f"actions={e['actions']} path={e['path']}")
            if len(elements) > 10:
                print(f"    ... and {len(elements) - 10} more")

            # Check for known Firefox buttons
            elem_names = [e["name"].lower() for e in elements]
            has_reload = any("reload" in n for n in elem_names)
            has_buttons = any(e["role"] in ("push button", "toggle button")
                             for e in elements)
            print(f"  Has 'Reload' button: {has_reload}")
            print(f"  Has button elements: {has_buttons}")

            ok = success and has_image and elem_count >= 3 and has_buttons
            print(f"  {'PASS' if ok else 'FAIL'}: annotated screenshot with elements")
            results["annotated_screenshot"] = ok

            # ── Test 2: Verify element list completeness ──
            print("\n--- Test 2: Element list quality ---")
            roles = set(e["role"] for e in elements)
            has_actions = sum(1 for e in elements if e["actions"])
            has_paths = sum(1 for e in elements if e["path"])
            has_bounds = sum(1 for e in elements
                            if e["bounds"].get("w", 0) > 0)
            print(f"  Roles found: {roles}")
            print(f"  Elements with actions: {has_actions}/{elem_count}")
            print(f"  Elements with paths: {has_paths}/{elem_count}")
            print(f"  Elements with bounds: {has_bounds}/{elem_count}")

            ok = has_paths == elem_count and has_bounds == elem_count
            print(f"  {'PASS' if ok else 'FAIL'}: all elements have paths and bounds")
            results["element_quality"] = ok

            # ── Test 3: som_click on Reload button ──
            print("\n--- Test 3: som_click(Reload button) ---")
            reload_elem = None
            for e in elements:
                if "reload" in e["name"].lower():
                    reload_elem = e
                    break

            if reload_elem:
                reload_idx = reload_elem["index"]
                print(f"  Clicking element [{reload_idx}]: {reload_elem['name']!r}")
                r = await session.call_tool(
                    "som_click", {"index": reload_idx},
                )
                text_parts = [c.text for c in r.content if hasattr(c, "text")]
                click_data = json.loads(text_parts[0]) if text_parts else {}
                click_success = click_data.get("success", False)
                click_method = click_data.get("method", "?")
                print(f"  success={click_success} method={click_method}")
                if click_data.get("element"):
                    ce = click_data["element"]
                    print(f"  element: [{ce.get('index')}] {ce.get('name')!r}")

                ok = click_success
                print(f"  {'PASS' if ok else 'FAIL'}: som_click executed")
            else:
                print("  SKIP: Reload button not found in elements")
                ok = False

            results["som_click_reload"] = ok

            # ── Test 4: Verify Firefox still open after reload ──
            print("\n--- Test 4: Firefox still open after reload ---")
            # Small delay for page reload
            await asyncio.sleep(1)
            r = await session.call_tool("list_windows", {})
            text_parts = [c.text for c in r.content if hasattr(c, "text")]
            win_data = json.loads(text_parts[0]) if text_parts else {}
            windows = win_data.get("windows", [])
            firefox_open = any("firefox" in w.get("title", "").lower()
                               or "firefox" in w.get("app_name", "").lower()
                               for w in windows)
            print(f"  Windows: {len(windows)}")
            for w in windows:
                print(f"    {w.get('app_name', '?')}: {w.get('title', '?')}")
            print(f"  Firefox still open: {firefox_open}")

            ok = firefox_open
            print(f"  {'PASS' if ok else 'FAIL'}: Firefox survived reload")
            results["firefox_alive"] = ok

            # ── Test 5: som_click with invalid index ──
            print("\n--- Test 5: som_click(index=999) [expect: error] ---")
            r = await session.call_tool("som_click", {"index": 999})
            text_parts = [c.text for c in r.content if hasattr(c, "text")]
            err_data = json.loads(text_parts[0]) if text_parts else {}
            err_success = err_data.get("success", True)
            err_msg = err_data.get("error", "")
            print(f"  success={err_success} error={err_msg!r}")

            ok = not err_success and "999" in err_msg
            print(f"  {'PASS' if ok else 'FAIL'}: invalid index rejected")
            results["invalid_index"] = ok

            # ── Summary ──
            print("\n" + "=" * 60)
            print("RESULTS SUMMARY")
            print("=" * 60)
            passed = sum(1 for v in results.values() if v)
            total = len(results)
            for name_r, ok in results.items():
                print(f"  {'PASS' if ok else 'FAIL'}: {name_r}")
            print(f"\n  {passed}/{total} tests passed")
            if passed == total:
                print("  ALL TESTS PASSED")
            print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
