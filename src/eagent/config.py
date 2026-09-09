"""Environment/config loading — one place that knows about env vars."""
from __future__ import annotations

import os
from typing import Optional

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

# Loads a local .env file if present (no-op in prod, where real env vars are
# injected by the platform/CI). Never commit a real .env — see .gitignore.
load_dotenv()


def get_engine(database_url: Optional[str] = None) -> Engine:
    """Build a SQLAlchemy Engine from DATABASE_URL (or an explicit override)."""
    url = database_url or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Copy .env.example to .env and fill it "
            "in, e.g. postgresql+psycopg2://user:pass@localhost:5432/eagent"
        )
    return create_engine(url, future=True)


def get_hunter_api_key() -> Optional[str]:
    """Returns the Hunter.io key, or None if enrichment should run fallback-only."""
    return os.environ.get("HUNTER_API_KEY") or None
