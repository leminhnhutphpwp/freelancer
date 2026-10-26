"""Structured JSON logging with secret redaction."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

REDACTED = "***REDACTED***"

# Substrings (lowercased key match) considered sensitive anywhere in payloads.
SENSITIVE_KEY_SUBSTRINGS = (
    "access_token",
    "refreshtoken",
    "refresh_token",
    "client_secret",
    "authorization",
    "oauth",
    "bot_token",
    "apikey",
    "api_key",
    "password",
    "secret",
    "token",
)

_SENSITIVE_MESSAGE_PATTERNS = ("freelancer-oauth-v1", "bearer ")


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(sub in lowered for sub in SENSITIVE_KEY_SUBSTRINGS)


def sanitize(value: Any) -> Any:
    """Recursively redact sensitive keys; truncate long strings."""
    if isinstance(value, dict):
        return {str(k): (REDACTED if is_sensitive_key(str(k)) else sanitize(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(v) for v in value]
    if isinstance(value, str):
        lowered = value.lower()
        if any(pattern in lowered for pattern in _SENSITIVE_MESSAGE_PATTERNS):
            return REDACTED
        if len(value) > 2000:
            return value[:2000] + "…[truncated]"
        return value
    return value


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(sanitize(extra))
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    # Quieten noisy third-party loggers.
    for name in ("httpx", "httpcore", "telegram", "asyncio", "uvicorn.access"):
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
