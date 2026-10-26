"""Engine/session construction helpers."""

from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import utcnow  # noqa: F401  (re-export convenience)


def make_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    kwargs: dict = {"echo": echo, "pool_pre_ping": True}
    if database_url.startswith("sqlite"):
        # StaticPool keeps an in-memory sqlite db alive across connections.
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in database_url:
            from sqlalchemy.pool import StaticPool

            kwargs["poolclass"] = StaticPool
    engine = create_async_engine(database_url, **kwargs)

    if database_url.startswith("sqlite"):

        @event.listens_for(engine.sync_engine, "connect")
        def _fk_pragma(dbapi_conn, _conn_record):  # enforce FKs in sqlite tests
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def create_engine_from_url(database_url: str, echo: bool = False) -> AsyncEngine:
    return make_engine(database_url, echo=echo)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


def get_session_factory() -> async_sessionmaker[AsyncSession]:  # pragma: no cover - convenience
    from app.config import get_settings

    engine = make_engine(get_settings().database_url, echo=get_settings().db_echo)
    return make_session_factory(engine)
