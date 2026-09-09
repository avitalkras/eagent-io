"""
Integration test for notify.get_todays_high_scorers() — verifies the real
query (threshold + "scored today" via drafted_at) against a real Postgres.
Skipped when no DATABASE_URL is configured.

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
        "DATABASE_URL not set — skipping notify integration test. See module "
        "docstring for how to run it against a scratch database.",
        allow_module_level=True,
    )

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "notify.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("notify", SCRIPT_PATH)
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
            "(SELECT job_id FROM dim_jobs WHERE external_source = 'notify_test_source')"
        ))
        conn.execute(text("DELETE FROM dim_jobs WHERE external_source = 'notify_test_source'"))
        conn.execute(text("DELETE FROM dim_companies WHERE domain = 'notifytest.example'"))
    eng.dispose()


@pytest.fixture()
def company_id(engine) -> int:
    with engine.begin() as conn:
        return conn.execute(
            dim_companies.insert()
            .values(name="Notify Test Co", domain="notifytest.example")
            .returning(dim_companies.c.company_id)
        ).scalar_one()


def _insert_scored_job(engine, company_id: int, external_id: str, ats_score: float) -> int:
    with engine.begin() as conn:
        job_id = conn.execute(
            dim_jobs.insert()
            .values(company_id=company_id, title=f"Job {external_id}",
                    external_source="notify_test_source", external_id=external_id)
            .returning(dim_jobs.c.job_id)
        ).scalar_one()
        date_id = int(conn.execute(text("SELECT date_id FROM dim_dates ORDER BY date LIMIT 1")).scalar_one())
        conn.execute(fact_outreach.insert().values(job_id=job_id, date_id=date_id, ats_score=ats_score))
    return job_id


def test_only_returns_rows_above_threshold(engine, company_id):
    notify = _load_module()
    high_id = _insert_scored_job(engine, company_id, "high", 92.0)
    low_id = _insert_scored_job(engine, company_id, "low", 60.0)

    result_ids = {row["outreach_id"] for row in notify.get_todays_high_scorers(engine, threshold=85.0)}
    result_job_ids = {row["title"] for row in notify.get_todays_high_scorers(engine, threshold=85.0)}

    with engine.connect() as conn:
        high_outreach_id = conn.execute(
            text("SELECT outreach_id FROM fact_outreach WHERE job_id = :jid"), {"jid": high_id}
        ).scalar_one()
        low_outreach_id = conn.execute(
            text("SELECT outreach_id FROM fact_outreach WHERE job_id = :jid"), {"jid": low_id}
        ).scalar_one()

    assert high_outreach_id in result_ids
    assert low_outreach_id not in result_ids
    assert "Job high" in result_job_ids


def test_ordered_best_match_first(engine, company_id):
    notify = _load_module()
    _insert_scored_job(engine, company_id, "mid", 88.0)
    _insert_scored_job(engine, company_id, "top", 97.0)

    result = notify.get_todays_high_scorers(engine, threshold=85.0)

    assert [r["ats_score"] for r in result] == sorted((r["ats_score"] for r in result), reverse=True)
    assert float(result[0]["ats_score"]) == 97.0
