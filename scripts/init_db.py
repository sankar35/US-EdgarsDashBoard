#!/usr/bin/env python3
"""Create the SQLite database and all tables. Safe to re-run."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import init_db  # noqa: E402

if __name__ == "__main__":
    path = init_db()
    print(f"Database ready at {path}")
