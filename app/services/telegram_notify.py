"""Minimal Telegram Bot API sender (httpx) usable from API and worker processes.

The interactive bot (callbacks/commands) lives in app.telegram and uses
python-telegram-bot polling; sending notifications only needs sendMessage.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from app.config import Settings
from app.log import get_logger
from app.services.notify import NotificationRequest

log = get_logger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"
MAX_TEXT = 4000  # Telegram hard limit is 4096; keep headroom.


class HttpTelegramNotifier:
    def __init__(self, settings: Settings, *, client: httpx.AsyncClient | None = None) -> None:
        self._token = settings.telegram_bot_token
        self._client = client or httpx.AsyncClient(timeout=15.0)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def send(self, request: NotificationRequest) -> int | None:
        if not self._token:
            log.warning(
                "telegram.notifier_unconfigured_dropped_message",
                extra={
                    "extra_fields": {
                        "chat_id": request.chat_id,
                        "text_preview": request.text[:120],
                    }
                },
            )
            return None
        payload: dict[str, Any] = {
            "chat_id": request.chat_id,
            "text": request.text[:MAX_TEXT],
            "disable_web_page_preview": request.disable_preview,
        }
        if request.buttons:
            payload["reply_markup"] = json.dumps({"inline_keyboard": _keyboard_rows(request.buttons)})
        url = f"{TELEGRAM_API_BASE}/bot{self._token}/sendMessage"
        # Retry on Telegram flood control (429 with retry_after), waiting the requested time.
        for attempt in range(3):
            response = await self._client.post(url, data=payload)
            body = response.json() if response.content else {}
            if body.get("ok"):
                return int(body["result"]["message_id"])
            description = body.get("description") or str(body)[:200]
            parameters = body.get("parameters") or {}
            retry_after = parameters.get("retry_after")
            if response.status_code == 429 and retry_after and attempt < 2:
                log.warning(
                    "telegram.flood_control_retry",
                    extra={
                        "extra_fields": {
                            "chat_id": request.chat_id,
                            "retry_after": retry_after,
                            "attempt": attempt + 1,
                        }
                    },
                )
                await asyncio.sleep(float(retry_after))
                continue
            log.warning(
                "telegram.send_failed",
                extra={
                    "extra_fields": {
                        "chat_id": request.chat_id,
                        "http_status": response.status_code,
                        "description": description,
                        "retry_after": retry_after,
                    }
                },
            )
            raise RuntimeError(f"telegram send failed: {response.status_code} {description}")
        raise RuntimeError("telegram send failed: exhausted retries")


def _keyboard_rows(buttons: list) -> list[list[dict[str, str]]]:
    """Two buttons per row for compact cards."""
    rows: list[list[dict[str, str]]] = []
    current: list[dict[str, str]] = []
    for button in buttons:
        current.append({"text": button.label, "callback_data": button.callback_data})
        if len(current) == 2:
            rows.append(current)
            current = []
    if current:
        rows.append(current)
    return rows


class NullNotifier:
    """Logs instead of sending (used when Telegram is not configured)."""

    async def send(self, request: NotificationRequest) -> int | None:
        log.info(
            "notification.dropped_no_telegram",
            extra={
                "extra_fields": {
                    "chat_id": request.chat_id,
                    "text_preview": request.text[:200],
                }
            },
        )
        return None


def build_notifier(settings: Settings) -> HttpTelegramNotifier | NullNotifier:
    if settings.telegram_bot_token:
        return HttpTelegramNotifier(settings)
    return NullNotifier()
