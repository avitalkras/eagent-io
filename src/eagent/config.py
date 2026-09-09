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


def get_llm_provider_name() -> str:
    """Which eagent.ats.llm provider to use for unattended runs — 'groq' or
    'gemini'. Defaults to 'groq'. The script that actually builds the
    provider instance decides what to do with this (same "config.py returns
    raw values, callers decide" split get_hunter_api_key already follows) —
    kept here rather than importing eagent.ats.llm into this module, which
    would invert the natural dependency direction (config is foundational;
    ats is a feature built on top of it)."""
    return os.environ.get("LLM_PROVIDER", "groq").strip().lower()


def get_groq_api_key() -> Optional[str]:
    return os.environ.get("GROQ_API_KEY") or None


def get_gemini_api_key() -> Optional[str]:
    return os.environ.get("GEMINI_API_KEY") or None
