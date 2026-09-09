"""
Integration test for load_jobs() — proves the idempotency guarantee for real.

WHY THIS ONE ISN'T A "UNIT" TEST:
    The loader builds real Postgres-dialect SQL (`ON CONFLICT`, `ARRAY`
    columns) via SQLAlchemy Core. Faking that at the HTTP/function level
    (like we do for the scraper and enricher) would mean re-implementing
    Postgres's conflict-resolution semantics in a mock — at which point the
    test isn't proving anything about the real behavior. So instead this
    test runs against a REAL database and is skipped when one isn't
    configured, rather than mocked.

HOW TO RUN IT LOCALLY:
    1. Have the Phase 1 schema applied to a scratch database:
         createdb eagent_test
         psql -d eagent_test -f sql/01_schema.sql
    2. Point the test at it and run pytest:
         DATABASE_URL=postgresql+psycopg2://localhost:5432/eagent_test pytest -m integration

Without DATABASE_URL set, this whole module is skipped — so `pytest` (no
args) stays fast and network/DB-free for everyday unit-test runs.
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from eagent.config import get_engine
from eagent.loader import load_jobs
from eagent.models import JobPosting

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("DATABASE_URL")

pytest.importorskip("psycopg2")
if not DATABASE_URL:
    pytest.skip(
        "DATABASE_URL not set — skipping loader integration test. See module "
        "docstring for how to run it against a scratch database.",
        allow_module_level=True,
    )


@pytest.fixture()
def engine():
    eng = get_engine(DATABASE_URL)
    yield eng
    # Clean up only the rows this test created, by our test's unique source tag.
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM dim_jobs WHERE external_source = 'test_source'"))
        conn.execute(text("DELETE FROM dim_companies WHERE domain = 'integrationtest.example'"))
    eng.dispose()


def _sample_posting(external_id: str) -> JobPosting:
    return JobPosting(
        external_source="test_source",
        external_id=external_id,
        title="Integration Test Data Engineer",
        company_name="Integration Test Co",
        company_domain="integrationtest.example",
        tech_stack=["python", "sql"],
    )


def test_load_jobs_is_idempotent(engine):
    posting = _sample_posting("int-test-1")

    first = load_jobs(engine, [posting])
    assert first == {"inserted": 1, "skipped_duplicate": 0}

    # Re-running with the SAME posting must not create a duplicate row.
    second = load_jobs(engine, [posting])
    assert second == {"inserted": 0, "skipped_duplicate": 1}

    with engine.connect() as conn:
        count = conn.execute(
            text(
                "SELECT COUNT(*) FROM dim_jobs "
                "WHERE external_source = 'test_source' AND external_id = 'int-test-1'"
            )
        ).scalar_one()
    assert count == 1


def test_load_jobs_reuses_company_row_across_postings(engine):
    posting_a = _sample_posting("int-test-2")
    posting_b = _sample_posting("int-test-3")

    load_jobs(engine, [posting_a, posting_b])

    with engine.connect() as conn:
        company_count = conn.execute(
            text("SELECT COUNT(*) FROM dim_companies WHERE domain = 'integrationtest.example'")
        ).scalar_one()
    # Same domain -> get_or_create_company must return the SAME company_id
    # both times, not create two company rows for one company.
    assert company_count == 1
