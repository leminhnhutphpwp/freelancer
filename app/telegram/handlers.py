"""Telegram command/callback handlers.

All outbound Freelancer writes funnel through PendingAction drafts:
button/command -> preview with confirm/cancel -> confirm executes via API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.config import Settings
from app.db.models import ActionStatus, Bid, PendingAction, Project, SearchRule
from app.db.session import async_sessionmaker
from app.freelancer.client import FreelancerClient, FreelancerError
from app.log import get_logger
from app.services import actions as action_service
from app.services import bids as bid_service
from app.services import profile as profile_service
from app.services import threads as thread_service
from app.services.render import project_link
from app.telegram.auth import is_authorized, parse_callback

log = get_logger(__name__)

# chat_id -> thread_id after tapping "Reply". Cleared on restart; harmless because
# no Freelancer write happens until an explicit confirm.
AWAITING_REPLY: dict[int, int] = {}

HELP_TEXT = (
    "🤖 Freelancer Assistant\n\n"
    "/status – kết nối + trạng thái\n"
    "/rules – danh sách rule tìm job\n"
    "/pending – draft đang chờ xác nhận\n"
    "/threads – chat mới nhất trên Freelancer\n"
    "/amount <giá> · /period <ngày> · /proposal <nội dung> – sửa draft bid gần nhất\n"
    "/confirm <token> · /cancel <token>\n\n"
    "Mọi hành động ghi (bid/accept/message) đều cần bấm ✅ Confirm."
)


@dataclass
class Deps:
    settings: Settings
    session_factory: async_sessionmaker
    client: FreelancerClient


def _deps(context: ContextTypes.DEFAULT_TYPE) -> Deps:
    return context.application.bot_data["deps"]


def _keyboard(buttons: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for label, cb in buttons:
        row.append(InlineKeyboardButton(label, callback_data=cb))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def _confirm_buttons(token: str) -> list[tuple[str, str]]:
    return [("✅ Confirm", f"ok:{token}"), ("✖️ Cancel", f"no:{token}")]


async def _guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    deps = _deps(context)
    chat_id = update.effective_chat.id if update.effective_chat else None
    user_id = update.effective_user.id if update.effective_user else None
    if is_authorized(deps.settings.telegram_allowed_chat_ids, chat_id, user_id):
        return True
    log.warning("telegram.unauthorized", extra={"extra_fields": {"chat_id": chat_id, "user_id": user_id}})
    if update.effective_chat:
        await context.bot.send_message(chat_id, "⛔ Chat này không nằm trong allowlist.")
    return False


# ------------------------------------------------------------------- commands


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _guard(update, context):
        await context.bot.send_message(update.effective_chat.id, HELP_TEXT)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    deps = _deps(context)
    async with deps.session_factory() as session:
        account = await profile_service.get_account(session)
        rules = list(await session.scalars(select(SearchRule).order_by(SearchRule.id)))
        open_count = await session.scalar(
            select(func.count())
            .select_from(PendingAction)
            .where(
                PendingAction.status == ActionStatus.PENDING_CONFIRMATION,
            )
        )
    lines = []
    if account:
        lines.append(f"👤 Freelancer: @{account.username or '?'} (id {account.user_id}) · {account.status}")
        lines.append(f"   validated: {account.last_validated_at:%Y-%m-%d %H:%M} UTC")
    else:
        lines.append("👤 Chưa kết nối – chạy POST /api/v1/account/connect")
    lines.append(f"📐 Rules: {len(rules)} ({sum(1 for r in rules if r.enabled)} đang bật)")
    lines.append(f"⏳ Draft chờ xác nhận: {open_count or 0}")
    await context.bot.send_message(update.effective_chat.id, "\n".join(lines))


async def cmd_rules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    async with _deps(context).session_factory() as session:
        rules = list(await session.scalars(select(SearchRule).order_by(SearchRule.id)))
    if not rules:
        await context.bot.send_message(
            update.effective_chat.id,
            "Chưa có rule nào. Tạo qua REST: POST /api/v1/rules",
        )
        return
    lines = ["📐 Search rules:"]
    for rule in rules:
        state = "🟢" if rule.enabled else "⚪"
        checked = rule.last_checked_at.strftime("%m-%d %H:%M") if rule.last_checked_at else "never"
        lines.append(f"{state} #{rule.id} {rule.name} · min_score={rule.min_score:g} · last={checked}")
    await context.bot.send_message(update.effective_chat.id, "\n".join(lines))


async def cmd_pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    chat_id = update.effective_chat.id
    async with _deps(context).session_factory() as session:
        rows = list(
            await session.scalars(
                select(PendingAction)
                .where(PendingAction.status == ActionStatus.PENDING_CONFIRMATION, PendingAction.chat_id == chat_id)
                .order_by(PendingAction.id.desc())
                .limit(10)
            )
        )
    if not rows:
        await context.bot.send_message(chat_id, "Không có draft nào đang chờ. ✅")
        return
    for action in rows:
        await context.bot.send_message(
            chat_id,
            f"{action.preview_text}\n\n(token: {action.token})",
            reply_markup=_keyboard(_confirm_buttons(action.token)),
        )


async def cmd_threads(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    chat_id = update.effective_chat.id
    async with _deps(context).session_factory() as session:
        threads = await thread_service.recent_threads(session, limit=8)
    if not threads:
        await context.bot.send_message(chat_id, "Chưa sync thread nào (worker chạy định kỳ).")
        return
    lines, buttons = ["💬 Threads gần đây:"], []
    for t in threads:
        ctx = f" · {t.context_type}#{t.context_id}" if t.context_type else ""
        when = t.last_message_at.strftime("%m-%d %H:%M") if t.last_message_at else "?"
        lines.append(f"#{t.id}{ctx} · last msg {when} UTC")
        buttons.append((f"↩️ Reply #{t.id}", f"reply:{t.id}"))
    await context.bot.send_message(chat_id, "\n".join(lines), reply_markup=_keyboard(buttons))


async def cmd_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _edit_draft(update, context, "amount", float)


async def cmd_period(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _edit_draft(update, context, "period_days", int)


async def cmd_proposal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _edit_draft(update, context, "description", str)


async def _edit_draft(update: Update, context: ContextTypes.DEFAULT_TYPE, field: str, cast: Any) -> None:
    if not await _guard(update, context):
        return
    chat_id = update.effective_chat.id
    if not context.args:
        await context.bot.send_message(chat_id, f"Dùng: /{field.split('_')[0]} <giá trị>")
        return
    try:
        value = cast(" ".join(context.args))
    except (TypeError, ValueError):
        await context.bot.send_message(chat_id, "Giá trị không hợp lệ.")
        return
    async with _deps(context).session_factory() as session:
        action = await bid_service.get_latest_bid_draft(session, chat_id)
        if action is None:
            await context.bot.send_message(chat_id, "Không có bid draft đang mở – bấm 📝 Tạo bid trước.")
            return
        payload = dict(action.payload or {})
        payload[field] = value
        action.payload = payload
        preview = action_service.build_bid_preview(
            {"id": payload.get("project_id"), "title": f"project #{payload.get('project_id')}"},
            payload,
        )
        action.preview_text = preview
        await session.commit()
    await context.bot.send_message(
        chat_id,
        f"✏️ Đã cập nhật {field}.\n\n{preview}\n\n(token: {action.token})",
        reply_markup=_keyboard(_confirm_buttons(action.token)),
    )


async def cmd_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    if not context.args:
        await context.bot.send_message(update.effective_chat.id, "Dùng: /confirm <token>")
        return
    await _execute_confirmation(update.effective_chat.id, context.args[0], context)


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    if not context.args:
        await context.bot.send_message(update.effective_chat.id, "Dùng: /cancel <token>")
        return
    async with _deps(context).session_factory() as session:
        action = await action_service.get_action_by_token(session, context.args[0])
        if action is None:
            await context.bot.send_message(update.effective_chat.id, "Không tìm thấy action với token đó.")
            return
        try:
            await action_service.cancel(session, action)
        except action_service.ActionError as exc:
            await context.bot.send_message(update.effective_chat.id, f"Không hủy được: {exc}")
            return
    await context.bot.send_message(update.effective_chat.id, "🗑 Đã hủy draft.")


# ------------------------------------------------------------------ callbacks


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    query = update.callback_query
    await query.answer()
    parsed = parse_callback(query.data or "")
    chat_id = update.effective_chat.id
    if parsed is None:
        await query.edit_message_text("Nút này không còn hợp lệ.")
        return
    try:
        if parsed.kind == "proj":
            await _show_project(chat_id, query, context, int(parsed.args[0]))
        elif parsed.kind == "bid":
            await _start_bid_draft(chat_id, query, context, int(parsed.args[0]), int(parsed.args[1]) or None)
        elif parsed.kind == "skip":
            await query.edit_message_reply_markup(reply_markup=None)
        elif parsed.kind in {"awardaccept", "awarddeny"}:
            await _start_award_response(
                chat_id, query, context, int(parsed.args[0]), accept=parsed.kind == "awardaccept"
            )
        elif parsed.kind == "reply":
            AWAITING_REPLY[chat_id] = int(parsed.args[0])
            await query.edit_message_text(
                f"Nhắn nội dung trả lời cho thread #{parsed.args[0]} (bot sẽ cho xem trước + xác nhận)."
            )
        elif parsed.kind == "ok":
            await _execute_confirmation(chat_id, parsed.args[0], context, query=query)
        elif parsed.kind == "no":
            await _cancel_confirmation(chat_id, parsed.args[0], context, query=query)
    except FreelancerError as exc:
        suffix = f" [{exc.error_code}]" if exc.error_code else ""
        await _respond(chat_id, query, context, f"❌ Freelancer từ chối{suffix}: {exc}")
    except action_service.ActionError as exc:
        await _respond(chat_id, query, context, f"⚠️ {exc}")
    except Exception as exc:  # noqa: BLE001
        log.exception("telegram.callback_failed")
        await _respond(chat_id, query, context, f"Lỗi nội bộ: {exc.__class__.__name__}: {exc}")


async def _show_project(chat_id: int, query, context: ContextTypes.DEFAULT_TYPE, project_id: int) -> None:
    async with _deps(context).session_factory() as session:
        row = await session.get(Project, project_id)
    if row is None:
        await query.edit_message_text("Không tìm thấy project trong cache.")
        return
    text = f"🔎 {row.title}\nID {row.id} · {row.project_type or '?'} · bids: {row.bid_count}"
    if row.description:
        text += "\n\n" + row.description[:1000]
    link = project_link({"id": row.id, "seo_url": row.seo_url})
    if link:
        text += f"\n🔗 {link}"
    text += "\n\nBấm 📝 để tạo bid draft (có bước xác nhận):"
    await context.bot.send_message(
        chat_id,
        text,
        reply_markup=_keyboard([("📝 Tạo bid", f"bid:{row.id}:0")]),
    )


async def _start_bid_draft(
    chat_id: int,
    query,
    context: ContextTypes.DEFAULT_TYPE,
    project_id: int,
    rule_id: int | None,
) -> None:
    deps = _deps(context)
    async with deps.session_factory() as session:
        row = await session.get(Project, project_id)
        if row is None:
            await query.edit_message_text("Project chưa được cache bởi rule nào.")
            return
        project = dict(row.raw or {})
        project.setdefault("id", row.id)
        project.setdefault("title", row.title)
        action, preview = await bid_service.create_bid_draft(
            session,
            deps.settings,
            project=project,
            chat_id=chat_id,
            rule_id=rule_id,
        )
    await context.bot.send_message(
        chat_id,
        f"{preview}\n\nToken: {action.token} · hết hạn sau {deps.settings.pending_action_ttl_seconds // 60} phút.",
        reply_markup=_keyboard(_confirm_buttons(action.token)),
    )
    await query.edit_message_reply_markup(reply_markup=None)


async def _start_award_response(
    chat_id: int,
    query,
    context: ContextTypes.DEFAULT_TYPE,
    bid_row_id: int,
    *,
    accept: bool,
) -> None:
    deps = _deps(context)
    async with deps.session_factory() as session:
        bid = await session.get(Bid, bid_row_id)
        if bid is None:
            await query.edit_message_text("Không tìm thấy bid.")
            return
        action = await bid_service.create_award_response_draft(
            session,
            bid=bid,
            accept=accept,
            chat_id=chat_id,
            ttl_seconds=deps.settings.pending_action_ttl_seconds,
        )
    await context.bot.send_message(
        chat_id,
        f"{action.preview_text}\n\nToken: {action.token}",
        reply_markup=_keyboard(_confirm_buttons(action.token)),
    )
    await query.edit_message_reply_markup(reply_markup=None)


async def _execute_confirmation(
    chat_id: int,
    token: str,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    query=None,
) -> None:
    deps = _deps(context)
    async with deps.session_factory() as session:
        account = await profile_service.get_account(session)
        if account is None:
            await _respond(chat_id, query, context, "Chưa kết nối tài khoản (POST /api/v1/account/connect).")
            return
        try:
            action = await action_service.confirm_and_execute(
                session,
                deps.client,
                token=token,
                chat_id=chat_id,
                user_id=int(account.user_id),
            )
        except action_service.ActionError as exc:
            await _respond(chat_id, query, context, f"⚠️ Không thực hiện được: {exc}")
            return
        except FreelancerError as exc:
            suffix = f" [{exc.error_code}]" if exc.error_code else ""
            await _respond(chat_id, query, context, f"❌ Freelancer từ chối{suffix}: {exc}")
            return
    done = "✅ Đã thực hiện thành công trên Freelancer.com!"
    if action.action_type == action_service.ActionType.BID_CREATE:
        done += " Bid đã được đặt."
    await _respond(chat_id, query, context, done)


async def _cancel_confirmation(
    chat_id: int,
    token: str,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    query=None,
) -> None:
    async with _deps(context).session_factory() as session:
        action = await action_service.get_action_by_token(session, token)
        if action is None or (action.chat_id is not None and action.chat_id != chat_id):
            await _respond(chat_id, query, context, "Không tìm thấy action cho token này.")
            return
        try:
            await action_service.cancel(session, action)
        except action_service.ActionError as exc:
            await _respond(chat_id, query, context, f"⚠️ {exc}")
            return
    await _respond(chat_id, query, context, "🗑 Đã hủy draft.")


async def _respond(chat_id: int, query, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    if query is not None:
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:  # noqa: BLE001 - message may already be edited
            pass
    await context.bot.send_message(chat_id, text)


async def on_plain_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Text arriving while composing a reply -> message draft + confirm."""
    if not await _guard(update, context):
        return
    chat_id = update.effective_chat.id
    thread_id = AWAITING_REPLY.pop(chat_id, None)
    text = (update.message.text or "").strip()
    if thread_id is None or not text:
        await context.bot.send_message(
            chat_id,
            "Không hiểu lệnh. Gõ /help. Muốn trả lời chat Freelancer: bấm ↩️ Reply trước.",
        )
        return
    deps = _deps(context)
    async with deps.session_factory() as session:
        action = await thread_service.create_reply_draft(
            session,
            thread_id=thread_id,
            text=text,
            chat_id=chat_id,
            ttl_seconds=deps.settings.pending_action_ttl_seconds,
        )
    await context.bot.send_message(
        chat_id,
        f"{action.preview_text}\n\nToken: {action.token}",
        reply_markup=_keyboard(_confirm_buttons(action.token)),
    )


# ------------------------------------------------------------------- wiring


def register_handlers(application: Application) -> None:
    for command, handler in {
        "start": cmd_start,
        "help": cmd_start,
        "status": cmd_status,
        "rules": cmd_rules,
        "pending": cmd_pending,
        "threads": cmd_threads,
        "amount": cmd_amount,
        "period": cmd_period,
        "proposal": cmd_proposal,
        "confirm": cmd_confirm,
        "cancel": cmd_cancel,
    }.items():
        application.add_handler(CommandHandler(command, handler))
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_plain_message))
