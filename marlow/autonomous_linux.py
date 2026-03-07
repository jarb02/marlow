"""Marlow Autonomous Agent — Linux entry point.

Usage:
    # Single goal from command line
    python3 -m marlow.autonomous_linux "open Firefox and search for cats"

    # Interactive mode
    python3 -m marlow.autonomous_linux

/ Punto de entrada autonomo Linux — ejecuta goals de alto nivel.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

# Ensure Sway environment is available
_SWAY_ENV_KEYS = ("SWAYSOCK", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR")


def _check_environment():
    """Verify required environment variables for Sway/Wayland."""
    missing = []
    for key in _SWAY_ENV_KEYS:
        if key not in os.environ:
            missing.append(key)

    if "SWAYSOCK" not in os.environ:
        # Try to auto-detect
        import glob as _glob
        uid = os.getuid()
        socks = _glob.glob(f"/run/user/{uid}/sway-ipc.*.sock")
        if socks:
            os.environ["SWAYSOCK"] = socks[0]
            missing = [k for k in missing if k != "SWAYSOCK"]

    if "WAYLAND_DISPLAY" not in os.environ:
        os.environ["WAYLAND_DISPLAY"] = "wayland-1"
        missing = [k for k in missing if k != "WAYLAND_DISPLAY"]

    if missing:
        print(f"WARNING: Missing environment variables: {', '.join(missing)}")
        print("Some features may not work correctly.")


def _setup_logging(verbose: bool = False):
    """Configure logging for the autonomous agent."""
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    logging.basicConfig(level=level, format=fmt, stream=sys.stderr)

    # Reduce noise from libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


async def run_goal(goal_text: str, provider: str = None, model: str = ""):
    """Execute a single goal and return the result."""
    from marlow.kernel.integration_linux import AutonomousMarlow

    marlow = AutonomousMarlow(
        llm_provider=provider,
        llm_model=model,
        auto_confirm=True,
        timeout=30.0,
    )

    setup_result = marlow.setup()
    total = setup_result["total_tools"]
    failed = len(setup_result["failed"])
    print(f"Marlow Autonomous Agent — Linux", file=sys.stderr)
    print(f"Tools: {total} registered ({failed} failed)", file=sys.stderr)

    if provider:
        print(f"LLM: {provider} ({model or 'default'})", file=sys.stderr)
    else:
        print("LLM: none (Template + GOAP only)", file=sys.stderr)
    print(file=sys.stderr)

    try:
        result = await marlow.execute(goal_text)
        return result
    finally:
        marlow.teardown()


async def interactive_mode(provider: str = None, model: str = ""):
    """Interactive REPL for goals."""
    from marlow.kernel.integration_linux import AutonomousMarlow

    marlow = AutonomousMarlow(
        llm_provider=provider,
        llm_model=model,
        auto_confirm=True,
        timeout=30.0,
    )

    setup_result = marlow.setup()
    total = setup_result["total_tools"]
    failed = len(setup_result["failed"])
    print(f"Marlow Autonomous Agent — Linux")
    print(f"Tools: {total} registered ({failed} failed)")
    if provider:
        print(f"LLM: {provider} ({model or 'default'})")
    else:
        print("LLM: none (Template + GOAP only)")
    print()
    print("Type a goal and press Enter. 'quit' to exit.")
    print()

    try:
        while True:
            try:
                goal = input("> ")
            except EOFError:
                break

            goal = goal.strip()
            if not goal:
                continue
            if goal.lower() in ("quit", "exit", "q"):
                break

            result = await marlow.execute(goal, context={})
            print()
            print(f"  Success: {result.success}")
            print(f"  Steps:   {result.steps_completed}/{result.steps_total}")
            print(f"  Score:   {result.avg_score:.2f}")
            if result.errors:
                print(f"  Errors:  {result.errors}")
            if result.replan_count:
                print(f"  Replans: {result.replan_count}")
            print()
    finally:
        marlow.teardown()


async def main():
    """Entry point — parse args and run."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Marlow Autonomous Agent — Linux",
    )
    parser.add_argument(
        "goal", nargs="*",
        help="Goal to execute (omit for interactive mode)",
    )
    parser.add_argument(
        "--provider", "-p",
        choices=["anthropic", "openai", "gemini", "ollama"],
        default=None,
        help="LLM provider for complex goals",
    )
    parser.add_argument(
        "--model", "-m",
        default="",
        help="Override default model name",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose logging (DEBUG level)",
    )

    args = parser.parse_args()

    _check_environment()
    _setup_logging(verbose=args.verbose)

    goal = " ".join(args.goal) if args.goal else None

    if goal:
        result = await run_goal(goal, provider=args.provider, model=args.model)
        # Print final result
        status = "SUCCESS" if result.success else "FAILED"
        print(f"\n[{status}] {goal}")
        print(f"  Steps: {result.steps_completed}/{result.steps_total}")
        print(f"  Score: {result.avg_score:.2f}")
        if result.errors:
            for err in result.errors:
                print(f"  Error: {err}")
        sys.exit(0 if result.success else 1)
    else:
        await interactive_mode(provider=args.provider, model=args.model)


if __name__ == "__main__":
    asyncio.run(main())
