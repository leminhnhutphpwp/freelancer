"""Pydantic request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ORMBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------- rules


class RuleMatchSpec(BaseModel):
    must_keywords: list[str] = Field(default_factory=list)
    any_keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    require_skills: list[int] = Field(default_factory=list)
    any_skills: list[int] = Field(default_factory=list)
    max_bid_count: int | None = Field(default=None, ge=0)
    max_age_hours: float | None = Field(default=None, gt=0)
    preferred_keywords: list[str] = Field(default_factory=list)
    weights: dict[str, float] = Field(default_factory=dict)


class BidTemplate(BaseModel):
    amount: float | None = Field(default=None, gt=0)
    period_days: int | None = Field(default=None, gt=0, le=3650)
    milestone_percentage: float | None = Field(default=None, ge=0, le=100)
    description: str = ""
    profile_id: int | None = None


class SearchRuleFilters(BaseModel):
    """Server-side query params forwarded to /projects/0.1/projects/active/."""

    model_config = ConfigDict(extra="forbid")

    query: str | None = None
    or_search_query: str | None = None
    project_types: list[Literal["fixed", "hourly"]] | None = None
    jobs: list[int] | None = None
    countries: list[str] | None = None
    languages: list[str] | None = None
    min_price: float | None = Field(default=None, ge=0)
    max_price: float | None = Field(default=None, ge=0)
    min_hourly_rate: float | None = Field(default=None, ge=0)
    max_hourly_rate: float | None = Field(default=None, ge=0)
    min_avg_price: float | None = Field(default=None, ge=0)
    max_avg_price: float | None = Field(default=None, ge=0)
    project_upgrades: list[str] | None = None
    max_avg_hourly_rate: float | None = Field(default=None, ge=0)
    min_avg_hourly_rate: float | None = Field(default=None, ge=0)

    @field_validator("jobs", "countries", "languages", "project_upgrades")
    @classmethod
    def _non_empty_lists(cls, v):
        if v is not None and len(v) == 0:
            return None
        return v


class SearchRuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    enabled: bool = True
    filters: SearchRuleFilters = Field(default_factory=SearchRuleFilters)
    match: RuleMatchSpec = Field(default_factory=RuleMatchSpec)
    bid_template: BidTemplate = Field(default_factory=BidTemplate)
    min_score: float = 0.0


class SearchRuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    enabled: bool | None = None
    filters: SearchRuleFilters | None = None
    match: RuleMatchSpec | None = None
    bid_template: BidTemplate | None = None
    min_score: float | None = None


class SearchRuleOut(ORMBase):
    id: int
    name: str
    enabled: bool
    filters: dict[str, Any]
    match: dict[str, Any]
    bid_template: dict[str, Any]
    min_score: float
    last_checked_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ScanTestResult(BaseModel):
    rule_id: int
    fetched: int
    matched: int
    candidates: list[dict[str, Any]]
    errors: list[str]


# -------------------------------------------------------------------- account


class AccountStatus(BaseModel):
    connected: bool
    user_id: int | None = None
    username: str | None = None
    display_name: str | None = None
    last_validated_at: datetime | None = None
    token_configured: bool
    detail: str | None = None


class ProfileCreateRequest(BaseModel):
    profile_name: str = Field(min_length=1, max_length=255)
    tagline: str = Field(min_length=1)
    description: str = Field(min_length=1)
    hourly_rate: float = Field(gt=0)
    skill_ids: list[int] = Field(default_factory=list)


class ProfileUpdateRequest(BaseModel):
    profile_id: int
    profile_name: str | None = Field(default=None, max_length=255)
    tagline: str | None = None
    description: str | None = None
    hourly_rate: float | None = Field(default=None, gt=0)
    skill_ids: list[int] | None = None


class SkillsRequest(BaseModel):
    job_ids: list[int] = Field(min_length=1)


class SkillItem(BaseModel):
    id: int
    name: str | None = None


# -------------------------------------------------------------------- projects


class ProjectOut(ORMBase):
    id: int
    title: str
    project_type: str | None
    currency_code: str | None
    budget_min: float | None
    budget_max: float | None
    hourly_min: float | None
    hourly_max: float | None
    bid_count: int | None
    avg_bid: float | None
    seo_url: str | None
    submitdate: int | None
    status: str
    first_seen_at: datetime


class MatchOut(BaseModel):
    project: ProjectOut
    score: float
    reasons: list[Any]
    notified: bool
    created_at: datetime


# --------------------------------------------------------------------- actions


class ActionOut(ORMBase):
    id: int
    token: str
    action_type: str
    status: str
    project_id: int | None
    bid_id: int | None
    thread_id: int | None
    preview_text: str | None
    payload: dict[str, Any]
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    expires_at: datetime
    created_at: datetime
    executed_at: datetime | None


class BidDraftRequest(BaseModel):
    project_id: int
    amount: float | None = Field(default=None, gt=0)
    period_days: int | None = Field(default=None, gt=0, le=3650)
    milestone_percentage: float | None = Field(default=None, ge=0, le=100)
    description: str = ""
    profile_id: int | None = None
    rule_id: int | None = None


class MessageDraftRequest(BaseModel):
    thread_id: int
    message: str = Field(min_length=1, max_length=4000)


class AuditOut(ORMBase):
    id: int
    event: str
    subject: str | None
    data: dict[str, Any]
    created_at: datetime
