"""Change detection: quarter-over-quarter moves and new filings.

``quarter_changes`` compares the latest quarterly duration frame against
the prior quarter for key concepts and flags moves over ±10% or sign flips.
``new_filings`` lists filings ingested since a given date.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy.orm import Session

from .metrics import _duration_quarters, build_facts_index
from .models import Company, Fact, Filing

WATCHED: dict[str, str] = {
    "revenues": "Revenue",
    "net_income": "Net income",
    "gross_profit": "Gross profit",
    "ocf": "Operating cash flow",
}

CHANGE_THRESHOLD = 0.10


def quarter_changes(company_id: int, session: Session) -> list[dict[str, Any]]:
    """Flag significant QoQ moves in the latest quarter vs the prior one."""
    facts = build_facts_index(session.query(Fact).filter_by(company_id=company_id).all())
    flags: list[dict[str, Any]] = []
    for concept, label in WATCHED.items():
        series = _duration_quarters(facts, concept)
        if len(series) < 2:
            continue
        (_, latest), (_, prior) = series[-1], series[-2]
        if prior == 0:
            continue
        pct = (latest - prior) / abs(prior)
        sign_flip = (latest > 0) != (prior > 0)
        if abs(pct) >= CHANGE_THRESHOLD or sign_flip:
            direction = "up" if pct > 0 else "down"
            flags.append(
                {
                    "concept": concept,
                    "label": label,
                    "latest": latest,
                    "prior": prior,
                    "pct_change": pct,
                    "direction": direction,
                    "sign_flip": sign_flip,
                    "headline": (
                        f"{label} flipped sign QoQ" if sign_flip
                        else f"{label} {direction} {abs(pct):.1%} QoQ"
                    ),
                }
            )
    # Margin move: gross margin latest quarter vs prior quarter
    rev = _duration_quarters(facts, "revenues")
    gp = _duration_quarters(facts, "gross_profit")
    if len(rev) >= 2 and len(gp) >= 2 and rev[-1][0] == gp[-1][0]:
        def margin(q_rev, q_gp):
            return q_gp[1] / q_rev[1] if q_rev[1] else None

        m_now, m_prev = margin(rev[-1], gp[-1]), margin(rev[-2], gp[-2])
        if m_now is not None and m_prev is not None:
            delta = m_now - m_prev
            if abs(delta) >= CHANGE_THRESHOLD:
                flags.append(
                    {
                        "concept": "gross_margin",
                        "label": "Gross margin",
                        "latest": m_now,
                        "prior": m_prev,
                        "pct_change": delta,
                        "direction": "up" if delta > 0 else "down",
                        "sign_flip": False,
                        "headline": f"Gross margin moved {delta * 100:+.1f}pp QoQ",
                    }
                )
    return flags


def new_filings(
    company_id: int, session: Session, since: dt.date, forms: list[str] | None = None
) -> list[Filing]:
    """Filings for a company with filing_date after ``since`` (newest first)."""
    q = (
        session.query(Filing)
        .filter(Filing.company_id == company_id, Filing.filing_date > since)
        .order_by(Filing.filing_date.desc())
    )
    if forms:
        q = q.filter(Filing.form.in_([f.upper() for f in forms]))
    return q.all()


def company_by_ticker(ticker: str, session: Session) -> Company | None:
    return session.query(Company).filter_by(ticker=ticker.upper()).one_or_none()
