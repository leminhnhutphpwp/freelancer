"""Projects + match history."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_client, session_dependency
from app.api.security import require_admin_key
from app.db.models import Project, ProjectMatch
from app.freelancer.client import FreelancerClient
from app.schemas import MatchOut, ProjectOut
from app.services.render import project_link

router = APIRouter(prefix="/projects", tags=["projects"], dependencies=[Depends(require_admin_key)])


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(session_dependency),
) -> list[Project]:
    return list(await session.scalars(select(Project).order_by(Project.id.desc()).limit(limit).offset(offset)))


@router.get("/matches", response_model=list[MatchOut])
async def list_matches(
    rule_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(session_dependency),
) -> list[MatchOut]:
    stmt = (
        select(ProjectMatch, Project)
        .join(Project, ProjectMatch.project_id == Project.id)
        .order_by(ProjectMatch.id.desc())
        .limit(limit)
    )
    if rule_id is not None:
        stmt = stmt.where(ProjectMatch.rule_id == rule_id)
    rows = (await session.execute(stmt)).all()
    return [
        MatchOut(
            project=ProjectOut.model_validate(project),
            score=match.score,
            reasons=list(match.reasons or []),
            notified=match.notified,
            created_at=match.created_at,
        )
        for match, project in rows
    ]


@router.get("/{project_id}")
async def get_project(
    project_id: int,
    live: bool = False,
    session: AsyncSession = Depends(session_dependency),
    client: FreelancerClient = Depends(get_client),
) -> dict[str, Any]:
    if live:
        data = await client.get_project(project_id, full_description=True, job_details=True)
        if not data:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found on Freelancer")
        data["link"] = project_link(data)
        return data
    row = await session.get(Project, project_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not seen by any rule yet")
    return {
        "project": ProjectOut.model_validate(row).model_dump(),
        "raw": row.raw,
        "link": project_link({"id": row.id, "seo_url": row.seo_url}),
    }
