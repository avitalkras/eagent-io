"""
Idempotent loader — writes JobPosting models into dim_companies / dim_jobs.

WHY SQLAlchemy CORE (not the ORM, not raw psycopg2 strings)?
    - Raw f-string SQL invites SQL injection and is easy to get subtly wrong.
    - The full ORM (declarative models + a Session/unit-of-work) is more
      machinery than we need for "insert rows, skip duplicates" — no object
      graph, no lazy loading, no identity map required here.
    - SQLAlchemy Core's `Table` + `insert()` gives us parameterized,
      injection-safe SQL, Postgres-specific `ON CONFLICT` support via
      `sqlalchemy.dialects.postgresql.insert`, and plain, explicit statements.
      Right-sized tool for this job.

The Table objects below intentionally mirror only the columns this loader
touches. sql/01_schema.sql (Phase 1) remains the single source of truth for
the real schema — this file does not create or alter tables.
"""
from __future__ import annotations

import logging
from typing import Dict, Optional, Sequence

from sqlalchemy import ARRAY, BigInteger, Column, DateTime, MetaData, Table, Text, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine

from eagent.models import JobPosting

logger = logging.getLogger(__name__)

metadata = MetaData()

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


def get_or_create_company(conn: Connection, name: str, domain: Optional[str]) -> int:
    """Return the company_id for (name, domain), inserting if it doesn't exist.

    WHY NOT `ON CONFLICT DO NOTHING` HERE?
        DO NOTHING silently no-ops on a duplicate and returns ZERO rows — so
        you'd have no way to learn the existing row's id, which we need for
        the job's foreign key. Instead we use `ON CONFLICT ... DO UPDATE ...
        RETURNING`: the UPDATE always "touches" a row (new or existing), so
        RETURNING always gives us exactly one id back. This is the standard
        Postgres "upsert-and-get-id" pattern.
    """
    if domain:
        stmt = (
            pg_insert(dim_companies)
            .values(name=name, domain=domain, created_at=func.now(), updated_at=func.now())
            .on_conflict_do_update(
                index_elements=[dim_companies.c.domain],
                set_={"updated_at": func.now()},
            )
            .returning(dim_companies.c.company_id)
        )
        return conn.execute(stmt).scalar_one()

    # No domain to key off of: fall back to SELECT-then-INSERT by name.
    # TRADE-OFF (worth knowing for an interview): dim_companies has no
    # UNIQUE constraint on `name` alone, so this get-or-create is NOT safe
    # under concurrent writers — two scrapers running at the same instant
    # could both fail to find the row and both insert it, creating a
    # duplicate. Fine for a single-process scraper; a concurrent system
    # would need a partial unique index (e.g. UNIQUE(name) WHERE domain IS
    # NULL) or to serialize writes per company name.
    existing = conn.execute(
        dim_companies.select().where(
            dim_companies.c.name == name, dim_companies.c.domain.is_(None)
        )
    ).first()
    if existing is not None:
        return existing.company_id

    inserted = conn.execute(
        dim_companies.insert()
        .values(name=name, domain=None, created_at=func.now(), updated_at=func.now())
        .returning(dim_companies.c.company_id)
    )
    return inserted.scalar_one()


def load_jobs(engine: Engine, postings: Sequence[JobPosting]) -> Dict[str, int]:
    """Idempotently upsert JobPostings into dim_companies / dim_jobs.

    Safe to call with the same postings repeatedly — a scraper re-run, a
    cron retry, an overlapping window — none of it creates duplicate rows.

    Returns:
        {"inserted": N, "skipped_duplicate": M}
    """
    inserted = 0
    skipped = 0

    # `engine.begin()` opens ONE transaction for the whole batch: either every
    # posting in this call commits, or (on an exception) none of them do.
    with engine.begin() as conn:
        for posting in postings:
            company_id = get_or_create_company(conn, posting.company_name, posting.company_domain)

            stmt = pg_insert(dim_jobs).values(
                company_id=company_id,
                title=posting.title,
                external_source=posting.external_source,
                external_id=posting.external_id,
                job_url=str(posting.url) if posting.url else None,
                location=posting.location,
                tech_stack=posting.tech_stack,
                raw_description=posting.raw_description,
                posted_at=posting.posted_at,
                created_at=func.now(),
            )

            # THE IDEMPOTENCY LINE: if (external_source, external_id) already
            # exists — the UNIQUE constraint from sql/01_schema.sql — do
            # nothing. No error, no duplicate row. This is what makes the
            # whole ingestion pipeline safe to re-run.
            stmt = stmt.on_conflict_do_nothing(
                index_elements=[dim_jobs.c.external_source, dim_jobs.c.external_id]
            ).returning(dim_jobs.c.job_id)

            result = conn.execute(stmt)
            if result.first() is not None:
                inserted += 1
            else:
                skipped += 1

    logger.info("load_jobs: %d inserted, %d skipped (already existed)", inserted, skipped)
    return {"inserted": inserted, "skipped_duplicate": skipped}
