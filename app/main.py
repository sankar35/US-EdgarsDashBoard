"""FastAPI server: dashboard, company pages, watchlist, filings, digest preview.

Server-rendered with Jinja2 (no JS build step). Every number shown comes
from the local SQLite DB or a live fetch performed during that request —
never invented.
"""

from __future__ import annotations

import datetime as dt
import statistics

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from . import changes as change_mod
from . import metrics as metrics_mod
from . import scoring as scoring_mod
from . import valuation as valuation_mod
from .db import session_scope
from .models import Company, Fact, Filing, Watchlist

app = FastAPI(title="EdgarDash")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ---------- template filters ----------

def fmt_money(v) -> str:
    if v is None:
        return "n/a"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "n/a"
    sign = "-" if v < 0 else ""
    a = abs(v)
    if a >= 1e12:
        return f"{sign}${a/1e12:.2f}T"
    if a >= 1e9:
        return f"{sign}${a/1e9:.2f}B"
    if a >= 1e6:
        return f"{sign}${a/1e6:.1f}M"
    if a >= 1e3:
        return f"{sign}${a/1e3:.1f}K"
    return f"{sign}${a:.2f}"


def fmt_pct(v, digits: int = 1) -> str:
    if v is None:
        return "n/a"
    try:
        return f"{float(v)*100:.{digits}f}%"
    except (TypeError, ValueError):
        return "n/a"


def fmt_num(v, digits: int = 2) -> str:
    if v is None:
        return "n/a"
    try:
        return f"{float(v):.{digits}f}"
    except (TypeError, ValueError):
        return "n/a"


templates.env.filters["money"] = fmt_money
templates.env.filters["pct"] = fmt_pct
templates.env.filters["num"] = fmt_num


# ---------- data helpers ----------

def company_facts(session: Session, company_id: int):
    rows = session.query(Fact).filter_by(company_id=company_id).all()
    return metrics_mod.build_facts_index(rows)


def company_overview(session: Session, company: Company) -> dict:
    """Metrics + valuation + score + price for one company (None-safe)."""
    facts = company_facts(session, company.id)
    m = metrics_mod.compute_metrics(facts)
    price_info = valuation_mod.get_price(company.ticker, session)
    price = price_info["close"] if price_info else None
    val = valuation_mod.compute_valuation(m, price)
    score = scoring_mod.score_company(m)
    return {
        "company": company,
        "metrics": m,
        "valuation": val,
        "score": score,
        "price": price,
        "price_date": price_info["date"] if price_info else None,
    }


def peer_comparison(session: Session, company: Company, overview: dict) -> dict:
    """Same-SIC peers with median score / P/E / revenue growth."""
    peers = []
    if company.sic:
        rows = (
            session.query(Company)
            .filter(Company.sic == company.sic, Company.id != company.id)
            .limit(12)
            .all()
        )
        for p in rows:
            try:
                facts = company_facts(session, p.id)
                m = metrics_mod.compute_metrics(facts)
                price_info = valuation_mod.get_price(p.ticker, session)
                price = price_info["close"] if price_info else None
                val = valuation_mod.compute_valuation(m, price)
                peers.append(
                    {
                        "ticker": p.ticker,
                        "name": p.name,
                        "score": scoring_mod.score_company(m)["total"],
                        "pe": val["pe"],
                        "revenue_yoy": m["revenue_yoy"],
                        "market_cap": val["market_cap"],
                    }
                )
            except Exception:
                continue
    def med(key):
        vals = [x[key] for x in peers if x[key] is not None]
        return statistics.median(vals) if vals else None

    return {
        "peers": peers,
        "median_score": med("score"),
        "median_pe": med("pe"),
        "median_revenue_yoy": med("revenue_yoy"),
        "sic_description": company.sic_description,
    }


# ---------- routes ----------

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    with session_scope() as session:
        rows = []
        for wl in session.query(Watchlist).order_by(Watchlist.ticker).all():
            company = change_mod.company_by_ticker(wl.ticker, session)
            if not company:
                rows.append({"ticker": wl.ticker, "missing": True})
                continue
            ov = company_overview(session, company)
            flags = change_mod.quarter_changes(company.id, session)
            rows.append(
                {
                    "ticker": company.ticker,
                    "name": company.name,
                    "price": ov["price"],
                    "score": ov["score"]["total"],
                    "metrics": ov["metrics"],
                    "valuation": ov["valuation"],
                    "flags": [f["headline"] for f in flags],
                    "missing": False,
                }
            )
        return templates.TemplateResponse(
            request, "dashboard.html", {"rows": rows, "today": dt.date.today()}
        )


@app.get("/company/{ticker}", response_class=HTMLResponse)
def company_page(request: Request, ticker: str):
    with session_scope() as session:
        company = change_mod.company_by_ticker(ticker, session)
        if not company:
            raise HTTPException(404, f"No data for {ticker.upper()} — ingest it first.")
        ov = company_overview(session, company)
        filings = (
            session.query(Filing)
            .filter_by(company_id=company.id)
            .order_by(Filing.filing_date.desc())
            .limit(40)
            .all()
        )
        flags = change_mod.quarter_changes(company.id, session)
        peers = peer_comparison(session, company, ov)
        return templates.TemplateResponse(
            request,
            "company.html",
            {
                "ov": ov,
                "filings": filings,
                "flags": flags,
                "peers": peers,
                "rules": scoring_mod.RULES,
            },
        )


@app.post("/watchlist")
def watchlist_add(ticker: str = Form(...), notes: str = Form("")):
    ticker = ticker.upper().strip()
    if not ticker:
        raise HTTPException(400, "ticker is required")
    with session_scope() as session:
        if not session.query(Watchlist).filter_by(ticker=ticker).one_or_none():
            session.add(Watchlist(ticker=ticker, notes=notes or None))
    return RedirectResponse("/", status_code=303)


@app.delete("/watchlist/{ticker}")
def watchlist_remove(ticker: str):
    with session_scope() as session:
        wl = session.query(Watchlist).filter_by(ticker=ticker.upper()).one_or_none()
        if not wl:
            raise HTTPException(404, f"{ticker.upper()} not on watchlist")
        session.delete(wl)
    return {"ok": True, "ticker": ticker.upper()}


@app.post("/watchlist/{ticker}/delete")
def watchlist_remove_form(ticker: str):
    """HTML-form friendly remove (forms can't send DELETE)."""
    watchlist_remove(ticker)
    return RedirectResponse("/", status_code=303)


@app.get("/filings", response_class=HTMLResponse)
def filings_page(request: Request):
    with session_scope() as session:
        tickers = [w.ticker for w in session.query(Watchlist).all()]
        company_ids = [
            c.id for c in session.query(Company).filter(Company.ticker.in_(tickers)).all()
        ]
        filings = (
            session.query(Filing)
            .filter(Filing.company_id.in_(company_ids))
            .order_by(Filing.filing_date.desc())
            .limit(100)
            .all()
        )
        by_id = {c.id: c.ticker for c in session.query(Company).filter(Company.id.in_(company_ids)).all()}
        return templates.TemplateResponse(
            request, "filings.html", {"filings": filings, "by_id": by_id}
        )


@app.get("/digest", response_class=HTMLResponse)
def digest_preview(request: Request):
    """Preview today's digest (builds it on demand if missing)."""
    import json as _json

    from . import digest as digest_mod
    from .config import get_settings
    from .models import Digest

    with session_scope() as session:
        row = session.query(Digest).filter_by(date=dt.date.today()).one_or_none()
        if row:
            html = open(row.html_path, encoding="utf-8").read()
        else:
            # Build without downloading filing text (no client) — fast preview.
            settings = get_settings()
            result = digest_mod.build_daily_digest(session, client=None, settings=settings)
            html = result["html"]
    return HTMLResponse(html)
