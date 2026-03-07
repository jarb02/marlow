"""Test final 3 modules: Adaptive, LfD, app_script via MCP on Linux."""

import asyncio
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

if "SWAYSOCK" not in ENV:
    import glob
    socks = glob.glob("/run/user/1000/sway-ipc.*.sock")
    if socks:
        ENV["SWAYSOCK"] = socks[0]
        print(f"Auto-detected SWAYSOCK: {socks[0]}")

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
            print("FINAL TOOLS TEST: Adaptive + LfD + app_script")
            print("=" * 60)

            tools_resp = await session.list_tools()
            tool_names = [t.name for t in tools_resp.tools]
            print(f"\nTotal tools: {len(tool_names)}")

            results = {}

            # ── Test 1: Total tool count >= 101 ──
            print("\n--- Test 1: Total tool count ---")
            ok = len(tool_names) >= 101
            print(f"  {'PASS' if ok else 'FAIL'}: {len(tool_names)} tools registered")
            results["total_count"] = ok

            # ── Test 2: Adaptive tools registered ──
            print("\n--- Test 2: Adaptive tools ---")
            adaptive_tools = ["get_suggestions", "accept_suggestion", "dismiss_suggestion"]
            missing = [t for t in adaptive_tools if t not in tool_names]
            ok = len(missing) == 0
            print(f"  {'PASS' if ok else 'FAIL'}: {3 - len(missing)}/3 adaptive tools")
            if missing:
                print(f"  Missing: {missing}")
            results["adaptive_registration"] = ok

            # ── Test 3: get_suggestions works ──
            print("\n--- Test 3: get_suggestions ---")
            r = await session.call_tool("get_suggestions", {})
            text_parts = [c.text for c in r.content if hasattr(c, "text")]
            data = json.loads(text_parts[0]) if text_parts else {}
            ok = data.get("success", False)
            print(f"  suggestions={len(data.get('suggestions', []))}")
            print(f"  actions_recorded={data.get('actions_recorded', 0)}")
            print(f"  {'PASS' if ok else 'FAIL'}: get_suggestions responded")
            results["get_suggestions"] = ok

            # ── Test 4: LfD tools registered ──
            print("\n--- Test 4: LfD tools ---")
            lfd_tools = ["demo_start", "demo_stop", "demo_status", "demo_list", "demo_replay"]
            missing = [t for t in lfd_tools if t not in tool_names]
            ok = len(missing) == 0
            print(f"  {'PASS' if ok else 'FAIL'}: {5 - len(missing)}/5 LfD tools")
            if missing:
                print(f"  Missing: {missing}")
            results["lfd_registration"] = ok

            # ── Test 5: demo_start + demo_status + demo_stop lifecycle ──
            print("\n--- Test 5: LfD lifecycle (start -> status -> stop) ---")

            # Start
            r = await session.call_tool("demo_start", {
                "name": "test_demo",
                "description": "Integration test demo",
            })
            text_parts = [c.text for c in r.content if hasattr(c, "text")]
            data = json.loads(text_parts[0]) if text_parts else {}
            start_ok = data.get("success", False)
            print(f"  start: success={start_ok} kb_hook={data.get('keyboard_hook')}")

            # Status
            r = await session.call_tool("demo_status", {})
            text_parts = [c.text for c in r.content if hasattr(c, "text")]
            data = json.loads(text_parts[0]) if text_parts else {}
            status_ok = data.get("recording", False)
            print(f"  status: recording={status_ok} name={data.get('name')}")

            # Stop
            r = await session.call_tool("demo_stop", {})
            text_parts = [c.text for c in r.content if hasattr(c, "text")]
            data = json.loads(text_parts[0]) if text_parts else {}
            stop_ok = data.get("success", False)
            print(f"  stop: success={stop_ok} events={data.get('events')} steps={data.get('steps')}")
            saved_to = data.get("saved_to", "")
            if saved_to:
                print(f"  saved_to: {saved_to}")

            ok = start_ok and status_ok and stop_ok
            print(f"  {'PASS' if ok else 'FAIL'}: LfD lifecycle")
            results["lfd_lifecycle"] = ok

            # ── Test 6: demo_list (should have at least 1 from test 5) ──
            print("\n--- Test 6: demo_list ---")
            r = await session.call_tool("demo_list", {})
            text_parts = [c.text for c in r.content if hasattr(c, "text")]
            data = json.loads(text_parts[0]) if text_parts else {}
            count = data.get("count", 0)
            print(f"  demos found: {count}")
            ok = count >= 1
            print(f"  {'PASS' if ok else 'FAIL'}: at least 1 demo saved")
            results["demo_list"] = ok

            # ── Test 7: demo_replay ──
            print("\n--- Test 7: demo_replay ---")
            demos = data.get("demos", [])
            if demos:
                filename = demos[0].get("file", "")
                r = await session.call_tool("demo_replay", {"filename": filename})
                text_parts = [c.text for c in r.content if hasattr(c, "text")]
                data = json.loads(text_parts[0]) if text_parts else {}
                ok = data.get("success", False)
                print(f"  replayed: {filename}")
                print(f"  name={data.get('name')} steps={data.get('step_count')}")
            else:
                ok = False
                print("  No demos to replay")
            print(f"  {'PASS' if ok else 'FAIL'}: demo_replay")
            results["demo_replay"] = ok

            # ── Test 8: run_app_script returns stub message ──
            print("\n--- Test 8: run_app_script (Windows-only stub) ---")
            r = await session.call_tool("run_app_script", {
                "script": "test",
                "app_name": "Word",
            })
            text_parts = [c.text for c in r.content if hasattr(c, "text")]
            data = json.loads(text_parts[0]) if text_parts else {}
            error_msg = data.get("error", "")
            ok = (
                data.get("success") is False
                and "COM" in error_msg
                and "Windows" in error_msg
                and data.get("platform") == "linux"
            )
            print(f"  error: {error_msg[:60]}")
            print(f"  alternatives: {data.get('alternatives', [])}")
            print(f"  {'PASS' if ok else 'FAIL'}: app_script returns helpful stub")
            results["app_script_stub"] = ok

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


if __name__ == "__main__":
    asyncio.run(main())
