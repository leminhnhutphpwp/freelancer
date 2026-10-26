#!/usr/bin/env python3
"""Temporary helper: log the chat id of any Telegram update the bot receives.

Run this, then send a message in your target group/chat. The script prints the
chat id (and title for groups) so you can fill TELEGRAM_ALLOWED_CHAT_IDS.

Usage: python scripts/chat_id.py
"""

from __future__ import annotations

import asyncio

from telegram import Update
from telegram.ext import Application, MessageHandler, filters

from app.config import get_settings
from app.log import configure_logging, get_logger

log = get_logger(__name__)


async def _on_update(update: Update, _context) -> None:
    chat = update.effective_chat
    if chat is None:
        return
    chat_type = chat.type
    title = getattr(chat, "title", None) or getattr(chat, "username", None) or "?"
    print(f"\n>>> CHAT ID: {chat.id}  (type={chat_type}, title={title})")
    print(f">>> Add this to TELEGRAM_ALLOWED_CHAT_IDS in .env\n")
    # Also echo so the user sees the bot is alive.
    if update.message and update.message.text:
        await update.message.reply_text(f"Bot nhận được. Chat id của đây là: `{chat.id}`")


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    if not settings.telegram_bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not configured")

    app = Application.builder().token(settings.telegram_bot_token).build()
    app.add_handler(MessageHandler(filters.ALL, _on_update))
    await app.initialize()
    await app.start()
    print("Listening for updates... send a message in your group/chat now.")
    print("Press Ctrl+C to stop.\n")
    try:
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)
        stop = asyncio.Event()
        await stop.wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())