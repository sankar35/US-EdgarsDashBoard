"""Ingestion: ticker -> CIK -> submissions + companyfacts -> DB tables.

``ingest_company`` upserts the company row, stores recent filings
(form / filing date / accession / primary document / URL), and normalizes
us-gaap facts into the ``facts`` table. No XBRL parsing is needed: the
companyfacts JSON is the structured fundamentals source.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy.orm import Session

from ..models import Company, Fact, Filing
from .client import EdgarClient

# Canonical concept -> us-gaap concepts in priority order (first hit wins).
CONCEPTS: dict[str, list[str]] = {
    "revenues": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"],
    "net_income": ["NetIncomeLoss"],
    "operating_income": ["OperatingIncomeLoss"],
    "gross_profit": ["GrossProfit"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "assets": ["Assets"],
    "liabilities": ["Liabilities"],
    "long_term_debt": ["LongTermDebt", "LongTermDebtNoncurrent"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "equity": ["StockholdersEquity"],
    "shares_outstanding": ["CommonStockSharesOutstanding"],
    "diluted_shares": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
    "depreciation": ["DepreciationDepletionAndAmortization"],
}


def _parse_date(value: Any) -> dt.date | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError:
        return None


def _upsert_company(session: Session, ticker: str, cik10: str, submissions: dict[str, Any]) -> Company:
    exchanges = submissions.get("exchanges") or []
    company = session.query(Company).filter_by(ticker=ticker.upper()).one_or_none()
    if company is None:
        company = Company(ticker=ticker.upper(), cik=cik10)
        session.add(company)
    company.cik = cik10
    company.name = submissions.get("name") or company.name or ticker.upper()
    company.sic = str(submissions.get("sic") or "") or None
    company.sic_description = submissions.get("sicDescription")
    company.exchange = exchanges[0] if exchanges else None
    session.flush()
    return company


def _store_filings(
    session: Session,
    company: Company,
    client: EdgarClient,
    submissions: dict[str, Any],
    forms: list[str] | None,
) -> tuple[int, int]:
    """Store recent filings; returns (added, total_seen)."""
    recent = (submissions.get("filings") or {}).get("recent") or {}
    forms_lc = {f.upper() for f in forms} if forms else None
    added = 0
    total = 0
    n = len(recent.get("form", []))
    for i in range(n):
        form = str(recent["form"][i]).upper()
        total += 1
        if forms_lc and form not in forms_lc:
            continue
        accession = str(recent["accessionNumber"][i])
        if session.query(Filing).filter_by(accession=accession).one_or_none():
            continue
        primary_doc = str(recent.get("primaryDocument", [""])[i] or "")
        filing = Filing(
            company_id=company.id,
            form=form,
            filing_date=_parse_date(recent.get("filingDate", [None])[i]),
            accession=accession,
            primary_doc=primary_doc,
            url=client.filing_url(company.cik, accession, primary_doc),
        )
        session.add(filing)
        added += 1
    session.flush()
    return added, total


def _store_facts(session: Session, company: Company, companyfacts: dict[str, Any]) -> int:
    """Normalize us-gaap facts into the facts table. Returns rows added/updated."""
    us_gaap = (companyfacts.get("facts") or {}).get("us-gaap") or {}
    count = 0
    for canonical, candidates in CONCEPTS.items():
        entries = None
        # Pick the candidate with the best coverage: companies change which
        # us-gaap tag they use over time (e.g. Apple moved from "Revenues" to
        # "RevenueFromContractWithCustomerExcludingAssessedTax"). Score by
        # recency of the latest period first, then entry count.
        best_score: tuple[str, int] | None = None
        for cand in candidates:
            units = (us_gaap.get(cand) or {}).get("units") or {}
            # Monetary concepts use USD; share counts use "shares".
            for unit in ("USD", "shares"):
                cand_entries = units.get(unit) or []
                framed = [e for e in cand_entries if e.get("frame")]
                if not framed:
                    continue
                latest = max(str(e.get("end") or "") for e in framed)
                score = (latest, len(framed))
                if best_score is None or score > best_score:
                    best_score = score
                    entries = framed
        if not entries:
            continue
        for entry in entries:
            frame = entry.get("frame")
            if not frame:
                continue
            try:
                value = float(entry["val"])
            except (TypeError, ValueError, KeyError):
                continue
            existing = (
                session.query(Fact)
                .filter_by(company_id=company.id, concept=canonical, frame=str(frame))
                .one_or_none()
            )
            if existing is None:
                existing = Fact(company_id=company.id, concept=canonical, frame=str(frame))
                session.add(existing)
            existing.period_end = _parse_date(entry.get("end"))
            existing.value = value
            existing.form = entry.get("form")
            existing.filed_date = _parse_date(entry.get("filed"))
            count += 1
    session.flush()
    return count


def ingest_company(
    ticker: str,
    session: Session,
    client: EdgarClient,
    forms: list[str] | None = None,
) -> dict[str, Any]:
    """Ingest one ticker: company, recent filings, and normalized facts.

    Returns a summary dict. Never invents data: everything stored comes
    from the EDGAR JSON endpoints.
    """
    ticker = ticker.upper().strip()
    cik, _title = client.resolve_ticker(ticker)
    submissions = client.get_submissions(cik)
    company = _upsert_company(session, ticker, cik, submissions)
    filings_added, filings_seen = _store_filings(session, company, client, submissions, forms)
    companyfacts = client.get_companyfacts(cik)
    facts_count = _store_facts(session, company, companyfacts)
    return {
        "ticker": ticker,
        "cik": cik,
        "name": company.name,
        "filings_added": filings_added,
        "filings_seen": filings_seen,
        "facts_stored": facts_count,
    }
