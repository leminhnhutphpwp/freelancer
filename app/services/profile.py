"""Account connection + profile/skill operations against Freelancer."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FreelancerAccount, utcnow
from app.freelancer.client import FreelancerClient
from app.log import get_logger

log = get_logger(__name__)


async def connect_account(session: AsyncSession, client: FreelancerClient) -> FreelancerAccount:
    """Validate the PAT via /users/0.1/self/ and upsert the cached account row."""
    me = await client.get_self(display_info=True)
    user_id = me.get("id") or me.get("user_id")
    if user_id is None:
        raise ValueError("Self response did not include a user id")
    user_id = int(user_id)
    account = await session.scalar(select(FreelancerAccount).where(FreelancerAccount.user_id == user_id))
    if account is None:
        account = FreelancerAccount(user_id=user_id)
        session.add(account)
    account.username = me.get("username") or me.get("seo_url") or account.username
    account.display_name = me.get("display_name") or account.display_name
    account.email = me.get("email") or account.email
    account.status = "connected"
    account.last_validated_at = utcnow()
    account.raw_profile = me
    await session.commit()
    log.info("account.connected", extra={"extra_fields": {"user_id": user_id}})
    return account


async def get_account(session: AsyncSession) -> FreelancerAccount | None:
    return await session.scalar(select(FreelancerAccount).order_by(FreelancerAccount.id).limit(1))


async def require_account(session: AsyncSession) -> FreelancerAccount:
    account = await get_account(session)
    if account is None:
        raise RuntimeError("No connected Freelancer account. Run POST /api/v1/account/connect first.")
    return account


async def list_portfolios(session: AsyncSession, client: FreelancerClient, *, limit: int = 50) -> list[dict]:
    account = await require_account(session)
    return await client.list_portfolios(int(account.user_id), limit=limit)


async def list_profiles(session: AsyncSession, client: FreelancerClient) -> list[dict]:
    account = await require_account(session)
    return await client.list_profiles(user_id=int(account.user_id))


async def create_profile(client: FreelancerClient, payload: dict[str, Any]) -> dict[str, Any]:
    return await client.create_profile(payload)


async def update_profile(client: FreelancerClient, payload: dict[str, Any]) -> dict[str, Any]:
    clean = {k: v for k, v in payload.items() if v is not None}
    if "profile_id" not in clean:
        raise ValueError("profile_id is required")
    if len(clean) == 1:
        raise ValueError("nothing to update")
    return await client.update_profile(clean)


async def read_skills(client: FreelancerClient) -> list[dict]:
    me = await client.get_self(jobs=True)
    jobs = me.get("jobs") or []
    normalized: list[dict] = []
    for job in jobs:
        if isinstance(job, dict) and job.get("id") is not None:
            normalized.append({"id": int(job["id"]), "name": job.get("name")})
        elif isinstance(job, (int, str)) and str(job).isdigit():
            normalized.append({"id": int(job), "name": None})
    return normalized


async def set_skills(client: FreelancerClient, job_ids: list[int]) -> dict[str, Any]:
    return await client.set_skills(job_ids)


async def add_skills(client: FreelancerClient, job_ids: list[int]) -> dict[str, Any]:
    return await client.add_skills(job_ids)


async def remove_skills(client: FreelancerClient, job_ids: list[int]) -> dict[str, Any]:
    return await client.delete_skills(job_ids)
