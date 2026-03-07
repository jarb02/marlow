"""Post-fix test: Level 4 (Shadow Mode) + Level 5 (Resilience) + response format check."""

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


def _parse(r):
    for c in r.content:
        if hasattr(c, "text"):
            try:
                parsed = json.loads(c.text)
                if isinstance(parsed, dict):
                    return parsed
                return {"_wrapped": parsed}
            except json.JSONDecodeError:
                return {"_raw": c.text}
    return {}


def _has_image(r) -> bool:
    return any(hasattr(c, "mimeType") and "image" in (c.mimeType or "") for c in r.content)


def _image_size(r) -> int:
    import base64
    for c in r.content:
        if hasattr(c, "data") and hasattr(c, "mimeType"):
            return len(base64.b64decode(c.data))
    return 0


results = {}


def log(name, status, elapsed, notes=""):
    results[name] = status
    tag = {"PASS": "PASS", "FAIL": "FAIL", "WARN": "WARN"}[status]
    n = f" — {notes}" if notes else ""
    print(f"  [{tag}] {name} ({elapsed:.2f}s){n}")


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
            tools = [t.name for t in (await session.list_tools()).tools]
            print(f"Connected — {len(tools)} tools\n")

            async def call(name, args=None):
                t0 = time.time()
                r = await session.call_tool(name, args or {})
                return r, time.time() - t0

            # ═══════════════════════════════════════════════════
            # FIX VERIFICATION: Response Format Consistency
            # ═══════════════════════════════════════════════════
            print("=" * 60)
            print("FIX VERIFY: Response format consistency")
            print("=" * 60)

            # type_text should return dict with success
            r, t = await call("type_text", {"text": "test"})
            d = _parse(r)
            ok = isinstance(d, dict) and "success" in d
            log("type_text returns dict", "PASS" if ok else "FAIL", t,
                f"keys={list(d.keys())[:4]}")

            # press_key should return dict with success
            r, t = await call("press_key", {"key": "Escape"})
            d = _parse(r)
            ok = isinstance(d, dict) and "success" in d
            log("press_key returns dict", "PASS" if ok else "FAIL", t,
                f"keys={list(d.keys())[:4]}")

            # hotkey should return dict with success
            r, t = await call("hotkey", {"keys": ["ctrl", "a"]})
            d = _parse(r)
            ok = isinstance(d, dict) and "success" in d
            log("hotkey returns dict", "PASS" if ok else "FAIL", t,
                f"keys={list(d.keys())[:4]}")

            # find_elements should return dict with elements list
            r, t = await call("find_elements", {"role": "push button"})
            d = _parse(r)
            ok = isinstance(d, dict) and "elements" in d and "count" in d
            log("find_elements returns dict", "PASS" if ok else "FAIL", t,
                f"keys={list(d.keys())[:4]} count={d.get('count')}")

            # focus_window (non-existent) should return dict with error
            r, t = await call("focus_window", {"window_title": "NoExiste_12345"})
            d = _parse(r)
            ok = isinstance(d, dict) and ("error" in d or "success" in d)
            log("focus_window(fail) returns dict", "PASS" if ok else "FAIL", t,
                f"keys={list(d.keys())[:4]}")

            # ═══════════════════════════════════════════════════
            # Launch Firefox for remaining tests
            # ═══════════════════════════════════════════════════
            await call("open_application", {"app_name": "firefox"})
            await call("wait_for_window", {"title": "Firefox", "timeout": 15})
            await asyncio.sleep(3)

            # focus_window (existing) should return dict with success=True
            r, t = await call("focus_window", {"window_title": "Firefox"})
            d = _parse(r)
            ok = isinstance(d, dict) and d.get("success") is True
            log("focus_window(ok) returns dict", "PASS" if ok else "FAIL", t,
                f"d={d}")

            # ═══════════════════════════════════════════════════
            # LEVEL 4: Shadow Mode (with rate limit fix)
            # ═══════════════════════════════════════════════════
            print(f"\n{'=' * 60}")
            print("LEVEL 4 — Shadow Mode (rate limit fix: 120/min)")
            print("=" * 60)

            # 4.1 Setup
            t0 = time.time()
            r, _ = await call("setup_background_mode")
            d = _parse(r)
            log("setup_background_mode",
                "PASS" if d.get("success") else "FAIL", time.time() - t0,
                f"agent={d.get('agent_workspace')} user={d.get('user_workspace')}")

            # 4.2 Move Firefox to agent
            t0 = time.time()
            r, _ = await call("move_to_agent_screen", {"window_title": "Firefox"})
            dm = _parse(r)
            await asyncio.sleep(1)
            r, _ = await call("get_agent_screen_state")
            ds = _parse(r)
            agent_wins = ds.get("windows", [])
            ff_in = any("firefox" in str(w).lower() for w in agent_wins)
            log("move Firefox → agent",
                "PASS" if dm.get("success") and ff_in else "FAIL", time.time() - t0,
                f"move={dm.get('success')} windows={len(agent_wins)} firefox={ff_in}")

            # 4.3 Operate in agent workspace
            t0 = time.time()
            await call("focus_window", {"window_title": "Firefox"})
            await asyncio.sleep(0.5)
            await call("hotkey", {"keys": ["ctrl", "l"]})
            await asyncio.sleep(0.5)
            await call("type_text", {"text": "https://example.com"})
            await asyncio.sleep(0.3)
            await call("press_key", {"key": "Return"})
            await asyncio.sleep(3)
            r, _ = await call("take_screenshot")
            img = _image_size(r)
            log("operate in agent ws",
                "PASS" if img > 1000 else "WARN", time.time() - t0,
                f"screenshot={img:,}b")

            # 4.4 Move back
            t0 = time.time()
            r, _ = await call("move_to_user_screen", {"window_title": "Firefox"})
            db = _parse(r)
            await asyncio.sleep(1)
            r, _ = await call("get_agent_screen_state")
            ds2 = _parse(r)
            empty = ds2.get("window_count", -1) == 0
            r, _ = await call("take_screenshot")
            back_img = _has_image(r)
            log("move back → verify",
                "PASS" if db.get("success") and empty else "WARN", time.time() - t0,
                f"back={db.get('success')} empty={empty} screenshot={back_img}")

            # ═══════════════════════════════════════════════════
            # LEVEL 5: Resilience
            # ═══════════════════════════════════════════════════
            print(f"\n{'=' * 60}")
            print("LEVEL 5 — Resilience (error handling)")
            print("=" * 60)

            # 5.1 focus non-existent
            r, t = await call("focus_window", {"window_title": "AppQueNoExiste_XYZ"})
            d = _parse(r)
            has_err = d.get("success") is False or "error" in d
            log("focus(non-existent)",
                "PASS" if has_err else "FAIL", t,
                f"d={d}")

            # 5.2 wait_for_element timeout
            r, t = await call("wait_for_element", {"name": "NoExiste", "timeout": 3})
            d = _parse(r)
            timed = d.get("found") is False or d.get("success") is False
            log("wait_for_element(timeout=3)",
                "PASS" if timed and t < 15 else "WARN", t,
                f"graceful={timed}")

            # 5.3 smart_find impossible
            r, t = await call("smart_find", {"target": "elementoImposible12345"})
            d = _parse(r)
            log("smart_find(impossible)",
                "PASS" if d.get("found") is False or d.get("requires_vision") else "WARN", t,
                f"keys={list(d.keys())[:4]}")

            # 5.4 type_text without focus context
            r, t = await call("type_text", {"text": "orphan"})
            d = _parse(r)
            log("type_text(no focus)",
                "PASS" if "success" in d else "WARN", t,
                f"d={d}")

            # 5.5 ocr_region on desktop
            r, t = await call("ocr_region", {})
            d = _parse(r)
            log("ocr_region(desktop)",
                "PASS" if "text" in d or d.get("success") is not None else "WARN", t,
                f"len={len(d.get('text', ''))}")

            # ═══════════════════════════════════════════════════
            # CLEANUP
            # ═══════════════════════════════════════════════════
            print(f"\n{'=' * 60}")
            print("CLEANUP")
            print("=" * 60)
            await call("run_command", {
                "command": (
                    "SWAYSOCK=$(ls /run/user/1000/sway-ipc.*.sock 2>/dev/null | head -1) "
                    "swaymsg '[app_id=org.mozilla.firefox] kill' 2>/dev/null; "
                    "sleep 1; pkill -f firefox 2>/dev/null; echo done"
                ),
            })
            await asyncio.sleep(2)
            r, _ = await call("list_windows")
            dl = _parse(r)
            remaining = [w.get("app_name") for w in dl.get("windows", [])
                         if "firefox" in str(w).lower()]
            print(f"  Firefox: {remaining or '(cleaned)'}")
            print(f"  Total windows: {dl.get('count', 0)}")

            # Summary
            print(f"\n{'=' * 60}")
            print("SUMMARY")
            print("=" * 60)
            p = sum(1 for v in results.values() if v == "PASS")
            w = sum(1 for v in results.values() if v == "WARN")
            f = sum(1 for v in results.values() if v == "FAIL")
            for name, status in results.items():
                tag = {"PASS": "PASS", "FAIL": "FAIL", "WARN": "WARN"}[status]
                print(f"  [{tag}] {name}")
            print(f"\n  {p} PASS / {w} WARN / {f} FAIL  (out of {len(results)})")
            if f == 0:
                print("  ALL ISSUES FIXED")
            print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
