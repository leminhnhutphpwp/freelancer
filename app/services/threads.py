"""Freelancer chat threads: inbound notification + drafted replies (confirm before send)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ActionStatus, ActionType, Notification, PendingAction, Thread, utcnow
from app.freelancer.client import FreelancerClient
from app.log import get_logger
from app.services import actions as action_service
from app.services.notify import Button, NotificationRequest, Notifier

log = get_logger(__name__)


def _thread_context(thread: dict[str, Any]) -> tuple[str | None, int | None]:
    ctx = thread.get("context") or {}
    if isinstance(ctx, dict):
        ctype = ctx.get("type")
        cid = ctx.get("id")
        return (ctype if ctype and ctype != "none" else None, int(cid) if cid else None)
    return None, None


def _inner_thread(thread: dict[str, Any]) -> dict[str, Any]:
    t = thread.get("thread")
    return t if isinstance(t, dict) else thread


async def sync_threads(
    session: AsyncSession,
    client: FreelancerClient,
    *,
    my_user_id: int,
    notifier: Notifier | None,
    chat_ids: list[int],
    ttl_seconds: int = 900,
) -> int:
    """Fetch recently updated threads, persist them, notify new inbound messages.

    Returns number of new inbound notifications sent.
    """
    threads = await client.list_threads(
        {
            "last_message": True,
            "user_details": True,
            "context_details": True,
            "limit": 50,
            "sort_field": "time_updated",
        }
    )
    inbound_count = 0
    for entry in threads:
        inner = _inner_thread(entry)
        thread_id = inner.get("id") or entry.get("id")
        if thread_id is None:
            continue
        thread_id = int(thread_id)
        members = [int(m) for m in (inner.get("members") or []) if str(m).lstrip("-").isdigit()]
        ctype, cid = _thread_context(entry if "context" in entry else inner)
        row = await session.get(Thread, thread_id)
        if row is None:
            row = Thread(id=thread_id, members=members, context_type=ctype, context_id=cid)
            session.add(row)
        else:
            row.members = members
            row.context_type = row.context_type or ctype
            row.context_id = row.context_id or cid

        last_message = entry.get("message") or inner.get("message") or {}
        message_id = last_message.get("id")
        from_user = last_message.get("from_user")
        ts = last_message.get("time_created")
        if ts:
            row.last_message_at = datetime.fromtimestamp(float(ts), tz=UTC)
        else:
            row.last_message_at = row.last_message_at or utcnow()

        is_inbound = (
            message_id is not None
            and from_user is not None
            and int(from_user) != int(my_user_id)
            and row.last_inbound_notified_message_id != int(message_id)
        )
        if is_inbound:
            row.last_seen_message_id = int(message_id)
            await session.commit()
            if notifier is not None and chat_ids:
                sender = str(from_user)
                users = entry.get("users") or inner.get("users") or []
                for user in users:
                    if isinstance(user, dict) and str(user.get("id")) == sender:
                        sender = user.get("display_name") or user.get("username") or sender
                        break
                text = "\n".join(
                    [
                        f"💬 Tin nhắn mới từ {sender}",
                        f"Thread #{thread_id}" + (f" · {ctype} #{cid}" if ctype else ""),
                        "",
                        str(last_message.get("message") or "(nội dung)")[:1200],
                        "",
                        "Dùng nút Reply (soạn trước, có bước xác nhận).",
                    ]
                )
                buttons = [Button("↩️ Soạn trả lời", f"reply:{thread_id}")]
                for chat_id in chat_ids:
                    telegram_message_id = await notifier.send(
                        NotificationRequest(chat_id=chat_id, text=text, buttons=buttons)
                    )
                    session.add(
                        Notification(
                            kind="inbound_message",
                            thread_id=thread_id,
                            chat_id=chat_id,
                            telegram_message_id=telegram_message_id,
                            text=text,
                        )
                    )
                    inbound_count += 1
        if message_id is not None:
            row.last_inbound_notified_message_id = int(message_id)
        await session.commit()
    return inbound_count


async def create_reply_draft(
    session: AsyncSession,
    *,
    thread_id: int,
    text: str,
    chat_id: int,
    ttl_seconds: int,
) -> PendingAction:
    payload = {
        "thread_id": thread_id,
        "message": text,
        "client_message_id": f"dsh-{utcnow().strftime('%Y%m%d%H%M%S')}-{thread_id}",
    }
    preview = action_service.build_message_preview({"id": thread_id}, text)
    return await action_service.create_action(
        session,
        action_type=ActionType.MESSAGE_SEND,
        payload=payload,
        preview_text=preview,
        chat_id=chat_id,
        thread_id=thread_id,
        ttl_seconds=ttl_seconds,
    )


async def get_latest_reply_draft(session: AsyncSession, chat_id: int) -> PendingAction | None:
    return await session.scalar(
        select(PendingAction)
        .where(
            PendingAction.chat_id == chat_id,
            PendingAction.action_type == ActionType.MESSAGE_SEND,
            PendingAction.status == ActionStatus.PENDING_CONFIRMATION,
        )
        .order_by(PendingAction.id.desc())
        .limit(1)
    )


async def recent_threads(session: AsyncSession, limit: int = 10) -> list[Thread]:
    return list(await session.scalars(select(Thread).order_by(Thread.last_message_at.desc().nullslast()).limit(limit)))
