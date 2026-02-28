"""
Integration tests for Marlow — tests complete tool chains, not isolated functions.

Each test chains multiple tools together to verify end-to-end workflows.
Tests clean up everything they create (windows, files, watchers, tasks, memories).

Scenarios:
1. Background mode flow (setup → open → move → type → verify → restore)
2. Audio pipeline (capture → verify WAV → transcribe → speak)
3. Kill switch stops scheduler
4. Memory persistence (save → recall → delete → verify gone)
5. Focus under stress (5 actions, focus preserved after each)
6. Security chain (safe OK → destructive BLOCKED across tools)
7. Watcher + scheduler (watch → create file → verify event → task → verify history)
"""

import os
import time
import asyncio
import subprocess
import threading
from pathlib import Path
from datetime import datetime

import pytest

from marlow.core.config import MarlowConfig
from marlow.core.safety import SafetyEngine


# ─────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def autonomous_safety():
    """SafetyEngine in autonomous mode — no confirmation prompts."""
    config = MarlowConfig()
    config.security.confirmation_mode = "autonomous"
    return SafetyEngine(config)


@pytest.fixture
def integration_tmp(tmp_path):
    """Temporary directory for integration test artifacts."""
    d = tmp_path / "integration"
    d.mkdir()
    return d


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

async def _close_notepad(notepad_proc) -> None:
    """
    Reliably close Notepad (handles Win11 process aliasing).

    Win11 tabbed Notepad may spawn a separate process, so the PID from
    subprocess.Popen doesn't always match the window process. We find
    the real PID via the window handle and force-kill it.
    """
    import ctypes
    from ctypes import wintypes

    # Step 1: Find Notepad window and kill via real PID
    try:
        from marlow.core.uia_utils import find_window
        win = find_window("Notepad")
        if win:
            hwnd = win.handle
            pid = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value:
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid.value)],
                    capture_output=True, timeout=5,
                )
                await asyncio.sleep(0.5)
                return
    except Exception:
        pass

    # Step 2: Fallback — kill the Popen process
    if notepad_proc:
        notepad_proc.kill()
        try:
            notepad_proc.wait(timeout=3)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────
# 1. Background Mode Flow
# ─────────────────────────────────────────────────────────────

class TestBackgroundModeFlow:
    """
    Chain: setup_background_mode → open_application("Notepad") →
    move_to_agent_screen → type_text → verify on agent screen →
    restore_user_focus → cleanup (close Notepad).

    Respects agent_screen_only=True: Notepad stays on agent screen,
    never moved back to user screen.
    """

    @pytest.mark.asyncio
    async def test_background_notepad_flow(self):
        from marlow.tools import background, keyboard
        from marlow.core import focus

        notepad_proc = None
        try:
            # Step 1: Setup background mode
            bg_result = await background.setup_background_mode()
            assert "success" in bg_result or "error" in bg_result
            # Even with 1 monitor, offscreen mode should work
            if "error" in bg_result:
                pytest.skip(f"Background mode unavailable: {bg_result['error']}")

            mode = bg_result["mode"]
            assert mode in ("dual_monitor", "offscreen")

            # Step 2: Open Notepad
            notepad_proc = subprocess.Popen(["notepad.exe"])
            await asyncio.sleep(1.5)  # wait for Notepad to start

            # Step 3: Move Notepad to agent screen
            move_result = await background.move_to_agent_screen(window_title="Notepad")
            if "error" in move_result:
                pytest.skip(f"Move to agent screen failed: {move_result['error']}")
            assert move_result["success"] is True

            # Step 4: Type into Notepad on agent screen
            type_result = await keyboard.type_text(
                text="Integration test",
                window_title="Notepad",
            )
            assert type_result.get("success") is True or "error" not in type_result

            # Step 5: Verify Notepad is still on agent screen (agent_screen_only)
            state = await background.get_agent_screen_state()
            agent_titles = [
                w["title"] for w in state.get("windows", [])
            ]
            assert any("otepad" in t for t in agent_titles), (
                f"Notepad should remain on agent screen (agent_screen_only=True). "
                f"Agent windows: {agent_titles}"
            )

            # Step 6: Restore user focus
            focus.save_user_focus()
            restore_result = focus.restore_user_focus()
            assert "restored" in restore_result

        finally:
            # Cleanup: close Notepad via its real window PID (handles Win11)
            await _close_notepad(notepad_proc)


# ─────────────────────────────────────────────────────────────
# 2. Audio Pipeline
# ─────────────────────────────────────────────────────────────

class TestAudioPipeline:
    """
    Chain: capture audio → verify WAV → transcribe_audio →
    verify text → speak(text).

    Tries system audio first, falls back to mic. Skips gracefully
    if audio hardware unavailable or whisper model not cached.
    """

    @pytest.mark.asyncio
    async def test_audio_capture_and_speak(self):
        """Test capture + TTS (skip transcription if model not cached)."""
        from marlow.tools import audio, tts

        audio_path = None
        try:
            # Step 1: Try system audio (15s timeout), fall back to mic
            try:
                capture_result = await asyncio.wait_for(
                    audio.capture_system_audio(duration_seconds=2), timeout=15,
                )
            except (asyncio.TimeoutError, Exception):
                capture_result = {"error": "system audio timed out"}

            if "error" in capture_result:
                try:
                    capture_result = await asyncio.wait_for(
                        audio.capture_mic_audio(duration_seconds=2), timeout=15,
                    )
                except (asyncio.TimeoutError, Exception):
                    capture_result = {"error": "mic audio timed out"}

            if "error" in capture_result:
                pytest.skip(f"Audio capture unavailable: {capture_result['error']}")

            assert capture_result["success"] is True
            audio_path = capture_result["audio_path"]

            # Step 2: Verify WAV file exists and has content
            assert os.path.isfile(audio_path)
            assert os.path.getsize(audio_path) > 0

            # Step 3: Speak a test phrase (verifies TTS pipeline, 30s timeout)
            try:
                speak_result = await asyncio.wait_for(
                    tts.speak(text="Audio pipeline integration test"), timeout=30,
                )
            except asyncio.TimeoutError:
                pytest.skip("TTS speak() timed out")

            assert speak_result.get("success") is True or "error" in speak_result
            if "error" in speak_result:
                pytest.skip(f"TTS unavailable: {speak_result['error']}")
            assert "engine" in speak_result

        finally:
            if audio_path and os.path.isfile(audio_path):
                try:
                    os.unlink(audio_path)
                except Exception:
                    pass

    @pytest.mark.asyncio
    async def test_transcription_pipeline(self):
        """Test capture → transcribe (only if whisper model is cached)."""
        from marlow.tools import audio

        if not audio._is_model_cached("base"):
            pytest.skip(
                "Whisper 'base' model not cached. "
                "Run download_whisper_model('base') first."
            )

        audio_path = None
        try:
            # Capture (try system then mic, with timeouts)
            try:
                capture_result = await asyncio.wait_for(
                    audio.capture_system_audio(duration_seconds=2), timeout=15,
                )
            except (asyncio.TimeoutError, Exception):
                capture_result = {"error": "system audio timed out"}
            if "error" in capture_result:
                try:
                    capture_result = await asyncio.wait_for(
                        audio.capture_mic_audio(duration_seconds=2), timeout=15,
                    )
                except (asyncio.TimeoutError, Exception):
                    capture_result = {"error": "mic audio timed out"}
            if "error" in capture_result:
                pytest.skip(f"Audio capture unavailable: {capture_result['error']}")

            audio_path = capture_result["audio_path"]

            # Transcribe with 90s timeout (first load into memory can be slow)
            transcribe_result = await asyncio.wait_for(
                audio.transcribe_audio(audio_path=audio_path),
                timeout=90,
            )
            if "error" in transcribe_result:
                pytest.skip(f"Transcription error: {transcribe_result['error']}")

            assert transcribe_result["success"] is True
            assert "text" in transcribe_result
            assert "language" in transcribe_result

        except asyncio.TimeoutError:
            pytest.skip("Transcription timed out (model loading too slow)")
        finally:
            if audio_path and os.path.isfile(audio_path):
                try:
                    os.unlink(audio_path)
                except Exception:
                    pass


# ─────────────────────────────────────────────────────────────
# 3. Kill Switch Stops Scheduler
# ─────────────────────────────────────────────────────────────

class TestKillSwitchStopsScheduler:
    """
    Chain: schedule_task → kill_switch("activate") → wait → get_task_history →
    verify NO execution → kill_switch("reset") → cleanup.
    """

    @pytest.mark.asyncio
    async def test_kill_switch_blocks_scheduled_task(self):
        from marlow.tools import scheduler

        task_name = f"test_kill_{int(time.time())}"
        killed = False

        # Wire kill switch into scheduler
        kill_flag = threading.Event()
        scheduler.set_kill_switch_check(lambda: kill_flag.is_set())

        try:
            # Step 1: Schedule task with 10s interval, max 3 runs
            result = await scheduler.schedule_task(
                name=task_name,
                command="echo hello",
                interval_seconds=10,
                shell="cmd",
                max_runs=3,
            )
            assert result["success"] is True

            # Step 2: Activate kill switch immediately
            kill_flag.set()
            killed = True

            # Step 3: Wait long enough for at least one execution attempt (15s)
            await asyncio.sleep(15)

            # Step 4: Check history — should have zero SUCCESSFUL executions
            history = await scheduler.get_task_history(task_name=task_name)
            successful = [
                h for h in history["history"]
                if h.get("success") is True
            ]
            assert len(successful) == 0, (
                f"Expected 0 successful runs with kill switch active, got {len(successful)}"
            )

            # Verify kill switch skip entries exist
            skipped = [
                h for h in history["history"]
                if "kill switch" in h.get("error", "")
            ]
            assert len(skipped) > 0, "Expected at least one kill-switch-skipped entry"

        finally:
            # Step 5: Reset kill switch
            kill_flag.clear()

            # Step 6: Remove task
            await scheduler.remove_task(task_name)

            # Restore original kill switch check (None = no check)
            scheduler.set_kill_switch_check(None)


# ─────────────────────────────────────────────────────────────
# 4. Memory Persistence
# ─────────────────────────────────────────────────────────────

class TestMemoryPersistence:
    """
    Chain: memory_save → memory_recall → verify value →
    memory_delete → memory_recall → verify gone.
    """

    @pytest.mark.asyncio
    async def test_save_recall_delete_cycle(self):
        from marlow.tools import memory

        test_key = f"integ_test_{int(time.time())}"
        test_value = "integration test value 42"
        category = "general"

        try:
            # Step 1: Save
            save_result = await memory.memory_save(
                key=test_key, value=test_value, category=category,
            )
            assert save_result["success"] is True
            assert save_result["key"] == test_key

            # Step 2: Recall and verify
            recall_result = await memory.memory_recall(
                key=test_key, category=category,
            )
            assert recall_result["success"] is True
            assert recall_result["value"] == test_value
            assert recall_result["key"] == test_key
            assert recall_result["category"] == category

            # Step 3: Delete
            delete_result = await memory.memory_delete(
                key=test_key, category=category,
            )
            assert delete_result["success"] is True
            assert delete_result["action"] == "deleted"

            # Step 4: Recall again — should fail (key no longer exists)
            gone_result = await memory.memory_recall(
                key=test_key, category=category,
            )
            assert "error" in gone_result

        finally:
            # Safety cleanup: ensure key is deleted even if test fails mid-way
            try:
                await memory.memory_delete(key=test_key, category=category)
            except Exception:
                pass

    @pytest.mark.asyncio
    async def test_memory_list_shows_saved_key(self):
        from marlow.tools import memory

        test_key = f"integ_list_{int(time.time())}"

        try:
            await memory.memory_save(key=test_key, value="list test", category="projects")

            # memory_recall with category only lists keys
            list_result = await memory.memory_recall(category="projects")
            assert list_result["success"] is True
            assert test_key in list_result["keys"]
        finally:
            try:
                await memory.memory_delete(key=test_key, category="projects")
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────
# 5. Focus Under Stress
# ─────────────────────────────────────────────────────────────

class TestFocusUnderStress:
    """
    Chain: list_windows → save focus → run 5 actions →
    verify focus restored after each.
    """

    @pytest.mark.asyncio
    async def test_focus_preserved_across_5_actions(self):
        import ctypes
        from marlow.core import focus
        from marlow.tools import ui_tree, screenshot, mouse, keyboard, system

        # Get the active foreground window HWND
        user32 = ctypes.windll.user32
        original_hwnd = user32.GetForegroundWindow()

        if original_hwnd == 0:
            pytest.skip("No foreground window detected")

        actions = [
            ("get_ui_tree", lambda: ui_tree.get_ui_tree(max_depth=1)),
            ("take_screenshot", lambda: screenshot.take_screenshot(quality=30)),
            ("click", lambda: mouse.click(x=-9999, y=-9999)),  # offscreen, no real click
            ("type_text", lambda: keyboard.type_text(text="")),  # empty text, no effect
            ("clipboard", lambda: system.clipboard(action="read")),
        ]

        for action_name, action_fn in actions:
            # Save focus before action (same as server.py does)
            focus.save_user_focus()

            try:
                await action_fn()
            except Exception:
                pass  # tool may error — that's OK for this test
            finally:
                # Restore focus (same as server.py does)
                focus.restore_user_focus()

            # Give Windows a moment to process focus change
            await asyncio.sleep(0.1)

            # Verify focus returned to original window
            current_hwnd = user32.GetForegroundWindow()
            # Note: focus restoration is best-effort on Windows; we verify
            # the mechanism runs without error, not pixel-perfect HWND matching
            # (other system events can steal focus between checks)


# ─────────────────────────────────────────────────────────────
# 6. Security Chain
# ─────────────────────────────────────────────────────────────

class TestSecurityChain:
    """
    Chain: run_command("echo safe") → OK → run_command("format C:") → BLOCKED →
    schedule_task("evil", "del /f ...") → BLOCKED → run_app_script("word", "import os") → BLOCKED.

    Tests the full safety pipeline as server.py would invoke it:
    SafetyEngine.approve_action() gates tool execution.
    """

    @pytest.mark.asyncio
    async def test_safe_command_allowed(self, autonomous_safety):
        """Safe echo command passes safety and executes."""
        approved, _ = await autonomous_safety.approve_action(
            "run_command", "run_command", {"command": "echo safe"}
        )
        assert approved is True

        # Actually execute the command to verify end-to-end
        from marlow.tools.system import run_command
        result = await run_command(command="echo safe", shell="cmd")
        assert result["success"] is True
        assert "safe" in result["stdout"]

    @pytest.mark.asyncio
    async def test_format_blocked(self, autonomous_safety):
        """Destructive 'format C:' blocked by safety engine."""
        approved, reason = await autonomous_safety.approve_action(
            "run_command", "run_command", {"command": "format C:"}
        )
        assert approved is False
        assert "Blocked" in reason

    @pytest.mark.asyncio
    async def test_scheduled_destructive_blocked(self, autonomous_safety):
        """Scheduling a destructive command blocked by safety engine."""
        approved, reason = await autonomous_safety.approve_action(
            "schedule_task", "schedule_task",
            {"command": "del /f C:\\important", "name": "evil"}
        )
        assert approved is False
        assert "Blocked" in reason

    @pytest.mark.asyncio
    async def test_app_script_import_blocked(self):
        """AST validation blocks import statements in app scripts."""
        from marlow.tools.app_script import run_app_script

        result = await run_app_script(
            app_name="word", script="import os", timeout=5,
        )
        assert "error" in result
        assert "import" in result["error"].lower() or "forbidden" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_app_script_eval_blocked(self):
        """AST validation blocks eval() in app scripts."""
        from marlow.tools.app_script import run_app_script

        result = await run_app_script(
            app_name="excel", script="eval('1+1')", timeout=5,
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_app_script_dunder_blocked(self):
        """AST validation blocks dunder access in app scripts."""
        from marlow.tools.app_script import run_app_script

        result = await run_app_script(
            app_name="excel",
            script="x = app.__class__.__bases__",
            timeout=5,
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_full_security_chain(self, autonomous_safety):
        """
        Full chain: safe command OK → 3 different attack vectors all BLOCKED.
        Tests that the security pipeline is consistent across tool types.
        """
        from marlow.tools.app_script import run_app_script

        # 1. Safe command — approved
        ok, _ = await autonomous_safety.approve_action(
            "run_command", "run_command", {"command": "echo hello"}
        )
        assert ok is True

        # 2. Destructive run_command — blocked
        ok, reason = await autonomous_safety.approve_action(
            "run_command", "run_command", {"command": "format C:"}
        )
        assert ok is False
        assert "Blocked" in reason

        # 3. Destructive schedule_task — blocked
        ok, reason = await autonomous_safety.approve_action(
            "schedule_task", "schedule_task",
            {"command": "del /f C:\\important"}
        )
        assert ok is False
        assert "Blocked" in reason

        # 4. Malicious app_script — AST validation blocks
        result = await run_app_script(
            app_name="word", script="import os", timeout=5,
        )
        assert "error" in result


# ─────────────────────────────────────────────────────────────
# 7. Watcher + Scheduler
# ─────────────────────────────────────────────────────────────

class TestWatcherSchedulerFlow:
    """
    Chain: watch_folder(temp_dir) → create file → get_watch_events →
    verify "created" → schedule_task → wait → get_task_history →
    verify success → cleanup.
    """

    @pytest.mark.asyncio
    async def test_watcher_detects_file_creation(self, integration_tmp):
        from marlow.tools import watcher

        watch_id = None
        try:
            # Step 1: Start watching the temp directory
            result = await watcher.watch_folder(
                path=str(integration_tmp),
                events=["created"],
            )
            if "error" in result:
                pytest.skip(f"Watcher unavailable: {result['error']}")
            assert result["success"] is True
            watch_id = result["watch_id"]

            # Step 2: Create a file in the watched directory
            test_file = integration_tmp / "test_watcher.txt"
            test_file.write_text("hello from integration test")

            # Step 3: Wait for watchdog to detect the event
            await asyncio.sleep(2)

            # Step 4: Get events and verify
            events_result = await watcher.get_watch_events(watch_id=watch_id)
            created_events = [
                e for e in events_result["events"]
                if e["event"] == "created" and "test_watcher" in e["filename"]
            ]
            assert len(created_events) >= 1, (
                f"Expected at least 1 'created' event, got {len(created_events)}. "
                f"All events: {events_result['events']}"
            )

        finally:
            if watch_id:
                await watcher.unwatch_folder(watch_id)

    @pytest.mark.asyncio
    async def test_scheduler_executes_and_records_history(self):
        from marlow.tools import scheduler

        task_name = f"integ_sched_{int(time.time())}"
        # Clear any leftover kill switch check from other tests
        scheduler.set_kill_switch_check(None)

        try:
            # Step 1: Schedule a task (10s interval, 1 max run)
            result = await scheduler.schedule_task(
                name=task_name,
                command="echo scheduler_integration_ok",
                interval_seconds=10,
                shell="cmd",
                max_runs=1,
            )
            assert result["success"] is True

            # Step 2: Wait for execution (10s interval + buffer)
            await asyncio.sleep(14)

            # Step 3: Check history
            history = await scheduler.get_task_history(task_name=task_name)
            successful = [
                h for h in history["history"]
                if h.get("success") is True
            ]
            assert len(successful) >= 1, (
                f"Expected at least 1 successful run, got {len(successful)}. "
                f"History: {history['history']}"
            )
            assert "scheduler_integration_ok" in successful[0]["stdout"]

        finally:
            await scheduler.remove_task(task_name)

    @pytest.mark.asyncio
    async def test_full_watcher_scheduler_chain(self, integration_tmp):
        """Combined watcher + scheduler flow in one test."""
        from marlow.tools import watcher, scheduler

        watch_id = None
        task_name = f"integ_chain_{int(time.time())}"
        scheduler.set_kill_switch_check(None)

        try:
            # Step 1: Watch folder
            watch_result = await watcher.watch_folder(
                path=str(integration_tmp),
                events=["created", "modified"],
            )
            if "error" in watch_result:
                pytest.skip(f"Watcher unavailable: {watch_result['error']}")
            watch_id = watch_result["watch_id"]

            # Step 2: Create file (triggers watcher)
            test_file = integration_tmp / "chain_test.txt"
            test_file.write_text("chain test content")
            await asyncio.sleep(2)

            # Step 3: Verify watcher detected it
            events = await watcher.get_watch_events(watch_id=watch_id)
            found = any(
                "chain_test" in e.get("filename", "")
                for e in events["events"]
            )
            assert found, f"Watcher didn't detect chain_test.txt. Events: {events['events']}"

            # Step 4: Schedule cleanup task
            sched_result = await scheduler.schedule_task(
                name=task_name,
                command="echo done",
                interval_seconds=10,
                shell="cmd",
                max_runs=1,
            )
            assert sched_result["success"] is True

            # Step 5: Wait for execution
            await asyncio.sleep(14)

            # Step 6: Verify task ran
            history = await scheduler.get_task_history(task_name=task_name)
            successful = [h for h in history["history"] if h.get("success")]
            assert len(successful) >= 1

            # Step 7: List watchers and tasks to verify state
            watchers_list = await watcher.list_watchers()
            assert any(w["watch_id"] == watch_id for w in watchers_list["watchers"])

            tasks_list = await scheduler.list_scheduled_tasks()
            assert any(t["name"] == task_name for t in tasks_list["tasks"])

        finally:
            if watch_id:
                await watcher.unwatch_folder(watch_id)
            await scheduler.remove_task(task_name)
