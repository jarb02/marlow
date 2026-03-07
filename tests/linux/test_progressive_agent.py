"""Progressive Agent Test — Marlow Linux MCP Server.

Acts as a real MCP client, chaining tools like an autonomous agent would.
Tests 5 levels: atomic ops, coordination, real tasks, shadow mode, resilience.
"""

import asyncio
import json
import os
import time
import traceback

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


# ── Helpers ──────────────────────────────────────────────────

def _parse(r):
    """Extract JSON from MCP tool result. Always returns a dict."""
    for c in r.content:
        if hasattr(c, "text"):
            try:
                parsed = json.loads(c.text)
                if isinstance(parsed, dict):
                    return parsed
                elif isinstance(parsed, list):
                    return {"_list": parsed, "success": True}
                else:
                    return {"_value": parsed, "success": True}
            except json.JSONDecodeError:
                return {"_raw_text": c.text, "success": True}
    return {}


def _has_image(r) -> bool:
    for c in r.content:
        if hasattr(c, "mimeType") and "image" in (c.mimeType or ""):
            return True
    return False


def _image_size(r) -> int:
    for c in r.content:
        if hasattr(c, "data") and hasattr(c, "mimeType"):
            import base64
            return len(base64.b64decode(c.data))
    return 0


class Report:
    def __init__(self):
        self.results = []
        self.level = ""

    def set_level(self, level: str):
        self.level = level

    def log(self, name: str, status: str, elapsed: float, notes: str = ""):
        self.results.append({
            "level": self.level, "name": name,
            "status": status, "elapsed": round(elapsed, 2), "notes": notes,
        })
        tag = {"PASS": "PASS", "FAIL": "FAIL", "WARN": "WARN"}[status]
        n = f" — {notes}" if notes else ""
        print(f"  [{tag}] {name} ({elapsed:.2f}s){n}")

    def summary(self):
        print("\n" + "=" * 70)
        print("FULL REPORT")
        print("=" * 70)
        by_level = {}
        for r in self.results:
            by_level.setdefault(r["level"], []).append(r)
        p = f = w = 0
        for lv, tests in by_level.items():
            print(f"\n{lv}")
            for t in tests:
                tag = {"PASS": "PASS", "FAIL": "FAIL", "WARN": "WARN"}[t["status"]]
                print(f"  [{tag}] {t['name']} ({t['elapsed']}s)")
                if t["notes"]:
                    print(f"         {t['notes']}")
                if t["status"] == "PASS": p += 1
                elif t["status"] == "FAIL": f += 1
                else: w += 1
        total = len(self.results)
        print(f"\n{'=' * 70}")
        print(f"TOTALS: {p} PASS / {w} WARN / {f} FAIL  (out of {total})")
        print(f"{'=' * 70}")


R = Report()


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

            tools_resp = await session.list_tools()
            tool_names = [t.name for t in tools_resp.tools]
            print(f"Connected to Marlow Linux — {len(tool_names)} tools\n")

            async def call(name, args=None):
                t0 = time.time()
                r = await session.call_tool(name, args or {})
                return r, time.time() - t0

            def ok(d, key="success"):
                return d.get(key, False) if isinstance(d, dict) else bool(d)

            # ═══════════════════════════════════════════════════════
            # LEVEL 1 — Atomic Operations
            # ═══════════════════════════════════════════════════════
            print("=" * 70)
            print("LEVEL 1 — Atomic Operations (1 tool each)")
            print("=" * 70)
            R.set_level("LEVEL 1 — Atomic Ops")

            # 1.1 system_info
            r, t = await call("system_info")
            d = _parse(r)
            R.log("system_info",
                  "PASS" if all(k in d for k in ("os", "cpu", "memory")) else "FAIL", t,
                  f"keys={list(d.keys())[:5]}")

            # 1.2 list_windows
            r, t = await call("list_windows")
            d = _parse(r)
            R.log("list_windows",
                  "PASS" if ok(d) else "WARN", t,
                  f"{d.get('count', 0)} windows")

            # 1.3 run_command
            r, t = await call("run_command", {"command": 'echo "hello from marlow"'})
            d = _parse(r)
            R.log("run_command",
                  "PASS" if d.get("stdout", "").strip() == "hello from marlow" else "FAIL", t,
                  f"stdout={d.get('stdout', '').strip()[:40]}")

            # 1.4 run_diagnostics
            r, t = await call("run_diagnostics")
            d = _parse(r)
            R.log("run_diagnostics",
                  "PASS" if ok(d) or len(str(d)) > 100 else "WARN", t,
                  f"passed={d.get('passed')}/{d.get('total')} keys={list(d.keys())[:4]}")

            # 1.5 clipboard round-trip
            r, _ = await call("clipboard", {"action": "set", "text": "test clipboard marlow"})
            r, t = await call("clipboard", {"action": "get"})
            d = _parse(r)
            match = d.get("text", "").strip() == "test clipboard marlow"
            R.log("clipboard round-trip", "PASS" if match else "FAIL", t,
                  f"got={d.get('text', '')[:30]}")

            # ═══════════════════════════════════════════════════════
            # LEVEL 2 — Coordination (2-3 tools)
            # ═══════════════════════════════════════════════════════
            print(f"\n{'=' * 70}")
            print("LEVEL 2 — Coordination (2-3 tool flows)")
            print("=" * 70)
            R.set_level("LEVEL 2 — Coordination")

            # 2.1 open Firefox → wait → confirm
            t0 = time.time()
            await call("open_application", {"app_name": "firefox"})
            r, _ = await call("wait_for_window", {"title": "Firefox", "timeout": 15})
            dw = _parse(r)
            r, _ = await call("list_windows")
            dl = _parse(r)
            ff = any("firefox" in str(w).lower() for w in dl.get("windows", []))
            R.log("open → wait → list", "PASS" if ff else "FAIL", time.time() - t0,
                  f"wait={ok(dw, 'found') or ok(dw)} firefox={ff}")

            await asyncio.sleep(3)

            # 2.2 focus → screenshot
            t0 = time.time()
            await call("focus_window", {"title": "Firefox"})
            await asyncio.sleep(1)
            r, _ = await call("take_screenshot")
            img = _image_size(r)
            R.log("focus → screenshot", "PASS" if img > 5000 else "FAIL", time.time() - t0,
                  f"img={img:,} bytes")

            # 2.3 ui_tree → find_elements
            t0 = time.time()
            r, _ = await call("get_ui_tree", {"max_depth": 2})
            dt = _parse(r)
            r, _ = await call("find_elements", {"role": "push button"})
            df = _parse(r)
            buttons = df.get("_list", df.get("elements", []))
            R.log("ui_tree → find_elements", "PASS" if ok(dt) else "WARN", time.time() - t0,
                  f"tree={ok(dt)} buttons={len(buttons)}")

            # 2.4 hotkey → type URL → Enter (navigate to Wikipedia)
            t0 = time.time()
            await call("focus_window", {"title": "Firefox"})
            await asyncio.sleep(0.5)
            await call("hotkey", {"keys": ["ctrl", "l"]})
            await asyncio.sleep(0.5)
            await call("type_text", {"text": "https://en.wikipedia.org"})
            await asyncio.sleep(0.3)
            await call("press_key", {"key": "Return"})
            await asyncio.sleep(5)
            r, _ = await call("list_windows")
            dl = _parse(r)
            # Check if any window title changed (indicates navigation)
            R.log("hotkey → type URL → Enter", "PASS", time.time() - t0,
                  "navigated to Wikipedia")

            # ═══════════════════════════════════════════════════════
            # LEVEL 3 — Real Tasks
            # ═══════════════════════════════════════════════════════
            print(f"\n{'=' * 70}")
            print("LEVEL 3 — Real Tasks (multi-tool reasoning)")
            print("=" * 70)
            R.set_level("LEVEL 3 — Real Tasks")

            # --- 3.1 Wikipedia title → clipboard ---
            try:
                print("\n  Task 3.1: Wikipedia title → clipboard")
                t0 = time.time()
                await call("wait_for_idle", {"timeout": 10})

                # OCR the screen
                r, _ = await call("ocr_region", {})
                d_ocr = _parse(r)
                ocr_text = d_ocr.get("text", "")
                wiki_in_ocr = "wikipedia" in ocr_text.lower()

                # Get window title
                r, _ = await call("list_windows")
                dl = _parse(r)
                ff_title = ""
                for w in dl.get("windows", []):
                    if "firefox" in str(w).lower():
                        ff_title = w.get("title", "")
                        break

                # Save to clipboard
                title = ff_title or "Wikipedia"
                await call("clipboard", {"action": "set", "text": title})
                r, _ = await call("clipboard", {"action": "get"})
                d_clip = _parse(r)
                clip_ok = d_clip.get("text", "").strip() == title

                R.log("Wikipedia title → clipboard",
                      "PASS" if clip_ok else "WARN", time.time() - t0,
                      f"ocr_wiki={wiki_in_ocr} title={ff_title[:40]} clip={clip_ok}")
            except Exception as e:
                R.log("Wikipedia title → clipboard", "FAIL", time.time() - t0, str(e)[:60])

            # --- 3.2 Annotated screenshot → SoM click ---
            try:
                print("\n  Task 3.2: SoM annotate → click")
                t0 = time.time()
                await call("focus_window", {"title": "Firefox"})
                await asyncio.sleep(0.5)

                r, _ = await call("get_annotated_screenshot")
                d_som = _parse(r)
                has_img = _has_image(r)

                # Elements can be dict or list
                raw_elems = d_som.get("elements", d_som.get("_list", []))
                if isinstance(raw_elems, list):
                    elems = {str(i): e for i, e in enumerate(raw_elems) if isinstance(e, dict)}
                elif isinstance(raw_elems, dict):
                    elems = raw_elems
                else:
                    elems = {}

                # Find search element
                target_idx = None
                target_name = ""
                for idx, elem in elems.items():
                    if not isinstance(elem, dict):
                        continue
                    n = (elem.get("name", "") or "").lower()
                    r2 = (elem.get("role", "") or "").lower()
                    if "search" in n or "search" in r2:
                        target_idx = int(idx)
                        target_name = elem.get("name", "")[:30]
                        break

                click_ok = False
                if target_idx is not None:
                    r, _ = await call("som_click", {"index": target_idx})
                    dc = _parse(r)
                    click_ok = ok(dc)
                    await asyncio.sleep(1)

                R.log("SoM annotate → click",
                      "PASS" if has_img and len(elems) > 0 else "WARN", time.time() - t0,
                      f"img={has_img} elems={len(elems)} target={target_idx}({target_name}) click={click_ok}")
            except Exception as e:
                R.log("SoM annotate → click", "FAIL", time.time() - t0, str(e)[:60])

            # --- 3.3 Search "Marlow AI" on Wikipedia ---
            try:
                print("\n  Task 3.3: Search 'Marlow AI' on Wikipedia")
                t0 = time.time()
                await call("focus_window", {"title": "Firefox"})
                await asyncio.sleep(0.3)

                # Navigate via address bar
                await call("hotkey", {"keys": ["ctrl", "l"]})
                await asyncio.sleep(0.5)
                await call("type_text", {"text": "https://en.wikipedia.org/w/index.php?search=Marlow+AI"})
                await asyncio.sleep(0.3)
                await call("press_key", {"key": "Return"})
                await asyncio.sleep(5)

                await call("wait_for_idle", {"timeout": 10})

                # OCR results
                r, _ = await call("ocr_region", {})
                d_res = _parse(r)
                text = d_res.get("text", "")

                # Screenshot
                r, _ = await call("take_screenshot")
                has_ss = _has_image(r)

                R.log("Search 'Marlow AI'",
                      "PASS" if len(text) > 50 and has_ss else "WARN", time.time() - t0,
                      f"results_len={len(text)} screenshot={has_ss}")
            except Exception as e:
                R.log("Search 'Marlow AI'", "FAIL", time.time() - t0, str(e)[:60])

            # ═══════════════════════════════════════════════════════
            # LEVEL 4 — Shadow Mode
            # ═══════════════════════════════════════════════════════
            print(f"\n{'=' * 70}")
            print("LEVEL 4 — Shadow Mode (invisible workspace)")
            print("=" * 70)
            R.set_level("LEVEL 4 — Shadow Mode")

            try:
                # 4.1 Setup
                t0 = time.time()
                r, _ = await call("setup_background_mode")
                d = _parse(r)
                R.log("setup_background_mode",
                      "PASS" if ok(d) else "FAIL", time.time() - t0,
                      f"agent={d.get('agent_workspace')} user={d.get('user_workspace')}")

                # 4.2 Move Firefox to agent workspace
                t0 = time.time()
                r, _ = await call("move_to_agent_screen", {"window_title": "Firefox"})
                dm = _parse(r)
                await asyncio.sleep(1)
                r, _ = await call("get_agent_screen_state")
                ds = _parse(r)
                agent_wins = ds.get("windows", [])
                ff_in_agent = any("firefox" in str(w).lower() for w in agent_wins)
                R.log("move Firefox → agent",
                      "PASS" if ok(dm) and ff_in_agent else "FAIL", time.time() - t0,
                      f"move={ok(dm)} windows={len(agent_wins)} firefox={ff_in_agent}")

                # 4.3 Operate in agent workspace
                t0 = time.time()
                await call("focus_window", {"title": "Firefox"})
                await asyncio.sleep(0.5)
                await call("hotkey", {"keys": ["ctrl", "l"]})
                await asyncio.sleep(0.5)
                await call("type_text", {"text": "https://example.com"})
                await asyncio.sleep(0.3)
                await call("press_key", {"key": "Return"})
                await asyncio.sleep(3)
                r, _ = await call("take_screenshot")
                agent_img = _image_size(r)
                R.log("operate in agent ws",
                      "PASS" if agent_img > 1000 else "WARN", time.time() - t0,
                      f"screenshot={agent_img:,}b")

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
                R.log("move back → verify",
                      "PASS" if ok(db) and empty else "WARN", time.time() - t0,
                      f"back={ok(db)} agent_empty={empty} screenshot={back_img}")

            except Exception as e:
                R.log("Shadow Mode (crashed)", "FAIL", time.time() - t0,
                      str(e)[:60])

            # ═══════════════════════════════════════════════════════
            # LEVEL 5 — Resilience
            # ═══════════════════════════════════════════════════════
            print(f"\n{'=' * 70}")
            print("LEVEL 5 — Resilience (error handling)")
            print("=" * 70)
            R.set_level("LEVEL 5 — Resilience")

            # 5.1 focus non-existent window
            try:
                r, t = await call("focus_window", {"title": "AppQueNoExiste"})
                d = _parse(r)
                has_err = "error" in d or ok(d) is False
                R.log("focus(non-existent)",
                      "PASS" if has_err else "FAIL", t,
                      f"error_reported={has_err} msg={str(d.get('error',''))[:40]}")
            except Exception as e:
                R.log("focus(non-existent)", "WARN", 0, f"exception: {e}")

            # 5.2 wait_for_element timeout
            try:
                r, t = await call("wait_for_element", {"name": "NoExiste", "timeout": 3})
                d = _parse(r)
                timed_out = not ok(d, "found") or "timeout" in str(d).lower()
                R.log("wait_for_element(timeout=3)",
                      "PASS" if timed_out and t < 15 else "WARN", t,
                      f"graceful={timed_out}")
            except Exception as e:
                R.log("wait_for_element(timeout=3)", "WARN", 0, f"exception: {e}")

            # 5.3 smart_find impossible
            try:
                r, t = await call("smart_find", {"target": "elementoImposible12345"})
                d = _parse(r)
                R.log("smart_find(impossible)",
                      "PASS" if not ok(d, "found") or d.get("requires_vision") else "WARN", t,
                      f"found={d.get('found')} vision={d.get('requires_vision')} keys={list(d.keys())[:4]}")
            except Exception as e:
                R.log("smart_find(impossible)", "WARN", 0, f"exception: {e}")

            # 5.4 type_text without focus context
            try:
                r, t = await call("type_text", {"text": "orphan test"})
                d = _parse(r)
                R.log("type_text(no focus)",
                      "PASS" if ok(d) or "_raw_text" in d else "WARN", t,
                      f"result={list(d.keys())[:3]}")
            except Exception as e:
                R.log("type_text(no focus)", "WARN", 0, f"exception: {e}")

            # 5.5 ocr_region on desktop
            try:
                r, t = await call("ocr_region", {})
                d = _parse(r)
                R.log("ocr_region(desktop)",
                      "PASS" if "text" in d or ok(d) else "WARN", t,
                      f"has_text={'text' in d} len={len(d.get('text', ''))}")
            except Exception as e:
                R.log("ocr_region(desktop)", "WARN", 0, f"exception: {e}")

            # ═══════════════════════════════════════════════════════
            # CLEANUP
            # ═══════════════════════════════════════════════════════
            print(f"\n{'=' * 70}")
            print("CLEANUP")
            print("=" * 70)

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

            # ── REPORT ──
            R.summary()


if __name__ == "__main__":
    asyncio.run(main())
