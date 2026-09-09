"""
Idempotent loader — writes ATS results into fact_outreach. Requirement #4.

Same SQLAlchemy Core approach and upsert-and-get-id pattern as
eagent.loader (Phase 2) — see that module's docstring for why Core over the
ORM. This file follows on from Migration #3 (sql/03_ats_outreach_columns.sql),
which added `resume_pdf_path` and the `UNIQUE(job_id)` constraint this
loader's idempotency depends on.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import List, Optional

from sqlalchemy import ARRAY, BigInteger, Column, DateTime, Integer, MetaData, Numeric, Table, Text, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine

from eagent.ats.models import ATSAnalysisResult

logger = logging.getLogger(__name__)

metadata = MetaData()

# Mirrors only the columns this loader touches (same convention as
# eagent.loader.dim_jobs). Notably absent: `outreach_status` and
# `is_approved` — both are intentionally left to their DB defaults
# ('Drafted' / FALSE) on first insert, and intentionally NEVER written by
# this loader on a re-run. If a human has already approved or sent an
# outreach, re-scoring the job must refresh its ATS data without silently
# reverting the workflow status back to 'Drafted' — that's a human decision,
# not something a re-score should undo.
fact_outreach = Table(
    "fact_outreach", metadata,
    Column("outreach_id", BigInteger, primary_key=True),
    Column("job_id", BigInteger, nullable=False),
    Column("recruiter_id", BigInteger),
    Column("date_id", Integer, nullable=False),
    Column("ats_score", Numeric(5, 2)),
    Column("missing_skills", ARRAY(Text)),
    Column("resume_pdf_path", Text),
    Column("drafted_at", DateTime(timezone=True)),
)


def today_date_id() -> int:
    """Today's date as the Phase 1 dim_dates 'smart integer' key (YYYYMMDD).

    Requires dim_dates to already cover today — sql/02_populate_dim_dates.sql
    populates 2 years forward from whenever it's run. If it doesn't, the
    fact_outreach.date_id foreign key raises a clear Postgres error rather
    than silently accepting a bad value.
    """
    return int(date.today().strftime("%Y%m%d"))


def upsert_outreach_result(
    engine: Engine,
    job_id: int,
    ats_result: ATSAnalysisResult,
    resume_pdf_path: str,
    recruiter_id: Optional[int] = None,
    date_id: Optional[int] = None,
) -> int:
    """Create or refresh the fact_outreach row for one job with ATS results.

    Idempotent on `job_id` (Migration #3's `UNIQUE(job_id)` constraint): the
    first call for a job INSERTs a new 'Drafted' row; every subsequent call
    for the same job UPDATEs that row's ats_score / missing_skills /
    resume_pdf_path in place rather than creating a duplicate. `drafted_at`
    and the workflow status columns are left untouched on update — see the
    fact_outreach Table comment above for why.

    Args:
        job_id: FK into dim_jobs — required.
        ats_result: the validated output of eagent.ats.llm.analyze_and_tailor().
        resume_pdf_path: path/URI to the PDF produced by
            eagent.ats.render.compile_pdf() for this job.
        recruiter_id: FK into dim_recruiters, if a contact has been found yet.
        date_id: FK into dim_dates for "today"; defaults to today_date_id().

    Returns:
        The fact_outreach.outreach_id of the created-or-updated row.
    """
    date_id = date_id if date_id is not None else today_date_id()
    missing_skills: List[str] = ats_result.missing_hard_skills

    with engine.begin() as conn:
        stmt = pg_insert(fact_outreach).values(
            job_id=job_id,
            recruiter_id=recruiter_id,
            date_id=date_id,
            ats_score=ats_result.ats_score,
            missing_skills=missing_skills,
            resume_pdf_path=str(resume_pdf_path),
            drafted_at=func.now(),
        )

        # THE IDEMPOTENCY LINE: on a re-run for the same job, refresh the ATS
        # fields from `EXCLUDED` (Postgres's name for "the row that would
        # have been inserted") instead of creating a duplicate outreach row.
        stmt = stmt.on_conflict_do_update(
            index_elements=[fact_outreach.c.job_id],
            set_={
                "recruiter_id": stmt.excluded.recruiter_id,
                "date_id": stmt.excluded.date_id,
                "ats_score": stmt.excluded.ats_score,
                "missing_skills": stmt.excluded.missing_skills,
                "resume_pdf_path": stmt.excluded.resume_pdf_path,
            },
        ).returning(fact_outreach.c.outreach_id)

        outreach_id = conn.execute(stmt).scalar_one()

    logger.info(
        "upsert_outreach_result: job_id=%s -> outreach_id=%s (ats_score=%.1f, %d missing skills)",
        job_id, outreach_id, ats_result.ats_score, len(missing_skills),
    )
    return outreach_id
