"""Rate-limited SEC EDGAR HTTP client.

SEC rules honored here:
  * Every data.sec.gov request sends ``User-Agent: EdgarDash <email>``
    (email from ``SEC_CONTACT_EMAIL``; fail fast when unset) and
    ``Accept-Encoding: gzip``.
  * Max 10 requests/second — a simple minimum-interval rate limiter
    keeps us well under that (0.15s between requests).
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

import httpx

# Well under the SEC's 10 req/s limit.
_MIN_INTERVAL_S = 0.15
_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"
_ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession}/{doc}"


class RateLimiter:
    """Minimum-interval limiter: at most one call per ``interval`` seconds."""

    def __init__(self, interval: float = _MIN_INTERVAL_S) -> None:
        self.interval = interval
        self._lock = threading.Lock()
        self._last: float = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            sleep_for = self.interval - (now - self._last)
            if sleep_for > 0:
                time.sleep(sleep_for)
            self._last = time.monotonic()


def cik10(cik: int | str) -> str:
    """Zero-pad a CIK to 10 digits for data.sec.gov URLs."""
    return str(int(str(cik))).zfill(10)


def accession_nodashes(accession: str) -> str:
    """Strip dashes from an accession number for Archives URLs."""
    return accession.replace("-", "")


def _resolve_proxy() -> str | None:
    """Pick an explicit proxy URL from the environment, if one is set.

    We pass ``trust_env=False`` to httpx and hand it the proxy directly
    because httpx's own environment parsing can crash on exotic no_proxy
    entries (e.g. ``*[::1]``). EdgarDash only talks to public hosts
    (sec.gov, data.sec.gov, stooq.com), so skipping no_proxy handling is
    harmless; a configured http(s) proxy is still honored.
    """
    for var in (
        "https_proxy", "HTTPS_PROXY",
        "http_proxy", "HTTP_PROXY",
        "all_proxy", "ALL_PROXY",
    ):
        val = os.environ.get(var, "").strip()
        if val:
            return val
    return None


def _ssl_context() -> "ssl.SSLContext":
    """Build the TLS context, honoring SSL_CERT_FILE explicitly.

    Needed because we run httpx with ``trust_env=False`` (see above),
    which also disables httpx's own SSL_CERT_FILE handling.
    """
    import ssl

    cafile = os.environ.get("SSL_CERT_FILE", "").strip()
    if cafile:
        return ssl.create_default_context(cafile=cafile)
    return ssl.create_default_context()


class EdgarClient:
    """Thin wrapper around the public EDGAR JSON endpoints."""

    def __init__(self, contact_email: str, timeout: float = 30.0) -> None:
        if not contact_email:
            raise RuntimeError(
                "SEC_CONTACT_EMAIL is not set. The SEC requires a User-Agent "
                "with a contact email for data.sec.gov requests."
            )
        self._limiter = RateLimiter()
        self._client = httpx.Client(
            timeout=timeout,
            trust_env=False,
            proxy=_resolve_proxy(),
            verify=_ssl_context(),
            headers={
                "User-Agent": f"EdgarDash {contact_email}",
                "Accept-Encoding": "gzip",
            },
            follow_redirects=True,
        )
        self._ticker_map: dict[str, tuple[str, str]] | None = None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "EdgarClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _get_json(self, url: str) -> dict[str, Any]:
        self._limiter.wait()
        resp = self._client.get(url)
        resp.raise_for_status()
        return resp.json()

    def get_ticker_map(self) -> dict[str, tuple[str, str]]:
        """Return {TICKER: (cik10, company title)}; cached in memory."""
        if self._ticker_map is None:
            raw = self._get_json(_TICKER_MAP_URL)
            mapping: dict[str, tuple[str, str]] = {}
            for entry in raw.values():
                mapping[entry["ticker"].upper()] = (
                    cik10(entry["cik_str"]),
                    entry.get("title", ""),
                )
            self._ticker_map = mapping
        return self._ticker_map

    def resolve_ticker(self, ticker: str) -> tuple[str, str]:
        """Return (cik10, title) for a ticker; raises KeyError when unknown."""
        return self.get_ticker_map()[ticker.upper()]

    def get_submissions(self, cik: str) -> dict[str, Any]:
        """Filing index JSON for a zero-padded 10-digit CIK."""
        return self._get_json(_SUBMISSIONS_URL.format(cik10=cik10(cik)))

    def get_companyfacts(self, cik: str) -> dict[str, Any]:
        """Structured us-gaap facts JSON for a zero-padded 10-digit CIK."""
        return self._get_json(_COMPANYFACTS_URL.format(cik10=cik10(cik)))

    def filing_url(self, cik: str, accession: str, primary_doc: str) -> str:
        """Build the Archives URL for a filing's primary document."""
        return _ARCHIVES_URL.format(
            cik_int=str(int(cik)),  # no padding in Archives paths
            accession=accession_nodashes(accession),
            doc=primary_doc,
        )

    def download_filing_text(self, url: str, max_chars: int = 200_000) -> str:
        """Download a filing document's raw text (truncated to max_chars)."""
        self._limiter.wait()
        resp = self._client.get(url)
        resp.raise_for_status()
        text = resp.text
        return text[:max_chars] if len(text) > max_chars else text
