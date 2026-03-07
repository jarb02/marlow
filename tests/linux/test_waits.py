"""Test wait_for_* tools via MCP on Linux.

Launches Firefox, then tests all 4 wait tools.
"""

import asyncio
import json
import os
import subprocess


ENV = {}
for key in ("PATH", "HOME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
            "SWAYSOCK", "WAYLAND_DISPLAY"):
    if key in os.environ:
        ENV[key] = os.environ[key]
ENV["PYTHONPATH"] = os.path.expanduser("~/marlow")
if "PATH" not in ENV:
    ENV["PATH"] = "/usr/local/bin:/usr/bin:/bin"

# Auto-detect SWAYSOCK if not in env
if "SWAYSOCK" not in ENV:
    import glob
    socks = glob.glob("/run/user/1000/sway-ipc.*.sock")
    if socks:
        ENV["SWAYSOCK"] = socks[0]
        print(f"Auto-detected SWAYSOCK: {socks[0]}")

if "WAYLAND_DISPLAY" not in ENV:
    ENV["WAYLAND_DISPLAY"] = "wayland-1"


def sway_cmd(cmd):
    """Run a swaymsg command."""
    return subprocess.run(
        ["swaymsg", cmd],
        capture_output=True, text=True, timeout=5,
        env={**os.environ, "SWAYSOCK": ENV.get("SWAYSOCK", "")},
    )


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
            print("WAIT_FOR_* TOOLS TEST")
            print("=" * 60)

            # Verify tools
            tools_resp = await session.list_tools()
            tool_names = [t.name for t in tools_resp.tools]
            print(f"\nTools: {len(tool_names)}")
            for needed in ["wait_for_element", "wait_for_text",
                           "wait_for_window", "wait_for_idle"]:
                status = "OK" if needed in tool_names else "MISSING"
                print(f"  {needed}: {status}")

            results = {}

            # ── Test 1: Launch Firefox + wait_for_window ──
            print("\n--- Test 1: wait_for_window after launching Firefox ---")
            # Launch Firefox (non-blocking)
            sway_cmd('exec firefox https://example.com')
            print("  Firefox launched, waiting...")

            r = await session.call_tool("wait_for_window", {
                "title": "Example Domain",
                "timeout": 15,
                "interval": 0.5,
            })
            text_parts = [c.text for c in r.content if hasattr(c, "text")]
            data = json.loads(text_parts[0]) if text_parts else {}
            found = data.get("found", False)
            elapsed = data.get("elapsed", 0)
            print(f"  found={found} elapsed={elapsed}s")
            if found:
                win = data.get("window", {})
                print(f"  window: {win.get('title')!r} ({win.get('app_name')})")

            ok = found
            print(f"  {'PASS' if ok else 'FAIL'}: window appeared")
            results["wait_for_window"] = ok

            # ── Test 2: wait_for_element (AT-SPI2) ──
            print("\n--- Test 2: wait_for_element(name='Reload', role='button') ---")
            r = await session.call_tool("wait_for_element", {
                "name": "Reload",
                "role": "button",
                "timeout": 5,
            })
            text_parts = [c.text for c in r.content if hasattr(c, "text")]
            data = json.loads(text_parts[0]) if text_parts else {}
            found = data.get("found", False)
            elapsed = data.get("elapsed", 0)
            method = data.get("method", "?")
            print(f"  found={found} elapsed={elapsed}s method={method}")
            if found:
                elem = data.get("element", {})
                print(f"  element: {elem.get('name')!r} [{elem.get('role')}]")

            ok = found and method == "atspi"
            print(f"  {'PASS' if ok else 'FAIL'}: element found via AT-SPI2")
            results["wait_for_element"] = ok

            # ── Test 3: wait_for_text (OCR) ──
            print("\n--- Test 3: wait_for_text(text='Example Domain') ---")
            r = await session.call_tool("wait_for_text", {
                "text": "Example Domain",
                "window_title": "Firefox",
                "timeout": 10,
            })
            text_parts = [c.text for c in r.content if hasattr(c, "text")]
            data = json.loads(text_parts[0]) if text_parts else {}
            found = data.get("found", False)
            elapsed = data.get("elapsed", 0)
            bounds = data.get("bounds")
            print(f"  found={found} elapsed={elapsed}s")
            if bounds:
                print(f"  bounds: {bounds}")

            ok = found
            print(f"  {'PASS' if ok else 'FAIL'}: text found via OCR")
            results["wait_for_text"] = ok

            # ── Test 4: wait_for_idle ──
            print("\n--- Test 4: wait_for_idle(timeout=5) ---")
            r = await session.call_tool("wait_for_idle", {
                "timeout": 5,
                "threshold": 0.95,
            })
            text_parts = [c.text for c in r.content if hasattr(c, "text")]
            data = json.loads(text_parts[0]) if text_parts else {}
            idle = data.get("idle", False)
            similarity = data.get("similarity", 0)
            elapsed = data.get("elapsed", 0)
            print(f"  idle={idle} similarity={similarity} elapsed={elapsed}s")

            ok = idle
            print(f"  {'PASS' if ok else 'FAIL'}: screen stable")
            results["wait_for_idle"] = ok

            # ── Test 5: wait_for_element timeout ──
            print("\n--- Test 5: wait_for_element(name='NoExiste', timeout=3) ---")
            r = await session.call_tool("wait_for_element", {
                "name": "NoExiste",
                "timeout": 3,
            })
            text_parts = [c.text for c in r.content if hasattr(c, "text")]
            data = json.loads(text_parts[0]) if text_parts else {}
            found = data.get("found", False)
            elapsed = data.get("elapsed", 0)
            error = data.get("error", "")
            print(f"  found={found} elapsed={elapsed}s")
            print(f"  error={error!r}")

            ok = not found and elapsed >= 2.5
            print(f"  {'PASS' if ok else 'FAIL'}: correctly timed out")
            results["wait_timeout"] = ok

            # ── Summary ──
            print("\n" + "=" * 60)
            print("RESULTS SUMMARY")
            print("=" * 60)
            passed = sum(1 for v in results.values() if v)
            total = len(results)
            for name_r, ok_r in results.items():
                print(f"  {'PASS' if ok_r else 'FAIL'}: {name_r}")
            print(f"\n  {passed}/{total} tests passed")
            if passed == total:
                print("  ALL TESTS PASSED")
            print("=" * 60)

    # ── Cleanup: close Firefox ──
    print("\nCleaning up: closing Firefox...")
    sway_cmd('[app_id="org.mozilla.firefox"] kill')
    await asyncio.sleep(1)
    # Verify clean desktop
    r = sway_cmd('-t get_tree')
    if r.returncode == 0:
        import re
        apps = re.findall(r'"app_id":\s*"([^"]+)"', r.stdout)
        print(f"Desktop apps remaining: {apps or ['(none)']}")


if __name__ == "__main__":
    asyncio.run(main())
