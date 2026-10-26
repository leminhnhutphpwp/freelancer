"""SQLAlchemy ORM models."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, JSONType


def utcnow() -> datetime:
    """Naive UTC now. SQLite stores naive datetimes; keeping everything naive UTC
    avoids offset-aware/naive comparison errors across dialects."""
    return datetime.now(UTC).replace(tzinfo=None)


def new_token(nbytes: int = 9) -> str:
    """Short URL-safe token for Telegram callback binding (< 64 bytes with prefix)."""
    return secrets.token_urlsafe(nbytes)


class ActionType(StrEnum):
    BID_CREATE = "bid_create"
    BID_ACCEPT = "bid_accept"
    BID_DENY = "bid_deny"
    MESSAGE_SEND = "message_send"


class ActionStatus(StrEnum):
    DRAFT = "draft"
    PENDING_CONFIRMATION = "pending_confirmation"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class FreelancerAccount(Base):
    """Cached identity of the single connected Freelancer account."""

    __tablename__ = "freelancer_account"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="connected")
    last_validated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    raw_profile: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)


class SearchRule(Base):
    __tablename__ = "search_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Server-side filters passed to /projects/active/ (query, project_types, jobs, budgets...).
    filters: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    # Client-side hard/soft matching (must/any/exclude keywords, bid caps, scoring weights).
    match: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    bid_template: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    min_score: Mapped[float] = mapped_column(Float, default=0.0)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    matches: Mapped[list[ProjectMatch]] = relationship(back_populates="rule", cascade="all, delete-orphan")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Freelancer project id
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    currency_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    budget_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    budget_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    hourly_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    hourly_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    bid_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    avg_bid: Mapped[float | None] = mapped_column(Float, nullable=True)
    owner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    owner_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    seo_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    submitdate: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    timestamp_updated: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    bid_enddate: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    jobs: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status: Mapped[str] = mapped_column(String(32), default="active")

    matches: Mapped[list[ProjectMatch]] = relationship(back_populates="project", cascade="all, delete-orphan")


class ProjectMatch(Base):
    __tablename__ = "project_matches"
    __table_args__ = (
        UniqueConstraint("project_id", "rule_id", name="uq_match_project_rule"),
        Index("ix_project_matches_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("search_rules.id", ondelete="CASCADE"), index=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    reasons: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    notified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    project: Mapped[Project] = relationship(back_populates="matches")
    rule: Mapped[SearchRule] = relationship(back_populates="matches")


class Bid(Base):
    """Local tracking of bids created through this assistant."""

    __tablename__ = "bids"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    freelancer_bid_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    amount: Mapped[float] = mapped_column(Float)
    period_days: Mapped[int] = mapped_column(Integer)
    milestone_percentage: Mapped[float | None] = mapped_column(Float, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    profile_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    award_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    frontend_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Thread(Base):
    __tablename__ = "message_threads"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Freelancer thread id
    context_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    context_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    members: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_inbound_notified_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PendingAction(Base):
    """Every outbound write (bid / award response / message) goes through this table."""

    __tablename__ = "pending_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[str] = mapped_column(String(32), unique=True, default=new_token)
    action_type: Mapped[ActionType] = mapped_column(String(32), index=True)
    status: Mapped[ActionStatus] = mapped_column(String(32), default=ActionStatus.DRAFT, index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    bid_id: Mapped[int | None] = mapped_column(ForeignKey("bids.id", ondelete="SET NULL"), nullable=True)
    thread_id: Mapped[int | None] = mapped_column(ForeignKey("message_threads.id", ondelete="SET NULL"), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    preview_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    confirmation_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("kind", "project_id", "chat_id", name="uq_notification_project"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))  # project_match | award | inbound_message
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    match_id: Mapped[int | None] = mapped_column(ForeignKey("project_matches.id", ondelete="SET NULL"), nullable=True)
    bid_id: Mapped[int | None] = mapped_column(ForeignKey("bids.id", ondelete="SET NULL"), nullable=True)
    thread_id: Mapped[int | None] = mapped_column(ForeignKey("message_threads.id", ondelete="SET NULL"), nullable=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event: Mapped[str] = mapped_column(String(64), index=True)
    subject: Mapped[str | None] = mapped_column(String(128), nullable=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
