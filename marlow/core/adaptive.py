"""
Marlow Adaptive Behavior — Pattern Detection

Detects repeating action patterns from tool call history and suggests
them to the user. Patterns are stored persistently in ~/.marlow/memory/.

/ Detecta patrones repetitivos de acciones y los sugiere al usuario.
"""

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from marlow.core.config import CONFIG_DIR

logger = logging.getLogger("marlow.core.adaptive")

PATTERNS_FILE = CONFIG_DIR / "memory" / "patterns.json"

# Only extract meaningful params — skip ephemeral ones like quality, timeout
_KEY_PARAMS = {"window_title", "app_name", "element_name", "text", "command"}

# Min/max sequence lengths to scan
_MIN_SEQ = 2
_MAX_SEQ = 10

# How many times a sequence must appear to be considered a pattern
_MIN_FREQUENCY = 3

# Max actions in the rolling buffer
_MAX_BUFFER = 500


class PatternDetector:
    """
    Detects repeating action sequences from tool call history.

    / Detecta secuencias repetitivas de llamadas a herramientas.
    """

    def __init__(self):
        self._actions: list[dict] = []

    # ── Recording ──────────────────────────────────────────────

    def record_action(self, tool: str, params: dict) -> None:
        """
        Record a tool call for pattern analysis.

        / Registra una llamada a herramienta para analisis de patrones.
        """
        key_params = {k: v for k, v in params.items() if k in _KEY_PARAMS and v}
        self._actions.append({
            "tool": tool,
            "params": key_params,
            "timestamp": datetime.now().isoformat(),
        })
        # Trim buffer
        if len(self._actions) > _MAX_BUFFER:
            self._actions = self._actions[-_MAX_BUFFER:]

    # ── Analysis ───────────────────────────────────────────────

    def _make_signature(self, action: dict) -> tuple:
        """Create a hashable signature from an action."""
        params_tuple = tuple(sorted(action["params"].items()))
        return (action["tool"], params_tuple)

    def _analyze_patterns(self) -> list[dict]:
        """
        Scan action buffer for repeating subsequences of length 2-10.

        / Escanea el buffer de acciones buscando subsecuencias repetidas.
        """
        if len(self._actions) < _MIN_SEQ * _MIN_FREQUENCY:
            return []

        signatures = [self._make_signature(a) for a in self._actions]
        found: dict[tuple, int] = {}  # signature_tuple -> count

        for window_size in range(_MIN_SEQ, min(_MAX_SEQ + 1, len(signatures) + 1)):
            for i in range(len(signatures) - window_size + 1):
                seq = tuple(signatures[i:i + window_size])
                found[seq] = found.get(seq, 0) + 1

        # Filter to patterns with enough frequency
        patterns = []
        existing = self._load_patterns()
        existing_sigs = {
            tuple(
                (s["tool"], tuple(sorted(s["params"].items())))
                for s in p["sequence"]
            )
            for p in existing
        }

        for seq_sig, count in found.items():
            if count < _MIN_FREQUENCY:
                continue
            # Skip single-action sequences that are just the same tool repeated
            if len(set(seq_sig)) == 1 and len(seq_sig) == 2:
                continue
            if seq_sig in existing_sigs:
                # Update frequency on existing pattern
                for p in existing:
                    p_sig = tuple(
                        (s["tool"], tuple(sorted(s["params"].items())))
                        for s in p["sequence"]
                    )
                    if p_sig == seq_sig:
                        p["frequency"] = count
                        p["last_seen"] = datetime.now().isoformat()
                        break
                continue

            # New pattern
            sequence = []
            for tool_name, params_tuple in seq_sig:
                sequence.append({
                    "tool": tool_name,
                    "params": dict(params_tuple),
                })

            patterns.append({
                "id": uuid.uuid4().hex[:8],
                "sequence": sequence,
                "frequency": count,
                "first_seen": datetime.now().isoformat(),
                "last_seen": datetime.now().isoformat(),
                "dismissed": False,
                "accepted": False,
            })

        # Merge new patterns with existing
        if patterns:
            existing.extend(patterns)
            self._save_patterns(existing)

        return existing

    # ── Persistence ────────────────────────────────────────────

    def _load_patterns(self) -> list[dict]:
        """Load patterns from disk."""
        if PATTERNS_FILE.exists():
            try:
                return json.loads(PATTERNS_FILE.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to load patterns: {e}")
        return []

    def _save_patterns(self, patterns: list[dict]) -> None:
        """Save patterns to disk."""
        PATTERNS_FILE.parent.mkdir(parents=True, exist_ok=True)
        PATTERNS_FILE.write_text(
            json.dumps(patterns, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ── Pattern lookup ─────────────────────────────────────────

    def _find_pattern(self, pattern_id: str) -> Optional[dict]:
        """Find a pattern by ID."""
        patterns = self._load_patterns()
        for p in patterns:
            if p["id"] == pattern_id:
                return p
        return None

    def _update_pattern(self, pattern_id: str, **updates) -> bool:
        """Update a pattern and save."""
        patterns = self._load_patterns()
        for p in patterns:
            if p["id"] == pattern_id:
                p.update(updates)
                self._save_patterns(patterns)
                return True
        return False


# Module-level singleton
_detector = PatternDetector()


# ─────────────────────────────────────────────────────────────
# MCP Tools
# ─────────────────────────────────────────────────────────────

async def get_suggestions() -> dict:
    """
    Analyze recorded actions and return pattern suggestions.
    Filters out dismissed patterns.

    / Analiza acciones grabadas y retorna sugerencias de patrones.
    """
    try:
        patterns = _detector._analyze_patterns()
        suggestions = [
            p for p in patterns
            if not p.get("dismissed", False)
        ]
        return {
            "success": True,
            "suggestions": suggestions,
            "total_patterns": len(patterns),
            "actions_recorded": len(_detector._actions),
        }
    except Exception as e:
        logger.error(f"get_suggestions error: {e}")
        return {"error": str(e)}


async def accept_suggestion(pattern_id: str) -> dict:
    """
    Mark a pattern suggestion as accepted.

    / Marca una sugerencia de patron como aceptada.
    """
    try:
        if _detector._update_pattern(pattern_id, accepted=True):
            return {
                "success": True,
                "pattern_id": pattern_id,
                "status": "accepted",
            }
        return {"error": f"Pattern not found: {pattern_id}"}
    except Exception as e:
        logger.error(f"accept_suggestion error: {e}")
        return {"error": str(e)}


async def dismiss_suggestion(pattern_id: str) -> dict:
    """
    Mark a pattern suggestion as dismissed (won't appear in future suggestions).

    / Marca una sugerencia como descartada (no aparecera en futuras sugerencias).
    """
    try:
        if _detector._update_pattern(pattern_id, dismissed=True):
            return {
                "success": True,
                "pattern_id": pattern_id,
                "status": "dismissed",
            }
        return {"error": f"Pattern not found: {pattern_id}"}
    except Exception as e:
        logger.error(f"dismiss_suggestion error: {e}")
        return {"error": str(e)}
