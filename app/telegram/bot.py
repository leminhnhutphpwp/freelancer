"""Telegram bot process: python -m app.telegram.bot

Runs long polling only; scheduled notifications are sent by the worker process.
"""

from __future__ import annotations

import asyncio
import contextlib

from telegram import Update
from telegram.ext import Application

from app.config import get_settings
from app.db.session import make_engine, make_session_factory
from app.freelancer.client import FreelancerClient
from app.log import configure_logging, get_logger
from app.telegram.handlers import Deps, register_handlers

log = get_logger(__name__)


async def run_bot() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    if not settings.telegram_bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not configured")
    if not settings.telegram_allowed_chat_ids:
        log.warning("telegram.allowlist_empty - the bot will refuse every chat")

    engine = make_engine(settings.database_url, echo=settings.db_echo)
    session_factory = make_session_factory(engine)
    client = FreelancerClient(
        access_token=settings.freelancer_access_token or "unconfigured",
        base_url=settings.freelancer_base_url,
        timeout=settings.freelancer_timeout_seconds,
    )

    application = Application.builder().token(settings.telegram_bot_token).build()
    application.bot_data["deps"] = Deps(
        settings=settings,
        session_factory=session_factory,
        client=client,
    )
    register_handlers(application)
    await application.initialize()
    await application.start()
    log.info(
        "telegram.bot_started",
        extra={
            "extra_fields": {
                "allowed_chats": settings.telegram_allowed_chat_ids,
            }
        },
    )
    try:
        await application.updater.start_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )
        # Keep alive until cancelled (docker stop).
        stop_event = asyncio.Event()
        try:
            await stop_event.wait()
        finally:
            stop_event.set()
    finally:
        with contextlib.suppress(RuntimeError):
            await application.updater.stop()
        await application.stop()
        await application.shutdown()
        await client.aclose()
        await engine.dispose()


def main() -> None:
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
