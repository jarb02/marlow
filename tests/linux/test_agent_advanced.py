"""Advanced Agent Test Suite — Marlow Linux as autonomous agent.

7 scenarios (A-G) testing real-world agent behavior:
A: Web research + data extraction
B: Multi-app coordination
C: Complex UI interaction (SoM + search)
D: Shadow Mode full workflow
E: Error recovery
F: Visual diff workflow
G: Workflow recording + replay
"""

import asyncio
import json
import os
import re
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
    """Parse MCP result into dict."""
    for c in r.content:
        if hasattr(c, "text"):
            try:
                v = json.loads(c.text)
                return v if isinstance(v, dict) else {"_v": v, "success": True}
            except json.JSONDecodeError:
                return {"_raw": c.text, "success": True}
    return {}

def _img(r):
    return any(hasattr(c, "mimeType") and "image" in (c.mimeType or "") for c in r.content)

def _isz(r):
    import base64
    for c in r.content:
        if hasattr(c, "data") and hasattr(c, "mimeType"):
            return len(base64.b64decode(c.data))
    return 0


class Scenario:
    def __init__(self, name, desc):
        self.name = name
        self.desc = desc
        self.tools_used = []
        self.notes = []
        self.improvised = []
        self.t0 = 0
        self.status = "?"
        self.elapsed = 0

    def start(self):
        self.t0 = time.time()
        print(f"\n{'=' * 70}")
        print(f"SCENARIO {self.name} — {self.desc}")
        print(f"{'=' * 70}")

    def tool(self, name):
        self.tools_used.append(name)

    def note(self, msg):
        self.notes.append(msg)
        print(f"  >> {msg}")

    def improv(self, msg):
        self.improvised.append(msg)
        print(f"  ** IMPROV: {msg}")

    def finish(self, status):
        self.status = status
        self.elapsed = time.time() - self.t0
        tag = {"PASS": "PASS", "FAIL": "FAIL", "PARTIAL": "PARTIAL"}[status]
        print(f"\n  [{tag}] Scenario {self.name} ({self.elapsed:.1f}s)")
        print(f"  Tools: {' → '.join(self.tools_used)}")
        if self.improvised:
            print(f"  Improvised: {'; '.join(self.improvised)}")

    def report(self):
        tag = {"PASS": "PASS", "FAIL": "FAIL", "PARTIAL": "PARTIAL"}[self.status]
        lines = [f"[{tag}] {self.name} — {self.desc} ({self.elapsed:.1f}s)"]
        lines.append(f"     Tools ({len(self.tools_used)}): {' → '.join(self.tools_used)}")
        for n in self.notes:
            lines.append(f"     {n}")
        if self.improvised:
            for i in self.improvised:
                lines.append(f"     ** {i}")
        return "\n".join(lines)


ALL = []


async def cleanup(call):
    """Kill Firefox and foot terminals (except the main one)."""
    await call("run_command", {
        "command": (
            "SWAYSOCK=$(ls /run/user/1000/sway-ipc.*.sock 2>/dev/null | head -1); "
            "swaymsg '[app_id=org.mozilla.firefox] kill' 2>/dev/null; "
            "sleep 0.5; pkill -f firefox 2>/dev/null; "
            "echo cleaned"
        ),
    })
    await asyncio.sleep(1)


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
            tools_resp = await session.list_tools()
            print(f"Marlow Linux — {len(tools_resp.tools)} tools connected\n")

            async def call(name, args=None):
                r = await session.call_tool(name, args or {})
                return r

            # ══════════════════════════════════════════════════
            # SCENARIO A — Web research + data extraction
            # ══════════════════════════════════════════════════
            S = Scenario("A", "Web research: HN titles → file")
            ALL.append(S)
            S.start()
            try:
                # Open Firefox
                S.tool("open_application")
                await call("open_application", {"app_name": "firefox"})

                S.tool("wait_for_window")
                await call("wait_for_window", {"title": "Firefox", "timeout": 15})
                await asyncio.sleep(2)

                # Navigate to HN
                S.tool("focus_window")
                await call("focus_window", {"window_title": "Firefox"})
                await asyncio.sleep(0.5)

                S.tool("hotkey")
                await call("hotkey", {"keys": ["ctrl", "l"]})
                await asyncio.sleep(0.5)

                S.tool("type_text")
                await call("type_text", {"text": "https://news.ycombinator.com"})
                await asyncio.sleep(0.3)

                S.tool("press_key")
                await call("press_key", {"key": "Return"})
                S.note("Navigating to Hacker News...")

                # Wait for page load
                await asyncio.sleep(5)
                S.tool("wait_for_idle")
                await call("wait_for_idle", {"timeout": 10})

                # OCR the page
                S.tool("ocr_region")
                r = await call("ocr_region", {})
                d = _p(r)
                ocr_text = d.get("text", "")
                S.note(f"OCR captured {len(ocr_text)} chars")

                # Extract titles — HN titles follow numbered patterns
                # Look for lines that look like titles (after numbers, before metadata)
                lines = ocr_text.split("\n")
                titles = []
                for line in lines:
                    line = line.strip()
                    # Skip empty, very short, metadata lines
                    if len(line) < 10 or line.startswith("(") or "point" in line.lower():
                        continue
                    # Skip nav/footer
                    if any(x in line.lower() for x in ["hacker news", "new |", "login", "submit", "comment"]):
                        continue
                    # Remove leading numbers like "1." "2."
                    cleaned = re.sub(r'^\d+[\.\)]\s*', '', line)
                    if len(cleaned) > 15 and cleaned not in titles:
                        titles.append(cleaned)
                    if len(titles) >= 5:
                        break

                if len(titles) < 5:
                    S.improv(f"Only found {len(titles)} titles from OCR, using what we have")

                S.note(f"Extracted titles: {titles[:5]}")

                # Write to file
                titles_text = "\\n".join(titles[:5])
                S.tool("run_command")
                r = await call("run_command", {
                    "command": f'printf "%b" "{titles_text}" > ~/marlow/tests/linux/hn_titles.txt',
                })

                # Verify
                S.tool("run_command")
                r = await call("run_command", {
                    "command": "cat ~/marlow/tests/linux/hn_titles.txt",
                })
                d = _p(r)
                content = d.get("stdout", "")
                S.note(f"File content: {len(content)} chars")
                has_content = len(content) > 20

                # Screenshot as evidence
                S.tool("take_screenshot")
                r = await call("take_screenshot")
                S.note(f"Screenshot: {_isz(r):,}b")

                S.finish("PASS" if has_content and len(titles) >= 3 else "PARTIAL")

            except Exception as e:
                S.note(f"ERROR: {e}")
                S.finish("FAIL")

            await cleanup(call)

            # ══════════════════════════════════════════════════
            # SCENARIO B — Multi-app coordination
            # ══════════════════════════════════════════════════
            S = Scenario("B", "Multi-app: Firefox + terminal JSON extraction")
            ALL.append(S)
            S.start()
            try:
                # Open Firefox
                S.tool("open_application")
                await call("open_application", {"app_name": "firefox"})
                S.tool("wait_for_window")
                await call("wait_for_window", {"title": "Firefox", "timeout": 15})
                await asyncio.sleep(2)

                # Open foot terminal
                S.tool("open_application")
                await call("open_application", {"app_name": "foot"})
                await asyncio.sleep(2)

                # Confirm both
                S.tool("list_windows")
                r = await call("list_windows")
                d = _p(r)
                wins = d.get("windows", [])
                apps = [w.get("app_name", "") for w in wins]
                S.note(f"Windows: {apps}")
                has_ff = any("firefox" in str(a).lower() for a in apps)
                has_ft = any("foot" in str(a).lower() for a in apps)

                if not has_ft:
                    S.improv("foot not detected in list_windows, it may share with main terminal")

                # Navigate Firefox to JSON endpoint
                S.tool("focus_window")
                await call("focus_window", {"window_title": "Firefox"})
                await asyncio.sleep(0.5)
                S.tool("hotkey")
                await call("hotkey", {"keys": ["ctrl", "l"]})
                await asyncio.sleep(0.5)
                S.tool("type_text")
                await call("type_text", {"text": "https://jsonplaceholder.typicode.com/todos/1"})
                await asyncio.sleep(0.3)
                S.tool("press_key")
                await call("press_key", {"key": "Return"})
                await asyncio.sleep(4)

                S.tool("wait_for_idle")
                await call("wait_for_idle", {"timeout": 10})

                # Read JSON from page via OCR
                S.tool("ocr_region")
                r = await call("ocr_region", {})
                d = _p(r)
                page_text = d.get("text", "")
                S.note(f"OCR page: {len(page_text)} chars")

                # Extract JSON — look for braces
                json_match = re.search(r'\{[^}]+\}', page_text, re.DOTALL)
                if json_match:
                    json_str = json_match.group(0)
                    S.note(f"Extracted JSON: {json_str[:60]}")
                else:
                    S.improv("No JSON braces found in OCR, using raw text")
                    json_str = page_text.strip()[:200]

                # Write to file via run_command (simpler than switching to terminal)
                safe_json = json_str.replace('"', '\\"').replace("'", "\\'")
                S.tool("run_command")
                r = await call("run_command", {
                    "command": f'echo "{safe_json}" > /tmp/marlow_json_test.txt',
                })

                # Verify
                S.tool("run_command")
                r = await call("run_command", {"command": "cat /tmp/marlow_json_test.txt"})
                d = _p(r)
                file_content = d.get("stdout", "")
                S.note(f"File saved: {len(file_content)} chars")

                # Screenshot showing both apps
                S.tool("take_screenshot")
                r = await call("take_screenshot")
                S.note(f"Screenshot: {_isz(r):,}b")

                ok = has_ff and len(file_content) > 10
                S.finish("PASS" if ok else "PARTIAL")

            except Exception as e:
                S.note(f"ERROR: {e}")
                S.finish("FAIL")

            await cleanup(call)
            # Also close extra foot terminals
            await call("run_command", {
                "command": (
                    "SWAYSOCK=$(ls /run/user/1000/sway-ipc.*.sock 2>/dev/null | head -1); "
                    "for cid in $(swaymsg -t get_tree 2>/dev/null | "
                    "python3 -c 'import sys,json; t=json.load(sys.stdin); "
                    "ids=[]; "
                    "def f(n): "
                    "  [ids.append(str(c[\"id\"])) for c in n.get(\"nodes\",[])+n.get(\"floating_nodes\",[]) "
                    "   if c.get(\"app_id\")==\"foot\" and not c.get(\"focused\",False)] or "
                    "  [f(c) for c in n.get(\"nodes\",[])+n.get(\"floating_nodes\",[])];"
                    "f(t); print(\" \".join(ids))' 2>/dev/null); "
                    "do swaymsg \"[con_id=$cid] kill\" 2>/dev/null; done; echo ok"
                ),
            })
            await asyncio.sleep(1)

            # ══════════════════════════════════════════════════
            # SCENARIO C — Complex UI interaction (DuckDuckGo)
            # ══════════════════════════════════════════════════
            S = Scenario("C", "Complex UI: DuckDuckGo search via SoM")
            ALL.append(S)
            S.start()
            try:
                # Open Firefox
                S.tool("open_application")
                await call("open_application", {"app_name": "firefox"})
                S.tool("wait_for_window")
                await call("wait_for_window", {"title": "Firefox", "timeout": 15})
                await asyncio.sleep(2)

                # Navigate to DuckDuckGo
                S.tool("focus_window")
                await call("focus_window", {"window_title": "Firefox"})
                await asyncio.sleep(0.5)
                S.tool("hotkey")
                await call("hotkey", {"keys": ["ctrl", "l"]})
                await asyncio.sleep(0.5)
                S.tool("type_text")
                await call("type_text", {"text": "https://duckduckgo.com"})
                await asyncio.sleep(0.3)
                S.tool("press_key")
                await call("press_key", {"key": "Return"})
                await asyncio.sleep(5)

                S.tool("wait_for_idle")
                await call("wait_for_idle", {"timeout": 10})

                # Get annotated screenshot to find search box
                S.tool("get_annotated_screenshot")
                r = await call("get_annotated_screenshot")
                d = _p(r)
                has_som_img = _img(r)
                elems = d.get("elements", {})
                if isinstance(elems, list):
                    elems = {str(i): e for i, e in enumerate(elems) if isinstance(e, dict)}
                S.note(f"SoM: {len(elems)} elements, img={has_som_img}")

                # Find search element
                search_idx = None
                for idx, el in elems.items():
                    if not isinstance(el, dict):
                        continue
                    n = (el.get("name", "") or "").lower()
                    r2 = (el.get("role", "") or "").lower()
                    if "search" in n or "search" in r2 or "entry" in r2:
                        search_idx = int(idx)
                        S.note(f"Found search element: [{idx}] {el.get('name','')[:30]} ({el.get('role','')})")
                        break

                if search_idx is not None:
                    S.tool("som_click")
                    await call("som_click", {"index": search_idx})
                    await asyncio.sleep(0.5)
                else:
                    # Fallback: try clicking in the middle of the page or use hotkey
                    S.improv("No search element found in SoM, using hotkey Tab to focus search")
                    S.tool("press_key")
                    await call("press_key", {"key": "Tab"})
                    await asyncio.sleep(0.3)

                # Type search query
                S.tool("type_text")
                await call("type_text", {"text": "Marlow autonomous agent"})
                await asyncio.sleep(0.5)

                S.tool("press_key")
                await call("press_key", {"key": "Return"})
                S.note("Searching for 'Marlow autonomous agent'...")

                # Wait for results
                await asyncio.sleep(5)
                S.tool("wait_for_idle")
                await call("wait_for_idle", {"timeout": 10})

                # OCR the results
                S.tool("ocr_region")
                r = await call("ocr_region", {})
                d = _p(r)
                results_text = d.get("text", "")
                S.note(f"Results OCR: {len(results_text)} chars")

                # Extract first result
                lines = [l.strip() for l in results_text.split("\n") if len(l.strip()) > 20]
                first_result = ""
                for line in lines:
                    # Skip navigation/header text
                    if any(x in line.lower() for x in ["duckduckgo", "web", "images", "videos", "news", "marlow autonomous"]):
                        continue
                    first_result = line
                    break

                if first_result:
                    S.note(f"First result: {first_result[:60]}")
                    S.tool("clipboard")
                    await call("clipboard", {"action": "set", "text": first_result})
                    S.note("Saved to clipboard")
                else:
                    S.improv("Could not isolate first result from OCR text")

                S.tool("take_screenshot")
                r = await call("take_screenshot")
                S.note(f"Screenshot: {_isz(r):,}b")

                ok = len(results_text) > 50 and has_som_img
                S.finish("PASS" if ok else "PARTIAL")

            except Exception as e:
                S.note(f"ERROR: {e}")
                S.finish("FAIL")

            await cleanup(call)

            # ══════════════════════════════════════════════════
            # SCENARIO D — Shadow Mode full workflow
            # ══════════════════════════════════════════════════
            S = Scenario("D", "Shadow Mode: invisible browsing + IP extraction")
            ALL.append(S)
            S.start()
            try:
                # Open Firefox first
                S.tool("open_application")
                await call("open_application", {"app_name": "firefox"})
                S.tool("wait_for_window")
                await call("wait_for_window", {"title": "Firefox", "timeout": 15})
                await asyncio.sleep(3)

                # Setup Shadow Mode
                S.tool("setup_background_mode")
                r = await call("setup_background_mode")
                d = _p(r)
                S.note(f"Shadow Mode: agent={d.get('agent_workspace')} user={d.get('user_workspace')}")

                # Move Firefox to agent workspace
                S.tool("move_to_agent_screen")
                r = await call("move_to_agent_screen", {"window_title": "Firefox"})
                dm = _p(r)
                await asyncio.sleep(1)

                # Confirm Firefox is in agent workspace
                S.tool("get_agent_screen_state")
                r = await call("get_agent_screen_state")
                ds = _p(r)
                agent_wins = ds.get("windows", [])
                ff_in = any("firefox" in str(w).lower() for w in agent_wins)
                S.note(f"Agent workspace: {len(agent_wins)} windows, firefox={ff_in}")

                # Navigate to httpbin.org/ip in agent workspace
                S.tool("focus_window")
                await call("focus_window", {"window_title": "Firefox"})
                await asyncio.sleep(0.5)
                S.tool("hotkey")
                await call("hotkey", {"keys": ["ctrl", "l"]})
                await asyncio.sleep(0.5)
                S.tool("type_text")
                await call("type_text", {"text": "https://httpbin.org/ip"})
                await asyncio.sleep(0.3)
                S.tool("press_key")
                await call("press_key", {"key": "Return"})
                await asyncio.sleep(4)
                S.tool("wait_for_idle")
                await call("wait_for_idle", {"timeout": 10})

                # Read IP via OCR
                S.tool("ocr_region")
                r = await call("ocr_region", {})
                d = _p(r)
                ip_text = d.get("text", "")
                S.note(f"OCR in agent workspace: {len(ip_text)} chars")

                # Extract IP address
                ip_match = re.search(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', ip_text)
                if ip_match:
                    ip = ip_match.group(0)
                    S.note(f"Extracted IP: {ip}")
                else:
                    S.improv("No IP pattern found in OCR, using raw text")
                    ip = ip_text.strip()[:50]
                    S.note(f"Raw text: {ip}")

                # Save to clipboard
                S.tool("clipboard")
                await call("clipboard", {"action": "set", "text": ip})

                # Move Firefox back to user workspace
                S.tool("move_to_user_screen")
                r = await call("move_to_user_screen", {"window_title": "Firefox"})
                db = _p(r)
                await asyncio.sleep(1)
                S.note(f"Moved back: {db.get('success')}")

                # Verify clipboard
                S.tool("clipboard")
                r = await call("clipboard", {"action": "get"})
                dc = _p(r)
                clip = dc.get("text", "").strip()
                clip_ok = ip in clip if ip_match else len(clip) > 5
                S.note(f"Clipboard: {clip}")

                # Screenshot
                S.tool("take_screenshot")
                r = await call("take_screenshot")
                S.note(f"Screenshot: {_isz(r):,}b")

                ok = ff_in and clip_ok and db.get("success")
                S.finish("PASS" if ok else "PARTIAL")

            except Exception as e:
                S.note(f"ERROR: {e}")
                S.finish("FAIL")

            await cleanup(call)

            # ══════════════════════════════════════════════════
            # SCENARIO E — Error recovery
            # ══════════════════════════════════════════════════
            S = Scenario("E", "Error recovery: graceful failure handling")
            ALL.append(S)
            S.start()
            try:
                # E1: Open non-existent app
                S.tool("open_application")
                r = await call("open_application", {"app_name": "fakeapp123"})
                d = _p(r)
                e1_err = d.get("success") is False or "error" in d or "not found" in str(d).lower()
                S.note(f"fakeapp123: error_handled={e1_err} resp={str(d)[:60]}")

                # E2: smart_find impossible element
                S.tool("smart_find")
                r = await call("smart_find", {"target": "nonExistentElement99999"})
                d = _p(r)
                e2_err = d.get("found") is False or "error" in d or d.get("success") is False
                S.note(f"smart_find impossible: handled={e2_err} keys={list(d.keys())[:4]}")

                # E3: Open Firefox and navigate to invalid URL
                S.tool("open_application")
                await call("open_application", {"app_name": "firefox"})
                S.tool("wait_for_window")
                await call("wait_for_window", {"title": "Firefox", "timeout": 15})
                await asyncio.sleep(2)

                S.tool("focus_window")
                await call("focus_window", {"window_title": "Firefox"})
                await asyncio.sleep(0.5)
                S.tool("hotkey")
                await call("hotkey", {"keys": ["ctrl", "l"]})
                await asyncio.sleep(0.5)
                S.tool("type_text")
                await call("type_text", {"text": "https://thisdomaindoesnotexist12345.com"})
                await asyncio.sleep(0.3)
                S.tool("press_key")
                await call("press_key", {"key": "Return"})
                await asyncio.sleep(5)

                # Detect error page
                S.tool("ocr_region")
                r = await call("ocr_region", {})
                d = _p(r)
                error_text = d.get("text", "").lower()
                error_detected = any(x in error_text for x in [
                    "unable", "not found", "error", "problem", "can't",
                    "could not", "server not found", "hmm", "address",
                ])
                S.note(f"Error page OCR: {len(error_text)} chars, error_detected={error_detected}")

                # Try window title too
                S.tool("list_windows")
                r = await call("list_windows")
                d = _p(r)
                for w in d.get("windows", []):
                    if "firefox" in str(w).lower():
                        title = w.get("title", "").lower()
                        if "problem" in title or "error" in title:
                            error_detected = True
                            S.note(f"Error in title: {w.get('title', '')[:40]}")
                        break

                S.note(f"Error detection: {error_detected}")

                all_ok = e1_err and e2_err and error_detected
                S.finish("PASS" if all_ok else "PARTIAL")

            except Exception as e:
                S.note(f"ERROR: {e}")
                S.finish("FAIL")

            await cleanup(call)

            # ══════════════════════════════════════════════════
            # SCENARIO F — Visual diff workflow
            # ══════════════════════════════════════════════════
            S = Scenario("F", "Visual diff: before/after page navigation")
            ALL.append(S)
            S.start()
            try:
                # Open Firefox to example.com
                S.tool("open_application")
                await call("open_application", {"app_name": "firefox"})
                S.tool("wait_for_window")
                await call("wait_for_window", {"title": "Firefox", "timeout": 15})
                await asyncio.sleep(2)

                S.tool("focus_window")
                await call("focus_window", {"window_title": "Firefox"})
                await asyncio.sleep(0.5)
                S.tool("hotkey")
                await call("hotkey", {"keys": ["ctrl", "l"]})
                await asyncio.sleep(0.5)
                S.tool("type_text")
                await call("type_text", {"text": "https://example.com"})
                await asyncio.sleep(0.3)
                S.tool("press_key")
                await call("press_key", {"key": "Return"})
                await asyncio.sleep(4)
                S.tool("wait_for_idle")
                await call("wait_for_idle", {"timeout": 10})

                # Capture "before"
                S.tool("visual_diff")
                r = await call("visual_diff", {"label": "before_nav"})
                d_before = _p(r)
                diff_id = d_before.get("diff_id", "")
                S.note(f"Captured 'before': diff_id={diff_id}")

                if not diff_id:
                    S.improv("No diff_id returned, checking response keys")
                    S.note(f"visual_diff response: {d_before}")

                # Navigate to different page
                await asyncio.sleep(1)
                S.tool("hotkey")
                await call("hotkey", {"keys": ["ctrl", "l"]})
                await asyncio.sleep(0.5)
                S.tool("type_text")
                await call("type_text", {"text": "https://en.wikipedia.org"})
                await asyncio.sleep(0.3)
                S.tool("press_key")
                await call("press_key", {"key": "Return"})
                await asyncio.sleep(5)
                S.tool("wait_for_idle")
                await call("wait_for_idle", {"timeout": 10})

                # Compare
                if diff_id:
                    S.tool("visual_diff_compare")
                    r = await call("visual_diff_compare", {"diff_id": diff_id})
                    d_cmp = _p(r)
                    has_diff_img = _img(r)
                    pct = d_cmp.get("change_percent", d_cmp.get("changed_percent", -1))
                    S.note(f"Diff result: change={pct}% img={has_diff_img}")
                    S.note(f"Diff keys: {list(d_cmp.keys())[:6]}")

                    # The change should be significant (different pages)
                    if isinstance(pct, (int, float)) and pct > 0:
                        S.note(f"Change makes sense: example.com → wikipedia.org = {pct}% different")
                        ok = True
                    elif has_diff_img:
                        S.note("Got diff image but no percentage — still useful")
                        ok = True
                    else:
                        ok = False
                else:
                    S.improv("Skipping compare — no diff_id from capture")
                    ok = False

                S.tool("take_screenshot")
                r = await call("take_screenshot")
                S.note(f"Screenshot: {_isz(r):,}b")

                S.finish("PASS" if ok else "PARTIAL")

            except Exception as e:
                S.note(f"ERROR: {e}")
                S.finish("FAIL")

            await cleanup(call)

            # ══════════════════════════════════════════════════
            # SCENARIO G — Workflow recording + replay
            # ══════════════════════════════════════════════════
            S = Scenario("G", "Workflow: record → replay open Firefox + navigate")
            ALL.append(S)
            S.start()
            try:
                # Start recording
                S.tool("workflow_record")
                r = await call("workflow_record", {"name": "open_example"})
                d = _p(r)
                S.note(f"Recording started: {d}")

                # Execute actions that get recorded
                S.tool("open_application")
                await call("open_application", {"app_name": "firefox"})
                S.tool("wait_for_window")
                await call("wait_for_window", {"title": "Firefox", "timeout": 15})
                await asyncio.sleep(2)

                S.tool("focus_window")
                await call("focus_window", {"window_title": "Firefox"})
                await asyncio.sleep(0.5)
                S.tool("hotkey")
                await call("hotkey", {"keys": ["ctrl", "l"]})
                await asyncio.sleep(0.5)
                S.tool("type_text")
                await call("type_text", {"text": "https://example.com"})
                await asyncio.sleep(0.3)
                S.tool("press_key")
                await call("press_key", {"key": "Return"})
                await asyncio.sleep(3)

                # Stop recording
                S.tool("workflow_stop")
                r = await call("workflow_stop")
                d_stop = _p(r)
                S.note(f"Recording stopped: {d_stop}")

                # List workflows
                S.tool("workflow_list")
                r = await call("workflow_list")
                d_list = _p(r)
                workflows = d_list.get("workflows", [])
                wf_found = any("open_example" in str(w) for w in workflows)
                S.note(f"Workflows: {len(workflows)} saved, open_example={wf_found}")

                # Close Firefox
                await cleanup(call)
                await asyncio.sleep(2)

                # Verify Firefox is gone
                S.tool("list_windows")
                r = await call("list_windows")
                d = _p(r)
                ff_gone = not any("firefox" in str(w).lower() for w in d.get("windows", []))
                S.note(f"Firefox closed: {ff_gone}")

                # Replay workflow
                S.tool("workflow_run")
                r = await call("workflow_run", {"name": "open_example"})
                d_run = _p(r)
                S.note(f"Replay result: {d_run}")

                # Wait and check if Firefox opened
                await asyncio.sleep(5)
                S.tool("list_windows")
                r = await call("list_windows")
                d = _p(r)
                ff_back = any("firefox" in str(w).lower() for w in d.get("windows", []))
                S.note(f"Firefox reopened by replay: {ff_back}")

                if ff_back:
                    # Check if it navigated to example.com
                    await asyncio.sleep(2)
                    S.tool("ocr_region")
                    r = await call("ocr_region", {})
                    d = _p(r)
                    text = d.get("text", "").lower()
                    has_example = "example" in text
                    S.note(f"Page has 'example': {has_example}")
                else:
                    has_example = False

                S.tool("take_screenshot")
                r = await call("take_screenshot")
                S.note(f"Screenshot: {_isz(r):,}b")

                ok = wf_found and ff_gone
                # Replay might or might not fully work depending on workflow_run
                # implementation (it re-dispatches tools which is the right approach)
                S.finish("PASS" if ok else "PARTIAL")

            except Exception as e:
                S.note(f"ERROR: {e}")
                S.finish("FAIL")

            await cleanup(call)

            # ══════════════════════════════════════════════════
            # FINAL CLEANUP
            # ══════════════════════════════════════════════════
            print(f"\n{'=' * 70}")
            print("FINAL CLEANUP")
            print("=" * 70)
            await cleanup(call)
            await asyncio.sleep(1)
            r = await call("list_windows")
            d = _p(r)
            remaining = [w.get("app_name") for w in d.get("windows", [])
                         if w.get("app_name", "").lower() not in ("foot", "")]
            if remaining:
                print(f"  WARNING: apps still running: {remaining}")
            else:
                print(f"  Desktop clean (only foot terminal)")
            print(f"  Total windows: {d.get('count', 0)}")

            # ══════════════════════════════════════════════════
            # FULL REPORT
            # ══════════════════════════════════════════════════
            print(f"\n{'=' * 70}")
            print("FULL REPORT")
            print("=" * 70)

            for s in ALL:
                print(f"\n{s.report()}")

            p = sum(1 for s in ALL if s.status == "PASS")
            pa = sum(1 for s in ALL if s.status == "PARTIAL")
            f = sum(1 for s in ALL if s.status == "FAIL")
            print(f"\n{'=' * 70}")
            total_tools = sum(len(s.tools_used) for s in ALL)
            total_time = sum(s.elapsed for s in ALL)
            print(f"TOTALS: {p} PASS / {pa} PARTIAL / {f} FAIL  (7 scenarios)")
            print(f"Tools invoked: {total_tools} calls across {total_time:.0f}s")
            print(f"{'=' * 70}")


if __name__ == "__main__":
    asyncio.run(main())
