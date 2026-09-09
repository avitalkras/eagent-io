"""
Integration test for eagent.workflow's transition functions against a real
Postgres — proves the state machine is actually enforced at the database
layer, not just in the pure ALLOWED_TRANSITIONS table (tests/test_workflow_state_machine.py
covers that half).

Same rationale as the Phase 2/3 loader integration tests: this module's
whole job is enforcing invariants via real transactions and row locks
(`SELECT ... FOR UPDATE`) — faking the database would mean re-implementing
Postgres's own locking semantics in a mock, which proves nothing. Skipped
when no DATABASE_URL is configured.

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
from sqlalchemy import text

from eagent.config import get_engine
from eagent.schema import dim_companies, dim_jobs, fact_outreach
from eagent.workflow import (
    InvalidTransitionError,
    approve_outreach,
    get_pending_approvals,
    mark_interview,
    mark_replied,
    mark_sent,
    reject_outreach,
)

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("DATABASE_URL")

pytest.importorskip("psycopg2")
if not DATABASE_URL:
    pytest.skip(
        "DATABASE_URL not set — skipping workflow integration test. See module "
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
            "(SELECT job_id FROM dim_jobs WHERE external_source = 'workflow_test_source')"
        ))
        conn.execute(text("DELETE FROM dim_jobs WHERE external_source = 'workflow_test_source'"))
        conn.execute(text("DELETE FROM dim_companies WHERE domain = 'workflowtest.example'"))
    eng.dispose()


@pytest.fixture()
def drafted_outreach_id(engine) -> int:
    """A fresh fact_outreach row in its natural starting state: Drafted, not approved."""
    with engine.begin() as conn:
        company_id = conn.execute(
            dim_companies.insert()
            .values(name="Workflow Test Co", domain="workflowtest.example")
            .returning(dim_companies.c.company_id)
        ).scalar_one()
        job_id = conn.execute(
            dim_jobs.insert()
            .values(
                company_id=company_id,
                title="Data Engineer",
                external_source="workflow_test_source",
                external_id="job-1",
            )
            .returning(dim_jobs.c.job_id)
        ).scalar_one()
        date_id = int(
            conn.execute(text("SELECT date_id FROM dim_dates ORDER BY date LIMIT 1")).scalar_one()
        )
        outreach_id = conn.execute(
            fact_outreach.insert()
            .values(job_id=job_id, date_id=date_id, ats_score=75, missing_skills=["snowflake"])
            .returning(fact_outreach.c.outreach_id)
        ).scalar_one()
    return outreach_id


def _status_and_flags(engine, outreach_id):
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT outreach_status, is_approved, sent_at, replied_at, interviewed_at "
                "FROM fact_outreach WHERE outreach_id = :id"
            ),
            {"id": outreach_id},
        ).one()


def test_new_row_starts_drafted_and_unapproved(engine, drafted_outreach_id):
    row = _status_and_flags(engine, drafted_outreach_id)
    assert row.outreach_status == "Drafted"
    assert row.is_approved is False


def test_full_happy_path_funnel(engine, drafted_outreach_id):
    approve_outreach(engine, drafted_outreach_id)
    row = _status_and_flags(engine, drafted_outreach_id)
    assert row.outreach_status == "Approved"
    assert row.is_approved is True

    mark_sent(engine, drafted_outreach_id)
    row = _status_and_flags(engine, drafted_outreach_id)
    assert row.outreach_status == "Sent"
    assert row.sent_at is not None

    mark_replied(engine, drafted_outreach_id)
    row = _status_and_flags(engine, drafted_outreach_id)
    assert row.outreach_status == "Replied"
    assert row.replied_at is not None

    mark_interview(engine, drafted_outreach_id)
    row = _status_and_flags(engine, drafted_outreach_id)
    assert row.outreach_status == "Interview"
    assert row.interviewed_at is not None


def test_cannot_send_without_approval(engine, drafted_outreach_id):
    with pytest.raises(InvalidTransitionError):
        mark_sent(engine, drafted_outreach_id)


def test_cannot_approve_twice(engine, drafted_outreach_id):
    approve_outreach(engine, drafted_outreach_id)
    with pytest.raises(InvalidTransitionError):
        approve_outreach(engine, drafted_outreach_id)


def test_reject_from_drafted(engine, drafted_outreach_id):
    reject_outreach(engine, drafted_outreach_id)
    row = _status_and_flags(engine, drafted_outreach_id)
    assert row.outreach_status == "Rejected"


def test_reject_after_sent_is_legal(engine, drafted_outreach_id):
    approve_outreach(engine, drafted_outreach_id)
    mark_sent(engine, drafted_outreach_id)
    reject_outreach(engine, drafted_outreach_id)
    row = _status_and_flags(engine, drafted_outreach_id)
    assert row.outreach_status == "Rejected"


def test_rejected_is_terminal_even_at_the_database_layer(engine, drafted_outreach_id):
    reject_outreach(engine, drafted_outreach_id)
    with pytest.raises(InvalidTransitionError):
        approve_outreach(engine, drafted_outreach_id)


def test_sent_can_go_straight_to_interview(engine, drafted_outreach_id):
    approve_outreach(engine, drafted_outreach_id)
    mark_sent(engine, drafted_outreach_id)
    mark_interview(engine, drafted_outreach_id)
    row = _status_and_flags(engine, drafted_outreach_id)
    assert row.outreach_status == "Interview"


def test_get_pending_approvals_includes_drafted_and_excludes_approved(engine, drafted_outreach_id):
    pending_before = get_pending_approvals(engine)
    assert drafted_outreach_id in {row["outreach_id"] for row in pending_before}

    approve_outreach(engine, drafted_outreach_id)

    pending_after = get_pending_approvals(engine)
    assert drafted_outreach_id not in {row["outreach_id"] for row in pending_after}
