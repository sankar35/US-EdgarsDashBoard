#!/usr/bin/env python3
"""Ingest EDGAR data for tickers.

Example:
    python scripts/ingest_watchlist.py --tickers AAPL,MSFT --forms 10-K,10-Q,8-K,4

Adds each ticker to the watchlist (if missing), pulls its submissions and
companyfacts from EDGAR, and stores a metrics/score snapshot.
Requires SEC_CONTACT_EMAIL in the environment (see .env.example).
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import metrics as metrics_mod  # noqa: E402
from app import scoring as scoring_mod  # noqa: E402
from app.changes import company_by_ticker  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import init_db, session_scope  # noqa: E402
from app.edgar.client import EdgarClient  # noqa: E402
from app.edgar.ingest import ingest_company  # noqa: E402
from app.models import Fact, MetricsSnapshot, Watchlist  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest EDGAR data for tickers.")
    parser.add_argument("--tickers", required=True, help="Comma-separated tickers, e.g. AAPL,MSFT")
    parser.add_argument(
        "--forms",
        default="10-K,10-Q,8-K,4",
        help="Comma-separated filing forms to store (default: 10-K,10-Q,8-K,4)",
    )
    args = parser.parse_args()

    settings = get_settings()  # fails fast when SEC_CONTACT_EMAIL is unset
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    forms = [f.strip().upper() for f in args.forms.split(",") if f.strip()]
    init_db()

    import datetime as dt

    with EdgarClient(settings.sec_contact_email) as client, session_scope() as session:
        for ticker in tickers:
            if not session.query(Watchlist).filter_by(ticker=ticker).one_or_none():
                session.add(Watchlist(ticker=ticker))
                print(f"Added {ticker} to watchlist")
            try:
                summary = ingest_company(ticker, session, client, forms=forms)
            except KeyError:
                print(f"ERROR: unknown ticker {ticker} (not in SEC ticker map)")
                continue
            except Exception as exc:
                print(f"ERROR ingesting {ticker}: {exc}")
                continue
            print(
                f"{ticker}: {summary['name']} | "
                f"filings +{summary['filings_added']} (seen {summary['filings_seen']}) | "
                f"facts stored {summary['facts_stored']}"
            )
            # Metrics + score snapshot for score-mover tracking in the digest.
            company = company_by_ticker(ticker, session)
            facts = metrics_mod.build_facts_index(
                session.query(Fact).filter_by(company_id=company.id).all()
            )
            m = metrics_mod.compute_metrics(facts)
            s = scoring_mod.score_company(m)
            snap = MetricsSnapshot(
                company_id=company.id,
                asof_date=dt.date.today(),
                metrics_json=json.dumps(m, default=str),
                score_json=json.dumps(s, default=str),
            )
            session.add(snap)
    print("Done.")


if __name__ == "__main__":
    main()
