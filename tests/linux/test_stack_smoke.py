#!/usr/bin/env python3
"""
Marlow Linux MVP -- Stack Smoke Tests
Verifies each layer of the Linux desktop automation stack.
Run with Wayland env vars exported.
"""

import os
import subprocess
import sys


def env_check():
    """Verify required environment variables are set."""
    print("=== Environment Check ===")
    required = ["XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"]
    ok = True
    for var in required:
        val = os.environ.get(var, "")
        if val:
            print(f"  {var}={val}")
        else:
            print(f"  {var} NOT SET")
            ok = False
    wl = os.environ.get("WAYLAND_DISPLAY", "")
    print(f"  WAYLAND_DISPLAY={wl or '(not set, will try wayland-0)'}")
    print()
    return ok


def test_atspi():
    """Test 1: AT-SPI2 accessibility -- enumerate desktop applications."""
    print("=== Test 1: AT-SPI2 Accessibility ===")
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi

        Atspi.init()
        desktop = Atspi.get_desktop(0)
        child_count = desktop.get_child_count()

        if child_count == 0:
            print("  WARNING: Desktop has 0 children")
            print("  Hint: ensure at-spi2-registryd is running")

        apps = []
        for i in range(child_count):
            child = desktop.get_child_at_index(i)
            if child:
                name = child.get_name() or "(unnamed)"
                role = child.get_role_name() or "(unknown)"
                apps.append(f"{name} [{role}]")

        print(f"  Desktop children: {child_count}")
        for app in apps[:15]:
            print(f"    - {app}")
        if len(apps) > 15:
            print(f"    ... and {len(apps) - 15} more")

        print("  PASS: AT-SPI2 working")
        return True

    except Exception as e:
        print(f"  FAIL: {e}")
        return False


def test_windows():
    """Test 2: Window management -- Sway IPC or AT-SPI2 fallback."""
    print("\n=== Test 2: Window Management ===")

    # Check if Sway is running
    sway_sock = os.environ.get("SWAYSOCK", "")
    if not sway_sock:
        runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        try:
            for name in os.listdir(runtime):
                if name.startswith("sway"):
                    sway_sock = os.path.join(runtime, name)
                    break
        except OSError:
            pass

    if sway_sock and os.path.exists(sway_sock):
        return _test_sway_ipc()
    else:
        print("  Sway not running, using AT-SPI2 for window enumeration")
        return _test_gnome_windows()


def _test_sway_ipc():
    """Enumerate windows via Sway IPC."""
    try:
        import i3ipc
        conn = i3ipc.Connection()
        tree = conn.get_tree()

        windows = []
        for leaf in tree.leaves():
            name = leaf.name or "(unnamed)"
            rect = leaf.rect
            windows.append(f"{name} @ ({rect.x},{rect.y}) {rect.width}x{rect.height}")

        print(f"  Windows found: {len(windows)}")
        for w in windows[:10]:
            print(f"    - {w}")

        print("  PASS: Sway IPC working")
        return True

    except Exception as e:
        print(f"  FAIL: {e}")
        return False


def _test_gnome_windows():
    """Enumerate windows via AT-SPI2 (GNOME fallback)."""
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi

        desktop = Atspi.get_desktop(0)
        windows = []
        for i in range(desktop.get_child_count()):
            app = desktop.get_child_at_index(i)
            if not app:
                continue
            for j in range(app.get_child_count()):
                win = app.get_child_at_index(j)
                if win:
                    role = win.get_role_name()
                    if role in ("frame", "window", "dialog"):
                        name = win.get_name() or "(unnamed)"
                        windows.append(f"{name} [{role}] ({app.get_name()})")

        print(f"  Windows via AT-SPI2: {len(windows)}")
        for w in windows[:10]:
            print(f"    - {w}")
        if len(windows) > 10:
            print(f"    ... and {len(windows) - 10} more")

        print("  PASS: GNOME window enumeration via AT-SPI2")
        return True
    except Exception as e:
        print(f"  FAIL (GNOME fallback): {e}")
        return False


def test_input_wtype():
    """Test 3: Keyboard input via wtype."""
    print("\n=== Test 3: Input (wtype) ===")
    try:
        result = subprocess.run(
            ["wtype", "--help"],
            capture_output=True, text=True, timeout=5,
        )
        print(f"  wtype found (returncode={result.returncode})")

        # Try to type -- may fail if no focused window accepts input
        result = subprocess.run(
            ["wtype", "Marlow was here"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            print("  Text sent to focused window")
            print("  PASS: wtype working")
            return True
        else:
            stderr = result.stderr.strip()
            if "compositor" in stderr.lower() or "wayland" in stderr.lower():
                print(f"  Error: {stderr}")
                print("  FAIL: wtype cannot connect to compositor")
                return False
            else:
                print(f"  stderr: {stderr}")
                print("  PASS: wtype binary works (no focused input target)")
                return True

    except FileNotFoundError:
        print("  FAIL: wtype not installed")
        return False
    except Exception as e:
        print(f"  FAIL: {e}")
        return False


def test_screenshot_grim():
    """Test 4: Screenshot capture via grim."""
    print("\n=== Test 4: Screenshot (grim) ===")
    out_path = "/tmp/marlow_test.png"
    try:
        if os.path.exists(out_path):
            os.remove(out_path)

        result = subprocess.run(
            ["grim", out_path],
            capture_output=True, text=True, timeout=10,
        )

        if result.returncode != 0:
            print(f"  grim error: {result.stderr.strip()}")
            print("  FAIL: grim capture failed")
            return False

        if os.path.exists(out_path):
            size = os.path.getsize(out_path)
            print(f"  Screenshot saved: {out_path} ({size:,} bytes)")
            if size > 0:
                print("  PASS: grim working")
                return True
            else:
                print("  FAIL: screenshot file is empty")
                return False
        else:
            print("  FAIL: screenshot file not created")
            return False

    except FileNotFoundError:
        print("  FAIL: grim not installed")
        return False
    except Exception as e:
        print(f"  FAIL: {e}")
        return False


def test_pipewire():
    """Test 5: PipeWire audio status."""
    print("\n=== Test 5: Audio (PipeWire) ===")
    try:
        result = subprocess.run(
            ["pw-cli", "info", "0"],
            capture_output=True, text=True, timeout=5,
        )

        if result.returncode != 0:
            print(f"  pw-cli error: {result.stderr.strip()}")
            print("  FAIL: PipeWire not responding")
            return False

        output = result.stdout.strip()
        for line in output.split("\n"):
            line = line.strip()
            if any(k in line.lower() for k in ["version", "name", "cookie"]):
                print(f"    {line}")

        print("  PASS: PipeWire responding")
        return True

    except FileNotFoundError:
        print("  FAIL: pw-cli not installed")
        return False
    except Exception as e:
        print(f"  FAIL: {e}")
        return False


def main():
    print("Marlow Linux MVP -- Stack Smoke Tests")
    print("=" * 50)
    print()

    env_check()

    results = {}
    results["AT-SPI2"] = test_atspi()
    results["Windows"] = test_windows()
    results["Input"] = test_input_wtype()
    results["Screenshot"] = test_screenshot_grim()
    results["Audio"] = test_pipewire()

    print()
    print("=" * 50)
    print("SUMMARY")
    print("=" * 50)
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    for name, ok in results.items():
        status = "PASS" if ok else "FAIL"
        print(f"  {name:15s} {status}")
    print(f"\n  {passed}/{total} passed")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
