# EdgarDash 📊

A personal, $0-cost SEC/EDGAR investment-research dashboard. It pulls public
U.S. regulatory filings and structured financial facts, computes fundamentals,
valuation multiples, and a transparent rule-based score — then emails you a
daily digest of what changed.

No API keys needed for anything core. No JavaScript build. Just Python.

## Architecture

```
                    ┌─────────────────────────────┐
  SEC EDGAR ───────▶│ app/edgar/client.py          │
  - company_tickers │   rate-limited httpx (10/s)  │
  - submissions     │   User-Agent w/ contact email│
  - companyfacts    └──────────────┬──────────────┘
  Stooq prices ───▶│ app/valuation │              │
  (no key, cached) │  daily price  │              ▼
                   └───────────────┘   ┌────────────────────┐
                                       │ SQLite (SQLAlchemy) │
                                       │ companies filings   │
                                       │ facts  prices       │
                                       │ watchlist digests   │
                                       └────────┬───────────┘
                                                │
        ┌───────────────┬───────────────┬───────┴────────┬──────────────┐
        ▼               ▼               ▼                ▼              ▼
  app/metrics.py  app/valuation.py app/scoring.py  app/changes.py  app/summarize.py
  TTM/margins/    mcap, EV, P/E,   0–100 RULES      QoQ flags,      extractive
  growth/debt     P/S, EV/EBITDA,  as data          new filings     sentences
                                   (rendered                        (+LLM TODO
                                   in UI)                           stub)
        └───────────────┴───────────────┴────────────────┴──────────────┘
                                        ▼
                              app/digest.py ──▶ data/digests/YYYY-MM-DD.html
                                        │         (+ email via SMTP, optional)
                                        ▼
                              app/main.py (FastAPI + Jinja2)
                              /  dashboard   /company/{ticker}
                              /filings       /digest
                              POST/DELETE /watchlist/...

  scripts/init_db.py · scripts/ingest_watchlist.py · scripts/daily_digest.py (cron)
```

## Setup — 3 commands

```bash
cd edgar-dash
cp .env.example .env        # then set SEC_CONTACT_EMAIL=you@example.com
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/init_db.py
```

Python 3.11+ required.

## Daily use

```bash
# Ingest companies (adds them to the watchlist, stores filings + facts + snapshot)
.venv/bin/python scripts/ingest_watchlist.py --tickers AAPL,MSFT,NVDA --forms 10-K,10-Q,8-K,4

# Run the web dashboard
.venv/bin/uvicorn app.main:app --reload        # → http://127.0.0.1:8000

# Daily digest (also the cron job — see scripts/daily_digest.py header)
.venv/bin/python scripts/daily_digest.py
```

Cron (weekdays 07:30):

```
30 7 * * 1-5 cd /path/to/edgar-dash && .venv/bin/python scripts/daily_digest.py >> data/cron.log 2>&1
```

## Detailed step-by-step

### Prerequisites

- **Python 3.11+** — check with `python3 --version`
- **git** — check with `git --version`
- **pip** — usually bundled with Python
- An email address (the SEC requires a contact email in the `User-Agent`
  header for all EDGAR API requests — it's just an identifier, nothing is sent to it)

### Step 1 — Clone the repo

```bash
git clone https://github.com/sankar35/US-EdgarsDashBoard.git
cd US-EdgarsDashBoard
```

### Step 2 — Configure your environment

```bash
cp .env.example .env
```

Open `.env` in any text editor and set:

```
SEC_CONTACT_EMAIL=you@example.com
```

This email goes into the HTTP `User-Agent` header on every SEC request
(SEC fair-access policy). The app refuses to run without it and tells you so.

Optional variables (all have working defaults — skip unless you need them):

| Variable | Purpose | Default |
|---|---|---|
| `LLM_API_KEY` | Enables the LLM summarizer hook (currently stubbed — see "What's real vs stubbed") | unset (extractive summaries) |
| `DIGEST_TO_EMAIL` | Email address to send the daily digest to | unset (saves HTML file only) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM` | SMTP settings for digest email delivery | unset |

### Step 3 — Create a virtual environment and install

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

(On Windows use `.venv\Scripts\pip` instead of `.venv/bin/pip`.)

### Step 4 — Initialize the database

```bash
.venv/bin/python scripts/init_db.py
```

This creates `data/edgardash.db` (SQLite — a single file, no server needed)
with tables for companies, filings, facts, metrics, watchlist, prices, digests.

### Step 5 — Ingest your first companies

```bash
.venv/bin/python scripts/ingest_watchlist.py --tickers AAPL,MSFT,NVDA
```

What this does per ticker:
1. Resolves ticker → CIK via `sec.gov/files/company_tickers.json`
2. Downloads the filing index (`data.sec.gov/submissions/…`) and stores
   recent 10-K / 10-Q / 8-K / 4 filings (change with `--forms 10-K,10-Q`)
3. Downloads structured financials (`companyfacts` JSON) and normalizes
   ~15 key concepts (revenue, net income, OCF, capex, debt, shares, …)
4. Computes TTM metrics, valuation, and the 0–100 rule score snapshot
5. Adds the ticker to your watchlist

It respects the SEC's ≤10 requests/second limit, so the first ingest of a
ticker takes ~30–60 seconds. Re-runs only fetch what's new.

### Step 6 — Open the dashboard

```bash
.venv/bin/python -m uvicorn app.main:app --reload --port 8000
```

Then open **http://127.0.0.1:8000** in your browser:

- `/` — dashboard: watchlist table with price, score, key metrics, change flags
- `/company/AAPL` — fundamentals, valuation, score breakdown (every rule shown),
  filing history, SIC peer comparison
- `/filings` — latest filings across your watchlist
- `/digest` — preview of today's digest
- Add/remove watchlist tickers from the dashboard (POST `/watchlist/...`)

### Step 7 — Daily digest (optional but recommended)

Generate it manually any time:

```bash
.venv/bin/python scripts/daily_digest.py
```

This re-ingests new filings for every watchlist ticker, flags significant
quarter-over-quarter changes, and writes
`data/digests/YYYY-MM-DD.html` (plus emails it if SMTP is configured).

To automate it, add a cron entry (`crontab -e`) — weekdays at 07:30:

```
30 7 * * 1-5 cd /path/to/US-EdgarsDashBoard && .venv/bin/python scripts/daily_digest.py >> data/cron.log 2>&1
```

On Windows, use Task Scheduler to run `daily_digest.py` on the same schedule.

### Step 8 — Verify it works

```bash
.venv/bin/python -m pytest tests/ -q
```

12 tests covering the metrics engine and scoring rules should pass.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `RuntimeError: SEC_CONTACT_EMAIL is not set` | Copy `.env.example` → `.env` and set your email |
| `python3 --version` shows < 3.11 | Install Python 3.11+ from python.org or your package manager |
| Port 8000 already in use | Run uvicorn with `--port 8001` |
| Valuation shows `n/a` | The free Stooq quote feed may be slow/blocked on your network; the page still renders, prices retry next ingest |
| Ingest seems slow | Normal — SEC rate limit is 10 req/s; first ingest per ticker takes ~30–60s |
| `ModuleNotFoundError` | You forgot to activate/install: re-run Step 3, then prefix commands with `.venv/bin/` |

## Cost: $0

| Piece | Cost |
|---|---|
| SEC EDGAR JSON (tickers, submissions, companyfacts) | free, no key |
| Filing documents (sec.gov/Archives) | free, no key |
| Prices (Stooq CSV, cached daily) | free, no key |
| Summaries | built-in extractive (free); LLM optional |
| Hosting | your own machine |

## What's real vs stubbed

**Real, working end-to-end:**
- Ticker→CIK resolution, filing index ingestion, filing document URLs
- Structured fundamentals from `companyfacts` JSON (no XBRL parsing)
- TTM revenue / net income / OCF / capex / FCF, margins, YoY growth, dilution, debt/equity
- Valuation: market cap, EV, P/E, P/S, EV/EBITDA (EBITDA ≈ operating income + D&A), FCF yield
- 0–100 rule score with per-rule breakdown rendered in the UI
- QoQ change flags (>10% moves, sign flips), new-filing detection
- Extractive filing summarizer (keyword + position scoring, no key)
- Daily digest → HTML file (+ email if you configure SMTP)
- Watchlist add/remove, company pages, peer comparison by SIC

**Stubbed / TODO:**
- `app/summarize.py::llm_summarize` — raises `NotImplementedError` by design.
  Set `LLM_API_KEY` and wire your provider's documented API there.
- Industry peer medians only cover tickers you've ingested (no bulk SIC universe).
- No auth on the web UI — bind to localhost; it's a personal tool.

## Roadmap ideas

- 10-K/10-Q section-aware summaries (MD&A + Risk Factors only)
- Insider transactions (Form 4) as a score input
- Price history charts (Chart.js is already loaded via CDN)
- Earnings-calendar awareness in the digest
- Postgres swap (SQLAlchemy makes this a one-line change)

## Disclaimer

Educational research tool. Scores and summaries are mechanical transformations
of public filings — not investment advice.
