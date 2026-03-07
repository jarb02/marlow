"""Scenario G re-test: Workflow recording + replay with recording hook."""

import asyncio
import json
import os
import time

ENV = {}
for key in ("PATH", "HOME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
            "SWAYSOCK", "WAYLAND_DISPLAY"):
    if key in os.environ:
        ENV[key] = os.environ[key]
ENV["PYTHONPATH"] = os.path.expanduser("~/marlow")
if "PATH" not in ENV:
    ENV["PATH"] = "/usr/local/bin:/usr/bin:/bin"
if "SWAYSOCK" not in ENV:
    import glob as _glob
    socks = _glob.glob("/run/user/1000/sway-ipc.*.sock")
    if socks:
        ENV["SWAYSOCK"] = socks[0]
if "WAYLAND_DISPLAY" not in ENV:
    ENV["WAYLAND_DISPLAY"] = "wayland-1"


def _p(r):
    for c in r.content:
        if hasattr(c, "text"):
            try:
                v = json.loads(c.text)
                return v if isinstance(v, dict) else {"_v": v}
            except json.JSONDecodeError:
                return {"_raw": c.text}
    return {}

def _img(r):
    return any(hasattr(c, "mimeType") and "image" in (c.mimeType or "") for c in r.content)


async def main():
    from mcp.client.stdio import stdio_client, StdioServerParameters
    from mcp.client.session import ClientSession

    params = StdioServerParameters(
        command="python3", args=["-m", "marlow"],
        cwd=os.path.expanduser("~/marlow"), env=ENV,
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print(f"Connected — {len((await session.list_tools()).tools)} tools\n")

            async def call(name, args=None):
                return await session.call_tool(name, args or {})

            results = {}

            print("=" * 60)
            print("SCENARIO G — Workflow record → replay")
            print("=" * 60)

            # Clean any leftover workflow
            await call("workflow_delete", {"name": "open_example"})

            # ── Step 1: Start recording ──
            print("\n--- Step 1: Start recording ---")
            r = await call("workflow_record", {"name": "open_example"})
            d = _p(r)
            print(f"  recording: {d.get('recording')} name={d.get('workflow_name')}")
            results["record_start"] = d.get("success", False)

            # ── Step 2: Execute actions (these should be recorded) ──
            print("\n--- Step 2: Execute actions ---")
            r = await call("open_application", {"app_name": "firefox"})
            d = _p(r)
            print(f"  open_application: {d.get('success')}")

            r = await call("wait_for_window", {"title": "Firefox", "timeout": 15})
            d = _p(r)
            print(f"  wait_for_window: {d.get('found', d.get('success'))}")
            await asyncio.sleep(2)

            r = await call("focus_window", {"window_title": "Firefox"})
            d = _p(r)
            print(f"  focus_window: {d.get('success')}")
            await asyncio.sleep(0.5)

            r = await call("hotkey", {"keys": ["ctrl", "l"]})
            d = _p(r)
            print(f"  hotkey ctrl+l: {d.get('success')}")
            await asyncio.sleep(0.5)

            r = await call("type_text", {"text": "https://example.com"})
            d = _p(r)
            print(f"  type_text: {d.get('success')}")
            await asyncio.sleep(0.3)

            r = await call("press_key", {"key": "Return"})
            d = _p(r)
            print(f"  press_key Enter: {d.get('success')}")
            await asyncio.sleep(3)

            r = await call("wait_for_idle", {"timeout": 10})
            d = _p(r)
            print(f"  wait_for_idle: {d.get('success', d.get('idle'))}")

            # ── Step 3: Stop recording ──
            print("\n--- Step 3: Stop recording ---")
            r = await call("workflow_stop")
            d = _p(r)
            steps_recorded = d.get("steps", 0)
            print(f"  workflow_stop: steps={steps_recorded} msg={d.get('message','')[:60]}")
            results["record_stop"] = steps_recorded > 0

            # ── Step 4: Verify workflow saved ──
            print("\n--- Step 4: List workflows ---")
            r = await call("workflow_list")
            d = _p(r)
            wfs = d.get("workflows", [])
            wf_found = False
            wf_steps = 0
            wf_tools = []
            for wf in wfs:
                if wf.get("name") == "open_example":
                    wf_found = True
                    wf_steps = wf.get("step_count", 0)
                    wf_tools = wf.get("tools", [])
                    break
            print(f"  found: {wf_found} steps={wf_steps}")
            print(f"  tools: {wf_tools}")
            results["workflow_saved"] = wf_found and wf_steps > 0

            # ── Step 5: Close Firefox ──
            print("\n--- Step 5: Close Firefox ---")
            await call("run_command", {
                "command": (
                    "SWAYSOCK=$(ls /run/user/1000/sway-ipc.*.sock 2>/dev/null | head -1) "
                    "swaymsg '[app_id=org.mozilla.firefox] kill' 2>/dev/null; "
                    "sleep 1; pkill -f firefox 2>/dev/null; echo done"
                ),
            })
            await asyncio.sleep(2)

            r = await call("list_windows")
            d = _p(r)
            ff_gone = not any("firefox" in str(w).lower() for w in d.get("windows", []))
            print(f"  firefox closed: {ff_gone}")
            results["firefox_closed"] = ff_gone

            # ── Step 6: Replay workflow ──
            print("\n--- Step 6: Replay workflow ---")
            r = await call("workflow_run", {"name": "open_example"})
            d = _p(r)
            replay_ok = d.get("success", False)
            completed = d.get("completed_steps", 0)
            total = d.get("total_steps", 0)
            print(f"  replay: success={replay_ok} completed={completed}/{total}")
            if d.get("results"):
                for step_r in d["results"][:5]:
                    print(f"    step {step_r.get('step')}: {step_r.get('tool')} → {step_r.get('status')}")
            results["replay_executed"] = completed > 0

            # ── Step 7: Verify Firefox opened ──
            print("\n--- Step 7: Verify Firefox opened ---")
            # Wait for Firefox to appear from replay
            await asyncio.sleep(5)
            r = await call("wait_for_window", {"title": "Firefox", "timeout": 10})
            d = _p(r)
            ff_back = d.get("found", d.get("success", False))
            print(f"  firefox back: {ff_back}")

            if ff_back:
                await asyncio.sleep(3)
                # Check if it navigated to example.com
                r = await call("list_windows")
                d = _p(r)
                for w in d.get("windows", []):
                    if "firefox" in str(w).lower():
                        print(f"  title: {w.get('title', '')}")
                        break

                r = await call("ocr_region", {})
                d = _p(r)
                text = d.get("text", "").lower()
                has_example = "example" in text
                print(f"  page has 'example': {has_example}")
                results["replay_navigated"] = has_example
            else:
                results["replay_navigated"] = False

            # Screenshot
            r = await call("take_screenshot")
            print(f"  screenshot: {'ok' if _img(r) else 'fail'}")

            # ── Cleanup ──
            print("\n--- Cleanup ---")
            await call("run_command", {
                "command": (
                    "SWAYSOCK=$(ls /run/user/1000/sway-ipc.*.sock 2>/dev/null | head -1) "
                    "swaymsg '[app_id=org.mozilla.firefox] kill' 2>/dev/null; "
                    "sleep 1; pkill -f firefox 2>/dev/null; echo done"
                ),
            })
            await asyncio.sleep(1)
            # Clean up the test workflow
            await call("workflow_delete", {"name": "open_example"})

            r = await call("list_windows")
            d = _p(r)
            remaining = [w.get("app_name") for w in d.get("windows", [])
                         if "firefox" in str(w).lower()]
            print(f"  firefox: {remaining or '(cleaned)'}")

            # ── Summary ──
            print(f"\n{'=' * 60}")
            print("SUMMARY")
            print("=" * 60)
            for k, v in results.items():
                tag = "PASS" if v else "FAIL"
                print(f"  [{tag}] {k}")

            passed = sum(1 for v in results.values() if v)
            total = len(results)
            print(f"\n  {passed}/{total} checks passed")

            all_pass = all(results.values())
            print(f"\n  SCENARIO G: {'PASS' if all_pass else 'PARTIAL'}")
            print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
