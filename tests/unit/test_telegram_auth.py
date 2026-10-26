"""Unit tests for Telegram callback parsing and allowlist."""

from __future__ import annotations

from app.telegram.auth import is_authorized, parse_callback


def test_parse_project_callback():
    parsed = parse_callback("proj:123")
    assert parsed is not None
    assert parsed.kind == "proj"
    assert parsed.args == ("123",)


def test_parse_bid_callback():
    parsed = parse_callback("bid:123:5")
    assert parsed is not None
    assert parsed.kind == "bid"
    assert parsed.args == ("123", "5")


def test_parse_confirm_callback():
    parsed = parse_callback("ok:abc123")
    assert parsed is not None
    assert parsed.kind == "ok"
    assert parsed.args == ("abc123",)


def test_parse_reply_callback():
    parsed = parse_callback("reply:42")
    assert parsed is not None
    assert parsed.kind == "reply"


def test_parse_award_callback():
    assert parse_callback("awardaccept:9").kind == "awardaccept"
    assert parse_callback("awarddeny:9").kind == "awarddeny"


def test_parse_malformed():
    assert parse_callback("") is None
    assert parse_callback("nocolon") is None
    assert parse_callback("proj:") is None
    assert parse_callback("proj:abc") is None  # non-numeric
    assert parse_callback("bid:1") is None  # needs two args


def test_allowlist():
    assert is_authorized([111, 222], 111) is True
    assert is_authorized([111, 222], 333) is False
    assert is_authorized([], 111) is False  # empty allowlist denies everything
    assert is_authorized([111], None) is False
