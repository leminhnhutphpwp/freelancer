"""Background worker: periodic rule scans, award polling, thread sync, action expiry.

Each job is wrapped in a PG advisory lock so multiple worker replicas are safe
(locking is a no-op on SQLite where jobs run single-process).
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.models import FreelancerAccount, SearchRule
from app.db.session import make_engine, make_session_factory
from app.freelancer.client import FreelancerClient, FreelancerError
from app.log import configure_logging, get_logger
from app.services import actions as action_service
from app.services import bids as bid_service
from app.services import threads as thread_service
from app.services.notify import Notifier
from app.services.search import scan_rule
from app.services.telegram_notify import build_notifier

log = get_logger(__name__)

ADVISORY_LOCK_NS = 721_001  # arbitrary namespace for our locks


class DatabaseLock:
    """Advisory lock helper; uncontended no-op outside PostgreSQL."""

    def __init__(self, session: AsyncSession, key: int) -> None:
        self.session = session
        self.key = key
        self.acquired = False

    async def __aenter__(self) -> DatabaseLock:
        bind = self.session.get_bind()
        if bind.dialect.name != "postgresql":
            self.acquired = True
            return self
        result = await self.session.execute(
            text("SELECT pg_try_advisory_lock(:ns, :key)"),
            {"ns": ADVISORY_LOCK_NS, "key": self.key},
        )
        self.acquired = bool(result.scalar())
        return self

    async def __aexit__(self, *exc: object) -> None:
        bind = self.session.get_bind()
        if self.acquired and bind.dialect.name == "postgresql":
            with contextlib.suppress(Exception):
                await self.session.execute(
                    text("SELECT pg_advisory_unlock(:ns, :key)"),
                    {"ns": ADVISORY_LOCK_NS, "key": self.key},
                )


class WorkerApp:
    def __init__(
        self,
        settings: Settings,
        *,
        notifier: Notifier | None = None,
        client: FreelancerClient | None = None,
    ) -> None:
        self.settings = settings
        self.notifier = notifier
        self._external_client = client
        self.engine = make_engine(settings.database_url, echo=settings.db_echo)
        self.session_factory = make_session_factory(self.engine)
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

    async def _client(self) -> FreelancerClient:
        if self._external_client is not None:
            return self._external_client
        if not self.settings.freelancer_access_token:
            raise FreelancerError("FREELANCER_ACCESS_TOKEN is not configured", kind="auth")
        return FreelancerClient(
            access_token=self.settings.freelancer_access_token,
            base_url=self.settings.freelancer_base_url,
            timeout=self.settings.freelancer_timeout_seconds,
            max_retries=self.settings.freelancer_max_retries,
        )

    def request_stop(self) -> None:
        self._stop.set()

    async def stop(self) -> None:
        self.request_stop()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.engine.dispose()

    async def run(self) -> None:
        configure_logging(self.settings.log_level)
        cadence = [
            ("scan_rules", self.settings.search_poll_interval_seconds, self.job_scan_rules),
            ("poll_awards", self.settings.bid_poll_interval_seconds, self.job_poll_awards),
            ("sync_threads", self.settings.thread_poll_interval_seconds, self.job_sync_threads),
            ("expire_actions", 60, self.job_expire_actions),
        ]
        self._tasks = [
            asyncio.create_task(self._loop(name, interval, fn), name=f"worker:{name}") for name, interval, fn in cadence
        ]
        log.info(
            "worker.started",
            extra={
                "extra_fields": {
                    "jobs": [t.get_name() for t in self._tasks],
                }
            },
        )
        await self._stop.wait()
        await self.stop()

    async def _loop(self, name: str, interval: int, fn: Any) -> None:
        # Stagger initial runs so startup is not a thundering herd.
        await self._sleep(interval * 0.1)
        while not self._stop.is_set():
            try:
                await fn()
            except FreelancerError as exc:
                log.error(
                    f"worker.{name}.freelancer_error",
                    extra={"extra_fields": {"error": str(exc), "kind": exc.kind}},
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - worker must never die on a job failure
                log.exception(f"worker.{name}.failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=max(15, interval))
            except TimeoutError:
                pass

    async def _sleep(self, seconds: float) -> None:
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)

    # ------------------------------------------------------------------ jobs

    async def job_scan_rules(self) -> None:
        async with self.session_factory() as session:
            rules = list(await session.scalars(select(SearchRule).where(SearchRule.enabled.is_(True))))
            for rule in rules:
                lock = DatabaseLock(session, rule.id)
                async with lock:
                    if not lock.acquired:
                        continue
                    client = await self._client()
                    try:
                        await scan_rule(
                            session,
                            client,
                            rule,
                            self.notifier,
                            chat_ids=list(self.settings.telegram_allowed_chat_ids),
                            page_limit=self.settings.search_page_limit,
                        )
                    finally:
                        if self._external_client is None:
                            await client.aclose()

    async def job_poll_awards(self) -> None:
        account = await self._account()
        if account is None:
            return
        async with self.session_factory() as session:
            lock = DatabaseLock(session, 900_001)
            async with lock:
                if not lock.acquired:
                    return
                client = await self._client()
                try:
                    await bid_service.poll_awards(
                        session,
                        client,
                        user_id=account.user_id,
                        notifier=self.notifier,
                        chat_ids=list(self.settings.telegram_allowed_chat_ids),
                    )
                finally:
                    if self._external_client is None:
                        await client.aclose()

    async def job_sync_threads(self) -> None:
        account = await self._account()
        if account is None:
            return
        async with self.session_factory() as session:
            lock = DatabaseLock(session, 900_002)
            async with lock:
                if not lock.acquired:
                    return
                client = await self._client()
                try:
                    await thread_service.sync_threads(
                        session,
                        client,
                        my_user_id=account.user_id,
                        notifier=self.notifier,
                        chat_ids=list(self.settings.telegram_allowed_chat_ids),
                    )
                finally:
                    if self._external_client is None:
                        await client.aclose()

    async def job_expire_actions(self) -> None:
        async with self.session_factory() as session:
            await action_service.expire_stale(session)

    async def _account(self) -> FreelancerAccount | None:
        async with self.session_factory() as session:
            return await session.scalar(select(FreelancerAccount).limit(1))


async def run_worker() -> None:  # pragma: no cover - process entry point
    settings = get_settings()
    configure_logging(settings.log_level)
    worker = WorkerApp(settings, notifier=build_notifier(settings))

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, worker.request_stop)
    await worker.run()
