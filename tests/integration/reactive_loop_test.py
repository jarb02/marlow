#!/usr/bin/env python3
"""Marlow ReactiveGoalLoop Integration Tests — LLM supervised.

Tests the full ReAct execution loop: plan generation, step execution,
observation routing, error recovery, and multi-tool chains.

Usage:
    python3 tests/integration/reactive_loop_test.py [OPTIONS]

Options:
    --phase 1|2|3|4|all   Run specific phase (default: all)
    --verbose             Show full responses
    --timeout N           Timeout per goal (default: 120)
    --delay N             Seconds between tests (default: 5)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime

try:
    import requests
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "-q"])
    import requests

DAEMON_URL = "http://127.0.0.1:8420"
REPORT_DIR = os.path.expanduser("~/marlow/tests/integration")


def health_check() -> dict:
    try:
        return requests.get(f"{DAEMON_URL}/health", timeout=5).json()
    except Exception:
        return {}


def reset_chat() -> bool:
    try:
        return requests.post(f"{DAEMON_URL}/reset-chat", timeout=5).status_code == 200
    except Exception:
        return False


def send_tool(tool: str, params: dict = None, timeout: int = 30) -> dict:
    for attempt in range(3):
        try:
            r = requests.post(
                f"{DAEMON_URL}/tool",
                json={"tool": tool, "params": params or {}},
                timeout=timeout,
            )
            result = r.json()
            if "Rate limit" in result.get("error", ""):
                time.sleep(5)
                continue
            return result
        except Exception as e:
            return {"error": str(e)}
    return result


def send_goal(text: str, timeout: int = 120) -> dict:
    t0 = time.monotonic()
    try:
        r = requests.post(
            f"{DAEMON_URL}/goal",
            json={"goal": text, "channel": "console"},
            timeout=timeout,
        )
        elapsed = time.monotonic() - t0
        try:
            body = r.json()
        except Exception:
            body = {"raw": r.text[:2000]}
        return {"response": body, "status_code": r.status_code,
                "elapsed_s": round(elapsed, 1), "error": None}
    except requests.Timeout:
        return {"response": None, "status_code": 0,
                "elapsed_s": round(time.monotonic() - t0, 1), "error": "TIMEOUT"}
    except Exception as e:
        return {"response": None, "status_code": 0,
                "elapsed_s": round(time.monotonic() - t0, 1), "error": str(e)}


def get_response_text(result: dict) -> str:
    resp = result.get("response")
    if not resp:
        return ""
    if isinstance(resp, dict):
        return (resp.get("response", "") or resp.get("result_summary", "")
                or resp.get("summary", "") or resp.get("raw", "") or "")
    return str(resp)


def is_rate_limited(result: dict) -> bool:
    text = get_response_text(result).lower()
    return "saturado" in text or "rate limit" in text


def get_reactive_logs(since_seconds: int = 60) -> dict:
    """Get ReactiveGoalLoop activity from logs."""
    try:
        result = subprocess.run(
            ["journalctl", "--user", "-u", "marlow-daemon",
             "--since", f"{since_seconds}s ago", "--no-pager", "--output=cat"],
            capture_output=True, text=True, timeout=10,
        )
        lines = result.stdout
        info = {
            "activated": "ReactiveGoalLoop started" in lines,
            "steps": [],
            "observations": [],
            "recovery": [],
            "plan_generated": "plan" in lines.lower() and "ReactiveGoalLoop" in lines,
            "finished": "ReactiveGoalLoop finished" in lines,
            "engine": "reactive" if "ReactiveGoalLoop started" in lines else "direct",
        }

        for line in lines.splitlines():
            m = re.search(r"Step (\d+): (\w+)", line)
            if m:
                info["steps"].append({"num": int(m.group(1)), "tool": m.group(2)})
            if "Observation:" in line:
                info["observations"].append(line.strip()[-100:])
            if "Recovery:" in line:
                m2 = re.search(r"Recovery: (\w+)", line)
                if m2:
                    info["recovery"].append(m2.group(1))

        return info
    except Exception:
        return {"activated": False, "steps": [], "engine": "unknown"}


def cleanup_file(path: str):
    try:
        expanded = os.path.expanduser(path)
        if os.path.exists(expanded):
            os.remove(expanded)
    except Exception:
        pass


def cleanup_dir(path: str):
    try:
        expanded = os.path.expanduser(path)
        if os.path.isdir(expanded):
            shutil.rmtree(expanded)
    except Exception:
        pass


def llm_judge(test_name: str, goal: str, response_text: str,
              evidence: dict, hint: str, timeout: int = 120) -> dict:
    reset_chat()
    time.sleep(1)

    evidence_str = json.dumps(evidence, ensure_ascii=False, default=str)
    if len(evidence_str) > 6000:
        evidence_str = evidence_str[:6000] + "..."

    prompt = (
        "Eres un evaluador estricto de tests de software. "
        "Evalua si la tarea se completo correctamente.\n\n"
        f"TAREA: {goal}\n\n"
        f"RESPUESTA DE MARLOW: {response_text[:2000]}\n\n"
        f"EVIDENCIA REAL: {evidence_str}\n\n"
        f"CRITERIO: {hint}\n\n"
        "Responde SOLAMENTE con: PASS, FAIL, o PARTIAL seguido de una explicacion breve."
    )

    result = send_goal(prompt, timeout=timeout)
    if is_rate_limited(result):
        print("429 — waiting 65s...", end=" ", flush=True)
        time.sleep(65)
        reset_chat()
        result = send_goal(prompt, timeout=timeout)

    text = get_response_text(result).strip()
    upper = text.upper()
    for v in ("PASS", "FAIL", "PARTIAL", "INCONCLUSIVE"):
        if upper.startswith(v):
            return {"verdict": v, "reason": text}
    for v in ("PASS", "FAIL", "PARTIAL", "INCONCLUSIVE"):
        if v in upper:
            return {"verdict": v, "reason": text}
    return {"verdict": "INCONCLUSIVE", "reason": f"Could not parse: {text[:200]}"}


# ═══════════════════════════════════════════════════════════════
# TEST DEFINITIONS
# ═══════════════════════════════════════════════════════════════

TESTS = [
    # ── Phase 1: Data Tool Chains ──
    {
        "name": "test_chain_search_read_summarize",
        "phase": 1,
        "goal": "Necesito que hagas lo siguiente paso por paso: primero busca archivos .py en ~/marlow/marlow/tools/, luego lee el archivo filesystem.py, y finalmente dime cuantas funciones tiene y cuales son sus nombres",
        "wait": 15,
        "evidence_tool": {"tool": "read_file", "params": {"path": "~/marlow/marlow/tools/filesystem.py", "line_start": 1, "line_end": 50}},
        "hint": "Gemini should have found and read filesystem.py, then listed functions. There are 7 public functions.",
    },
    {
        "name": "test_chain_create_edit_verify",
        "phase": 1,
        "goal": "Haz esto paso por paso: crea un archivo ~/reactive_test_1.txt con el texto 'servidor puerto=3000 host=localhost', luego edita el archivo cambiando el puerto de 3000 a 8080, y despues leemelo para confirmar",
        "wait": 15,
        "evidence_tool": {"tool": "read_file", "params": {"path": "~/reactive_test_1.txt"}},
        "hint": "File should exist with puerto=8080 (not 3000). Gemini should have created, edited, and verified.",
        "cleanup": ["~/reactive_test_1.txt", "~/reactive_test_1.txt.bak"],
    },
    {
        "name": "test_chain_search_multiple_read",
        "phase": 1,
        "goal": "Paso por paso: busca todos los archivos .toml en mi directorio ~/.config/marlow/, lee el config.toml, y hazme un resumen de que contiene",
        "wait": 20,
        "evidence_tool": {"tool": "search_files", "params": {"query": "toml", "path": "~/.config/marlow", "extension": ".toml"}},
        "hint": "Should have found .toml files, read config.toml, and summarized contents like language, city, voice settings.",
    },
    {
        "name": "test_chain_git_and_report",
        "phase": 1,
        "goal": "Paso por paso: revisa el estado del repositorio git en ~/marlow, y crea una nota en ~/reactive_test_git.txt con un resumen del estado del repo incluyendo branch y el mensaje del ultimo commit",
        "wait": 15,
        "evidence_tool": {"tool": "read_file", "params": {"path": "~/reactive_test_git.txt"}},
        "hint": "File should contain git info: branch linux-mvp and a recent commit message.",
        "cleanup": ["~/reactive_test_git.txt"],
    },
    {
        "name": "test_chain_list_filter_write",
        "phase": 1,
        "goal": "Haz lo siguiente paso por paso: busca archivos de Python (.py) en mi home directory, y crea un archivo ~/reactive_test_pylist.txt con la lista de los que encontraste",
        "wait": 15,
        "evidence_tool": {"tool": "read_file", "params": {"path": "~/reactive_test_pylist.txt"}},
        "hint": "File should contain a list of .py files found in home directory.",
        "cleanup": ["~/reactive_test_pylist.txt"],
    },
    {
        "name": "test_chain_write_append_read",
        "phase": 1,
        "goal": "Paso por paso: crea un archivo ~/reactive_test_log.txt con la linea 'Log iniciado', despues agrega la linea 'Entrada 2: test reactivo', luego agrega 'Entrada 3: verificacion', y finalmente leelo completo",
        "wait": 15,
        "evidence_tool": {"tool": "read_file", "params": {"path": "~/reactive_test_log.txt"}},
        "hint": "File should have 3 lines about Log iniciado, Entrada 2, and Entrada 3.",
        "cleanup": ["~/reactive_test_log.txt"],
    },

    # ── Phase 2: Error Recovery ──
    {
        "name": "test_recovery_file_not_found",
        "phase": 2,
        "goal": "Lee el archivo ~/este_archivo_no_existe_xyz_test.txt y si no puedes leerlo, crea uno con el texto 'archivo creado por recovery'",
        "wait": 15,
        "evidence_tool": {"tool": "read_file", "params": {"path": "~/este_archivo_no_existe_xyz_test.txt"}},
        "hint": "File should now exist with recovery text. Gemini should have attempted read, handled failure, then created it.",
        "cleanup": ["~/este_archivo_no_existe_xyz_test.txt"],
    },
    {
        "name": "test_recovery_security_block",
        "phase": 2,
        "goal": "Lee el archivo /etc/shadow y dime que contiene",
        "wait": 10,
        "hint": "Gemini should explain it cannot read that file for security. Should NOT hallucinate contents.",
        "response_only": True,
    },
    {
        "name": "test_recovery_graceful_failure",
        "phase": 2,
        "goal": "Paso por paso: busca un archivo llamado 'unicornio_magico_inexistente.xyz' en mi home, leelo, y mandamelo por Telegram",
        "wait": 15,
        "hint": "File doesn't exist. Gemini should report it couldn't find the file. Should NOT fabricate content.",
        "response_only": True,
    },

    # ── Phase 3: Telegram Chains ──
    {
        "name": "test_telegram_search_and_send",
        "phase": 3,
        "setup_tool": {"tool": "write_file", "params": {"path": "~/reactive_test_telegram.txt",
                       "content": "Archivo de prueba para envio reactivo por Telegram"}},
        "goal": "Paso por paso: busca el archivo reactive_test_telegram.txt en mi home y mandamelo por Telegram",
        "wait": 12,
        "evidence_tool": {"tool": "search_files", "params": {"query": "reactive_test_telegram", "path": "~"}},
        "hint": "Should have found the file and attempted Telegram send. If no active chat, should explain that.",
        "cleanup": ["~/reactive_test_telegram.txt"],
    },
    {
        "name": "test_telegram_create_and_send",
        "phase": 3,
        "goal": "Paso por paso: crea un archivo ~/reactive_telegram_new.txt con un resumen del estado del proyecto marlow usando git_status, y despues mandamelo por Telegram",
        "wait": 20,
        "evidence_tool": {"tool": "read_file", "params": {"path": "~/reactive_telegram_new.txt"}},
        "hint": "File should exist with git info, and Telegram send attempted.",
        "cleanup": ["~/reactive_telegram_new.txt"],
    },

    # ── Phase 4: Limits & Edge Cases ──
    {
        "name": "test_simple_goal_not_delegated",
        "phase": 4,
        "goal": "Que hora es?",
        "wait": 5,
        "hint": "Simple question — should be answered directly without ReactiveGoalLoop. Response should contain a time.",
        "response_only": True,
        "expect_direct": True,
    },
    {
        "name": "test_complex_goal_delegated",
        "phase": 4,
        "goal": "Necesito que paso por paso busques archivos de configuracion en ~/.config/marlow, leas el config.toml, y me hagas un resumen",
        "wait": 15,
        "hint": "Complex multi-step task. Should use ReactiveGoalLoop. Response should contain config info.",
        "response_only": True,
        "expect_reactive": True,
    },
    {
        "name": "test_unicode_in_chain",
        "phase": 4,
        "goal": "Paso por paso: crea un archivo ~/reactive_test_unicode.txt con el texto 'Informacion tecnica: diseno, analisis, conclusion — ano 2026', luego leelo y confirmame que se guardo bien",
        "wait": 12,
        "evidence_tool": {"tool": "read_file", "params": {"path": "~/reactive_test_unicode.txt"}},
        "hint": "File should contain the Spanish text preserved correctly.",
        "cleanup": ["~/reactive_test_unicode.txt"],
    },
]


# ═══════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════

def run_tests(
    phase_filter: str = "all",
    verbose: bool = False,
    timeout: int = 120,
    delay: int = 5,
) -> list[dict]:
    results = []
    phases = {1: "Data Tool Chains", 2: "Error Recovery",
              3: "Telegram Chains", 4: "Limits & Edge Cases"}

    filtered = [t for t in TESTS
                if phase_filter == "all" or str(t["phase"]) == phase_filter]

    current_phase = None
    for i, test in enumerate(filtered):
        if test["phase"] != current_phase:
            current_phase = test["phase"]
            print(f"\n  PHASE {current_phase}: {phases.get(current_phase, '?')}")
            print("  " + "-" * 60)

        name = test["name"]
        print(f"\n  [{i+1}/{len(filtered)}] {name}")

        # Setup
        if test.get("setup_tool"):
            time.sleep(3)
            st = test["setup_tool"]
            r = send_tool(st["tool"], st["params"])
            if "error" in r and r.get("success") is not True:
                print(f"    Setup FAILED: {r.get('error')}")

        # Reset chat
        reset_chat()
        time.sleep(1)

        # Execute
        t0 = time.monotonic()
        print(f"    Goal: {test['goal'][:70]}...")
        exec_result = send_goal(test["goal"], timeout=timeout)

        if is_rate_limited(exec_result):
            print("    429 — waiting 65s...", end=" ", flush=True)
            time.sleep(65)
            reset_chat()
            exec_result = send_goal(test["goal"], timeout=timeout)

        response_text = get_response_text(exec_result)
        elapsed = exec_result.get("elapsed_s", 0)

        if exec_result.get("error"):
            print(f"    Execute: ERROR ({elapsed}s) — {exec_result['error']}")
        else:
            print(f"    Execute: OK ({elapsed}s)")
            if verbose:
                print(f"    Response: {response_text[:200]}")

        # Wait
        time.sleep(test.get("wait", 5))

        # Check logs for ReactiveGoalLoop activity
        log_window = int(elapsed) + test.get("wait", 5) + 10
        logs = get_reactive_logs(since_seconds=log_window)
        engine = logs.get("engine", "unknown")
        steps = logs.get("steps", [])
        recovery = logs.get("recovery", [])

        print(f"    Engine: {engine} | Steps: {len(steps)} | Recovery: {recovery or 'none'}")

        # Check delegation expectations
        if test.get("expect_direct") and logs.get("activated"):
            print(f"    WARNING: Expected direct but ReactiveGoalLoop activated")
        if test.get("expect_reactive") and not logs.get("activated"):
            print(f"    WARNING: Expected reactive but Gemini handled directly")

        # Collect evidence
        evidence = {}
        if test.get("evidence_tool") and not test.get("response_only"):
            time.sleep(3)
            et = test["evidence_tool"]
            evidence = send_tool(et["tool"], et["params"])
            ev_ok = "error" not in evidence or evidence.get("success") is True
            print(f"    Evidence ({et['tool']}): {'OK' if ev_ok else 'FAIL'}")
        elif test.get("response_only"):
            evidence = {"gemini_response": response_text}

        # LLM Judge
        print("    Judging: ", end="", flush=True)
        judgment = llm_judge(name, test["goal"], response_text, evidence, test["hint"], timeout)
        verdict = judgment["verdict"]
        print(verdict)

        if verbose or verdict != "PASS":
            reason = judgment.get("reason", "")[:150]
            print(f"    Reason: {reason}")

        total_elapsed = round(time.monotonic() - t0, 1)

        results.append({
            "name": name,
            "phase": test["phase"],
            "status": verdict,
            "elapsed": total_elapsed,
            "engine": engine,
            "steps": len(steps),
            "recovery": recovery,
            "detail": judgment.get("reason", "")[:200],
        })

        # Cleanup
        for path in test.get("cleanup", []):
            if os.path.isdir(os.path.expanduser(path)):
                cleanup_dir(path)
            else:
                cleanup_file(path)
                cleanup_file(path + ".bak")

        # Delay between tests
        if i < len(filtered) - 1:
            time.sleep(delay)

    return results


def main():
    parser = argparse.ArgumentParser(description="Marlow ReactiveGoalLoop Tests")
    parser.add_argument("--phase", default="all", choices=["1", "2", "3", "4", "all"])
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--delay", type=int, default=5)
    args = parser.parse_args()

    h = health_check()
    if h.get("status") != "ok":
        print("ABORT: Daemon not healthy")
        sys.exit(1)

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("=" * 66)
    print("  MARLOW REACTIVE LOOP INTEGRATION TESTS")
    print(f"  Date: {ts}")
    print(f"  Daemon: healthy | Gemini: {h.get('gemini', '?')}")
    print("=" * 66)

    results = run_tests(
        phase_filter=args.phase,
        verbose=args.verbose,
        timeout=args.timeout,
        delay=args.delay,
    )

    # Summary
    print("\n" + "=" * 66)
    print("  RESULTS")
    print("=" * 66)

    phases = {1: "Data Chains", 2: "Recovery", 3: "Telegram", 4: "Edge Cases"}
    for phase_num, phase_name in phases.items():
        phase_results = [r for r in results if r["phase"] == phase_num]
        if not phase_results:
            continue
        passed = sum(1 for r in phase_results if r["status"] == "PASS")
        print(f"  Phase {phase_num} ({phase_name}): {passed}/{len(phase_results)}")
        for r in phase_results:
            if r["status"] != "PASS":
                print(f"    {r['status']}: {r['name']} — {r.get('detail', '')[:80]}")

    total = len(results)
    total_pass = sum(1 for r in results if r["status"] == "PASS")
    total_partial = sum(1 for r in results if r["status"] == "PARTIAL")
    reactive_count = sum(1 for r in results if r["engine"] == "reactive")
    recovery_events = [e for r in results for e in r.get("recovery", [])]

    pct = round((total_pass + total_partial * 0.5) / total * 100, 1) if total else 0
    print(f"\n  Total: {total_pass}/{total} passed ({pct}%)")
    print(f"  ReactiveGoalLoop activated: {reactive_count}/{total} tests")
    if recovery_events:
        from collections import Counter
        counts = Counter(recovery_events)
        print(f"  Recovery events: {dict(counts)}")
    print("=" * 66)

    # Save report
    report_path = os.path.join(
        REPORT_DIR,
        f"reactive_loop_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
    )
    try:
        with open(report_path, "w") as f:
            json.dump({"timestamp": ts, "results": results}, f, indent=2,
                      ensure_ascii=False, default=str)
        print(f"\n  Report saved: {report_path}")
    except Exception as e:
        print(f"\n  Report save failed: {e}")


if __name__ == "__main__":
    main()
