"""Pure project-filtering and scoring functions (no I/O).

A rule has two config sections:
- ``filters``: passed server-side to /projects/0.1/projects/active/
- ``match``: client-side hard filters + scoring spec

``match`` schema (all optional):
{
  "must_keywords": ["fastapi", ...],       # title+description must contain ALL (any-of per entry list allowed)
  "any_keywords": ["python", "django"],    # must contain at least one
  "exclude_keywords": ["crypto", "adult"], # must contain NONE (hard reject)
  "require_skills": [123, 456],            # project job ids must include ALL
  "any_skills": [789],                     # project job ids must include at least one
  "max_bid_count": 15,
  "max_age_hours": 48,
  "preferred_keywords": ["long term", "urgent"],  # scoring only
  "weights": {"keywords": 3.0, "skills": 2.0, "budget": 1.0, "freshness": 1.0, "low_bids": 1.0}
}
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

DEFAULT_WEIGHTS = {"keywords": 3.0, "skills": 2.0, "budget": 1.0, "freshness": 1.0, "low_bids": 1.0}


@dataclass
class MatchResult:
    accepted: bool
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    rejected_because: str | None = None


def project_text(project: dict[str, Any]) -> str:
    parts = [project.get("title") or "", project.get("description") or ""]
    return " ".join(parts).lower()


def project_job_ids(project: dict[str, Any]) -> set[int]:
    jobs = project.get("jobs") or []
    ids: set[int] = set()
    for job in jobs:
        if isinstance(job, dict) and job.get("id") is not None:
            ids.add(int(job["id"]))
        elif isinstance(job, (int, str)) and str(job).isdigit():
            ids.add(int(job))
    return ids


def _keyword_hits(text: str, keywords: list[str]) -> tuple[list[str], list[str]]:
    hits, misses = [], []
    for kw in keywords:
        needle = kw.lower().strip()
        if not needle:
            continue
        # Allow alternatives inside one entry: "react|next.js"
        alternatives = [a.strip() for a in needle.split("|") if a.strip()] or [needle]
        if any(a in text for a in alternatives):
            hits.append(kw)
        else:
            misses.append(kw)
    return hits, misses


def evaluate_project(
    project: dict[str, Any],
    match_spec: dict[str, Any],
    *,
    min_score: float = 0.0,
    now_ts: float | None = None,
) -> MatchResult:
    """Hard-filter a project, then score it. Returns accept/reject with reasons."""
    spec = match_spec or {}
    weights = {**DEFAULT_WEIGHTS, **(spec.get("weights") or {})}
    text = project_text(project)
    job_ids = project_job_ids(project)
    reasons: list[str] = []

    # ---- hard rejects -------------------------------------------------------
    excludes = [str(k) for k in spec.get("exclude_keywords") or []]
    excluded_hits, _ = _keyword_hits(text, excludes)
    if excluded_hits:
        return MatchResult(False, rejected_because=f"contains excluded keyword(s): {', '.join(excluded_hits)}")

    must = [str(k) for k in spec.get("must_keywords") or []]
    _, must_misses = _keyword_hits(text, must)
    if must_misses:
        return MatchResult(False, rejected_because=f"missing required keyword(s): {', '.join(must_misses)}")

    any_kws = [str(k) for k in spec.get("any_keywords") or []]
    if any_kws:
        hits, _ = _keyword_hits(text, any_kws)
        if not hits:
            return MatchResult(False, rejected_because="none of the any_keywords matched")

    require_skills = [int(s) for s in spec.get("require_skills") or []]
    missing_skills = [s for s in require_skills if s not in job_ids]
    if missing_skills:
        return MatchResult(False, rejected_because=f"missing required skill ids: {missing_skills}")

    any_skills = [int(s) for s in spec.get("any_skills") or []]
    if any_skills and not (set(any_skills) & job_ids):
        return MatchResult(False, rejected_because="none of the any_skills present")

    max_bids = spec.get("max_bid_count")
    bid_count = project.get("bidcount")
    if bid_count is None:
        bid_count = project.get("bid_count")
    if max_bids is not None and bid_count is not None and int(bid_count) > int(max_bids):
        return MatchResult(False, rejected_because=f"bid count {bid_count} exceeds cap {max_bids}")

    max_age_hours = spec.get("max_age_hours")
    submit_ts = project.get("submitdate") or project.get("submit_date")
    if max_age_hours and submit_ts:
        age_hours = ((now_ts or time.time()) - float(submit_ts)) / 3600.0
        if age_hours > float(max_age_hours):
            return MatchResult(False, rejected_because=f"older than {max_age_hours}h ({age_hours:.1f}h)")

    # ---- scoring ------------------------------------------------------------
    score = 0.0
    preferred = [str(k) for k in spec.get("preferred_keywords") or []]
    phits, _ = _keyword_hits(text, preferred)
    if phits:
        score += weights["keywords"] * len(phits)
        reasons.append(f"preferred keywords: {', '.join(phits)}")

    configured_skills = {int(s) for s in (spec.get("require_skills") or []) + (spec.get("any_skills") or [])}
    skill_overlap = len(job_ids & configured_skills)
    if skill_overlap:
        score += weights["skills"] * skill_overlap
        reasons.append(f"{skill_overlap} configured skill id(s) matched")

    budget = project.get("budget") or {}
    max_budget = None
    if isinstance(budget, dict):
        max_budget = budget.get("maximum") or budget.get("minimum")
    # Fallback to flat fields for compacted payloads.
    if max_budget is None:
        max_budget = project.get("budget_max") or project.get("min_avg_price") or project.get("avg_bid_price")
    try:
        if max_budget is not None and float(max_budget) > 0:
            # Gentle log-scale: +1 at $100, +2 at $10k, +3 at $1M.
            import math

            bonus = weights["budget"] * min(3.0, max(0.0, math.log10(float(max_budget)) - 2.0))
            score += bonus
            if bonus > 0:
                reasons.append(f"budget signal ${max_budget}")
    except (TypeError, ValueError):
        pass

    if submit_ts:
        age_hours = ((now_ts or time.time()) - float(submit_ts)) / 3600.0
        freshness = max(0.0, 1.0 - age_hours / 72.0)
        score += weights["freshness"] * freshness * 3
        if freshness > 0.5:
            reasons.append(f"fresh ({age_hours:.1f}h old)")

    if max_bids is not None and bid_count is not None:
        try:
            ratio = 1.0 - (float(bid_count) / float(max_bids))
            score += weights["low_bids"] * max(0.0, min(1.0, ratio)) * 2
            if ratio > 0.4:
                reasons.append(f"low competition ({bid_count} bids)")
        except ZeroDivisionError:
            pass

    accepted = score >= float(min_score)
    if not accepted:
        return MatchResult(False, score=score, rejected_because=f"score {score:.2f} below threshold {min_score}")
    if not reasons:
        reasons.append("passed all hard filters")
    return MatchResult(True, score=round(score, 3), reasons=reasons)
