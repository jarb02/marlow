"""
Tests for Marlow DataSanitizer — redacts sensitive data before it leaves the machine.

Credit cards, SSNs, emails, phone numbers, and password fields are all
detected and replaced with [REDACTED] markers BEFORE being returned to the caller.
"""

import pytest

from marlow.core.config import MarlowConfig
from marlow.core.sanitizer import DataSanitizer


@pytest.fixture
def sanitizer():
    """Fresh DataSanitizer with default config."""
    config = MarlowConfig()
    return DataSanitizer(config)


# ─────────────────────────────────────────────────────────────
# Credit Cards
# ─────────────────────────────────────────────────────────────

class TestCreditCardRedaction:
    """Credit card numbers must never reach the caller."""

    @pytest.mark.parametrize("cc", [
        "4532123456789012",        # No separators
        "4532-1234-5678-9012",     # Dashes
        "4532 1234 5678 9012",     # Spaces
    ])
    def test_credit_card_redacted(self, sanitizer, cc):
        result = sanitizer.sanitize(f"Card: {cc}")
        assert "[CREDIT-CARD-REDACTED]" in result
        assert cc not in result

    def test_partial_card_not_redacted(self, sanitizer):
        """Short numbers should not be treated as credit cards."""
        result = sanitizer.sanitize("Order #12345678")
        assert "[CREDIT-CARD-REDACTED]" not in result

    def test_card_in_context(self, sanitizer):
        text = "Please charge 4532-1234-5678-9012 for the order"
        result = sanitizer.sanitize(text)
        assert "Please charge" in result
        assert "[CREDIT-CARD-REDACTED]" in result
        assert "4532" not in result


# ─────────────────────────────────────────────────────────────
# SSN
# ─────────────────────────────────────────────────────────────

class TestSSNRedaction:
    """Social Security Numbers must be redacted."""

    def test_ssn_redacted(self, sanitizer):
        result = sanitizer.sanitize("SSN: 123-45-6789")
        assert "[SSN-REDACTED]" in result
        assert "123-45-6789" not in result

    def test_ssn_in_form_data(self, sanitizer):
        text = "Name: John Doe\nSSN: 987-65-4321\nDOB: 1990-01-01"
        result = sanitizer.sanitize(text)
        assert "[SSN-REDACTED]" in result
        assert "987-65-4321" not in result
        # Non-SSN data preserved
        assert "John Doe" in result


# ─────────────────────────────────────────────────────────────
# Email
# ─────────────────────────────────────────────────────────────

class TestEmailRedaction:
    """Email addresses are redacted."""

    @pytest.mark.parametrize("email", [
        "user@example.com",
        "john.doe@company.org",
        "test+tag@sub.domain.co.uk",
    ])
    def test_email_redacted(self, sanitizer, email):
        result = sanitizer.sanitize(f"Contact: {email}")
        assert "[EMAIL-REDACTED]" in result
        assert email not in result

    def test_non_email_preserved(self, sanitizer):
        result = sanitizer.sanitize("Visit example.com for details")
        assert "[EMAIL-REDACTED]" not in result


# ─────────────────────────────────────────────────────────────
# Phone Numbers
# ─────────────────────────────────────────────────────────────

class TestPhoneRedaction:
    """US phone numbers are redacted."""

    @pytest.mark.parametrize("phone", [
        "(555) 123-4567",
        "555-123-4567",
        "555 123 4567",
        "+1 555-123-4567",
    ])
    def test_phone_redacted(self, sanitizer, phone):
        result = sanitizer.sanitize(f"Call: {phone}")
        assert "[PHONE-REDACTED]" in result
        assert phone not in result


# ─────────────────────────────────────────────────────────────
# Password Fields
# ─────────────────────────────────────────────────────────────

class TestPasswordFieldDetection:
    """Password-related field names are flagged."""

    @pytest.mark.parametrize("keyword", [
        "password", "passwd", "pwd", "secret", "token", "api_key", "apikey",
        "api-key",
    ])
    def test_password_keywords_detected(self, sanitizer, keyword):
        result = sanitizer.sanitize(f"{keyword}: some_value")
        assert "[PASSWORD-FIELD]" in result

    def test_case_insensitive(self, sanitizer):
        result = sanitizer.sanitize("PASSWORD: hunter2")
        assert "[PASSWORD-FIELD]" in result


# ─────────────────────────────────────────────────────────────
# UI Tree Sanitization
# ─────────────────────────────────────────────────────────────

class TestUITreeSanitization:
    """sanitize_ui_tree recursively sanitizes all strings in a dict."""

    def test_nested_dict_sanitized(self, sanitizer):
        tree = {
            "name": "Credit Card Field",
            "value": "4532-1234-5678-9012",
            "children": [
                {"name": "SSN", "value": "123-45-6789"},
            ],
        }

        result = sanitizer.sanitize_ui_tree(tree)

        assert "[CREDIT-CARD-REDACTED]" in result["value"]
        assert "[SSN-REDACTED]" in result["children"][0]["value"]

    def test_non_string_values_preserved(self, sanitizer):
        tree = {
            "is_enabled": True,
            "position": {"x": 100, "y": 200},
            "count": 42,
        }

        result = sanitizer.sanitize_ui_tree(tree)
        assert result["is_enabled"] is True
        assert result["position"]["x"] == 100
        assert result["count"] == 42

    def test_list_in_tree_sanitized(self, sanitizer):
        tree = {
            "items": ["user@example.com", "no-sensitive-data", "123-45-6789"],
        }

        result = sanitizer.sanitize_ui_tree(tree)
        assert "[EMAIL-REDACTED]" in result["items"][0]
        assert result["items"][1] == "no-sensitive-data"
        assert "[SSN-REDACTED]" in result["items"][2]

    def test_empty_dict(self, sanitizer):
        assert sanitizer.sanitize_ui_tree({}) == {}

    def test_empty_string(self, sanitizer):
        assert sanitizer.sanitize("") == ""

    def test_none_passthrough(self, sanitizer):
        assert sanitizer.sanitize_ui_tree(None) is None


# ─────────────────────────────────────────────────────────────
# Password Field UI Detection
# ─────────────────────────────────────────────────────────────

class TestPasswordFieldUI:
    """is_password_field detects password input elements."""

    def test_password_control_type(self, sanitizer):
        assert sanitizer.is_password_field("PasswordBox", {})

    def test_password_in_name(self, sanitizer):
        assert sanitizer.is_password_field("Edit", {"name": "Password"})

    def test_password_in_automation_id(self, sanitizer):
        assert sanitizer.is_password_field("Edit", {"automation_id": "txtPassword"})

    def test_spanish_password(self, sanitizer):
        assert sanitizer.is_password_field("Edit", {"name": "Contraseña"})

    def test_pin_field(self, sanitizer):
        assert sanitizer.is_password_field("Edit", {"name": "Enter PIN"})

    def test_regular_field_not_flagged(self, sanitizer):
        assert not sanitizer.is_password_field("Edit", {"name": "Username"})


# ─────────────────────────────────────────────────────────────
# Stats
# ─────────────────────────────────────────────────────────────

class TestSanitizerStats:
    """Sanitizer tracks redaction statistics."""

    def test_initial_count_zero(self, sanitizer):
        assert sanitizer.total_redactions == 0

    def test_count_increments(self, sanitizer):
        sanitizer.sanitize("Card: 4532-1234-5678-9012")
        assert sanitizer.total_redactions >= 1

    def test_get_stats(self, sanitizer):
        stats = sanitizer.get_stats()
        assert "total_redactions" in stats
        assert "active_patterns" in stats
        assert "patterns_count" in stats
        assert stats["patterns_count"] > 0

    def test_multiple_redactions_counted(self, sanitizer):
        sanitizer.sanitize("Cards: 4532-1234-5678-9012 and 4532-9876-5432-1098")
        assert sanitizer.total_redactions >= 2
