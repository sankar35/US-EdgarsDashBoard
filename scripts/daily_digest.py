#!/usr/bin/env python3
"""Daily job: refresh watchlist filings, then build and deliver the digest.

Cron entry (runs weekdays at 07:30 local, after EDGAR's overnight updates):
    30 7 * * 1-5 cd /path/to/edgar-dash && .venv/bin/python scripts/daily_digest.py >> data/cron.log 2>&1

Requires SEC_CONTACT_EMAIL in the environment (see .env.example).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import digest as digest_mod  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import init_db, session_scope  # noqa: E402
from app.edgar.client import EdgarClient  # noqa: E402
from app.edgar.ingest import ingest_company  # noqa: E402
from app.models import Watchlist  # noqa: E402

FORMS = ["10-K", "10-Q", "8-K", "4"]


def main() -> None:
    settings = get_settings()  # fails fast when SEC_CONTACT_EMAIL is unset
    init_db()
    with EdgarClient(settings.sec_contact_email) as client, session_scope() as session:
        tickers = [w.ticker for w in session.query(Watchlist).all()]
        print(f"Refreshing {len(tickers)} watchlist tickers: {', '.join(tickers)}")
        for ticker in tickers:
            try:
                summary = ingest_company(ticker, session, client, forms=FORMS)
                print(f"  {ticker}: +{summary['filings_added']} filings, {summary['facts_stored']} facts")
            except Exception as exc:
                print(f"  ERROR {ticker}: {exc}")
        result = digest_mod.build_daily_digest(session, client=client, settings=settings)
        digest_mod.deliver_digest(result["html"], result["date"], settings=settings)
    print("Done.")


if __name__ == "__main__":
    main()
