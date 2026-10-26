"""Rule scanning: fetch candidate projects from Freelancer, filter/score, dedupe, notify."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Notification, Project, ProjectMatch, SearchRule, utcnow
from app.freelancer.client import FreelancerClient, FreelancerError
from app.log import get_logger
from app.services.matching import evaluate_project
from app.services.notify import Button, NotificationRequest, Notifier
from app.services.render import render_project_card

log = get_logger(__name__)

MAX_PAGES = 3


@dataclass
class ScanOutcome:
    rule_id: int
    rule_name: str
    fetched: int = 0
    matched: int = 0
    new_matches: int = 0
    notified: int = 0
    errors: list[str] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)


def _project_to_row(project: dict[str, Any]) -> Project:
    budget = project.get("budget") or {}
    currency = None
    if isinstance(budget, dict):
        cur = budget.get("currency")
        if isinstance(cur, dict):
            currency = cur.get("code")
    return Project(
        id=int(project["id"]),
        title=str(project.get("title") or "")[:8000] or "(untitled)",
        description=project.get("description"),
        project_type=project.get("type") or project.get("project_type"),
        currency_code=currency,
        budget_min=budget.get("minimum") if isinstance(budget, dict) else None,
        budget_max=budget.get("maximum") if isinstance(budget, dict) else None,
        hourly_min=budget.get("minimum") if (project.get("type") == "hourly" and isinstance(budget, dict)) else None,
        hourly_max=budget.get("maximum") if (project.get("type") == "hourly" and isinstance(budget, dict)) else None,
        bid_count=project.get("bidcount") or project.get("bid_count"),
        avg_bid=project.get("avg_bid") or project.get("avg_bid_price"),
        owner_id=project.get("owner_id"),
        seo_url=project.get("seo_url"),
        submitdate=project.get("submitdate"),
        timestamp_updated=project.get("timestamp_updated"),
        bid_enddate=project.get("bid_enddate"),
        jobs=[j.get("id") if isinstance(j, dict) else j for j in (project.get("jobs") or [])],
        raw=project,
    )


async def ensure_project(session: AsyncSession, project: dict[str, Any]) -> Project:
    row = await session.get(Project, int(project["id"]))
    if row is None:
        row = _project_to_row(project)
        session.add(row)
        try:
            # Savepoint so a duplicate insert does not expire the whole session.
            async with session.begin_nested():
                await session.flush()
        except IntegrityError:
            row = await session.get(Project, int(project["id"]))
            assert row is not None
    else:
        row.bid_count = project.get("bidcount") or project.get("bid_count") or row.bid_count
        row.avg_bid = project.get("avg_bid") or row.avg_bid
        row.raw = project
    return row


def build_api_params(rule: SearchRule, *, limit: int, offset: int) -> dict[str, Any]:
    params: dict[str, Any] = dict(rule.filters or {})
    params.setdefault("sort_field", "time_updated")
    params.setdefault("full_description", True)
    params.setdefault("job_details", True)
    params["limit"] = limit
    params["offset"] = offset
    if rule.last_checked_at and not params.get("from_time"):
        params["from_time"] = int(rule.last_checked_at.replace(microsecond=0).timestamp()) - 60
    return params


async def scan_rule(
    session: AsyncSession,
    client: FreelancerClient,
    rule: SearchRule,
    notifier: Notifier | None,
    *,
    chat_ids: list[int],
    page_limit: int = 100,
    notify: bool = True,
    max_notifications: int = 15,
    notify_delay_seconds: float = 3.0,
) -> ScanOutcome:
    """Run one scan pass for a rule. Idempotent: unique (project_id, rule_id) prevents re-notify."""
    rule_id = rule.id
    rule_name = rule.name
    outcome = ScanOutcome(rule_id=rule_id, rule_name=rule_name)
    now = utcnow()
    projects: list[dict[str, Any]] = []
    for page in range(MAX_PAGES):
        params = build_api_params(rule, limit=page_limit, offset=page * page_limit)
        try:
            result = await client.search_projects(params)
        except FreelancerError as exc:
            outcome.errors.append(f"search failed: {exc}")
            return outcome
        batch = result.get("projects") or []
        projects.extend(batch)
        if len(batch) < page_limit:
            break

    seen_ids: set[int] = set()
    # Collect new matches first (without notifying), then notify the highest-scoring ones.
    # Each entry: (score, project_id, match_id, reasons, project)
    pending: list[tuple[float, int, int, list[str], dict[str, Any]]] = []
    for project in projects:
        try:
            pid = int(project["id"])
        except (KeyError, TypeError, ValueError):
            outcome.errors.append("skipped malformed project payload")
            continue
        if pid in seen_ids:
            continue
        seen_ids.add(pid)
        outcome.fetched += 1
        verdict = evaluate_project(project, rule.match or {}, min_score=float(rule.min_score))
        if not verdict.accepted:
            continue
        outcome.matched += 1
        outcome.candidates.append(
            {
                "project_id": pid,
                "score": verdict.score,
                "reasons": verdict.reasons,
                "title": project.get("title"),
            }
        )
        if not notify:
            continue

        await ensure_project(session, project)
        existing = await session.scalar(
            select(ProjectMatch).where(ProjectMatch.project_id == pid, ProjectMatch.rule_id == rule_id)
        )
        if existing is not None:
            continue
        match = ProjectMatch(project_id=pid, rule_id=rule_id, score=verdict.score, reasons=verdict.reasons)
        session.add(match)
        try:
            async with session.begin_nested():
                await session.flush()
        except IntegrityError:
            # Lost a race with another worker: duplicate match, skip notification.
            await session.rollback()
            continue
        outcome.new_matches += 1
        match_id = match.id  # capture before any possible rollback below
        pending.append((verdict.score, pid, match_id, verdict.reasons, project))

    # Notify the highest-scoring matches first (most relevant to the freelancer).
    pending.sort(key=lambda item: item[0], reverse=True)
    for score, pid, match_id, reasons, project in pending:
        if notifier is not None and chat_ids and outcome.notified < max_notifications:
            text = render_project_card(project, score=score, reasons=reasons)
            buttons = [
                Button("🔎 Chi tiết", f"proj:{pid}"),
                Button("📝 Tạo bid", f"bid:{pid}:{rule_id}"),
                Button("🚫 Bỏ qua", f"skip:{pid}:{rule_id}"),
            ]
            sent_any = False
            for chat_id in chat_ids:
                if outcome.notified >= max_notifications:
                    break
                try:
                    telegram_message_id = await notifier.send(
                        NotificationRequest(chat_id=chat_id, text=text, buttons=buttons)
                    )
                except Exception as exc:  # noqa: BLE001 - notifier failures must not kill scan
                    outcome.errors.append(f"telegram send failed: {exc.__class__.__name__}")
                    continue
                outcome.notified += 1
                sent_any = True
                # Check before insert to avoid IntegrityError (which would force a rollback).
                already = await session.scalar(
                    select(Notification).where(
                        Notification.kind == "project_match",
                        Notification.project_id == pid,
                        Notification.chat_id == chat_id,
                    )
                )
                if already is None:
                    note = Notification(
                        kind="project_match",
                        project_id=pid,
                        match_id=match_id,
                        chat_id=chat_id,
                        telegram_message_id=telegram_message_id,
                        text=text,
                    )
                    session.add(note)
                    try:
                        async with session.begin_nested():
                            await session.flush()
                    except IntegrityError:
                        # Rare race with another worker; notification already recorded.
                        await session.rollback()
                # Space out sends to avoid Telegram flood control.
                if notify_delay_seconds > 0:
                    await asyncio.sleep(notify_delay_seconds)
            # Mark notified only if we actually sent (or there was nothing to send).
            match = await session.get(ProjectMatch, match_id)
            if match is not None:
                match.notified = sent_any or notifier is None or not chat_ids

    if notify:
        rule.last_checked_at = now
    await session.commit()
    log.info(
        "scan.finished",
        extra={
            "extra_fields": {
                "rule_id": rule_id,
                "fetched": outcome.fetched,
                "matched": outcome.matched,
                "new": outcome.new_matches,
                "notified": outcome.notified,
                "errors": outcome.errors,
            }
        },
    )
    return outcome
