"""Chat threads visibility for the REST API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_client, session_dependency
from app.api.security import require_admin_key
from app.db.models import Thread
from app.freelancer.client import FreelancerClient

router = APIRouter(prefix="/threads", tags=["threads"], dependencies=[Depends(require_admin_key)])


@router.get("")
async def list_threads(
    session: AsyncSession = Depends(session_dependency),
) -> list[dict[str, Any]]:
    threads = list(await session.scalars(select(Thread).order_by(Thread.last_message_at.desc().nullslast()).limit(100)))
    return [
        {
            "id": t.id,
            "context_type": t.context_type,
            "context_id": t.context_id,
            "members": t.members,
            "last_message_at": t.last_message_at,
        }
        for t in threads
    ]


@router.get("/{thread_id}/messages")
async def thread_messages(
    thread_id: int,
    limit: int = 20,
    session: AsyncSession = Depends(session_dependency),
    client: FreelancerClient = Depends(get_client),
) -> list[dict[str, Any]]:
    thread = await session.get(Thread, thread_id)
    if thread is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "thread not synced yet (worker fetches periodically)")
    return await client.list_messages(thread_id, params={"limit": limit})
