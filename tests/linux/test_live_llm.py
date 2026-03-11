"""
marlow/kernel/test_live_llm.py
Live test: LLM-planned goals on real desktop.

Usage: python -m marlow.kernel.test_live_llm

Requires: ANTHROPIC_API_KEY environment variable set.
"""

import asyncio
import logging
import os
import sys


async def main():
    # Check API key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: Set ANTHROPIC_API_KEY environment variable first.")
        print("  PowerShell: $env:ANTHROPIC_API_KEY = 'sk-ant-...'")
        print("  CMD: set ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    from marlow.kernel.integration import AutonomousMarlow

    marlow = AutonomousMarlow(
        llm_provider="anthropic",
    )

    try:
        info = marlow.setup()
        print(f"\nSetup: {info['total_tools']} tools registered")

        print("\n" + "=" * 60)
        print("  MARLOW LLM PLANNING — LIVE TEST")
        print("=" * 60)

        # Test 1: Simple but needs LLM (no template match)
        print("\n--- Test 1: LLM-planned goal ---")
        print("Goal: 'Open Notepad, type Hello World, then save the file"
              " as test.txt on the Desktop'")
        print("(This requires LLM because it's too complex for template"
              " matching)\n")

        result = await marlow.execute(
            "Open Notepad, type Hello World, then save the file"
            " as test.txt on the Desktop",
        )

        print(f"\nResult: {'SUCCESS' if result.success else 'FAILED'}")
        print(f"Steps: {result.steps_completed}/{result.steps_total}")
        print(f"Score: {result.avg_score:.2f}")
        print(f"Time: {result.duration_s}s")
        if result.errors:
            print(f"Errors: {result.errors}")

        # Test 2: Another LLM goal
        print("\n--- Test 2: System info goal ---")
        print("Goal: 'List all open windows and tell me which one is"
              " using the most space'\n")

        result = await marlow.execute(
            "List all open windows and tell me which one is"
            " using the most space",
        )

        print(f"\nResult: {'SUCCESS' if result.success else 'FAILED'}")
        print(f"Steps: {result.steps_completed}/{result.steps_total}")
        print(f"Score: {result.avg_score:.2f}")
        print(f"Time: {result.duration_s}s")
        if result.errors:
            print(f"Errors: {result.errors}")

        print("\n" + "=" * 60)
        print("  LLM LIVE TEST COMPLETE")
        print("=" * 60)

    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        marlow.teardown()


if __name__ == "__main__":
    asyncio.run(main())
