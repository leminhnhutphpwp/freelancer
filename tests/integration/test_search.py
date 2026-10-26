"""Integration tests for rule scanning: dedup, notifications, checkpoint."""

from __future__ import annotations

from sqlalchemy import select

from app.db.models import Notification, ProjectMatch, SearchRule
from app.services.notify import RecordingNotifier
from app.services.search import scan_rule


async def _make_rule(session, *, name="rule1"):
    rule = SearchRule(
        name=name,
        enabled=True,
        filters={"query": "python"},
        match={"must_keywords": ["fastapi"], "preferred_keywords": ["backend"]},
        bid_template={"amount": 150, "period_days": 7},
        min_score=0.0,
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    return rule


class FakeClient:
    def __init__(self, projects):
        self.projects = projects
        self.calls = 0

    async def search_projects(self, params):
        self.calls += 1
        return {"projects": self.projects}


def _project(pid, title="FastAPI backend", **kw):
    data = {
        "id": pid,
        "title": title,
        "description": "python REST API with postgres",
        "type": "fixed",
        "budget": {"minimum": 500, "maximum": 2000},
        "bidcount": 3,
        "submitdate": 1_700_000_000,
        "jobs": [{"id": 3, "name": "Python"}],
    }
    data.update(kw)
    return data


async def test_scan_creates_match_and_notifies(session):
    rule = await _make_rule(session)
    notifier = RecordingNotifier()
    client = FakeClient([_project(1)])

    outcome = await scan_rule(session, client, rule, notifier, chat_ids=[111], notify=True)

    assert outcome.fetched == 1
    assert outcome.matched == 1
    assert outcome.new_matches == 1
    assert outcome.notified == 1
    assert len(notifier.sent) == 1
    assert notifier.sent[0].chat_id == 111
    assert notifier.sent[0].buttons[0].callback_data == "proj:1"

    matches = list(await session.scalars(select(ProjectMatch)))
    assert len(matches) == 1
    notes = list(await session.scalars(select(Notification)))
    assert len(notes) == 1


async def test_scan_dedupes_on_second_run(session):
    rule = await _make_rule(session)
    notifier = RecordingNotifier()
    client = FakeClient([_project(1)])

    first = await scan_rule(session, client, rule, notifier, chat_ids=[111], notify=True)
    second = await scan_rule(session, client, rule, notifier, chat_ids=[111], notify=True)

    assert first.new_matches == 1
    assert second.new_matches == 0  # duplicate suppressed
    assert second.notified == 0
    assert len(notifier.sent) == 1  # only one notification ever
    matches = list(await session.scalars(select(ProjectMatch)))
    assert len(matches) == 1


async def test_scan_dry_run_persists_nothing(session):
    rule = await _make_rule(session)
    outcome = await scan_rule(session, FakeClient([_project(1)]), rule, None, chat_ids=[], notify=False)
    assert outcome.matched == 1
    assert outcome.new_matches == 0
    assert len(list(await session.scalars(select(ProjectMatch)))) == 0


async def test_scan_updates_checkpoint(session):
    rule = await _make_rule(session)
    await scan_rule(session, FakeClient([_project(1)]), rule, None, chat_ids=[], notify=True)
    await session.refresh(rule)
    assert rule.last_checked_at is not None


async def test_scan_skips_non_matching(session):
    rule = await _make_rule(session)
    client = FakeClient([_project(1, title="Crypto trading bot")])  # no fastapi keyword
    outcome = await scan_rule(session, client, rule, None, chat_ids=[], notify=True)
    assert outcome.matched == 0
    assert outcome.new_matches == 0
