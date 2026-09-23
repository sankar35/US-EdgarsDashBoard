"""Environment-based configuration.

All settings come from environment variables (loaded from a .env file if
present). ``SEC_CONTACT_EMAIL`` is required by the SEC for data.sec.gov
access and the app fails fast with a clear message when it is missing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    sec_contact_email: str
    llm_api_key: str
    digest_to_email: str
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_from: str
    data_dir: Path
    db_path: Path


def get_settings() -> Settings:
    """Return settings. Raises RuntimeError if the SEC contact email is unset."""
    email = _get("SEC_CONTACT_EMAIL")
    if not email:
        raise RuntimeError(
            "SEC_CONTACT_EMAIL is not set. The SEC requires a User-Agent with a "
            "contact email for data.sec.gov requests. Copy .env.example to .env "
            "and set SEC_CONTACT_EMAIL=you@example.com, then retry."
        )
    port = _get("SMTP_PORT", "587")
    return Settings(
        sec_contact_email=email,
        llm_api_key=_get("LLM_API_KEY"),
        digest_to_email=_get("DIGEST_TO_EMAIL"),
        smtp_host=_get("SMTP_HOST"),
        smtp_port=int(port) if port.isdigit() else 587,
        smtp_user=_get("SMTP_USER"),
        smtp_password=_get("SMTP_PASSWORD"),
        smtp_from=_get("SMTP_FROM") or _get("DIGEST_TO_EMAIL"),
        data_dir=DATA_DIR,
        db_path=DATA_DIR / "edgardash.db",
    )


def smtp_configured(s: Settings) -> bool:
    """True when enough SMTP settings exist to attempt email delivery."""
    return bool(s.smtp_host and s.digest_to_email)
