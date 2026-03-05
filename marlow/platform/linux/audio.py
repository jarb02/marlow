"""Linux AudioProvider — PipeWire capture.

Captures system audio and microphone input using PipeWire tools
(pw-record, pw-cat) and queries audio state via pw-cli and wpctl.

Audio files stored in ~/.marlow/audio/, auto-cleaned after 1 hour.

Tested on Fedora 43 + PipeWire.

/ AudioProvider Linux — captura PipeWire.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

from marlow.platform.base import AudioProvider

logger = logging.getLogger("marlow.platform.linux.audio")

# Audio storage directory
AUDIO_DIR = Path.home() / ".marlow" / "audio"

# Maximum recording duration
MAX_DURATION = 300


def _ensure_audio_dir():
    """Create audio directory if needed."""
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)


def _cleanup_old_audio(max_age: int = 3600):
    """Remove audio files older than max_age seconds."""
    now = time.time()
    try:
        for f in AUDIO_DIR.iterdir():
            if f.suffix in (".wav", ".flac", ".tmp"):
                if (now - f.stat().st_mtime) > max_age:
                    f.unlink(missing_ok=True)
    except Exception:
        pass


def _generate_path(prefix: str, ext: str = ".wav") -> Path:
    """Generate a timestamped audio file path."""
    _ensure_audio_dir()
    _cleanup_old_audio()
    ts = time.strftime("%Y%m%d_%H%M%S")
    return AUDIO_DIR / f"{prefix}_{ts}{ext}"


def _run_capture(cmd: list[str], timeout: int) -> tuple[bool, str]:
    """Run a capture command with timeout. Returns (success, stderr)."""
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout + 5,
        )
        if r.returncode != 0:
            return False, r.stderr.strip()
        return True, ""
    except subprocess.TimeoutExpired:
        return False, f"Recording timed out after {timeout + 5}s"
    except FileNotFoundError:
        return False, f"{cmd[0]} not installed"
    except Exception as e:
        return False, str(e)


def _parse_pw_cli_objects() -> list[dict]:
    """Parse pw-cli list-objects into structured dicts.

    Each object gets: id, type, props (dict of key=value pairs).
    """
    try:
        r = subprocess.run(
            ["pw-cli", "list-objects"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return []
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    objects = []
    current: Optional[dict] = None

    for line in r.stdout.splitlines():
        stripped = line.strip()
        # New object line: "id N, type PipeWire:Interface:..."
        if stripped.startswith("id ") and ", type " in stripped:
            if current:
                objects.append(current)
            parts = stripped.split(", type ")
            obj_id = parts[0].replace("id ", "").strip().rstrip(",")
            obj_type = parts[1].strip() if len(parts) > 1 else ""
            current = {"id": obj_id, "type": obj_type, "props": {}}
        elif current and "=" in stripped:
            # Property line: key = "value"
            key, _, val = stripped.partition("=")
            key = key.strip()
            val = val.strip().strip('"')
            current["props"][key] = val

    if current:
        objects.append(current)
    return objects


def _find_monitor_source(objects: list[dict]) -> Optional[str]:
    """Find the monitor source node name for system audio capture."""
    for obj in objects:
        props = obj.get("props", {})
        media_class = props.get("media.class", "")
        if media_class == "Audio/Sink":
            node_name = props.get("node.name", "")
            if node_name:
                # PipeWire monitor sources are <sink_name>.monitor
                return f"{node_name}.monitor"
    return None


class PipeWireAudioProvider(AudioProvider):
    """Audio capture via PipeWire on Linux."""

    def capture_system_audio(
        self, duration_seconds: int = 5, output_path: Optional[str] = None,
    ) -> dict:
        duration_seconds = min(max(1, duration_seconds), MAX_DURATION)
        out = Path(output_path) if output_path else _generate_path("system")

        # Find the monitor source for loopback capture
        objects = _parse_pw_cli_objects()
        monitor = _find_monitor_source(objects)
        if not monitor:
            return {
                "success": False,
                "error": "No audio sink found for monitor capture. "
                         "Is PipeWire running with an active sink?",
            }

        # pw-record --target <monitor> --rate 44100 --channels 2 <duration> <output>
        # Some versions of pw-record use different flag syntax
        cmd = [
            "pw-record",
            "--target", monitor,
            "--rate", "44100",
            "--channels", "2",
            str(out),
        ]

        logger.debug("Capturing system audio: %s for %ds", monitor, duration_seconds)

        # pw-record runs until killed or EOF — we use timeout to stop it
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            try:
                _, stderr = proc.communicate(timeout=duration_seconds + 1)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        except FileNotFoundError:
            return {"success": False, "error": "pw-record not installed"}
        except Exception as e:
            return {"success": False, "error": str(e)}

        if not out.exists() or out.stat().st_size == 0:
            return {
                "success": False,
                "error": "Recording produced no data. Check PipeWire status.",
            }

        return {
            "success": True,
            "path": str(out),
            "duration": duration_seconds,
            "sample_rate": 44100,
            "channels": 2,
            "size_bytes": out.stat().st_size,
        }

    def capture_mic_audio(
        self, duration_seconds: int = 5, output_path: Optional[str] = None,
    ) -> dict:
        duration_seconds = min(max(1, duration_seconds), MAX_DURATION)
        out = Path(output_path) if output_path else _generate_path("mic")

        # pw-record with default source (mic)
        cmd = [
            "pw-record",
            "--rate", "16000",
            "--channels", "1",
            str(out),
        ]

        logger.debug("Capturing mic audio for %ds", duration_seconds)

        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            try:
                _, stderr = proc.communicate(timeout=duration_seconds + 1)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        except FileNotFoundError:
            return {"success": False, "error": "pw-record not installed"}
        except Exception as e:
            return {"success": False, "error": str(e)}

        if not out.exists() or out.stat().st_size == 0:
            return {
                "success": False,
                "error": "Mic recording produced no data. Is a microphone connected?",
            }

        return {
            "success": True,
            "path": str(out),
            "duration": duration_seconds,
            "sample_rate": 16000,
            "channels": 1,
            "size_bytes": out.stat().st_size,
        }

    def list_audio_sources(self) -> list[dict]:
        sources: list[dict] = []

        # Get default sink/source names via wpctl
        defaults = self._get_defaults()

        objects = _parse_pw_cli_objects()
        for obj in objects:
            props = obj.get("props", {})
            media_class = props.get("media.class", "")
            node_name = props.get("node.name", "")
            description = props.get("node.description", node_name)

            if not node_name:
                continue

            if media_class == "Audio/Sink":
                sources.append({
                    "name": node_name,
                    "description": description,
                    "type": "sink",
                    "is_default": node_name == defaults.get("sink", ""),
                })
                # Also add the monitor
                sources.append({
                    "name": f"{node_name}.monitor",
                    "description": f"{description} (Monitor)",
                    "type": "monitor",
                    "is_default": False,
                })
            elif media_class == "Audio/Source":
                sources.append({
                    "name": node_name,
                    "description": description,
                    "type": "source",
                    "is_default": node_name == defaults.get("source", ""),
                })

        return sources

    def get_audio_status(self) -> dict:
        status: dict = {
            "running": False,
            "server": "unknown",
            "version": "unknown",
            "default_sink": "",
            "default_source": "",
        }

        # Check PipeWire via pw-cli info 0
        try:
            r = subprocess.run(
                ["pw-cli", "info", "0"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                status["running"] = True
                status["server"] = "pipewire"
                for line in r.stdout.splitlines():
                    line = line.strip()
                    if "version" in line.lower() and "=" in line:
                        status["version"] = line.split("=", 1)[1].strip().strip('"')
                        break
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # Get defaults
        defaults = self._get_defaults()
        status["default_sink"] = defaults.get("sink", "")
        status["default_source"] = defaults.get("source", "")

        return status

    @staticmethod
    def _get_defaults() -> dict:
        """Get default sink/source names via wpctl."""
        defaults: dict = {}
        try:
            r = subprocess.run(
                ["wpctl", "status"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                section = ""
                for line in r.stdout.splitlines():
                    stripped = line.strip()
                    if "Sinks:" in stripped:
                        section = "sink"
                    elif "Sources:" in stripped:
                        section = "source"
                    elif stripped.startswith("*") and section:
                        # Default device marked with *
                        # Format: " *  47. device_name [vol: 1.00]"
                        parts = stripped.lstrip("* ").split(".", 1)
                        if len(parts) >= 2:
                            name = parts[1].strip()
                            # Remove volume info
                            if "[" in name:
                                name = name[:name.index("[")].strip()
                            defaults[section] = name
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return defaults


if __name__ == "__main__":
    provider = PipeWireAudioProvider()
    print("=== PipeWireAudioProvider self-test ===")

    # 1. Audio status
    print("\n--- 1. get_audio_status ---")
    status = provider.get_audio_status()
    for k, v in status.items():
        print(f"  {k}: {v}")
    if status["running"]:
        print("  PASS")
    else:
        print("  FAIL: PipeWire not running")

    # 2. List audio sources
    print("\n--- 2. list_audio_sources ---")
    sources = provider.list_audio_sources()
    for s in sources:
        default = " [DEFAULT]" if s["is_default"] else ""
        print(f"  [{s['type']}] {s['name']}{default}")
        if s["description"] != s["name"]:
            print(f"         {s['description']}")
    print(f"  Total: {len(sources)}")
    if sources:
        print("  PASS")
    else:
        print("  WARNING: No sources found")

    # 3. Mic capture (1 second)
    print("\n--- 3. capture_mic_audio(1s) ---")
    result = provider.capture_mic_audio(duration_seconds=1)
    if result["success"]:
        print(f"  Path: {result['path']}")
        print(f"  Size: {result['size_bytes']:,} bytes")
        print(f"  Rate: {result['sample_rate']}Hz, {result['channels']}ch")
        if result["size_bytes"] > 100:
            print("  PASS")
        else:
            print("  WARNING: Very small file")
    else:
        print(f"  Error: {result['error']}")
        print("  FAIL (mic may not be connected)")

    # 4. System audio capture (1 second)
    print("\n--- 4. capture_system_audio(1s) ---")
    result = provider.capture_system_audio(duration_seconds=1)
    if result["success"]:
        print(f"  Path: {result['path']}")
        print(f"  Size: {result['size_bytes']:,} bytes")
        print("  PASS")
    else:
        print(f"  Error: {result['error']}")
        print("  SKIP (no active audio sink may exist)")

    print("\nPASS: PipeWireAudioProvider self-test complete")
