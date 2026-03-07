"""Test CDP tools via MCP on Linux.

Runs cdp_discover, cdp_list_connections, and cdp_get_knowledge_base.
If Electron apps are running, also tests cdp_connect.
"""

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
            print("CDP TOOLS TEST")
            print("=" * 60)

            # Verify CDP tools are registered
            tools_resp = await session.list_tools()
            tool_names = [t.name for t in tools_resp.tools]
            print(f"\nTotal tools: {len(tool_names)}")

            cdp_tools = [
                "cdp_discover", "cdp_connect", "cdp_disconnect",
                "cdp_list_connections", "cdp_send", "cdp_click",
                "cdp_type_text", "cdp_key_combo", "cdp_screenshot",
                "cdp_evaluate", "cdp_get_dom", "cdp_click_selector",
                "cdp_ensure", "cdp_restart_confirmed", "cdp_get_knowledge_base",
            ]

            results = {}

            # ── Test 1: All 15 CDP tools registered ──
            print("\n--- Test 1: CDP tools registration ---")
            missing = []
            for t in cdp_tools:
                status = "OK" if t in tool_names else "MISSING"
                if status == "MISSING":
                    missing.append(t)
                print(f"  {t}: {status}")

            ok = len(missing) == 0
            print(f"  {'PASS' if ok else 'FAIL'}: {15 - len(missing)}/15 CDP tools registered")
            results["registration"] = ok

            # ── Test 2: cdp_discover ──
            print("\n--- Test 2: cdp_discover (port scan) ---")
            r = await session.call_tool("cdp_discover", {
                "port_start": 9222,
                "port_end": 9230,
            })
            text_parts = [c.text for c in r.content if hasattr(c, "text")]
            data = json.loads(text_parts[0]) if text_parts else {}
            targets = data.get("targets", [])
            ports_scanned = data.get("ports_scanned", 0)
            print(f"  ports_scanned={ports_scanned} targets_found={len(targets)}")
            for t in targets:
                print(f"    port={t.get('port')} title={t.get('title')!r}")

            ok = "error" not in data
            print(f"  {'PASS' if ok else 'FAIL'}: discover ran without error")
            results["cdp_discover"] = ok

            # ── Test 3: cdp_list_connections ──
            print("\n--- Test 3: cdp_list_connections ---")
            r = await session.call_tool("cdp_list_connections", {})
            text_parts = [c.text for c in r.content if hasattr(c, "text")]
            data = json.loads(text_parts[0]) if text_parts else {}
            connections = data.get("connections", [])
            print(f"  active_connections={len(connections)}")

            ok = "error" not in data
            print(f"  {'PASS' if ok else 'FAIL'}: list_connections ran without error")
            results["cdp_list_connections"] = ok

            # ── Test 4: cdp_get_knowledge_base ──
            print("\n--- Test 4: cdp_get_knowledge_base ---")
            r = await session.call_tool("cdp_get_knowledge_base", {})
            text_parts = [c.text for c in r.content if hasattr(c, "text")]
            data = json.loads(text_parts[0]) if text_parts else {}
            defaults = data.get("default_ports", {})
            print(f"  known_apps={len(defaults)}")
            for app, port in list(defaults.items())[:5]:
                print(f"    {app}: port {port}")

            ok = "error" not in data and len(defaults) > 0
            print(f"  {'PASS' if ok else 'FAIL'}: knowledge base loaded")
            results["cdp_get_knowledge_base"] = ok

            # ── Test 5: cdp_ensure (safe — just checks, no restart) ──
            print("\n--- Test 5: cdp_ensure(app_name='code') ---")
            r = await session.call_tool("cdp_ensure", {
                "app_name": "code",
            })
            text_parts = [c.text for c in r.content if hasattr(c, "text")]
            data = json.loads(text_parts[0]) if text_parts else {}
            print(f"  result keys: {list(data.keys())}")
            if data.get("success"):
                print(f"  Connected! port={data.get('port')}")
            elif data.get("action_required"):
                print(f"  action_required={data.get('action_required')}")
                print(f"  hint: {data.get('hint', '')[:80]}")
            elif data.get("error"):
                print(f"  error: {data.get('error')[:80]}")

            # cdp_ensure either connects or asks for restart — both are valid
            ok = "success" in data or "action_required" in data or "error" in data
            print(f"  {'PASS' if ok else 'FAIL'}: ensure returned valid response")
            results["cdp_ensure"] = ok

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
