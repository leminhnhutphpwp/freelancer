"""Unit tests for config parsing and secret redaction."""

from __future__ import annotations

from app.config import Settings
from app.log import sanitize


def test_chat_ids_parsing():
    s = Settings(telegram_allowed_chat_ids="111, 222;333")
    assert s.telegram_allowed_chat_ids == [111, 222, 333]


def test_defaults():
    s = Settings()
    assert s.freelancer_base_url == "https://www.freelancer.com/api"
    assert s.is_production is False


def test_production_flag():
    assert Settings(environment="production").is_production is True


def test_sanitize_redacts_sensitive_keys():
    payload = {
        "access_token": "secret123",
        "freelancer-oauth-v1": "secret456",
        "result": {"ok": True},
        "nested": {"client_secret": "abc", "safe": "keep"},
    }
    cleaned = sanitize(payload)
    assert cleaned["access_token"] == "***REDACTED***"
    assert cleaned["freelancer-oauth-v1"] == "***REDACTED***"
    assert cleaned["nested"]["client_secret"] == "***REDACTED***"
    assert cleaned["nested"]["safe"] == "keep"
    assert cleaned["result"]["ok"] is True


def test_sanitize_truncates_long_strings():
    cleaned = sanitize({"x": "a" * 5000})
    assert len(cleaned["x"]) <= 2000 + len("…[truncated]")


def test_sanitize_redacts_bearer_in_message():
    cleaned = sanitize({"message": "Authorization: Bearer abcdef"})
    assert cleaned["message"] == "***REDACTED***"
