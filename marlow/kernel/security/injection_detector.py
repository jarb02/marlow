"""InjectionDetector — detects prompt injection in external content.

Scans text from web pages (CDP), documents (COM), clipboard, emails,
files — anything Marlow reads from the outside world before passing
to an LLM prompt.

Uses pattern matching + spotlighting (mark untrusted input boundaries).

Based on:
- Spotlighting: mark user input vs system instructions
- Instruction hierarchy: system > user > context
- OWASP ASI01 Agent Goal Hijack defense

/ Detector de inyeccion de prompts en contenido externo.
"""

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger("marlow.kernel.security.injection_detector")


@dataclass(frozen=True)
class InjectionDetection:
    """Result of injection detection scan."""

    is_suspicious: bool
    threat_level: str = "none"  # none, low, medium, high
    patterns_found: tuple = ()  # which patterns matched
    sanitized_text: str = ""    # text with injections neutralized


class InjectionDetector:
    """Detects prompt injection attempts in content read from external sources.

    Scans text from: web pages (CDP), documents (COM), clipboard, emails, files.
    Uses pattern matching + structural analysis.
    """

    # Patterns that suggest prompt injection
    INJECTION_PATTERNS: list[tuple[str, str]] = [
        # Direct instruction injection
        (r"ignore\s+(all\s+)?previous\s+instructions", "ignore_previous"),
        (r"ignore\s+(all\s+)?above", "ignore_above"),
        (r"disregard\s+(all\s+)?previous", "disregard_previous"),
        (r"forget\s+(all\s+)?previous", "forget_previous"),
        (r"new\s+instructions?\s*:", "new_instructions"),
        (r"system\s*prompt\s*:", "system_prompt"),
        (r"you\s+are\s+now\s+a", "role_override"),
        (r"act\s+as\s+(if\s+you\s+are|a)", "role_override"),
        (r"pretend\s+you\s+are", "role_override"),

        # Tool/action injection
        (r"execute\s+(this\s+)?command", "command_injection"),
        (r"run\s+(this\s+)?command", "command_injection"),
        (r"open\s+powershell\s+and", "command_injection"),
        (r"delete\s+all\s+files", "destructive_injection"),
        (r"format\s+(the\s+)?drive", "destructive_injection"),
        (r"send\s+(this\s+)?(email|message)\s+to", "exfiltration_injection"),
        (r"upload\s+(this|the)\s+(file|data)\s+to", "exfiltration_injection"),
        (r"copy\s+(all|the)\s+(files?|data)\s+to", "exfiltration_injection"),

        # Obfuscation attempts
        (r"base64\s*:\s*[A-Za-z0-9+/=]{20,}", "base64_payload"),
        (r"\\x[0-9a-f]{2}(\\x[0-9a-f]{2}){5,}", "hex_encoded"),
        (r"\{\\rtf", "rtf_injection"),
    ]

    # Spotlighting delimiters
    SPOTLIGHT_PREFIX = "<<<EXTERNAL_CONTENT>>>"
    SPOTLIGHT_SUFFIX = "<<<END_EXTERNAL_CONTENT>>>"

    def __init__(self):
        self._compiled_patterns = [
            (re.compile(p, re.IGNORECASE), name)
            for p, name in self.INJECTION_PATTERNS
        ]

    def scan(self, text: str, source: str = "unknown") -> InjectionDetection:
        """Scan text for prompt injection patterns.

        Args:
            text: content to scan
            source: where it came from (web, clipboard, document, etc.)
        """
        if not text or not text.strip():
            return InjectionDetection(is_suspicious=False, sanitized_text=text or "")

        found: list[str] = []
        for pattern, name in self._compiled_patterns:
            if pattern.search(text):
                found.append(name)

        if not found:
            return InjectionDetection(
                is_suspicious=False,
                threat_level="none",
                sanitized_text=text,
            )

        # Determine threat level
        high_threats = {"destructive_injection", "exfiltration_injection", "base64_payload"}
        medium_threats = {"command_injection", "role_override", "ignore_previous", "system_prompt"}

        if any(f in high_threats for f in found):
            level = "high"
        elif any(f in medium_threats for f in found):
            level = "medium"
        else:
            level = "low"

        logger.warning(
            "Injection detected in %s: level=%s, patterns=%s",
            source, level, found,
        )

        return InjectionDetection(
            is_suspicious=True,
            threat_level=level,
            patterns_found=tuple(found),
            sanitized_text=self.spotlight(text),
        )

    def spotlight(self, text: str) -> str:
        """Wrap external content in spotlighting delimiters.

        This makes it clear to the LLM that this is untrusted input.
        """
        return f"{self.SPOTLIGHT_PREFIX}\n{text}\n{self.SPOTLIGHT_SUFFIX}"

    def neutralize(self, text: str) -> str:
        """Remove or neutralize known injection patterns from text.

        Use when the content needs to be passed to LLM as context.
        """
        result = text
        for pattern, name in self._compiled_patterns:
            result = pattern.sub(f"[REDACTED:{name}]", result)
        return result
