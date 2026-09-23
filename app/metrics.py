"""Fundamental metrics computed from normalized EDGAR facts.

All functions are pure: they take a facts dict and return plain dicts.
Missing concepts never crash anything — the metric is simply ``None``.

Facts dict format:
    {canonical_concept: [FactPoint(frame, period_end, value, form), ...]}

Quarterly duration frames look like ``CY2024Q4``; instant (point-in-time)
frames look like ``CY2024Q4I`` (trailing ``I``). Income-statement and
cash-flow concepts use duration frames; balance-sheet concepts use instant.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import NamedTuple


class FactPoint(NamedTuple):
    frame: str
    period_end: dt.date | None
    value: float | None
    form: str | None
    filed: dt.date | None = None


FactsDict = dict[str, list[FactPoint]]

_QTR_RE = re.compile(r"^CY\d{4}Q[1-4]$")
_QTR_INSTANT_RE = re.compile(r"^CY\d{4}Q[1-4]I$")


def build_facts_index(rows: list) -> FactsDict:
    """Build a facts dict from DB Fact rows (or any objects with the fields)."""
    index: FactsDict = {}
    for r in rows:
        index.setdefault(r.concept, []).append(
            FactPoint(
                frame=r.frame,
                period_end=r.period_end,
                value=r.value,
                form=r.form,
                filed=getattr(r, "filed_date", None),
            )
        )
    for pts in index.values():
        pts.sort(key=lambda p: (p.period_end or dt.date.min, p.frame))
    return index


def _dedupe_frames(points: list[FactPoint]) -> list[FactPoint]:
    """One point per frame: keep the latest-filed entry (restatements win)."""
    best: dict[str, FactPoint] = {}
    for p in points:
        cur = best.get(p.frame)
        key = (p.filed or dt.date.min, p.period_end or dt.date.min)
        cur_key = (cur.filed or dt.date.min, cur.period_end or dt.date.min) if cur else None
        if cur is None or key > cur_key:
            best[p.frame] = p
    return sorted(best.values(), key=lambda p: (p.period_end or dt.date.min, p.frame))


def _duration_quarters(facts: FactsDict, concept: str) -> list[tuple[dt.date, float]]:
    """Quarterly duration-frame series, oldest -> newest (deduped by frame)."""
    pts = [
        p for p in facts.get(concept, [])
        if p.period_end and p.value is not None and _QTR_RE.match(p.frame or "")
    ]
    out = [(p.period_end, p.value) for p in _dedupe_frames(pts)]
    out.sort(key=lambda t: t[0])
    return out


def _instant_series(facts: FactsDict, concept: str) -> list[tuple[dt.date, float]]:
    """Instant-frame series, oldest -> newest (deduped by frame)."""
    pts = [
        p for p in facts.get(concept, [])
        if p.period_end and p.value is not None and _QTR_INSTANT_RE.match(p.frame or "")
    ]
    out = [(p.period_end, p.value) for p in _dedupe_frames(pts)]
    out.sort(key=lambda t: t[0])
    return out


def _safe_div(num: float | None, den: float | None) -> float | None:
    if num is None or den is None or den == 0:
        return None
    return num / den


def _ttm(series: list[tuple[dt.date, float]], offset: int = 0) -> float | None:
    """Sum of 4 quarters ending ``offset`` quarters back (0 = latest TTM)."""
    if len(series) < 4 + offset:
        return None
    window = series[-(4 + offset) : len(series) - offset if offset else None]
    return sum(v for _, v in window)


def _latest_instant(facts: FactsDict, concept: str) -> float | None:
    s = _instant_series(facts, concept)
    return s[-1][1] if s else None


def compute_metrics(facts: FactsDict) -> dict[str, float | str | None]:
    """Compute TTM fundamentals, margins, growth, and balance-sheet ratios."""
    rev = _duration_quarters(facts, "revenues")
    ni = _duration_quarters(facts, "net_income")
    op = _duration_quarters(facts, "operating_income")
    gp = _duration_quarters(facts, "gross_profit")
    ocf = _duration_quarters(facts, "ocf")
    capex = _duration_quarters(facts, "capex")
    depr = _duration_quarters(facts, "depreciation")

    ttm_revenue = _ttm(rev)
    ttm_net_income = _ttm(ni)
    ttm_op = _ttm(op)
    ttm_gp = _ttm(gp)
    ttm_ocf = _ttm(ocf)
    ttm_capex = _ttm(capex)
    ttm_depr = _ttm(depr)
    # capex is usually reported as a negative cash outflow; FCF = OCF - capex
    # when capex is negative, or OCF + capex equivalently. Handle both signs:
    if ttm_ocf is not None and ttm_capex is not None:
        ttm_fcf = ttm_ocf + ttm_capex if ttm_capex < 0 else ttm_ocf - ttm_capex
    else:
        ttm_fcf = None

    prior_revenue = _ttm(rev, offset=4)
    prior_ni = _ttm(ni, offset=4)
    prior_ocf = _ttm(ocf, offset=4)
    prior_fcf = None
    if prior_ocf is not None:
        prior_capex = _ttm(capex, offset=4)
        if prior_capex is not None:
            prior_fcf = prior_ocf + prior_capex if prior_capex < 0 else prior_ocf - prior_capex

    def yoy(cur: float | None, prev: float | None) -> float | None:
        if cur is None or prev is None or prev == 0:
            return None
        return (cur - prev) / abs(prev)

    shares = _instant_series(facts, "shares_outstanding")
    shares_now = shares[-1][1] if shares else None
    shares_yoy = None
    if len(shares) >= 5:
        then = shares[-5][1]
        shares_yoy = (shares_now - then) / then if then else None

    debt = _latest_instant(facts, "long_term_debt")
    cash = _latest_instant(facts, "cash")
    equity = _latest_instant(facts, "equity")

    latest_frame = rev[-1][0].isoformat() if rev else None

    return {
        "ttm_revenue": ttm_revenue,
        "ttm_net_income": ttm_net_income,
        "ttm_operating_income": ttm_op,
        "ttm_gross_profit": ttm_gp,
        "ttm_ocf": ttm_ocf,
        "ttm_capex": ttm_capex,
        "ttm_fcf": ttm_fcf,
        "ttm_depreciation": ttm_depr,
        "revenue_yoy": yoy(ttm_revenue, prior_revenue),
        "net_income_yoy": yoy(ttm_net_income, prior_ni),
        "fcf_yoy": yoy(ttm_fcf, prior_fcf),
        "gross_margin": _safe_div(ttm_gp, ttm_revenue),
        "net_margin": _safe_div(ttm_net_income, ttm_revenue),
        "ocf_margin": _safe_div(ttm_ocf, ttm_revenue),
        "fcf_margin": _safe_div(ttm_fcf, ttm_revenue),
        "shares_outstanding": shares_now,
        "shares_yoy": shares_yoy,
        "total_debt": debt,
        "cash": cash,
        "equity": equity,
        "debt_to_equity": _safe_div(debt, equity),
        "net_debt": (debt - cash) if debt is not None and cash is not None else None,
        "latest_period_end": latest_frame,
    }
