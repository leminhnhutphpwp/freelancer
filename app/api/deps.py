"""FastAPI dependency wiring."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.freelancer.client import FreelancerClient
from app.services import notify as notify_mod


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_sessionmaker(request: Request) -> async_sessionmaker[AsyncSession]:
    return request.app.state.session_factory


async def session_dependency(request: Request) -> AsyncIterator[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        yield session


def get_client(request: Request) -> FreelancerClient:
    """Shared per-process Freelancer client, created lazily by main.lifespan."""
    client = getattr(request.app.state, "freelancer_client", None)
    if client is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Freelancer client unavailable (check FREELANCER_ACCESS_TOKEN)",
        )
    return client


def get_notifier(request: Request) -> notify_mod.Notifier | None:
    return getattr(request.app.state, "notifier", None)
