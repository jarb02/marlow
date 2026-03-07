"""Test clipboard, visual_diff, workflows, and diagnostics via MCP on Linux."""

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

# Auto-detect SWAYSOCK if not in env
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
            print("EASY TOOLS TEST (clipboard, visual_diff, workflows, diagnostics)")
            print("=" * 60)

            # Verify tools registered
            tools_resp = await session.list_tools()
            tool_names = [t.name for t in tools_resp.tools]
            print(f"\nTotal tools: {len(tool_names)}")

            needed = [
                "clipboard", "clipboard_history",
                "visual_diff", "visual_diff_compare",
                "workflow_record", "workflow_stop", "workflow_run",
                "workflow_list", "workflow_delete",
                "run_diagnostics",
            ]
            for t in needed:
                status = "OK" if t in tool_names else "MISSING"
                print(f"  {t}: {status}")

            results = {}

            # ── Test 1: Clipboard set + get ──
            print("\n--- Test 1: clipboard set + get ---")
            r = await session.call_tool("clipboard", {
                "action": "set",
                "text": "Marlow clipboard test 2026",
            })
            text_parts = [c.text for c in r.content if hasattr(c, "text")]
            data = json.loads(text_parts[0]) if text_parts else {}
            set_ok = data.get("success", False)
            print(f"  set: success={set_ok}")

            r = await session.call_tool("clipboard", {"action": "get"})
            text_parts = [c.text for c in r.content if hasattr(c, "text")]
            data = json.loads(text_parts[0]) if text_parts else {}
            got = data.get("text", "")
            print(f"  get: text={got!r}")

            ok = set_ok and got == "Marlow clipboard test 2026"
            print(f"  {'PASS' if ok else 'FAIL'}: clipboard round-trip")
            results["clipboard"] = ok

            # ── Test 2: Clipboard history ──
            print("\n--- Test 2: clipboard_history ---")
            r = await session.call_tool("clipboard_history", {"limit": 10})
            text_parts = [c.text for c in r.content if hasattr(c, "text")]
            data = json.loads(text_parts[0]) if text_parts else {}
            entries = data.get("entries", [])
            total = data.get("total", 0)
            print(f"  entries={len(entries)} total={total}")
            if entries:
                print(f"  last: {entries[-1].get('content', '')[:40]!r}")

            ok = data.get("success", False) and total > 0
            print(f"  {'PASS' if ok else 'FAIL'}: history has entries")
            results["clipboard_history"] = ok

            # ── Test 3: Visual diff — static desktop ──
            print("\n--- Test 3: visual_diff (static desktop, ~0% change) ---")
            r = await session.call_tool("visual_diff", {
                "label": "test_static",
            })
            text_parts = [c.text for c in r.content if hasattr(c, "text")]
            data = json.loads(text_parts[0]) if text_parts else {}
            diff_id = data.get("diff_id", "")
            print(f"  capture: diff_id={diff_id} success={data.get('success')}")

            # Wait a moment
            await asyncio.sleep(1.5)

            r = await session.call_tool("visual_diff_compare", {
                "diff_id": diff_id,
            })
            text_parts = [c.text for c in r.content if hasattr(c, "text")]
            data = json.loads(text_parts[0]) if text_parts else {}
            change_pct = data.get("change_percent", -1)
            print(f"  compare: change_percent={change_pct}%")

            ok = data.get("success", False) and change_pct < 5.0
            print(f"  {'PASS' if ok else 'FAIL'}: static desktop has <5% change")
            results["visual_diff"] = ok

            # ── Test 4: Workflow list ──
            print("\n--- Test 4: workflow_list ---")
            r = await session.call_tool("workflow_list", {})
            text_parts = [c.text for c in r.content if hasattr(c, "text")]
            data = json.loads(text_parts[0]) if text_parts else {}
            wf_count = data.get("total", -1)
            print(f"  workflows={wf_count}")

            ok = data.get("success", False) and wf_count >= 0
            print(f"  {'PASS' if ok else 'FAIL'}: workflow_list works")
            results["workflow_list"] = ok

            # ── Test 5: Run diagnostics ──
            print("\n--- Test 5: run_diagnostics ---")
            r = await session.call_tool("run_diagnostics", {})
            text_parts = [c.text for c in r.content if hasattr(c, "text")]
            data = json.loads(text_parts[0]) if text_parts else {}
            passed_checks = data.get("passed", 0)
            total_checks = data.get("total", 0)
            print(f"  checks: {passed_checks}/{total_checks}")

            if data.get("checks"):
                for name_c, info in data["checks"].items():
                    status = info.get("status", "?")
                    detail = info.get("detail", "")[:60]
                    print(f"    {status}: {name_c} — {detail}")

            ok = data.get("success", False) and passed_checks > 5
            print(f"  {'PASS' if ok else 'FAIL'}: diagnostics ran ({passed_checks}/{total_checks} checks pass)")
            results["diagnostics"] = ok

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
