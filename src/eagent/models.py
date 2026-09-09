"""
Pydantic models — the typed contract every layer of the pipeline agrees on.

WHY THIS FILE EXISTS (the "why", for learning):
    Scrapers return messy, inconsistent JSON from the internet. Before that
    data touches the database, an LLM prompt, or a Power BI export, it should
    pass through ONE well-defined shape. Pydantic enforces that shape at
    *runtime* (unlike a plain @dataclass, which only documents types but
    never checks them) — if a scraper hands us a job with no title, we find
    out immediately with a clear error, not three steps later as a cryptic
    NULL-constraint violation in Postgres.

    This is the same idea as the DDL CHECK constraints in Phase 1
    (docs/01-database-modeling-star-schema.md) — push validation as close to
    the source of bad data as possible.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, EmailStr, Field, HttpUrl, field_validator


class JobPosting(BaseModel):
    """A single, normalized job posting — the output of any BaseScraper.

    Mirrors the columns of `dim_jobs` (see sql/01_schema.sql) closely on
    purpose: the loader (loader.py) maps this model 1:1 onto that table.
    """

    # --- idempotency key (matches the UNIQUE constraint in dim_jobs) -------
    external_source: str = Field(..., description="e.g. 'remoteok', 'greenhouse'")
    external_id: str = Field(..., description="the source's own id for this posting")

    # --- descriptive fields --------------------------------------------------
    title: str = Field(..., min_length=1)
    company_name: str = Field(..., min_length=1)
    company_domain: Optional[str] = Field(
        default=None, description="e.g. 'stripe.com' — used later for enrichment"
    )
    url: Optional[HttpUrl] = None
    location: Optional[str] = None
    tech_stack: List[str] = Field(default_factory=list)
    raw_description: Optional[str] = None
    posted_at: Optional[datetime] = None

    @field_validator("tech_stack", mode="before")
    @classmethod
    def _normalize_tech_stack(cls, value: object) -> list[str]:
        """Lowercase, strip, and dedupe tags so 'Python' and 'python' don't
        end up as two different entries in dim_jobs.tech_stack."""
        if not value:
            return []
        return sorted({str(tag).strip().lower() for tag in value if str(tag).strip()})

    @field_validator("title", "company_name", mode="before")
    @classmethod
    def _strip_whitespace(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class RecruiterContact(BaseModel):
    """A recruiter/HR contact found (or guessed) for a company.

    Mirrors `dim_recruiters` (see sql/01_schema.sql). `confidence_score` is
    bounded 0.0-1.0 by Pydantic here — the SAME rule the database enforces
    with a CHECK constraint. Defense in depth: catch it in Python before it
    ever reaches a SQL error.
    """

    company_domain: str
    full_name: Optional[str] = None
    email: Optional[EmailStr] = None
    linkedin_url: Optional[HttpUrl] = None
    enrichment_source: str = Field(..., description="e.g. 'hunter_io', 'generic_fallback'")
    confidence_score: float = Field(ge=0.0, le=1.0)
