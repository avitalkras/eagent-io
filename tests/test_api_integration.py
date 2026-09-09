"""
Integration test for eagent.api against a real Postgres — proves the whole
stack (HTTP -> FastAPI -> eagent.workflow -> Postgres transaction) actually
works together, not just that each layer works in isolation (unit tests
cover that: tests/test_api.py mocks eagent.workflow, tests/test_workflow_integration.py
exercises eagent.workflow directly against a real DB).

HOW TO RUN IT LOCALLY:
    createdb eagent_test
    psql -d eagent_test -f sql/01_schema.sql
    psql -d eagent_test -f sql/02_populate_dim_dates.sql
    psql -d eagent_test -f sql/03_ats_outreach_columns.sql
    psql -d eagent_test -f sql/04_interviewed_at_column.sql
    DATABASE_URL=postgresql+psycopg2://localhost:5432/eagent_test pytest -m integration
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from eagent import api
from eagent.config import get_engine
from eagent.schema import dim_companies, dim_jobs, fact_outreach

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("DATABASE_URL")

pytest.importorskip("psycopg2")
if not DATABASE_URL:
    pytest.skip(
        "DATABASE_URL not set — skipping API integration test. See module "
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
            "(SELECT job_id FROM dim_jobs WHERE external_source = 'api_test_source')"
        ))
        conn.execute(text("DELETE FROM dim_jobs WHERE external_source = 'api_test_source'"))
        conn.execute(text("DELETE FROM dim_companies WHERE domain = 'apitest.example'"))
    eng.dispose()


@pytest.fixture()
def client(engine):
    api.app.dependency_overrides[api.get_db_engine] = lambda: engine
    yield TestClient(api.app)
    api.app.dependency_overrides.clear()


@pytest.fixture()
def drafted_outreach_id(engine) -> int:
    with engine.begin() as conn:
        company_id = conn.execute(
            dim_companies.insert()
            .values(name="API Test Co", domain="apitest.example")
            .returning(dim_companies.c.company_id)
        ).scalar_one()
        job_id = conn.execute(
            dim_jobs.insert()
            .values(company_id=company_id, title="Data Engineer",
                    external_source="api_test_source", external_id="job-1")
            .returning(dim_jobs.c.job_id)
        ).scalar_one()
        date_id = int(
            conn.execute(text("SELECT date_id FROM dim_dates ORDER BY date LIMIT 1")).scalar_one()
        )
        outreach_id = conn.execute(
            fact_outreach.insert()
            .values(job_id=job_id, date_id=date_id, ats_score=80)
            .returning(fact_outreach.c.outreach_id)
        ).scalar_one()
    return outreach_id


def test_post_approve_actually_updates_postgres(client, engine, drafted_outreach_id):
    response = client.post(f"/outreach/{drafted_outreach_id}/approve")
    assert response.status_code == 200

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT outreach_status, is_approved FROM fact_outreach WHERE outreach_id = :id"),
            {"id": drafted_outreach_id},
        ).one()
    assert row.outreach_status == "Approved"
    assert row.is_approved is True


def test_post_approve_twice_returns_409_the_second_time(client, drafted_outreach_id):
    first = client.post(f"/outreach/{drafted_outreach_id}/approve")
    assert first.status_code == 200

    second = client.post(f"/outreach/{drafted_outreach_id}/approve")
    assert second.status_code == 409


def test_post_reject_actually_updates_postgres(client, engine, drafted_outreach_id):
    response = client.post(f"/outreach/{drafted_outreach_id}/reject")
    assert response.status_code == 200

    with engine.connect() as conn:
        status = conn.execute(
            text("SELECT outreach_status FROM fact_outreach WHERE outreach_id = :id"),
            {"id": drafted_outreach_id},
        ).scalar_one()
    assert status == "Rejected"


def test_post_approve_unknown_outreach_id_returns_404(client):
    response = client.post("/outreach/999999999/approve")
    assert response.status_code == 404
