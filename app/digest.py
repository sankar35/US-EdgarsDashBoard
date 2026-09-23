"""Daily digest: new filings, significant changes, score movers, 8-K summaries.

``build_daily_digest`` assembles the digest for every watchlist company,
renders it to HTML, saves it under ``data/digests/YYYY-MM-DD.html``,
records it in the ``digests`` table, and emails it when SMTP is configured.
"""

from __future__ import annotations

import datetime as dt
import json
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape as select_jinja_autoescape
from sqlalchemy.orm import Session

from . import changes as change_mod
from . import metrics as metrics_mod
from . import scoring as scoring_mod
from . import summarize as summarize_mod
from . import valuation as valuation_mod
from .config import Settings, get_settings, smtp_configured
from .models import Digest, Fact, Filing, MetricsSnapshot, Watchlist

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


def _jinja() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_jinja_autoescape(["html"]),
    )


def _snapshot_score(company_id: int, session: Session) -> tuple[dict | None, dict | None]:
    """Return (latest_score, previous_score) from stored snapshots."""
    snaps = (
        session.query(MetricsSnapshot)
        .filter_by(company_id=company_id)
        .order_by(MetricsSnapshot.asof_date.desc())
        .limit(2)
        .all()
    )
    def _total(s: MetricsSnapshot | None) -> dict | None:
        if not s:
            return None
        try:
            return json.loads(s.score_json)
        except Exception:
            return None
    latest = _total(snaps[0]) if snaps else None
    prev = _total(snaps[1]) if len(snaps) > 1 else None
    return latest, prev


def _store_snapshot(company_id: int, session: Session, metrics: dict, score: dict) -> None:
    today = dt.date.today()
    snap = (
        session.query(MetricsSnapshot)
        .filter_by(company_id=company_id, asof_date=today)
        .one_or_none()
    )
    if snap is None:
        snap = MetricsSnapshot(company_id=company_id, asof_date=today)
        session.add(snap)
    snap.metrics_json = json.dumps(metrics, default=str)
    snap.score_json = json.dumps(score, default=str)
    session.flush()


def build_daily_digest(
    session: Session,
    client: Any | None = None,
    settings: Settings | None = None,
    max_8k_downloads: int = 3,
) -> dict[str, Any]:
    """Build today's digest. Returns {'date', 'html', 'path', 'sections'}."""
    settings = settings or get_settings()
    today = dt.date.today()
    last = session.query(Digest).order_by(Digest.date.desc()).first()
    since = last.date if last else today - dt.timedelta(days=7)

    companies_out: list[dict[str, Any]] = []
    eight_ks: list[dict[str, Any]] = []

    for wl in session.query(Watchlist).order_by(Watchlist.ticker).all():
        company = change_mod.company_by_ticker(wl.ticker, session)
        if not company:
            continue
        facts = metrics_mod.build_facts_index(
            session.query(Fact).filter_by(company_id=company.id).all()
        )
        metrics = metrics_mod.compute_metrics(facts)
        score = scoring_mod.score_company(metrics)
        prev_score, older = _snapshot_score(company.id, session)
        _store_snapshot(company.id, session, metrics, score)

        price_info = valuation_mod.get_price(company.ticker, session)
        price = price_info["close"] if price_info else None

        new = change_mod.new_filings(company.id, session, since)
        flags = change_mod.quarter_changes(company.id, session)

        score_delta = None
        if prev_score and score:
            score_delta = score["total"] - prev_score.get("total", 0)

        companies_out.append(
            {
                "ticker": company.ticker,
                "name": company.name,
                "price": price,
                "score": score,
                "score_delta": score_delta,
                "metrics": metrics,
                "new_filings": [
                    {"form": f.form, "filing_date": f.filing_date, "url": f.url, "id": f.id}
                    for f in new
                ],
                "changes": flags,
            }
        )
        for f in new:
            if f.form == "8-K" and len(eight_ks) < max_8k_downloads:
                eight_ks.append({"ticker": company.ticker, "filing": f})

    # Summarize top 8-Ks (extractive; downloads are rate-limited by the client).
    eight_k_out: list[dict[str, Any]] = []
    for item in eight_ks:
        f: Filing = item["filing"]
        summary = f.summary
        if not summary and client is not None:
            try:
                text = client.download_filing_text(f.url)
                summary = summarize_mod.summarize_filing_text(text)
                f.summary = summary
                session.flush()
            except Exception as exc:
                summary = f"(could not download filing text: {exc})"
        eight_k_out.append(
            {
                "ticker": item["ticker"],
                "filing_date": f.filing_date,
                "url": f.url,
                "summary": summary or "(no summary available)",
            }
        )

    html = _jinja().get_template("digest.html").render(
        date=today.isoformat(),
        generated_at=dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        companies=companies_out,
        eight_ks=eight_k_out,
    )

    digest_dir = settings.data_dir / "digests"
    digest_dir.mkdir(parents=True, exist_ok=True)
    path = digest_dir / f"{today.isoformat()}.html"
    path.write_text(html, encoding="utf-8")

    row = session.query(Digest).filter_by(date=today).one_or_none()
    if row is None:
        row = Digest(date=today, html_path=str(path))
        session.add(row)
    else:
        row.html_path = str(path)
    session.flush()

    print(f"Digest saved to {path}")
    return {"date": today.isoformat(), "html": html, "path": str(path),
            "companies": companies_out, "eight_ks": eight_k_out}


def deliver_digest(html: str, date_str: str, settings: Settings | None = None) -> bool:
    """Email the digest HTML when SMTP is configured. Returns True if sent."""
    settings = settings or get_settings()
    if not smtp_configured(settings):
        print("SMTP not configured; skipping email delivery.")
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"EdgarDash daily digest — {date_str}"
    msg["From"] = settings.smtp_from
    msg["To"] = settings.digest_to_email
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
        server.starttls()
        if settings.smtp_user:
            server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(msg)
    print(f"Digest emailed to {settings.digest_to_email}")
    return True
