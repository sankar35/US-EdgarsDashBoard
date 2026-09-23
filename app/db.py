"""SQLite engine and session factory.

The database lives at ``data/edgardash.db`` (created on first use).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base


def get_db_path() -> Path:
    """Resolve the SQLite file path, creating the data dir if needed."""
    from .config import get_settings

    # Avoid requiring SEC_CONTACT_EMAIL just to locate the DB file:
    # fall back to the conventional path when env is unset.
    try:
        path = get_settings().db_path
    except RuntimeError:
        path = Path(__file__).resolve().parent.parent / "data" / "edgardash.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


_engine = None
_SessionFactory = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(f"sqlite:///{get_db_path()}", future=True)
    return _engine


def get_session_factory() -> sessionmaker:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine(), class_=Session, expire_on_commit=False)
    return _SessionFactory


def init_db() -> Path:
    """Create all tables. Returns the DB path."""
    Base.metadata.create_all(get_engine())
    return get_db_path()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Provide a transactional scope: commit on success, rollback on error."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
