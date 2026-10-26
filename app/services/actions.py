"""PendingAction lifecycle: draft -> pending_confirmation -> executing -> succeeded/failed.

Guarantees:
- a confirmation token can only be redeemed once (atomic conditional UPDATE);
- expired actions cannot be executed;
- execution failures move the action to `failed` and are surfaced, never retried silently.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ActionStatus, ActionType, Bid, PendingAction, utcnow
from app.freelancer.client import FreelancerClient, FreelancerError
from app.log import get_logger, sanitize

log = get_logger(__name__)

# Statuses that may be moved to EXECUTING by a confirmation.
_CONFIRMABLE = {ActionStatus.PENDING_CONFIRMATION}


class ActionError(Exception):
    """Business error while creating/confirming/executing an action."""


async def create_action(
    session: AsyncSession,
    *,
    action_type: ActionType,
    payload: dict[str, Any],
    preview_text: str,
    chat_id: int | None,
    project_id: int | None = None,
    bid_row_id: int | None = None,
    thread_id: int | None = None,
    ttl_seconds: int = 900,
    status: ActionStatus = ActionStatus.PENDING_CONFIRMATION,
) -> PendingAction:
    if action_type.value not in {t.value for t in ActionType}:  # defensive, enum already validated
        raise ActionError(f"unknown action type {action_type}")
    action = PendingAction(
        action_type=action_type,
        status=status,
        payload=payload,
        preview_text=preview_text,
        chat_id=chat_id,
        project_id=project_id,
        bid_id=bid_row_id,
        thread_id=thread_id,
        expires_at=utcnow() + timedelta(seconds=ttl_seconds),
    )
    session.add(action)
    await session.commit()
    await session.refresh(action)
    return action


async def get_action_by_token(session: AsyncSession, token: str) -> PendingAction | None:
    return await session.scalar(select(PendingAction).where(PendingAction.token == token))


async def cancel(session: AsyncSession, action: PendingAction) -> PendingAction:
    if action.status not in {ActionStatus.DRAFT, ActionStatus.PENDING_CONFIRMATION}:
        raise ActionError(f"cannot cancel action in status {action.status}")
    action.status = ActionStatus.CANCELLED
    await session.commit()
    return action


async def mark_executed_fields(session: AsyncSession, action: PendingAction, **fields: Any) -> None:
    for key, value in fields.items():
        setattr(action, key, value)
    await session.commit()


async def redeem_and_claim(session: AsyncSession, token: str, *, chat_id: int | None) -> PendingAction:
    """Atomically move pending_confirmation -> executing. Raises ActionError if not possible.

    Uses a conditional UPDATE so concurrent double-clicks of the same confirm button
    (or API + Telegram racing) can only succeed once.
    """
    stmt = (
        update(PendingAction)
        .where(
            PendingAction.token == token,
            PendingAction.status.in_(_CONFIRMABLE),
            PendingAction.expires_at > utcnow(),
        )
        .values(status=ActionStatus.EXECUTING, confirmed_at=utcnow(), attempts=PendingAction.attempts + 1)
    )
    if chat_id is not None:
        stmt = stmt.where(PendingAction.chat_id == chat_id)
    result = await session.execute(stmt)
    if result.rowcount != 1:
        await session.rollback()
        action = await get_action_by_token(session, token)
        if action is None:
            raise ActionError("action not found")
        if action.status == ActionStatus.EXECUTING:
            raise ActionError("action is already being executed")
        if action.status == ActionStatus.SUCCEEDED:
            raise ActionError("action already completed")
        if action.status in {ActionStatus.CANCELLED, ActionStatus.FAILED, ActionStatus.DRAFT}:
            raise ActionError(f"action is {action.status}")
        raise ActionError("action expired, create a new one")
    await session.commit()
    action = await get_action_by_token(session, token)
    assert action is not None
    return action


async def execute_action(
    session: AsyncSession,
    client: FreelancerClient,
    action: PendingAction,
    *,
    user_id: int,
) -> PendingAction:
    """Dispatch a claimed action to the Freelancer API and record the outcome.

    The action must already be in EXECUTING status (see redeem_and_claim).
    """
    payload = dict(action.payload or {})
    kind = action.action_type
    try:
        if kind == ActionType.BID_CREATE:
            bid_result = await client.create_bid(
                {
                    "project_id": payload["project_id"],
                    "bidder_id": user_id,
                    "amount": float(payload["amount"]),
                    "period": int(payload["period_days"]),
                    "milestone_percentage": float(payload.get("milestone_percentage", 50.0)),
                    "description": payload.get("description", ""),
                    **({"profile_id": payload["profile_id"]} if payload.get("profile_id") else {}),
                }
            )
            bid = await session.get(Bid, action.bid_id) if action.bid_id else None
            remote_id = bid_result.get("id")
            if bid is not None:
                bid.freelancer_bid_id = int(remote_id) if remote_id is not None else None
                bid.award_status = bid_result.get("award_status")
                bid.frontend_status = bid_result.get("frontend_bid_status")
                await session.commit()
            await _finish(session, action, succeeded=True, result=sanitize(bid_result))

        elif kind in (ActionType.BID_ACCEPT, ActionType.BID_DENY):
            remote_bid_id = payload["freelancer_bid_id"]
            action_name = "accept" if kind == ActionType.BID_ACCEPT else "deny"
            result = await client.bid_action(int(remote_bid_id), action_name)
            bid = await session.get(Bid, action.bid_id) if action.bid_id else None
            if bid is not None and kind == ActionType.BID_ACCEPT:
                bid.accepted = True
                await session.commit()
            await _finish(session, action, succeeded=True, result=sanitize(result))

        elif kind == ActionType.MESSAGE_SEND:
            client_message_id = payload.get("client_message_id") or f"dsh-{uuid.uuid4()}"
            result = await client.create_message(
                int(payload["thread_id"]),
                str(payload["message"]),
                client_message_id=client_message_id,
            )
            await _finish(session, action, succeeded=True, result=sanitize(result))

        else:  # pragma: no cover - ActionType is closed
            raise ActionError(f"unsupported action type {kind}")

    except FreelancerError as exc:
        await _finish(session, action, succeeded=False, error=sanitize(exc.__dict__ | {"message": str(exc)}))
        raise
    return action


async def _finish(
    session: AsyncSession,
    action: PendingAction,
    *,
    succeeded: bool,
    result: dict | None = None,
    error: dict | None = None,
) -> None:
    action.status = ActionStatus.SUCCEEDED if succeeded else ActionStatus.FAILED
    action.result = result
    action.error = error
    action.executed_at = utcnow()
    await session.commit()
    log.info(
        "freelancer_action.finished",
        extra={
            "extra_fields": {
                "action_id": action.id,
                "type": action.action_type,
                "succeeded": succeeded,
            }
        },
    )


async def expire_stale(session: AsyncSession, *, grace_message: bool = False) -> int:
    """Expire pending confirmations older than their TTL. Returns number expired."""
    result = await session.execute(
        update(PendingAction)
        .where(
            PendingAction.status == ActionStatus.PENDING_CONFIRMATION,
            PendingAction.expires_at <= utcnow(),
        )
        .values(status=ActionStatus.EXPIRED)
    )
    await session.commit()
    return int(result.rowcount or 0)


async def record_audit(session: AsyncSession, event: str, subject: str | None = None, **data: Any) -> None:
    from app.db.models import AuditEvent

    session.add(AuditEvent(event=event, subject=subject, data=sanitize(data)))
    await session.commit()


async def confirm_and_execute(
    session: AsyncSession,
    client: FreelancerClient,
    *,
    token: str,
    chat_id: int | None,
    user_id: int,
) -> PendingAction:
    """Redeem a confirmation token and run the action. Single-use by construction."""
    action = await redeem_and_claim(session, token, chat_id=chat_id)
    await record_audit(
        session,
        "action.confirmed",
        subject=f"action:{action.id}",
        type=action.action_type,
        token=action.token[:4] + "…",
    )
    try:
        await execute_action(session, client, action, user_id=user_id)
    except (FreelancerError, ActionError) as exc:
        await record_audit(
            session,
            "action.failed",
            subject=f"action:{action.id}",
            error=str(exc),
            kind=getattr(exc, "kind", type(exc).__name__),
        )
        raise
    await record_audit(session, "action.succeeded", subject=f"action:{action.id}", type=action.action_type)
    await session.refresh(action)
    return action


def build_bid_preview(project: dict, params: dict) -> str:
    lines = [
        "📝 Draft bid",
        f"Project: {project.get('title', '?')} (#{project.get('id', '?')})",
        f"Amount: {params.get('amount')} {project.get('currency_code') or 'USD'}",
        f"Period: {params.get('period_days')} days",
        f"Milestone split: {params.get('milestone_percentage')}%",
    ]
    description = (params.get("description") or "").strip()
    if description:
        lines += ["", "Proposal:", description[:900]]
    lines += ["", "Confirm to place this bid on Freelancer.com."]
    return "\n".join(lines)


def build_message_preview(thread: dict, text: str) -> str:
    label = thread.get("title") or f"thread {thread.get('id', '?')}"
    return f"✉️ Reply to {label}\n\n{text[:1500]}\n\nConfirm to send this message via Freelancer chat."


def build_award_preview(bid: dict, project: dict) -> str:
    return "\n".join(
        [
            "🏆 Your bid was awarded!",
            f"Project: {project.get('title', '?')} (#{project.get('id', '?')})",
            f"Bid amount: {bid.get('amount')} over {bid.get('period')} days",
            "",
            "Accept to start the project, or deny to decline the award.",
        ]
    )
