"""Unit tests for the pure matching/scoring engine."""

from __future__ import annotations

from app.services.matching import evaluate_project


def _project(**overrides):
    base = {
        "id": 1,
        "title": "Build a FastAPI backend",
        "description": "Need a python developer for a REST API with PostgreSQL.",
        "type": "fixed",
        "budget": {"minimum": 500, "maximum": 2000, "currency": {"code": "USD"}},
        "bidcount": 5,
        "submitdate": 1_700_000_000,
        "jobs": [{"id": 3, "name": "Python"}, {"id": 7, "name": "API"}],
    }
    base.update(overrides)
    return base


def test_accepts_matching_project():
    spec = {"must_keywords": ["fastapi"], "any_keywords": ["python", "django"], "preferred_keywords": ["backend"]}
    result = evaluate_project(_project(), spec, min_score=0.0, now_ts=1_700_000_000)
    assert result.accepted is True
    assert result.score > 0
    assert any("preferred" in r for r in result.reasons)


def test_rejects_missing_required_keyword():
    spec = {"must_keywords": ["django"]}
    result = evaluate_project(_project(), spec, min_score=0.0)
    assert result.accepted is False
    assert "django" in result.rejected_because


def test_rejects_excluded_keyword():
    spec = {"exclude_keywords": ["crypto"]}
    result = evaluate_project(_project(description="crypto trading bot"), spec, min_score=0.0)
    assert result.accepted is False
    assert "excluded" in result.rejected_because


def test_any_keyword_requires_at_least_one():
    spec = {"any_keywords": ["react", "vue"]}
    result = evaluate_project(_project(), spec, min_score=0.0)
    assert result.accepted is False


def test_skill_requirements():
    spec = {"require_skills": [3, 99]}
    result = evaluate_project(_project(), spec, min_score=0.0)
    assert result.accepted is False
    assert "99" in result.rejected_because


def test_bid_count_cap():
    spec = {"max_bid_count": 3}
    result = evaluate_project(_project(bidcount=10), spec, min_score=0.0)
    assert result.accepted is False
    assert "10" in result.rejected_because


def test_max_age_hours():
    spec = {"max_age_hours": 24}
    # submitdate 48h before now_ts
    result = evaluate_project(_project(submitdate=1_700_000_000 - 48 * 3600), spec, min_score=0.0, now_ts=1_700_000_000)
    assert result.accepted is False


def test_score_threshold():
    spec = {"preferred_keywords": ["fastapi", "backend", "postgresql"]}
    result = evaluate_project(_project(), spec, min_score=100.0, now_ts=1_700_000_000)
    assert result.accepted is False
    assert "below threshold" in result.rejected_because


def test_keyword_alternatives_with_pipe():
    spec = {"must_keywords": ["react|next.js"]}
    result = evaluate_project(_project(title="Next.js dashboard"), spec, min_score=0.0)
    assert result.accepted is True


def test_compact_payload_budget_fallback():
    project = {"id": 2, "title": "x", "description": "y", "budget_max": 1500, "bid_count": 2}
    result = evaluate_project(project, {"preferred_keywords": ["x"]}, min_score=0.0, now_ts=1_700_000_000)
    assert result.accepted is True
    assert result.score > 0
