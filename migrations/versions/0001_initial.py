"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2025-01-01
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "freelancer_account",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(255), nullable=True),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="connected"),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_profile", sa.JSON(), nullable=True),
        sa.UniqueConstraint("user_id", name="uq_freelancer_account_user_id"),
    )
    op.create_index("ix_freelancer_account_user_id", "freelancer_account", ["user_id"])

    op.create_table(
        "search_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("filters", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("match", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("bid_template", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("min_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", name="uq_search_rules_name"),
    )

    op.create_table(
        "projects",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("project_type", sa.String(32), nullable=True),
        sa.Column("currency_code", sa.String(16), nullable=True),
        sa.Column("budget_min", sa.Float(), nullable=True),
        sa.Column("budget_max", sa.Float(), nullable=True),
        sa.Column("hourly_min", sa.Float(), nullable=True),
        sa.Column("hourly_max", sa.Float(), nullable=True),
        sa.Column("bid_count", sa.Integer(), nullable=True),
        sa.Column("avg_bid", sa.Float(), nullable=True),
        sa.Column("owner_id", sa.BigInteger(), nullable=True),
        sa.Column("owner_name", sa.String(255), nullable=True),
        sa.Column("seo_url", sa.String(512), nullable=True),
        sa.Column("submitdate", sa.BigInteger(), nullable=True),
        sa.Column("timestamp_updated", sa.BigInteger(), nullable=True),
        sa.Column("bid_enddate", sa.BigInteger(), nullable=True),
        sa.Column("jobs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("raw", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
    )

    op.create_table(
        "message_threads",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("context_type", sa.String(32), nullable=True),
        sa.Column("context_id", sa.BigInteger(), nullable=True),
        sa.Column("project_id", sa.BigInteger(), nullable=True),
        sa.Column("members", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("title", sa.String(512), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_message_id", sa.BigInteger(), nullable=True),
        sa.Column("last_inbound_notified_message_id", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
    )

    op.create_table(
        "project_matches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("reasons", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("notified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["rule_id"], ["search_rules.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id", "rule_id", name="uq_match_project_rule"),
    )
    op.create_index("ix_project_matches_project_id", "project_matches", ["project_id"])
    op.create_index("ix_project_matches_rule_id", "project_matches", ["rule_id"])
    op.create_index("ix_project_matches_created", "project_matches", ["created_at"])

    op.create_table(
        "bids",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("freelancer_bid_id", sa.BigInteger(), nullable=True),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("period_days", sa.Integer(), nullable=False),
        sa.Column("milestone_percentage", sa.Float(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("profile_id", sa.BigInteger(), nullable=True),
        sa.Column("award_status", sa.String(32), nullable=True),
        sa.Column("frontend_status", sa.String(32), nullable=True),
        sa.Column("accepted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("freelancer_bid_id", name="uq_bids_freelancer_bid_id"),
    )
    op.create_index("ix_bids_project_id", "bids", ["project_id"])
    op.create_index("ix_bids_freelancer_bid_id", "bids", ["freelancer_bid_id"])

    op.create_table(
        "pending_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token", sa.String(32), nullable=False),
        sa.Column("action_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("project_id", sa.BigInteger(), nullable=True),
        sa.Column("bid_id", sa.Integer(), nullable=True),
        sa.Column("thread_id", sa.BigInteger(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("preview_text", sa.Text(), nullable=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("confirmation_message_id", sa.BigInteger(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["bid_id"], ["bids.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["thread_id"], ["message_threads.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("token", name="uq_pending_actions_token"),
    )
    op.create_index("ix_pending_actions_action_type", "pending_actions", ["action_type"])
    op.create_index("ix_pending_actions_status", "pending_actions", ["status"])

    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=True),
        sa.Column("match_id", sa.Integer(), nullable=True),
        sa.Column("bid_id", sa.Integer(), nullable=True),
        sa.Column("thread_id", sa.BigInteger(), nullable=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["match_id"], ["project_matches.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["bid_id"], ["bids.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["thread_id"], ["message_threads.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("kind", "project_id", "chat_id", name="uq_notification_project"),
    )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event", sa.String(64), nullable=False),
        sa.Column("subject", sa.String(128), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_events_event", "audit_events", ["event"])
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("notifications")
    op.drop_table("pending_actions")
    op.drop_table("bids")
    op.drop_table("project_matches")
    op.drop_table("message_threads")
    op.drop_table("projects")
    op.drop_table("search_rules")
    op.drop_table("freelancer_account")