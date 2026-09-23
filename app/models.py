"""SQLAlchemy models for EdgarDash.

Tables:
  companies, filings, facts, metrics_snapshots, watchlist, prices, digests
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cik: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    ticker: Mapped[str] = mapped_column(String(16), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    sic: Mapped[str | None] = mapped_column(String(8), nullable=True, index=True)
    sic_description: Mapped[str | None] = mapped_column(String(256), nullable=True)
    exchange: Mapped[str | None] = mapped_column(String(32), nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    filings: Mapped[list["Filing"]] = relationship(back_populates="company", cascade="all, delete-orphan")
    facts: Mapped[list["Fact"]] = relationship(back_populates="company", cascade="all, delete-orphan")


class Filing(Base):
    __tablename__ = "filings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False, index=True)
    form: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    filing_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True, index=True)
    accession: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    primary_doc: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    url: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)

    company: Mapped[Company] = relationship(back_populates="filings")


class Fact(Base):
    """One normalized us-gaap fact value for a company/period (from companyfacts JSON)."""

    __tablename__ = "facts"
    __table_args__ = (UniqueConstraint("company_id", "concept", "frame", name="uq_fact_company_concept_frame"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False, index=True)
    concept: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    frame: Mapped[str] = mapped_column(String(24), nullable=False)
    period_end: Mapped[dt.date | None] = mapped_column(Date, nullable=True, index=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    form: Mapped[str | None] = mapped_column(String(16), nullable=True)
    filed_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)

    company: Mapped[Company] = relationship(back_populates="facts")


class MetricsSnapshot(Base):
    __tablename__ = "metrics_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False, index=True)
    asof_date: Mapped[dt.date] = mapped_column(Date, nullable=False, default=lambda: dt.date.today())
    metrics_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    score_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class Watchlist(Base):
    __tablename__ = "watchlist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    added_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
    notes: Mapped[str | None] = mapped_column(String(512), nullable=True)


class Price(Base):
    __tablename__ = "prices"
    __table_args__ = (UniqueConstraint("ticker", "date", name="uq_price_ticker_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    close: Mapped[float | None] = mapped_column(Float, nullable=True)


class Digest(Base):
    __tablename__ = "digests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, unique=True, nullable=False)
    html_path: Mapped[str] = mapped_column(String(512), nullable=False)
