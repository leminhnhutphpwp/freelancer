"""FastAPI application factory and entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.routes import account, actions, health, projects, rules, threads
from app.config import Settings, get_settings
from app.db.session import make_engine, make_session_factory
from app.freelancer.client import FreelancerClient, FreelancerError
from app.log import configure_logging, get_logger, sanitize
from app.services.telegram_notify import build_notifier

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    engine = make_engine(settings.database_url, echo=settings.db_echo)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.notifier = build_notifier(settings)
    if settings.freelancer_access_token:
        app.state.freelancer_client = FreelancerClient(
            access_token=settings.freelancer_access_token,
            base_url=settings.freelancer_base_url,
            timeout=settings.freelancer_timeout_seconds,
            max_retries=settings.freelancer_max_retries,
        )
    log.info(
        "api.startup",
        extra={
            "extra_fields": {
                "environment": settings.environment,
                "freelancer_token_configured": bool(settings.freelancer_access_token),
                "telegram_configured": bool(settings.telegram_bot_token),
                "allowed_chats": len(settings.telegram_allowed_chat_ids),
            }
        },
    )
    yield
    client = getattr(app.state, "freelancer_client", None)
    if client is not None:
        await client.aclose()
    notifier = getattr(app.state, "notifier", None)
    if isinstance(notifier, object) and hasattr(notifier, "aclose"):
        await notifier.aclose()
    await engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    if settings.is_production and not settings.admin_api_key:
        raise RuntimeError("ADMIN_API_KEY must be set in production")

    app = FastAPI(
        title="Freelancer Assistant API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/v1/docs",
        openapi_url="/api/v1/openapi.json",
    )
    app.state.settings = settings

    app.include_router(health.router, prefix="/health")
    v1_prefix = "/api/v1"
    app.include_router(account.router, prefix=v1_prefix)
    app.include_router(rules.router, prefix=v1_prefix)
    app.include_router(projects.router, prefix=v1_prefix)
    app.include_router(actions.router, prefix=v1_prefix)
    app.include_router(threads.router, prefix=v1_prefix)

    @app.get("/health/ready", tags=["health"])
    async def ready(request: Request) -> JSONResponse:
        try:
            async with request.app.state.session_factory() as session:
                await session.execute(text("SELECT 1"))
            db_ok = True
        except Exception as exc:  # noqa: BLE001
            log.warning("readiness.db_failed", extra={"extra_fields": {"error": str(exc)}})
            db_ok = False
        code = 200 if db_ok else 503
        return JSONResponse({"status": "ok" if db_ok else "degraded", "database": db_ok}, status_code=code)

    @app.exception_handler(FreelancerError)
    async def freelancer_error_handler(request: Request, exc: FreelancerError) -> JSONResponse:
        mapping = {
            "auth": 424,  # token invalid/expired -> Failed Dependency
            "rate_limited": 429,
            "not_found": 404,
            "server": 502,
            "network": 502,
            "invalid_response": 502,
            "client": 400,
        }
        code = mapping.get(exc.kind, 400)
        return JSONResponse(
            {
                "detail": sanitize(
                    {
                        "error": str(exc),
                        "kind": exc.kind,
                        "freelancer_error_code": exc.error_code,
                        "request_id": exc.request_id,
                    }
                )
            },
            status_code=code,
        )

    return app


app = create_app()
