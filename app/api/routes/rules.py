"""Search rule CRUD + scan/test endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_client, get_notifier, get_settings_dep, session_dependency
from app.api.security import require_admin_key
from app.config import Settings
from app.db.models import SearchRule
from app.freelancer.client import FreelancerClient
from app.schemas import ScanTestResult, SearchRuleCreate, SearchRuleOut, SearchRuleUpdate
from app.services.notify import Notifier
from app.services.search import scan_rule

router = APIRouter(prefix="/rules", tags=["rules"], dependencies=[Depends(require_admin_key)])


async def _get_rule(session: AsyncSession, rule_id: int) -> SearchRule:
    rule = await session.get(SearchRule, rule_id)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "rule not found")
    return rule


@router.get("", response_model=list[SearchRuleOut])
async def list_rules(session: AsyncSession = Depends(session_dependency)) -> list[SearchRule]:
    return list(await session.scalars(select(SearchRule).order_by(SearchRule.id)))


@router.post("", response_model=SearchRuleOut, status_code=status.HTTP_201_CREATED)
async def create_rule(
    body: SearchRuleCreate,
    session: AsyncSession = Depends(session_dependency),
) -> SearchRule:
    rule = SearchRule(
        name=body.name,
        enabled=body.enabled,
        filters=body.filters.model_dump(exclude_none=True),
        match=body.match.model_dump(exclude_none=True),
        bid_template=body.bid_template.model_dump(exclude_none=True),
        min_score=body.min_score,
    )
    session.add(rule)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "rule name already exists") from exc
    await session.refresh(rule)
    return rule


@router.get("/{rule_id}", response_model=SearchRuleOut)
async def get_rule(rule_id: int, session: AsyncSession = Depends(session_dependency)) -> SearchRule:
    return await _get_rule(session, rule_id)


@router.patch("/{rule_id}", response_model=SearchRuleOut)
async def update_rule(
    rule_id: int,
    body: SearchRuleUpdate,
    session: AsyncSession = Depends(session_dependency),
) -> SearchRule:
    rule = await _get_rule(session, rule_id)
    data = body.model_dump(exclude_none=True)
    for key in ("filters", "match", "bid_template"):
        if key in data:
            field_model = {"filters": body.filters, "match": body.match, "bid_template": body.bid_template}[key]
            data[key] = field_model.model_dump(exclude_none=True)
    for key, value in data.items():
        setattr(rule, key, value)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "rule name already exists") from exc
    await session.refresh(rule)
    return rule


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(rule_id: int, session: AsyncSession = Depends(session_dependency)) -> None:
    rule = await _get_rule(session, rule_id)
    await session.delete(rule)
    await session.commit()


@router.post("/{rule_id}/test", response_model=ScanTestResult)
async def test_rule(
    rule_id: int,
    session: AsyncSession = Depends(session_dependency),
    client: FreelancerClient = Depends(get_client),
) -> ScanTestResult:
    """Dry run: fetch + match live projects, but persist nothing and notify nobody."""
    rule = await _get_rule(session, rule_id)
    outcome = await scan_rule(
        session,
        client,
        rule,
        notifier=None,
        chat_ids=[],
        notify=False,
    )
    return ScanTestResult(
        rule_id=outcome.rule_id,
        fetched=outcome.fetched,
        matched=outcome.matched,
        candidates=outcome.candidates,
        errors=outcome.errors,
    )


@router.post("/{rule_id}/scan", response_model=ScanTestResult)
async def scan_now(
    rule_id: int,
    session: AsyncSession = Depends(session_dependency),
    client: FreelancerClient = Depends(get_client),
    notifier: Notifier | None = Depends(get_notifier),
    settings: Settings = Depends(get_settings_dep),
) -> ScanTestResult:
    """Force a full scan pass (persists matches and sends Telegram notifications)."""
    rule = await _get_rule(session, rule_id)
    outcome = await scan_rule(
        session,
        client,
        rule,
        notifier=notifier,
        chat_ids=list(settings.telegram_allowed_chat_ids),
        page_limit=settings.search_page_limit,
    )
    return ScanTestResult(
        rule_id=outcome.rule_id,
        fetched=outcome.fetched,
        matched=outcome.matched,
        candidates=outcome.candidates,
        errors=outcome.errors,
    )
