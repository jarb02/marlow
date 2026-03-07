"""Test OCR, smart_find, and cascade_find via MCP on Linux.

Requires Firefox open on example.com.
"""

import asyncio
import json
import os
import sys


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
            print("OCR + SMART_FIND + CASCADE_FIND TEST")
            print("=" * 60)

            # Verify tools registered
            tools_resp = await session.list_tools()
            tool_names = [t.name for t in tools_resp.tools]
            print(f"\nTools: {len(tool_names)}")
            for needed in ["ocr_region", "list_ocr_languages", "smart_find", "cascade_find"]:
                status = "OK" if needed in tool_names else "MISSING"
                print(f"  {needed}: {status}")

            results = {}

            # ── Test 1: list_ocr_languages ──
            print("\n--- Test 1: list_ocr_languages ---")
            r = await session.call_tool("list_ocr_languages", {})
            data = json.loads(r.content[0].text)
            langs = data.get("languages", [])
            print(f"  Languages: {langs}")
            ok = "eng" in langs
            print(f"  {'PASS' if ok else 'FAIL'}: eng available")
            results["list_ocr_languages"] = ok

            # ── Test 2: ocr_region (Firefox window) ──
            print("\n--- Test 2: ocr_region (window_title='Firefox') ---")
            r = await session.call_tool("ocr_region", {"window_title": "Firefox"})
            data = json.loads(r.content[0].text)
            if data.get("success"):
                text = data.get("text", "")
                words = data.get("words", [])
                print(f"  Text ({len(text)} chars): {text[:100]}...")
                print(f"  Words: {len(words)}")
                has_example = "example" in text.lower()
                print(f"  Contains 'Example': {has_example}")
                if words:
                    w = words[0]
                    print(f"  First word: '{w['text']}' at ({w['x']},{w['y']}) "
                          f"conf={w['confidence']}")
                ok = has_example and len(words) > 5
                print(f"  {'PASS' if ok else 'FAIL'}: OCR found text")
            else:
                print(f"  Error: {data.get('error')}")
                ok = False
                print("  FAIL")
            results["ocr_region"] = ok

            # ── Test 3: smart_find (AT-SPI2 — should find Reload button) ──
            print("\n--- Test 3: smart_find(name='Reload') [expect: atspi] ---")
            r = await session.call_tool("smart_find", {"name": "Reload"})
            text_parts = [c.text for c in r.content if hasattr(c, "text")]
            data = json.loads(text_parts[0]) if text_parts else {}
            has_image = any(hasattr(c, "data") for c in r.content)
            method = data.get("method", "?")
            success = data.get("success", False)
            conf = data.get("confidence", 0)
            print(f"  success={success} method={method} confidence={conf:.2f}")
            print(f"  has_image={has_image}")
            if success:
                elem = data.get("element", {})
                print(f"  element: {elem.get('name')} [{elem.get('role')}]")
            ok = success and method == "atspi"
            print(f"  {'PASS' if ok else 'FAIL'}: found via AT-SPI2")
            results["smart_find_atspi"] = ok

            # ── Test 4: smart_find (page text — may use AT-SPI2 or OCR) ──
            print("\n--- Test 4: smart_find(name='Example Domain') [expect: atspi or ocr] ---")
            r = await session.call_tool("smart_find", {"name": "Example Domain"})
            # May have image content if screenshot fallback
            text_parts = [c.text for c in r.content if hasattr(c, "text")]
            data = json.loads(text_parts[0]) if text_parts else {}
            method = data.get("method", "?")
            success = data.get("success", False)
            print(f"  success={success} method={method}")
            if success:
                elem = data.get("element", {})
                print(f"  element: {elem.get('name')} bounds={elem.get('bounds')}")
            ok = success and method in ("atspi", "ocr")
            print(f"  {'PASS' if ok else 'FAIL'}: found via {method}")
            results["smart_find_text"] = ok

            # ── Test 5: cascade_find (should find Reload via exact strategy) ──
            print("\n--- Test 5: cascade_find(name='Reload') ---")
            r = await session.call_tool("cascade_find", {"name": "Reload"})
            text_parts = [c.text for c in r.content if hasattr(c, "text")]
            data = json.loads(text_parts[0]) if text_parts else {}
            strategy = data.get("strategy_used", "?")
            success = data.get("success", False)
            attempts = data.get("attempts", [])
            print(f"  success={success} strategy={strategy}")
            for a in attempts:
                print(f"    {a['strategy']}: success={a['success']}")
            ok = success and strategy == "exact"
            print(f"  {'PASS' if ok else 'FAIL'}: cascade exact match")
            results["cascade_find_exact"] = ok

            # ── Test 6: cascade_find (nonexistent — should fail all) ──
            print("\n--- Test 6: cascade_find(name='xyznonexistent123') ---")
            r = await session.call_tool("cascade_find", {"name": "xyznonexistent123"})
            # May have image from screenshot fallback
            text_parts = [c.text for c in r.content if hasattr(c, "text")]
            data = json.loads(text_parts[0]) if text_parts else {}
            success = data.get("success", False)
            strategy = data.get("strategy_used", "?")
            attempts = data.get("attempts", [])
            print(f"  success={success} strategy={strategy}")
            for a in attempts:
                note = f" ({a.get('note', '')})" if a.get("note") else ""
                print(f"    {a['strategy']}: success={a['success']}{note}")
            ok = not success and len(attempts) >= 3
            print(f"  {'PASS' if ok else 'FAIL'}: all strategies exhausted")
            results["cascade_find_fail"] = ok

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
