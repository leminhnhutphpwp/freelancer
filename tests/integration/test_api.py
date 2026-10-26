"""API integration tests via httpx ASGI transport (sqlite)."""

from __future__ import annotations

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.db.session import make_engine
from app.main import create_app


@pytest_asyncio.fixture
async def api_client(settings):
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app = create_app(settings)
    app.state.engine = engine
    app.state.session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    app.state.notifier = None
    app.state.freelancer_client = None
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await engine.dispose()


def _auth_headers():
    return {"x-api-key": "test-admin-key"}


async def test_health_live(api_client):
    resp = await api_client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_health_ready(api_client):
    resp = await api_client.get("/health/ready")
    assert resp.status_code == 200
    assert resp.json()["database"] is True


async def test_admin_auth_required(api_client):
    resp = await api_client.get("/api/v1/rules")
    assert resp.status_code == 401


async def test_admin_auth_wrong_key(api_client):
    resp = await api_client.get("/api/v1/rules", headers={"x-api-key": "wrong"})
    assert resp.status_code == 401


async def test_rules_crud(api_client):
    headers = _auth_headers()
    # create
    resp = await api_client.post(
        "/api/v1/rules",
        headers=headers,
        json={
            "name": "python jobs",
            "filters": {"query": "python", "project_types": ["fixed"]},
            "match": {"must_keywords": ["fastapi"]},
            "bid_template": {"amount": 100, "period_days": 5},
            "min_score": 1.0,
        },
    )
    assert resp.status_code == 201, resp.text
    rule = resp.json()
    assert rule["name"] == "python jobs"
    assert rule["filters"]["query"] == "python"
    rule_id = rule["id"]

    # duplicate name -> 409
    dup = await api_client.post("/api/v1/rules", headers=headers, json={"name": "python jobs"})
    assert dup.status_code == 409

    # list
    listed = await api_client.get("/api/v1/rules", headers=headers)
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    # patch
    patched = await api_client.patch(f"/api/v1/rules/{rule_id}", headers=headers, json={"enabled": False})
    assert patched.status_code == 200
    assert patched.json()["enabled"] is False

    # delete
    deleted = await api_client.delete(f"/api/v1/rules/{rule_id}", headers=headers)
    assert deleted.status_code == 204
    assert len((await api_client.get("/api/v1/rules", headers=headers)).json()) == 0


async def test_account_status_unconnected(api_client):
    resp = await api_client.get("/api/v1/account/status", headers=_auth_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is False
    assert body["token_configured"] is True


async def test_rules_validation(api_client):
    resp = await api_client.post("/api/v1/rules", headers=_auth_headers(), json={"name": ""})
    assert resp.status_code == 422
