"""Linux CascadeRecoveryProvider — multi-strategy element search.

Tries progressively broader strategies to find a UI element:
1. Exact smart_find in target window
2. Partial name match (split words, try each)
3. Search all windows (no window filter)
4. Full-screen OCR as last resort

/ CascadeRecoveryProvider Linux — busqueda multi-estrategia.
"""

from __future__ import annotations

import logging
from typing import Optional

from marlow.platform.base import CascadeRecoveryProvider

logger = logging.getLogger("marlow.platform.linux.cascade_recovery")

# Default strategy order
_DEFAULT_STRATEGIES = [
    "exact",
    "partial_name",
    "all_windows",
    "full_screen_ocr",
]


class LinuxCascadeRecoveryProvider(CascadeRecoveryProvider):
    """Multi-strategy element search with progressive fallback."""

    def __init__(self, escalation=None, ocr=None):
        self._escalation = escalation
        self._ocr = ocr

    def cascade_find(
        self,
        name: Optional[str] = None,
        role: Optional[str] = None,
        window_title: Optional[str] = None,
        strategies: Optional[list[str]] = None,
    ) -> dict:
        if not name and not role:
            return {"success": False, "error": "Must provide name or role"}

        strategy_order = strategies or _DEFAULT_STRATEGIES
        attempts = []

        for strategy in strategy_order:
            result = self._try_strategy(strategy, name, role, window_title)
            attempts.append({
                "strategy": strategy,
                "success": result.get("success", False),
                "method": result.get("method", "none"),
            })

            if result.get("success"):
                result["strategy_used"] = strategy
                result["attempts"] = attempts
                return result

            # Skip screenshot fallback from smart_find — we want structured results
            if result.get("requires_vision"):
                attempts[-1]["note"] = "screenshot_available"
                continue

        # All strategies exhausted
        return {
            "success": False,
            "error": f"All {len(attempts)} strategies failed for: "
                     f"name={name!r} role={role!r}",
            "attempts": attempts,
            "strategy_used": "none",
        }

    def _try_strategy(
        self,
        strategy: str,
        name: Optional[str],
        role: Optional[str],
        window_title: Optional[str],
    ) -> dict:
        """Execute a single search strategy."""

        if strategy == "exact":
            if self._escalation is None:
                return {"success": False, "method": "none"}
            return self._escalation.smart_find(
                name=name, role=role, window_title=window_title,
            )

        elif strategy == "partial_name":
            if not name or self._escalation is None:
                return {"success": False, "method": "none"}
            # Split name into words, try each as a search
            words = name.split()
            if len(words) <= 1:
                return {"success": False, "method": "none"}
            for word in words:
                if len(word) < 3:
                    continue
                result = self._escalation.smart_find(
                    name=word, role=role, window_title=window_title,
                )
                if result.get("success"):
                    result["partial_match"] = True
                    result["search_word"] = word
                    result["original_name"] = name
                    return result
            return {"success": False, "method": "none"}

        elif strategy == "all_windows":
            if self._escalation is None:
                return {"success": False, "method": "none"}
            # Search without window filter
            return self._escalation.smart_find(
                name=name, role=role, window_title=None,
            )

        elif strategy == "full_screen_ocr":
            if not name or self._ocr is None:
                return {"success": False, "method": "none"}
            try:
                ocr_result = self._ocr.ocr_region(language="eng")
                if not ocr_result.get("success"):
                    return {"success": False, "method": "ocr",
                            "error": ocr_result.get("error")}

                # Search for text in OCR results
                from marlow.platform.linux.escalation import LinuxEscalationProvider
                match = LinuxEscalationProvider._find_text_in_ocr(
                    name, ocr_result.get("words", []),
                )
                if match:
                    return {
                        "success": True,
                        "method": "ocr",
                        "element": match,
                        "confidence": match.get("confidence", 0) / 100.0,
                    }
                return {"success": False, "method": "ocr"}
            except Exception as e:
                return {"success": False, "method": "ocr", "error": str(e)}

        else:
            logger.warning("Unknown strategy: %s", strategy)
            return {"success": False, "method": "none"}


if __name__ == "__main__":
    from marlow.platform.linux.screenshot import GrimScreenCapture
    from marlow.platform.linux.ui_tree import AtSpiUITreeProvider
    from marlow.platform.linux.ocr import TesseractOCRProvider
    from marlow.platform.linux.escalation import LinuxEscalationProvider

    screen = GrimScreenCapture()
    ui_tree = AtSpiUITreeProvider()
    ocr = TesseractOCRProvider(screen_provider=screen)
    escalation = LinuxEscalationProvider(ui_tree=ui_tree, ocr=ocr, screen=screen)
    provider = LinuxCascadeRecoveryProvider(escalation=escalation, ocr=ocr)

    print("=== LinuxCascadeRecoveryProvider self-test ===")

    # 1. Should find via exact AT-SPI2
    print("\n--- 1. cascade_find(name='Reload') ---")
    r = provider.cascade_find(name="Reload")
    print(f"  success={r.get('success')} strategy={r.get('strategy_used')} "
          f"method={r.get('method', '?')}")
    for a in r.get("attempts", []):
        print(f"    {a['strategy']}: success={a['success']}")

    # 2. Should find via OCR
    print("\n--- 2. cascade_find(name='Example Domain') ---")
    r = provider.cascade_find(name="Example Domain")
    print(f"  success={r.get('success')} strategy={r.get('strategy_used')} "
          f"method={r.get('method', '?')}")
    for a in r.get("attempts", []):
        print(f"    {a['strategy']}: success={a['success']}")

    # 3. Should fail all strategies
    print("\n--- 3. cascade_find(name='xyznonexistent123') ---")
    r = provider.cascade_find(name="xyznonexistent123")
    print(f"  success={r.get('success')} strategy={r.get('strategy_used')}")
    for a in r.get("attempts", []):
        print(f"    {a['strategy']}: success={a['success']}")

    print("\nPASS: LinuxCascadeRecoveryProvider self-test complete")
