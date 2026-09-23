"""Valuation: free daily prices (Stooq, no API key) + multiples from metrics.

Prices are cached per ticker/day in the ``prices`` table so a page load
never needs more than one Stooq call per ticker per day.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import os

import httpx
from sqlalchemy.orm import Session

from .models import Price

_STOOQ_URL = "https://stooq.com/q/l/?s={symbol}&f=sd2t2ohlcv&h&e=csv"


def _proxy_kwargs() -> dict:
    """Explicit proxy + trust_env=False (see app.edgar.client._resolve_proxy)."""
    from .edgar.client import _resolve_proxy, _ssl_context

    return {"trust_env": False, "proxy": _resolve_proxy(), "verify": _ssl_context()}


def _parse_stooq_csv(text: str) -> tuple[dt.date, float] | None:
    """Parse Stooq CSV; returns (date, close) or None when unavailable."""
    try:
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            close = row.get("Close")
            date_s = row.get("Date")
            if close and close.upper() != "N/D" and date_s:
                return dt.date.fromisoformat(date_s), float(close)
    except Exception:
        return None
    return None


def fetch_stooq_close(ticker: str, timeout: float = 8.0) -> tuple[dt.date, float] | None:
    """Fetch the latest close from Stooq (no key). Returns None on failure.

    Kept short on purpose: quotes are best-effort and the dashboard must
    not stall when Stooq is unreachable.
    """
    symbol = f"{ticker.upper()}.us"
    try:
        resp = httpx.get(_STOOQ_URL.format(symbol=symbol), timeout=timeout, **_proxy_kwargs())
        resp.raise_for_status()
        return _parse_stooq_csv(resp.text)
    except Exception:
        return None


def get_price(ticker: str, session: Session, on_date: dt.date | None = None) -> dict | None:
    """Return {'date': date, 'close': float, 'cached': bool} for a ticker.

    Uses the cached row for ``on_date`` (default today) when present,
    otherwise fetches from Stooq and stores it. Returns None when the
    price is unavailable.
    """
    ticker = ticker.upper()
    day = on_date or dt.date.today()
    row = session.query(Price).filter_by(ticker=ticker, date=day).one_or_none()
    if row and row.close:
        return {"date": row.date, "close": row.close, "cached": True}
    fetched = fetch_stooq_close(ticker)
    if not fetched:
        return None
    fdate, close = fetched
    existing = session.query(Price).filter_by(ticker=ticker, date=fdate).one_or_none()
    if existing is None:
        session.add(Price(ticker=ticker, date=fdate, close=close))
        session.flush()
    elif not existing.close:
        existing.close = close
        session.flush()
    return {"date": fdate, "close": close, "cached": False}


def _safe_div(num: float | None, den: float | None) -> float | None:
    if num is None or den is None or den == 0:
        return None
    return num / den


def compute_valuation(
    metrics: dict, price: float | None
) -> dict[str, float | None]:
    """Compute market cap, EV, and multiples. All-None-safe.

    EBITDA is approximated as TTM operating income + TTM depreciation.
    """
    shares = metrics.get("shares_outstanding")
    mcap = price * shares if price and shares else None
    debt = metrics.get("total_debt")
    cash = metrics.get("cash")
    ev = None
    if mcap is not None:
        ev = mcap + (debt or 0) - (cash or 0)

    ttm_ni = metrics.get("ttm_net_income")
    eps = _safe_div(ttm_ni, shares)
    ebitda = None
    op = metrics.get("ttm_operating_income")
    depr = metrics.get("ttm_depreciation")
    if op is not None:
        ebitda = op + (depr or 0)

    return {
        "price": price,
        "market_cap": mcap,
        "enterprise_value": ev,
        "eps_ttm": eps,
        "pe": _safe_div(price, eps),
        "ps": _safe_div(mcap, metrics.get("ttm_revenue")),
        "ev_ebitda": _safe_div(ev, ebitda),
        "fcf_yield": _safe_div(metrics.get("ttm_fcf"), mcap),
        "ebitda_ttm": ebitda,
    }
