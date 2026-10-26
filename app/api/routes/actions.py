"""Outbound action drafts, confirmations, history and audit."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_client, get_settings_dep, session_dependency
from app.api.security import require_admin_key
from app.config import Settings
from app.db.models import AuditEvent, PendingAction, Project
from app.freelancer.client import FreelancerClient
from app.schemas import ActionOut, AuditOut, BidDraftRequest, MessageDraftRequest
from app.services import actions as action_service
from app.services import bids as bid_service
from app.services import profile as profile_service
from app.services import threads as thread_service

router = APIRouter(prefix="/actions", tags=["actions"], dependencies=[Depends(require_admin_key)])


@router.get("", response_model=list[ActionOut])
async def list_actions(
    only_open: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(session_dependency),
) -> list[PendingAction]:
    from app.db.models import ActionStatus

    stmt = select(PendingAction).order_by(PendingAction.id.desc()).limit(limit)
    if only_open:
        stmt = stmt.where(PendingAction.status == ActionStatus.PENDING_CONFIRMATION)
    return list(await session.scalars(stmt))


@router.post("/bid-draft", response_model=ActionOut, status_code=status.HTTP_201_CREATED)
async def create_bid_draft(
    body: BidDraftRequest,
    session: AsyncSession = Depends(session_dependency),
    settings: Settings = Depends(get_settings_dep),
) -> PendingAction:
    project = await session.get(Project, body.project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not known; scan a rule first")
    overrides = body.model_dump(exclude_none=True, exclude={"project_id", "rule_id"})
    action, _preview = await bid_service.create_bid_draft(
        session,
        settings,
        project=project.raw or {"id": project.id, "title": project.title},
        chat_id=0,
        rule_id=body.rule_id,
        overrides=overrides,
    )
    return action


@router.post("/message-draft", response_model=ActionOut, status_code=status.HTTP_201_CREATED)
async def create_message_draft(
    body: MessageDraftRequest,
    session: AsyncSession = Depends(session_dependency),
    settings: Settings = Depends(get_settings_dep),
) -> PendingAction:
    return await thread_service.create_reply_draft(
        session,
        thread_id=body.thread_id,
        text=body.message,
        chat_id=0,
        ttl_seconds=settings.pending_action_ttl_seconds,
    )


@router.post("/{token}/confirm", response_model=ActionOut)
async def confirm(
    token: str,
    session: AsyncSession = Depends(session_dependency),
    client: FreelancerClient = Depends(get_client),
) -> PendingAction:
    account = await profile_service.require_account(session)
    try:
        return await action_service.confirm_and_execute(
            session,
            client,
            token=token,
            chat_id=None,
            user_id=int(account.user_id),
        )
    except action_service.ActionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/{token}/cancel", response_model=ActionOut)
async def cancel(
    token: str,
    session: AsyncSession = Depends(session_dependency),
) -> PendingAction:
    action = await action_service.get_action_by_token(session, token)
    if action is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "action not found")
    try:
        return await action_service.cancel(session, action)
    except action_service.ActionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get("/audit", response_model=list[AuditOut])
async def audit(
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(session_dependency),
) -> list[AuditEvent]:
    return list(await session.scalars(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)))
