#!/usr/bin/env python3
"""
Marlow Linux MVP -- Full Interaction Test
Cycle: focus -> input -> capture -> verify via a11y
"""

import os
import subprocess
import sys
import time

import i3ipc
import gi
gi.require_version("Atspi", "2.0")
from gi.repository import Atspi

Atspi.init()

results = {}


def test_focus():
    """1. Focus Firefox via Sway IPC."""
    name = "1. Focus Firefox via Sway IPC"
    print(f"\n=== {name} ===")
    try:
        conn = i3ipc.Connection()
        tree = conn.get_tree()
        ff = None
        for leaf in tree.leaves():
            if leaf.app_id and "firefox" in leaf.app_id.lower():
                ff = leaf
                break

        if ff is None:
            print("  Firefox window not found in Sway tree")
            results[name] = False
            return False

        print(f"  Found: {ff.name}")
        print(f"  app_id={ff.app_id} pid={ff.pid}")

        ff.command("focus")
        time.sleep(0.3)

        tree = conn.get_tree()
        focused = tree.find_focused()
        if focused and focused.app_id and "firefox" in focused.app_id.lower():
            print(f"  Focused: {focused.name}")
            print(f"  PASS")
            results[name] = True
            return True
        else:
            fn = focused.name if focused else "None"
            print(f"  Focus verify failed, got: {fn}")
            results[name] = False
            return False
    except Exception as e:
        print(f"  FAIL: {e}")
        results[name] = False
        return False


def test_input():
    """2. Input via wtype."""
    name = "2. Input via wtype (Ctrl+L, URL, Enter)"
    print(f"\n=== {name} ===")
    try:
        # Ctrl+L to focus URL bar
        r = subprocess.run(["wtype", "-M", "ctrl", "-P", "l", "-m", "ctrl", "-p", "l"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            print(f"  wtype Ctrl+L failed: {r.stderr.strip()}")
            results[name] = False
            return False
        print("  Sent Ctrl+L")
        time.sleep(0.3)

        # Select all + type URL
        subprocess.run(["wtype", "-M", "ctrl", "-P", "a", "-m", "ctrl", "-p", "a"],
                       capture_output=True, text=True, timeout=5)
        time.sleep(0.1)

        r = subprocess.run(["wtype", "https://example.com"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            print(f"  wtype URL failed: {r.stderr.strip()}")
            results[name] = False
            return False
        print("  Typed https://example.com")
        time.sleep(0.2)

        # Enter
        r = subprocess.run(["wtype", "-P", "Return", "-p", "Return"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            print(f"  wtype Enter failed: {r.stderr.strip()}")
            results[name] = False
            return False
        print("  Sent Enter")
        print("  PASS")
        results[name] = True
        return True
    except Exception as e:
        print(f"  FAIL: {e}")
        results[name] = False
        return False


def test_screenshot():
    """3. Screenshot via grim."""
    name = "3. Screenshot via grim"
    print(f"\n=== {name} ===")
    try:
        out = "/tmp/marlow_interaction_test.png"
        if os.path.exists(out):
            os.remove(out)

        print("  Waiting 3s for page load...")
        time.sleep(3)

        r = subprocess.run(["grim", out], capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            print(f"  grim error: {r.stderr.strip()}")
            results[name] = False
            return False

        if os.path.exists(out) and os.path.getsize(out) > 0:
            size = os.path.getsize(out)
            print(f"  Saved: {out} ({size:,} bytes)")
            print("  PASS")
            results[name] = True
            return True

        print("  Screenshot file missing or empty")
        results[name] = False
        return False
    except Exception as e:
        print(f"  FAIL: {e}")
        results[name] = False
        return False


def test_verify():
    """4. Verify via AT-SPI2 + Sway IPC."""
    name = "4. Verify via AT-SPI2 + Sway IPC"
    print(f"\n=== {name} ===")
    try:
        ok_title = False
        ok_content = False

        # Window title via Sway
        conn = i3ipc.Connection()
        tree = conn.get_tree()
        for leaf in tree.leaves():
            if leaf.app_id and "firefox" in leaf.app_id.lower():
                title = leaf.name or ""
                print(f"  Window title: {title}")
                if "example" in title.lower():
                    ok_title = True
                    print("  Title check: PASS")
                else:
                    print("  Title check: FAIL (no example)")
                break

        # Search AT-SPI2 for "Example Domain"
        desktop = Atspi.get_desktop(0)
        found_text = [False]

        def search(node, depth, max_depth):
            if node is None or depth > max_depth or found_text[0]:
                return
            try:
                n = node.get_name() or ""
                if "example domain" in n.lower():
                    role = node.get_role_name()
                    print(f"  AT-SPI2 found: [{role}] {n}")
                    found_text[0] = True
                    return
                try:
                    ti = node.get_text_iface()
                    if ti:
                        txt = Atspi.Text.get_text(ti, 0, 200)
                        if txt and "example domain" in txt.lower():
                            role = node.get_role_name()
                            print(f"  AT-SPI2 text: [{role}] {txt[:80]}")
                            found_text[0] = True
                            return
                except Exception:
                    pass
            except Exception:
                pass
            try:
                for i in range(node.get_child_count()):
                    search(node.get_child_at_index(i), depth + 1, max_depth)
            except Exception:
                pass

        for i in range(desktop.get_child_count()):
            app = desktop.get_child_at_index(i)
            if app and "firefox" in (app.get_name() or "").lower():
                print("  Searching Firefox a11y tree (depth 8)...")
                search(app, 0, 8)
                break

        ok_content = found_text[0]
        if not ok_content:
            print("  Example Domain NOT found in a11y tree")

        ok = ok_title and ok_content
        print(f"  {'PASS' if ok else 'FAIL'}")
        results[name] = ok
        return ok
    except Exception as e:
        print(f"  FAIL: {e}")
        results[name] = False
        return False


def main():
    print("Marlow Linux MVP -- Full Interaction Test")
    print("=" * 55)

    test_focus()
    test_input()
    test_screenshot()
    test_verify()

    print()
    print("=" * 55)
    print("SUMMARY: focus -> input -> capture -> verify")
    print("=" * 55)
    passed = 0
    for sname, ok in results.items():
        status = "PASS" if ok else "FAIL"
        print(f"  {status}: {sname}")
        if ok:
            passed += 1
    total = len(results)
    print(f"\n  {passed}/{total} passed", end="")
    if passed == total:
        print(" -- FULL CYCLE COMPLETE")
    else:
        print()

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
