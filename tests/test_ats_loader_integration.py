"""
Integration test for eagent.ats.loader.upsert_outreach_result() — proves the
fact_outreach idempotency guarantee (requirement #4) against a real Postgres.

Same rationale as tests/test_loader_integration.py (Phase 2): this loader
builds real Postgres-dialect SQL (ON CONFLICT, array columns) via SQLAlchemy
Core, so faking the database would mean re-implementing Postgres's own
conflict-resolution semantics in a mock. Instead this runs for real and is
skipped when no DATABASE_URL is configured.

HOW TO RUN IT LOCALLY:
    createdb eagent_test
    psql -d eagent_test -f sql/01_schema.sql
    psql -d eagent_test -f sql/02_populate_dim_dates.sql
    psql -d eagent_test -f sql/03_ats_outreach_columns.sql
    DATABASE_URL=postgresql+psycopg2://localhost:5432/eagent_test pytest -m integration
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from eagent.ats.loader import upsert_outreach_result
from eagent.ats.models import ATSAnalysisResult, TailoredResume
from eagent.config import get_engine
from eagent.schema import dim_companies, dim_jobs

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("DATABASE_URL")

pytest.importorskip("psycopg2")
if not DATABASE_URL:
    pytest.skip(
        "DATABASE_URL not set — skipping ATS loader integration test. See module "
        "docstring for how to run it against a scratch database.",
        allow_module_level=True,
    )


@pytest.fixture()
def engine():
    eng = get_engine(DATABASE_URL)
    yield eng
    with eng.begin() as conn:
        conn.execute(text(
            "DELETE FROM fact_outreach WHERE job_id IN "
            "(SELECT job_id FROM dim_jobs WHERE external_source = 'ats_test_source')"
        ))
        conn.execute(text("DELETE FROM dim_jobs WHERE external_source = 'ats_test_source'"))
        conn.execute(text("DELETE FROM dim_companies WHERE domain = 'atstest.example'"))
    eng.dispose()


@pytest.fixture()
def job_id(engine) -> int:
    """Insert one real dim_jobs row (fact_outreach.job_id is a FK) and return its id."""
    with engine.begin() as conn:
        company_id = conn.execute(
            dim_companies.insert()
            .values(name="ATS Test Co", domain="atstest.example")
            .returning(dim_companies.c.company_id)
        ).scalar_one()
        job_id_ = conn.execute(
            dim_jobs.insert()
            .values(
                company_id=company_id,
                title="Data Engineer",
                external_source="ats_test_source",
                external_id="job-1",
            )
            .returning(dim_jobs.c.job_id)
        ).scalar_one()
    return job_id_


def _ats_result(score: float) -> ATSAnalysisResult:
    return ATSAnalysisResult(
        ats_score=score,
        matched_keywords=["python"],
        missing_hard_skills=["snowflake"],
        tailored_resume=TailoredResume(summary="s", prioritized_skills=["python"], bullet_points=[]),
    )


def test_upsert_creates_a_drafted_row(engine, job_id):
    outreach_id = upsert_outreach_result(
        engine, job_id=job_id, ats_result=_ats_result(75), resume_pdf_path="/tmp/resume-1.pdf"
    )

    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT ats_score, missing_skills, resume_pdf_path, outreach_status "
                "FROM fact_outreach WHERE outreach_id = :id"
            ),
            {"id": outreach_id},
        ).one()

    assert float(row.ats_score) == 75.0
    assert row.missing_skills == ["snowflake"]
    assert row.resume_pdf_path == "/tmp/resume-1.pdf"
    assert row.outreach_status == "Drafted"  # DB default — loader never sets this


def test_upsert_is_idempotent_and_refreshes_in_place(engine, job_id):
    first_id = upsert_outreach_result(
        engine, job_id=job_id, ats_result=_ats_result(60), resume_pdf_path="/tmp/v1.pdf"
    )
    second_id = upsert_outreach_result(
        engine, job_id=job_id, ats_result=_ats_result(88), resume_pdf_path="/tmp/v2.pdf"
    )

    assert first_id == second_id  # same outreach row, not a duplicate

    with engine.connect() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM fact_outreach WHERE job_id = :jid"), {"jid": job_id}
        ).scalar_one()
        row = conn.execute(
            text("SELECT ats_score, resume_pdf_path FROM fact_outreach WHERE outreach_id = :id"),
            {"id": first_id},
        ).one()

    assert count == 1
    assert float(row.ats_score) == 88.0  # refreshed to the latest score
    assert row.resume_pdf_path == "/tmp/v2.pdf"


def test_upsert_does_not_revert_an_already_approved_status(engine, job_id):
    outreach_id = upsert_outreach_result(
        engine, job_id=job_id, ats_result=_ats_result(60), resume_pdf_path="/tmp/v1.pdf"
    )
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE fact_outreach SET is_approved = TRUE, outreach_status = 'Approved' "
                 "WHERE outreach_id = :id"),
            {"id": outreach_id},
        )

    # Re-scoring the job (e.g. the JD changed) must not silently undo a human's approval.
    upsert_outreach_result(engine, job_id=job_id, ats_result=_ats_result(91), resume_pdf_path="/tmp/v2.pdf")

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT outreach_status, is_approved, ats_score FROM fact_outreach WHERE outreach_id = :id"),
            {"id": outreach_id},
        ).one()

    assert row.outreach_status == "Approved"
    assert row.is_approved is True
    assert float(row.ats_score) == 91.0  # score still refreshes
