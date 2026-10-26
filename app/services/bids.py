"""Bid drafting (from project cards), award detection polling, award responses."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import ActionStatus, ActionType, Bid, Notification, PendingAction, Project, SearchRule
from app.freelancer.client import FreelancerClient
from app.log import get_logger
from app.services import actions as action_service
from app.services.notify import Button, NotificationRequest, Notifier
from app.services.render import project_link, render_project_card

log = get_logger(__name__)


def resolve_bid_amount(project: dict, template: dict) -> float | None:
    if template.get("amount") is not None:
        return float(template["amount"])
    for key in ("avg_bid", "avg_bid_price"):
        if project.get(key):
            return float(project[key])
    budget = project.get("budget") or {}
    if isinstance(budget, dict):
        for key in ("maximum", "minimum"):
            if budget.get(key) is not None:
                return float(budget[key])
    for key in ("budget_max", "budget_min"):
        if project.get(key) is not None:
            return float(project[key])
    return None


def render_bid_overrides(template: dict, project: dict) -> dict:
    return {
        "project_id": int(project["id"]),
        "amount": resolve_bid_amount(project, template),
        "period_days": int(template.get("period_days") or project.get("period") or 7),
        "milestone_percentage": float(
            template.get("milestone_percentage") if template.get("milestone_percentage") is not None else 50.0
        ),
        "description": template.get("description", ""),
        "profile_id": template.get("profile_id"),
    }


async def create_bid_draft(
    session: AsyncSession,
    settings: Settings,
    *,
    project: dict[str, Any],
    chat_id: int,
    rule_id: int | None = None,
    overrides: dict[str, Any] | None = None,
    ttl_seconds: int | None = None,
) -> tuple[PendingAction, str]:
    template: dict[str, Any] = {}
    if rule_id:
        rule = await session.get(SearchRule, rule_id)
        if rule is not None:
            template = dict(rule.bid_template or {})
    # Global defaults as fallback base, rule template overrides them.
    base = {
        "period_days": settings.default_bid_period_days,
        "milestone_percentage": settings.default_milestone_percentage,
    }
    merged = {**base, **template, **(overrides or {})}
    params = render_bid_overrides(merged, project)

    bid_row = Bid(
        project_id=int(project["id"]),
        amount=float(params["amount"] or 0),
        period_days=int(params["period_days"]),
        milestone_percentage=float(params["milestone_percentage"]),
        description=params["description"],
        profile_id=params.get("profile_id"),
    )
    session.add(bid_row)
    await session.flush()

    preview = action_service.build_bid_preview(project, params)
    if params["amount"] is None:
        preview += "\n\n⚠️ Chưa có amount – dùng /amount <giá trị> trước khi xác nhận."
    action = await action_service.create_action(
        session,
        action_type=ActionType.BID_CREATE,
        payload=params,
        preview_text=preview,
        chat_id=chat_id,
        project_id=int(project["id"]),
        bid_row_id=bid_row.id,
        ttl_seconds=ttl_seconds or settings.pending_action_ttl_seconds,
    )
    return action, preview


async def get_latest_bid_draft(session: AsyncSession, chat_id: int) -> PendingAction | None:
    return await session.scalar(
        select(PendingAction)
        .where(
            PendingAction.chat_id == chat_id,
            PendingAction.action_type == ActionType.BID_CREATE,
            PendingAction.status == ActionStatus.PENDING_CONFIRMATION,
        )
        .order_by(PendingAction.id.desc())
        .limit(1)
    )


async def poll_awards(
    session: AsyncSession,
    client: FreelancerClient,
    *,
    user_id: int,
    notifier: Notifier | None,
    chat_ids: list[int],
    ttl_seconds: int = 900,
) -> int:
    """Detect bids awarded to us that have no response yet; notify via Telegram.

    Returns the number of new award notifications sent.
    """
    awarded = await client.list_bids(bidders=[user_id], award_statuses=["awarded"], limit=100)
    new_count = 0
    for remote in awarded:
        remote_id = remote.get("id")
        if remote_id is None:
            continue
        existing = await session.scalar(select(Bid).where(Bid.freelancer_bid_id == int(remote_id)))
        project_data: dict[str, Any] = {}
        project_id = remote.get("project_id")
        if project_id is not None:
            row = await session.get(Project, int(project_id))
            if row is not None:
                project_data = {"id": row.id, "title": row.title, "seo_url": row.seo_url}
            else:
                try:
                    fetched = await client.get_project(int(project_id))
                    if fetched:
                        from app.services.search import ensure_project

                        saved = await ensure_project(session, fetched)
                        await session.commit()
                        project_data = {"id": saved.id, "title": saved.title, "seo_url": saved.seo_url}
                except Exception as exc:  # noqa: BLE001 - award notify must survive project fetch failures
                    log.warning(
                        "awards.project_fetch_failed",
                        extra={
                            "extra_fields": {
                                "project_id": project_id,
                                "error": str(exc),
                            }
                        },
                    )

        if existing is None:
            existing = Bid(
                freelancer_bid_id=int(remote_id),
                project_id=int(project_id or 0),
                amount=float(remote.get("amount") or 0),
                period_days=int(remote.get("period") or 0),
                description=remote.get("description"),
            )
            session.add(existing)
            await session.flush()
        existing.award_status = remote.get("award_status")
        existing.frontend_status = remote.get("frontend_bid_status")
        await session.commit()

        if existing.accepted:
            continue
        already_notified = await session.scalar(
            select(Notification)
            .where(
                Notification.kind == "award",
                Notification.bid_id == existing.id,
            )
            .limit(1)
        )
        if already_notified is not None:
            continue
        if notifier is None or not chat_ids:
            continue
        text = "\n".join(
            [
                "🏆 Bid của bạn đã được award!",
                f"Project: {project_data.get('title', '?')} (#{project_data.get('id', remote.get('project_id'))})",
                f"Bid: ${existing.amount:g} · {existing.period_days} ngày",
                "",
                "Chọn hành động bên dưới (sẽ yêu cầu xác nhận thêm một lần nữa).",
            ]
        )
        link = project_link(project_data)
        buttons = [
            Button("✅ Xem trước Accept", f"awardaccept:{existing.id}"),
            Button("❌ Xem trước Deny", f"awarddeny:{existing.id}"),
        ]
        if link:
            buttons.insert(0, Button("🔎 Project", f"proj:{project_data.get('id')}"))
        for chat_id in chat_ids:
            telegram_message_id = await notifier.send(NotificationRequest(chat_id=chat_id, text=text, buttons=buttons))
            session.add(
                Notification(
                    kind="award",
                    bid_id=existing.id,
                    project_id=existing.project_id or None,
                    chat_id=chat_id,
                    telegram_message_id=telegram_message_id,
                    text=text,
                )
            )
            new_count += 1
        await session.commit()
    return new_count


async def create_award_response_draft(
    session: AsyncSession,
    *,
    bid_row: Bid,
    accept: bool,
    chat_id: int,
    ttl_seconds: int,
) -> PendingAction:
    if bid_row.freelancer_bid_id is None:
        raise action_service.ActionError("bid chưa có id trên Freelancer, không thể respond")
    action_type = ActionType.BID_ACCEPT if accept else ActionType.BID_DENY
    label = "ACCEPT (vào job)" if accept else "DENY (từ chối award)"
    preview = "\n".join(
        [
            f"⚠️ Xác nhận: {label}",
            f"Bid #{bid_row.freelancer_bid_id} · project #{bid_row.project_id}",
            f"Số tiền: ${bid_row.amount:g} · {bid_row.period_days} ngày",
            "",
            "Hành động này ảnh hưởng trực tiếp tới tài khoản Freelancer của bạn.",
        ]
    )
    return await action_service.create_action(
        session,
        action_type=action_type,
        payload={"freelancer_bid_id": bid_row.freelancer_bid_id},
        preview_text=preview,
        chat_id=chat_id,
        project_id=bid_row.project_id,
        bid_row_id=bid_row.id,
        ttl_seconds=ttl_seconds,
    )


async def render_project_detail(client: FreelancerClient, project_id: int) -> str:
    project = await client.get_project(project_id, full_description=True, job_details=True)
    if not project:
        return f"Không tìm thấy project #{project_id} (có thể đã đóng/xoá)."
    card = render_project_card(project, score=None, reasons=[])
    link = project_link(project)
    if link:
        card += f"\n🔗 {link}"
    return card
