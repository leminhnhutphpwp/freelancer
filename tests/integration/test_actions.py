"""Integration tests for the PendingAction state machine (sqlite)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models import ActionStatus, ActionType, PendingAction
from app.freelancer.client import FreelancerError
from app.services import actions as action_service


async def _make_action(session, *, status=ActionStatus.PENDING_CONFIRMATION, ttl=900, chat_id=111):
    return await action_service.create_action(
        session,
        action_type=ActionType.BID_CREATE,
        payload={"project_id": 1, "amount": 100, "period_days": 7, "milestone_percentage": 50},
        preview_text="preview",
        chat_id=chat_id,
        ttl_seconds=ttl,
        status=status,
    )


async def test_create_and_redeem_once(session):
    action = await _make_action(session)
    assert action.status == ActionStatus.PENDING_CONFIRMATION
    assert action.token

    claimed = await action_service.redeem_and_claim(session, action.token, chat_id=111)
    assert claimed.status == ActionStatus.EXECUTING
    assert claimed.confirmed_at is not None

    # Second redeem must fail (single-use).
    with pytest.raises(action_service.ActionError, match="already being executed"):
        await action_service.redeem_and_claim(session, action.token, chat_id=111)


async def test_redeem_wrong_chat_rejected(session):
    action = await _make_action(session, chat_id=111)
    with pytest.raises(action_service.ActionError):
        await action_service.redeem_and_claim(session, action.token, chat_id=999)


async def test_expired_action_cannot_redeem(session):
    action = await _make_action(session, ttl=-10)
    with pytest.raises(action_service.ActionError, match="expired"):
        await action_service.redeem_and_claim(session, action.token, chat_id=111)


async def test_cancel(session):
    action = await _make_action(session)
    cancelled = await action_service.cancel(session, action)
    assert cancelled.status == ActionStatus.CANCELLED
    with pytest.raises(action_service.ActionError):
        await action_service.redeem_and_claim(session, action.token, chat_id=111)


async def test_expire_stale(session):
    await _make_action(session, ttl=-5)
    await _make_action(session, ttl=900)  # still valid
    expired = await action_service.expire_stale(session)
    assert expired == 1
    rows = list(await session.scalars(select(PendingAction)))
    statuses = {r.status for r in rows}
    assert ActionStatus.EXPIRED in statuses
    assert ActionStatus.PENDING_CONFIRMATION in statuses


async def test_confirm_and_execute_bid(session, monkeypatch):
    action = await _make_action(session)
    calls = {}

    class FakeClient:
        async def create_bid(self, payload):
            calls["payload"] = payload
            return {"id": 42, "award_status": "pending"}

    result = await action_service.confirm_and_execute(
        session,
        FakeClient(),
        token=action.token,
        chat_id=111,
        user_id=7,
    )
    assert result.status == ActionStatus.SUCCEEDED
    assert calls["payload"]["bidder_id"] == 7
    assert calls["payload"]["project_id"] == 1
    assert result.result["id"] == 42


async def test_confirm_and_execute_failure(session):
    action = await _make_action(session)

    class FailingClient:
        async def create_bid(self, payload):
            raise FreelancerError("bid rejected", status_code=400, error_code="BID_AMOUNT_INVALID")

    with pytest.raises(FreelancerError):
        await action_service.confirm_and_execute(
            session,
            FailingClient(),
            token=action.token,
            chat_id=111,
            user_id=7,
        )
    # Action should be marked failed, not left executing.
    refreshed = await session.get(PendingAction, action.id)
    assert refreshed.status == ActionStatus.FAILED
    assert refreshed.error is not None


async def test_preview_builders():
    preview = action_service.build_bid_preview(
        {"id": 1, "title": "Job", "currency_code": "USD"},
        {"amount": 150, "period_days": 7, "milestone_percentage": 50, "description": "hi"},
    )
    assert "150" in preview and "7 days" in preview
    msg = action_service.build_message_preview({"id": 5}, "hello")
    assert "hello" in msg
