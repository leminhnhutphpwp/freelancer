"""Callback-data grammar and allowlist helpers (pure, unit-tested)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedCallback:
    kind: str
    args: tuple[str, ...]


def parse_callback(data: str) -> ParsedCallback | None:
    """Parse `kind:arg1:arg2` callback data. Returns None when malformed."""
    if not data or ":" not in data:
        return None
    kind, _, rest = data.partition(":")
    kind = kind.strip().lower()
    args = tuple(part.strip() for part in rest.split(":") if part.strip())
    if kind in {"proj", "reply"} and len(args) == 1:
        if args[0].isdigit():
            return ParsedCallback(kind, args)
        return None
    if kind in {"bid", "skip"} and len(args) == 2:
        if args[0].isdigit() and args[1].isdigit():
            return ParsedCallback(kind, args)
        return None
    if kind in {"awardaccept", "awarddeny"} and len(args) == 1:
        if args[0].isdigit():
            return ParsedCallback(kind, args)
        return None
    if kind in {"ok", "no"} and len(args) == 1 and args[0]:
        return ParsedCallback(kind, args)
    return None


def is_authorized(
    allowed_chat_ids: list[int],
    chat_id: int | None,
    user_id: int | None = None,
) -> bool:
    """Chat allowlist check. If TELEGRAM_ALLOWED_CHAT_IDS is empty, deny everything."""
    if not allowed_chat_ids:
        return False
    if chat_id is not None and int(chat_id) in {int(x) for x in allowed_chat_ids}:
        return True
    return False
