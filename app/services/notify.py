"""Notification abstraction so services stay free of Telegram SDK imports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol


@dataclass
class Button:
    label: str
    callback_data: str


@dataclass
class NotificationRequest:
    chat_id: int
    text: str
    buttons: list[Button] = field(default_factory=list)
    disable_preview: bool = True


class Notifier(Protocol):
    async def send(self, request: NotificationRequest) -> int | None:
        """Deliver a notification; return the telegram message id when available."""
        ...


class RecordingNotifier:
    """In-memory notifier used by tests and rule 'dry run'."""

    def __init__(self) -> None:
        self.sent: list[NotificationRequest] = []

    async def send(self, request: NotificationRequest) -> int | None:
        self.sent.append(request)
        return None


def ts_to_datetime(ts: int | float | None) -> datetime | None:
    if not ts:
        return None
    return datetime.fromtimestamp(float(ts), tz=UTC)
