"""
Shared SQLAlchemy Core Table definitions — the single Python-side mapping of
the star schema built in sql/01_schema.sql (+ sql/03_ats_outreach_columns.sql,
sql/04_interviewed_at_column.sql).

WHY ONE SHARED MODULE INSTEAD OF EACH MODULE DEFINING ITS OWN?
    Phase 2's `loader.py` and Phase 3's `ats/loader.py` each originally
    defined their own partial `Table` object for just the columns they
    touched — a reasonable "only map what you use" instinct while each
    phase stood alone. By Phase 4, a third module (`workflow.py`) needing
    yet another overlapping-but-not-identical view of `fact_outreach` would
    mean three places that could silently drift from each other and from
    the real schema. This module is the single source of truth: every
    loader/workflow module imports its Table objects from here instead of
    redefining them — the same instinct behind Phase 4's own
    `ALLOWED_TRANSITIONS` single source of truth (see `workflow.py`).

sql/01_schema.sql and its migrations remain the actual source of truth for
the DATABASE itself — this file does not create or alter tables, it only
describes their shape to SQLAlchemy so Core can build parameterized SQL
against them.
"""
from __future__ import annotations

from sqlalchemy import ARRAY, BigInteger, Boolean, Column, Date, DateTime, Integer, MetaData, Numeric, Table, Text
from sqlalchemy.dialects.postgresql import ENUM as PGEnum

metadata = MetaData()

OUTREACH_STATUS_VALUES = ("Drafted", "Approved", "Sent", "Replied", "Interview", "Rejected")

# create_type=False: the enum type already exists in Postgres — created by
# sql/01_schema.sql's `CREATE TYPE outreach_status_enum`. SQLAlchemy should
# only describe it here, never try to (re)create it.
outreach_status_enum = PGEnum(*OUTREACH_STATUS_VALUES, name="outreach_status_enum", create_type=False)

dim_companies = Table(
    "dim_companies", metadata,
    Column("company_id", BigInteger, primary_key=True),
    Column("name", Text, nullable=False),
    Column("domain", Text),
    Column("industry", Text),
    Column("location", Text),
    Column("created_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True)),
)

dim_jobs = Table(
    "dim_jobs", metadata,
    Column("job_id", BigInteger, primary_key=True),
    Column("company_id", BigInteger, nullable=False),
    Column("title", Text, nullable=False),
    Column("external_source", Text, nullable=False),
    Column("external_id", Text, nullable=False),
    Column("job_url", Text),
    Column("location", Text),
    Column("tech_stack", ARRAY(Text)),
    Column("raw_description", Text),
    Column("posted_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True)),
)

dim_recruiters = Table(
    "dim_recruiters", metadata,
    Column("recruiter_id", BigInteger, primary_key=True),
    Column("company_id", BigInteger, nullable=False),
    Column("full_name", Text),
    Column("email", Text),
    Column("linkedin_url", Text),
    Column("enrichment_source", Text),
    Column("confidence_score", Numeric(4, 3)),
    Column("created_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True)),
)

dim_dates = Table(
    "dim_dates", metadata,
    Column("date_id", Integer, primary_key=True),
    Column("date", Date, nullable=False),
    Column("day", Integer, nullable=False),
    Column("month", Integer, nullable=False),
    Column("quarter", Integer, nullable=False),
    Column("year", Integer, nullable=False),
    Column("is_weekend", Boolean, nullable=False),
)

fact_outreach = Table(
    "fact_outreach", metadata,
    Column("outreach_id", BigInteger, primary_key=True),
    Column("job_id", BigInteger, nullable=False),
    Column("recruiter_id", BigInteger),
    Column("date_id", Integer, nullable=False),
    Column("ats_score", Numeric(5, 2)),
    Column("missing_skills", ARRAY(Text)),
    Column("resume_pdf_path", Text),
    Column("is_approved", Boolean, nullable=False),
    Column("outreach_status", outreach_status_enum, nullable=False),
    Column("drafted_at", DateTime(timezone=True)),
    Column("sent_at", DateTime(timezone=True)),
    Column("replied_at", DateTime(timezone=True)),
    Column("interviewed_at", DateTime(timezone=True)),
)
