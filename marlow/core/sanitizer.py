"""
Marlow Data Sanitizer

Detects and redacts sensitive information BEFORE it leaves the machine.
Credit cards, SSNs, passwords, emails — all replaced with [REDACTED].

This is what OpenClaw should have had from day 1.
"""

import re
import logging

from marlow.core.config import MarlowConfig

logger = logging.getLogger("marlow.sanitizer")


class DataSanitizer:
    """
    Scans text for sensitive data patterns and redacts them.
    
    Used automatically on:
    - UI Tree text content
    - OCR results
    - Clipboard content
    - Command output
    - Any text before it's returned to the caller
    """

    # Replacement markers
    REDACTED = "[REDACTED]"
    REDACTED_CC = "[CREDIT-CARD-REDACTED]"
    REDACTED_SSN = "[SSN-REDACTED]"
    REDACTED_EMAIL = "[EMAIL-REDACTED]"
    REDACTED_PHONE = "[PHONE-REDACTED]"
    REDACTED_PWD = "[PASSWORD-FIELD]"

    def __init__(self, config: MarlowConfig):
        self.config = config
        self._patterns: dict[str, re.Pattern] = {}
        self._compile_patterns()
        self._redaction_count = 0

    def _compile_patterns(self):
        """Compile regex patterns from config."""
        for name, pattern in self.config.security.sensitive_patterns.items():
            try:
                self._patterns[name] = re.compile(pattern)
            except re.error as e:
                logger.error(f"Invalid regex pattern '{name}': {e}")

    def sanitize(self, text: str) -> str:
        """
        Scan text for sensitive data and redact it.
        
        Returns sanitized text with sensitive data replaced.
        """
        if not text:
            return text

        sanitized = text
        redactions_made = 0

        # Apply each pattern
        for name, pattern in self._patterns.items():
            replacement = self._get_replacement(name)
            new_text, count = pattern.subn(replacement, sanitized)
            if count > 0:
                redactions_made += count
                sanitized = new_text

        if redactions_made > 0:
            self._redaction_count += redactions_made
            logger.info(f"🔒 Sanitized {redactions_made} sensitive data match(es)")

        return sanitized

    def sanitize_ui_tree(self, tree_data: dict) -> dict:
        """
        Recursively sanitize all string values in a UI tree dictionary.
        """
        if isinstance(tree_data, str):
            return self.sanitize(tree_data)
        elif isinstance(tree_data, dict):
            return {k: self.sanitize_ui_tree(v) for k, v in tree_data.items()}
        elif isinstance(tree_data, list):
            return [self.sanitize_ui_tree(item) for item in tree_data]
        return tree_data

    def is_password_field(self, control_type: str, properties: dict) -> bool:
        """
        Check if a UI element is a password field.
        These should never have their content read or sent to AI.
        """
        # Check control type
        if "password" in str(control_type).lower():
            return True

        # Check common password field indicators
        name = str(properties.get("name", "")).lower()
        auto_id = str(properties.get("automation_id", "")).lower()
        class_name = str(properties.get("class_name", "")).lower()

        password_indicators = [
            "password", "passwd", "pwd", "pin", "secret",
            "contraseña", "clave",  # Spanish
        ]

        for indicator in password_indicators:
            if indicator in name or indicator in auto_id or indicator in class_name:
                return True

        return False

    def _get_replacement(self, pattern_name: str) -> str:
        """Get the appropriate replacement text for a pattern type."""
        replacements = {
            "credit_card": self.REDACTED_CC,
            "ssn": self.REDACTED_SSN,
            "email": self.REDACTED_EMAIL,
            "phone_us": self.REDACTED_PHONE,
            "password_field": self.REDACTED_PWD,
        }
        return replacements.get(pattern_name, self.REDACTED)

    @property
    def total_redactions(self) -> int:
        """Total number of redactions made since startup."""
        return self._redaction_count

    def get_stats(self) -> dict:
        """Get sanitizer statistics."""
        return {
            "total_redactions": self._redaction_count,
            "active_patterns": list(self._patterns.keys()),
            "patterns_count": len(self._patterns),
        }
