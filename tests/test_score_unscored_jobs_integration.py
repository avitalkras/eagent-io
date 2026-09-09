"""
Integration test for score_unscored_jobs.get_unscored_jobs() — the LEFT JOIN
query is new, real SQL worth verifying against a real Postgres rather than
trusting by inspection, same rationale as every other *_integration.py test
in this suite. Skipped when no DATABASE_URL is configured.

HOW TO RUN IT LOCALLY:
    createdb eagent_test
    psql -d eagent_test -f sql/01_schema.sql
    psql -d eagent_test -f sql/02_populate_dim_dates.sql
    psql -d eagent_test -f sql/03_ats_outreach_columns.sql
    psql -d eagent_test -f sql/04_interviewed_at_column.sql
    DATABASE_URL=postgresql+psycopg2://localhost:5432/eagent_test pytest -m integration
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest
from sqlalchemy import text

from eagent.config import get_engine
from eagent.schema import dim_companies, dim_jobs, fact_outreach

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("DATABASE_URL")

pytest.importorskip("psycopg2")
if not DATABASE_URL:
    pytest.skip(
        "DATABASE_URL not set — skipping score_unscored_jobs integration test. "
        "See module docstring for how to run it against a scratch database.",
        allow_module_level=True,
    )

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "score_unscored_jobs.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("score_unscored_jobs", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def engine():
    eng = get_engine(DATABASE_URL)
    yield eng
    with eng.begin() as conn:
        conn.execute(text(
            "DELETE FROM fact_outreach WHERE job_id IN "
            "(SELECT job_id FROM dim_jobs WHERE external_source = 'suj_test_source')"
        ))
        conn.execute(text("DELETE FROM dim_jobs WHERE external_source = 'suj_test_source'"))
        conn.execute(text("DELETE FROM dim_companies WHERE domain = 'sujtest.example'"))
    eng.dispose()


@pytest.fixture()
def company_id(engine) -> int:
    with engine.begin() as conn:
        return conn.execute(
            dim_companies.insert()
            .values(name="SUJ Test Co", domain="sujtest.example")
            .returning(dim_companies.c.company_id)
        ).scalar_one()


def _insert_job(engine, company_id: int, external_id: str, description: str = "a real JD") -> int:
    with engine.begin() as conn:
        return conn.execute(
            dim_jobs.insert()
            .values(
                company_id=company_id, title=f"Job {external_id}",
                external_source="suj_test_source", external_id=external_id,
                raw_description=description,
            )
            .returning(dim_jobs.c.job_id)
        ).scalar_one()


def _mark_scored(engine, job_id: int) -> None:
    with engine.begin() as conn:
        date_id = int(conn.execute(text("SELECT date_id FROM dim_dates ORDER BY date LIMIT 1")).scalar_one())
        conn.execute(fact_outreach.insert().values(job_id=job_id, date_id=date_id, ats_score=70))


def test_excludes_jobs_that_already_have_a_fact_outreach_row(engine, company_id):
    suj = _load_module()
    unscored_id = _insert_job(engine, company_id, "unscored-1")
    scored_id = _insert_job(engine, company_id, "scored-1")
    _mark_scored(engine, scored_id)

    result_ids = {row["job_id"] for row in suj.get_unscored_jobs(engine, limit=100)}

    assert unscored_id in result_ids
    assert scored_id not in result_ids


def test_respects_the_limit(engine, company_id):
    suj = _load_module()
    for i in range(5):
        _insert_job(engine, company_id, f"limit-test-{i}")

    result = suj.get_unscored_jobs(engine, limit=2)

    assert len(result) == 2


def test_returns_the_raw_description_for_scoring(engine, company_id):
    suj = _load_module()
    job_id = _insert_job(engine, company_id, "desc-test", description="Senior Data Engineer, Python + SQL")

    result = suj.get_unscored_jobs(engine, limit=100)
    row = next(r for r in result if r["job_id"] == job_id)

    assert row["raw_description"] == "Senior Data Engineer, Python + SQL"
