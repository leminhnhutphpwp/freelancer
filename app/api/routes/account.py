"""Routes for the connected account: connect/status, profiles, portfolio, skills."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_client, get_settings_dep, session_dependency
from app.api.security import require_admin_key
from app.config import Settings
from app.freelancer.client import FreelancerClient
from app.schemas import (
    AccountStatus,
    ProfileCreateRequest,
    ProfileUpdateRequest,
    SkillItem,
    SkillsRequest,
)
from app.services import profile as profile_service

router = APIRouter(prefix="/account", tags=["account"], dependencies=[Depends(require_admin_key)])


@router.post("/connect", response_model=AccountStatus)
async def connect(
    session: AsyncSession = Depends(session_dependency),
    client: FreelancerClient = Depends(get_client),
) -> AccountStatus:
    """Validate the configured PAT against /users/0.1/self/ and cache identity."""
    try:
        account = await profile_service.connect_account(session, client)
    except ValueError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"unexpected self response: {exc}") from exc
    return AccountStatus(
        connected=True,
        user_id=account.user_id,
        username=account.username,
        display_name=account.display_name,
        last_validated_at=account.last_validated_at,
        token_configured=True,
    )


@router.get("/status", response_model=AccountStatus)
async def account_status(
    session: AsyncSession = Depends(session_dependency),
    settings: Settings = Depends(get_settings_dep),
) -> AccountStatus:
    account = await profile_service.get_account(session)
    if account is None:
        return AccountStatus(
            connected=False,
            token_configured=bool(settings.freelancer_access_token),
            detail="run POST /api/v1/account/connect",
        )
    return AccountStatus(
        connected=account.status == "connected",
        user_id=account.user_id,
        username=account.username,
        display_name=account.display_name,
        last_validated_at=account.last_validated_at,
        token_configured=bool(settings.freelancer_access_token),
        detail=account.status,
    )


@router.get("/portfolio")
async def portfolio(
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(session_dependency),
    client: FreelancerClient = Depends(get_client),
) -> list[dict[str, Any]]:
    account = await profile_service.get_account(session)
    if account is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "account not connected yet")
    return await client.list_portfolios(int(account.user_id), limit=limit, offset=offset)


@router.get("/profiles")
async def profiles(
    session: AsyncSession = Depends(session_dependency),
    client: FreelancerClient = Depends(get_client),
) -> list[dict[str, Any]]:
    return await profile_service.list_profiles(session, client)


@router.post("/profiles", status_code=status.HTTP_201_CREATED)
async def create_profile(
    body: ProfileCreateRequest,
    client: FreelancerClient = Depends(get_client),
) -> dict[str, Any]:
    return await profile_service.create_profile(client, body.model_dump())


@router.put("/profiles")
async def update_profile(
    body: ProfileUpdateRequest,
    client: FreelancerClient = Depends(get_client),
) -> dict[str, Any]:
    payload = body.model_dump(exclude_none=True)
    try:
        return await profile_service.update_profile(client, payload)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.get("/skills", response_model=list[SkillItem])
async def read_skills(client: FreelancerClient = Depends(get_client)) -> list[SkillItem]:
    return [SkillItem(**item) for item in await profile_service.read_skills(client)]


@router.put("/skills")
async def set_skills(
    body: SkillsRequest,
    client: FreelancerClient = Depends(get_client),
) -> dict[str, Any]:
    return await profile_service.set_skills(client, body.job_ids)


@router.post("/skills/add")
async def add_skills(
    body: SkillsRequest,
    client: FreelancerClient = Depends(get_client),
) -> dict[str, Any]:
    return await profile_service.add_skills(client, body.job_ids)


@router.post("/skills/remove", status_code=status.HTTP_200_OK)
async def remove_skills(
    body: SkillsRequest,
    client: FreelancerClient = Depends(get_client),
) -> dict[str, Any]:
    return await profile_service.remove_skills(client, body.job_ids)
