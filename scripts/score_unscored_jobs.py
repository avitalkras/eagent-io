#!/usr/bin/env python3
"""
Batch ATS scoring — Phase 7, PR #10.

Finds every `dim_jobs` row with no matching `fact_outreach` row yet — i.e.
scraped (Phase 2's `run_ingestion.py`) but never scored (Phase 3's
pipeline) — and runs each through the same steps
`scripts/run_ats_pipeline.py` runs for one job, looped:

    analyze_and_tailor -> render_typst_source -> compile_pdf -> upsert_outreach_result

Built to run **unattended**, once a day, via
`.github/workflows/daily_harvest.yml`.

WHY PER-ROW ERROR ISOLATION MATTERS HERE SPECIFICALLY:
    If job #412 has a malformed description, or the LLM returns an
    unrecoverable validation failure after every retry attempt (see
    `eagent.ats.llm.analyze_and_tailor`'s own generate -> validate -> retry
    loop), that must not take down the other 19 jobs in the same batch.
    Every row is wrapped in its own try/except; failures are logged with
    the job_id and counted, never silently swallowed and never allowed to
    abort the loop. The batch's own exit code (0 if every attempted job
    either scored or was cleanly skipped, 1 if anything failed) is what
    gives the GitHub Actions run a visible red X worth investigating —
    see the workflow's failure-notification step.

CREDENTIALS: entirely environment-sourced (`DATABASE_URL`, `LLM_PROVIDER`,
    `GROQ_API_KEY`/`GEMINI_API_KEY`) — no required interactive CLI flags,
    so this runs unattended in CI. See
    docs/08-going-live-secrets-and-migrations.md for the full reference.

KNOWN GAP, DELIBERATELY NOT PAPERED OVER HERE: every `fact_outreach` row
    this script writes gets `recruiter_id=None`. Nothing in this codebase
    persists a `RecruiterEnricher` result to `dim_recruiters` yet —
    `scripts/run_ingestion.py` calls the enricher and only logs the
    result. Wiring that up is a separate, not-yet-scoped change; this
    script isn't the place to quietly invent it.

Usage:
    python scripts/score_unscored_jobs.py --limit 20
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.engine import Engine

from eagent.ats.llm import GeminiProvider, GroqProvider, LLMProvider, analyze_and_tailor
from eagent.ats.loader import upsert_outreach_result
from eagent.ats.models import MasterResume
from eagent.ats.render import compile_pdf, render_typst_source
from eagent.config import get_engine, get_gemini_api_key, get_groq_api_key, get_llm_provider_name
from eagent.schema import dim_jobs, fact_outreach

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("score_unscored_jobs")


def build_provider() -> LLMProvider:
    """Construct the configured LLMProvider from environment variables alone.

    Fails fast — before touching the database or fetching anything — if the
    key required by the selected provider is missing, rather than failing
    20 jobs into a batch on the first LLM call.
    """
    name = get_llm_provider_name()
    if name == "groq":
        api_key = get_groq_api_key()
        if not api_key:
            raise RuntimeError("LLM_PROVIDER=groq but GROQ_API_KEY is not set.")
        return GroqProvider(api_key=api_key)
    if name == "gemini":
        api_key = get_gemini_api_key()
        if not api_key:
            raise RuntimeError("LLM_PROVIDER=gemini but GEMINI_API_KEY is not set.")
        return GeminiProvider(api_key=api_key)
    raise RuntimeError(f"Unknown LLM_PROVIDER={name!r} — expected 'groq' or 'gemini'.")


def get_unscored_jobs(engine: Engine, limit: int) -> List[Dict[str, Any]]:
    """Every dim_jobs row with no fact_outreach row yet, oldest-posted first.

    Oldest-first (not newest-first) means a size-limited batch makes steady
    progress through a growing backlog run after run, instead of always
    re-discovering whatever's newest and never reaching older postings.
    """
    query = (
        select(dim_jobs.c.job_id, dim_jobs.c.title, dim_jobs.c.raw_description)
        .select_from(dim_jobs.outerjoin(fact_outreach, fact_outreach.c.job_id == dim_jobs.c.job_id))
        .where(fact_outreach.c.job_id.is_(None))
        .order_by(dim_jobs.c.posted_at.asc().nulls_last())
        .limit(limit)
    )
    with engine.connect() as conn:
        return [dict(row._mapping) for row in conn.execute(query)]


def score_one_job(
    engine: Engine,
    provider: LLMProvider,
    master_resume: MasterResume,
    job: Dict[str, Any],
    output_dir: Path,
) -> float:
    """Score, tailor, render, and save one job. Returns the ats_score.

    Raises on any failure — the caller (main's loop) is responsible for
    catching it and moving on to the next job.
    """
    result = analyze_and_tailor(job["raw_description"], master_resume, provider)

    typst_source = render_typst_source(master_resume, result.tailored_resume)
    pdf_path = output_dir / f"job-{job['job_id']}.pdf"
    compile_pdf(typst_source, pdf_path)

    upsert_outreach_result(
        engine,
        job_id=job["job_id"],
        ats_result=result,
        resume_pdf_path=str(pdf_path),
        recruiter_id=None,  # see module docstring's "KNOWN GAP" note
    )
    return result.ats_score


def run_batch(
    engine: Engine,
    provider: LLMProvider,
    master_resume: MasterResume,
    jobs: List[Dict[str, Any]],
    output_dir: Path,
) -> Dict[str, int]:
    """The per-row error-isolation loop itself, factored out of main() so it's
    directly unit-testable without needing argparse/env/DB setup around it.

    Returns a summary dict: {"scored": N, "skipped": N, "failed": N}.
    """
    scored = skipped = failed = 0
    for job in jobs:
        job_id = job["job_id"]
        description = job["raw_description"]
        if not description or not description.strip():
            logger.warning("job_id=%s: skipping — no raw_description to score against", job_id)
            skipped += 1
            continue
        try:
            score = score_one_job(engine, provider, master_resume, job, output_dir)
            logger.info("job_id=%s: scored %.1f", job_id, score)
            scored += 1
        except Exception:
            # Deliberately broad and deliberately NOT re-raised: any failure
            # scoring one job (LLM outage, a malformed JD, a typst compile
            # error, a transient DB hiccup) must not stop the rest of the
            # batch. logger.exception captures the traceback so the
            # workflow's own logs show exactly which job needs a second look.
            logger.exception("job_id=%s: failed to score — skipping, batch continues", job_id)
            failed += 1

    return {"scored": scored, "skipped": skipped, "failed": failed}


def main() -> None:
    parser = argparse.ArgumentParser(description="Score every dim_jobs row that has no fact_outreach row yet.")
    parser.add_argument("--limit", type=int, default=20, help="Max jobs to score in this run.")
    parser.add_argument("--master-resume", type=Path, default=Path("data/master_resume.example.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("output/resumes"))
    args = parser.parse_args()

    provider = build_provider()
    master_resume = MasterResume.model_validate(json.loads(args.master_resume.read_text()))
    engine = get_engine()

    jobs = get_unscored_jobs(engine, args.limit)
    logger.info("Found %d unscored job(s) (limit=%d)", len(jobs), args.limit)

    summary = run_batch(engine, provider, master_resume, jobs, args.output_dir)
    logger.info(
        "Batch complete: %d scored, %d skipped (no description), %d failed",
        summary["scored"], summary["skipped"], summary["failed"],
    )
    if summary["failed"] > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
