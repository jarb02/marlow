"""Test Voice/TTS and Background Mode tools via MCP on Linux."""

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
            print("VOICE/TTS + BACKGROUND MODE TEST")
            print("=" * 60)

            tools_resp = await session.list_tools()
            tool_names = [t.name for t in tools_resp.tools]
            print(f"\nTotal tools: {len(tool_names)}")

            needed = [
                "speak", "speak_and_listen", "listen_for_command",
                "transcribe_audio", "download_whisper_model",
                "get_voice_hotkey_status", "toggle_voice_overlay",
                "setup_background_mode", "move_to_agent_screen",
                "move_to_user_screen", "get_agent_screen_state",
                "set_agent_screen_only",
            ]
            for t in needed:
                status = "OK" if t in tool_names else "MISSING"
                print(f"  {t}: {status}")

            results = {}

            # ── Test 1: All 12 new tools registered ──
            print("\n--- Test 1: Tool registration ---")
            missing = [t for t in needed if t not in tool_names]
            ok = len(missing) == 0
            print(f"  {'PASS' if ok else 'FAIL'}: {12 - len(missing)}/12 tools registered")
            if missing:
                print(f"  Missing: {missing}")
            results["registration"] = ok

            # ── Test 2: setup_background_mode ──
            print("\n--- Test 2: setup_background_mode ---")
            r = await session.call_tool("setup_background_mode", {})
            text_parts = [c.text for c in r.content if hasattr(c, "text")]
            data = json.loads(text_parts[0]) if text_parts else {}
            agent_ws = data.get("agent_workspace", "?")
            print(f"  agent_workspace={agent_ws}")
            print(f"  user_workspace={data.get('user_workspace', '?')}")

            ok = data.get("success", False)
            print(f"  {'PASS' if ok else 'FAIL'}: background mode set up")
            results["setup_background"] = ok

            # ── Test 3: get_agent_screen_state (empty) ──
            print("\n--- Test 3: get_agent_screen_state (should be empty) ---")
            r = await session.call_tool("get_agent_screen_state", {})
            text_parts = [c.text for c in r.content if hasattr(c, "text")]
            data = json.loads(text_parts[0]) if text_parts else {}
            win_count = data.get("window_count", -1)
            print(f"  windows_in_agent={win_count} active={data.get('active')}")

            ok = data.get("success", False) and win_count == 0
            print(f"  {'PASS' if ok else 'FAIL'}: agent workspace empty")
            results["agent_state_empty"] = ok

            # ── Test 4: Launch Firefox, move to agent, verify, move back ──
            print("\n--- Test 4: Move Firefox to agent workspace and back ---")
            sway_cmd('exec firefox https://example.com')
            print("  Launched Firefox, waiting 5s...")
            await asyncio.sleep(5)

            # Move to agent workspace
            r = await session.call_tool("move_to_agent_screen", {
                "window_title": "Example Domain",
            })
            text_parts = [c.text for c in r.content if hasattr(c, "text")]
            data = json.loads(text_parts[0]) if text_parts else {}
            move_ok = data.get("success", False)
            print(f"  move_to_agent: success={move_ok}")

            # Check agent workspace has Firefox
            await asyncio.sleep(1)
            r = await session.call_tool("get_agent_screen_state", {})
            text_parts = [c.text for c in r.content if hasattr(c, "text")]
            data = json.loads(text_parts[0]) if text_parts else {}
            agent_wins = data.get("windows", [])
            print(f"  agent windows: {len(agent_wins)}")
            for w in agent_wins:
                print(f"    {w.get('app_id', '?')}: {w.get('title', '?')[:40]}")

            firefox_in_agent = any(
                "firefox" in (w.get("app_id", "") or "").lower()
                or "example" in (w.get("title", "") or "").lower()
                for w in agent_wins
            )

            # Move back to user workspace
            r = await session.call_tool("move_to_user_screen", {
                "window_title": "Example Domain",
            })
            text_parts = [c.text for c in r.content if hasattr(c, "text")]
            data = json.loads(text_parts[0]) if text_parts else {}
            move_back_ok = data.get("success", False)
            print(f"  move_to_user: success={move_back_ok}")

            await asyncio.sleep(1)

            ok = move_ok and firefox_in_agent and move_back_ok
            print(f"  {'PASS' if ok else 'FAIL'}: Firefox moved to agent and back")
            results["move_windows"] = ok

            # ── Test 5: get_voice_hotkey_status ──
            print("\n--- Test 5: get_voice_hotkey_status ---")
            r = await session.call_tool("get_voice_hotkey_status", {})
            text_parts = [c.text for c in r.content if hasattr(c, "text")]
            data = json.loads(text_parts[0]) if text_parts else {}
            print(f"  hotkey_active={data.get('hotkey_active')}")
            print(f"  note: {data.get('platform_note', '')[:60]}")

            ok = data.get("success", False)
            print(f"  {'PASS' if ok else 'FAIL'}: hotkey status responded")
            results["voice_hotkey_status"] = ok

            # ── Test 6: transcribe_audio import check ──
            print("\n--- Test 6: transcribe_audio (no-file error check) ---")
            r = await session.call_tool("transcribe_audio", {
                "audio_path": "/tmp/nonexistent_audio_file.wav",
            })
            text_parts = [c.text for c in r.content if hasattr(c, "text")]
            data = json.loads(text_parts[0]) if text_parts else {}
            error = data.get("error", "")
            print(f"  error={error[:60]}")

            # Should get a "file not found" error, not an import error
            ok = "not found" in error.lower() or "not installed" in error.lower()
            print(f"  {'PASS' if ok else 'FAIL'}: transcribe_audio responds correctly")
            results["transcribe_audio"] = ok

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
    r = sway_cmd('-t get_tree')
    if r.returncode == 0:
        import re
        apps = re.findall(r'"app_id":\s*"([^"]+)"', r.stdout)
        print(f"Desktop apps remaining: {apps or ['(none)']}")


if __name__ == "__main__":
    asyncio.run(main())
