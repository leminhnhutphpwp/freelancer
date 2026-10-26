"""Telegram-friendly text rendering for cards (kept out of bot module for testing)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def project_link(project: dict[str, Any] | None) -> str | None:
    if not project:
        return None
    seo = project.get("seo_url")
    if seo:
        return f"https://www.freelancer.com/projects/{seo.strip('/')}"
    pid = project.get("id")
    if pid:
        # Legacy preview URL still resolves for logged-in users.
        return f"https://www.freelancer.com/projects/preview.php?i={pid}"
    return None


def _fmt_money(value: float | None) -> str | None:
    if value is None:
        return None
    return f"${value:,.0f}" if float(value) == int(float(value)) else f"${float(value):,.2f}"


def budget_line(project: dict[str, Any]) -> str:
    ptype = (project.get("type") or project.get("project_type") or "").lower()
    budget = project.get("budget") or {}
    if isinstance(budget, dict):
        lo = budget.get("minimum")
        hi = budget.get("maximum")
        currency = budget.get("currency", {}).get("code") if isinstance(budget.get("currency"), dict) else None
    else:
        lo, hi, currency = None, None, None
    lo = lo if lo is not None else project.get("budget_min")
    hi = hi if hi is not None else project.get("budget_max")
    currency = currency or project.get("currency_code") or "USD"
    unit = "/hr" if "hourly" in ptype else ""
    if lo is not None and hi is not None and lo != hi:
        return f"{currency} {lo:g} – {hi:g}{unit}"
    single = hi if hi is not None else lo
    if single is not None:
        return f"{currency} {single:g}{unit}"
    return "budget n/a"


def render_project_card(project: dict[str, Any], *, score: float | None, reasons: list[str]) -> str:
    lines = [
        f"🎯 {project.get('title', 'Untitled project')}",
        f"ID {project.get('id')} · {budget_line(project)}",
    ]
    bidcount = project.get("bidcount") or project.get("bid_count")
    avg = project.get("avg_bid") or project.get("avg_bid_price")
    stats_bits = []
    if bidcount is not None:
        stats_bits.append(f"{bidcount} bids")
    if avg:
        money = _fmt_money(avg)
        if money:
            stats_bits.append(f"avg {money}")
    if stats_bits:
        lines.append(" · ".join(stats_bits))
    jobs = project.get("jobs") or []
    names = [j.get("name") for j in jobs if isinstance(j, dict) and j.get("name")]
    if names:
        lines.append("Skills: " + ", ".join(names[:12]))
    if score is not None:
        lines.append(f"Score: {score:.2f}")
    if reasons:
        lines.append("Why: " + "; ".join(reasons[:6]))
    desc = (project.get("description") or "").strip().replace("\r", " ")
    if desc:
        lines += ["", desc[:600] + ("…" if len(desc) > 600 else "")]
    submitted = project.get("submitdate")
    if submitted:
        when = datetime.fromtimestamp(float(submitted), tz=UTC)
        lines += ["", f"Posted: {when:%Y-%m-%d %H:%M} UTC"]
    return "\n".join(lines)


def render_thread_card(thread: dict[str, Any], members_label: str | None) -> str:
    bits = [f"💬 Thread {thread.get('id')}"]
    ctx = thread.get("context") or {}
    if ctx.get("type") and ctx.get("type") != "none":
        bits.append(f"({ctx['type']} {ctx.get('id')})")
    if members_label:
        bits.append(members_label)
    return " · ".join(bits)
