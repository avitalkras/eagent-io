#!/usr/bin/env python3
"""
Wires the Phase 3 pieces together end-to-end: score -> tailor -> render -> save.

    Master Resume + Job Description
              |
              v
    analyze_and_tailor()   (LLM: score + tailor, validated + hallucination-checked)
              |
              v
    render_typst_source()  (pure: JSON -> Typst markup)
              |
              v
    compile_pdf()          (typst CLI: markup -> PDF file)
              |
              v
    upsert_outreach_result()  (idempotent write into fact_outreach)

Usage:
    python scripts/run_ats_pipeline.py \\
        --master-resume data/master_resume.example.json \\
        --job-description-file /path/to/jd.txt \\
        --job-id 123 \\
        --provider groq
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from eagent.ats.llm import GeminiProvider, GroqProvider, analyze_and_tailor
from eagent.ats.loader import upsert_outreach_result
from eagent.ats.models import MasterResume
from eagent.ats.render import compile_pdf, render_typst_source
from eagent.config import get_engine

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("run_ats_pipeline")


def main() -> None:
    parser = argparse.ArgumentParser(description="Score a job vs. a Master Resume and generate a tailored PDF.")
    parser.add_argument("--master-resume", type=Path, default=Path("data/master_resume.example.json"))
    parser.add_argument("--job-description-file", type=Path, required=True)
    parser.add_argument("--job-id", type=int, required=True, help="dim_jobs.job_id to attach this result to")
    parser.add_argument("--recruiter-id", type=int, default=None)
    parser.add_argument("--provider", choices=["groq", "gemini"], default="groq")
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("output/resumes"))
    args = parser.parse_args()

    master_resume = MasterResume.model_validate(json.loads(args.master_resume.read_text()))
    job_description = args.job_description_file.read_text()

    provider = (
        GroqProvider(api_key=args.api_key) if args.provider == "groq" else GeminiProvider(api_key=args.api_key)
    )

    logger.info("Scoring job_id=%s against %s via %s...", args.job_id, args.master_resume, provider.name)
    result = analyze_and_tailor(job_description, master_resume, provider)
    logger.info(
        "ats_score=%.1f matched=%d missing=%d",
        result.ats_score, len(result.matched_keywords), len(result.missing_hard_skills),
    )

    typst_source = render_typst_source(master_resume, result.tailored_resume)
    pdf_path = args.output_dir / f"job-{args.job_id}.pdf"
    compile_pdf(typst_source, pdf_path)
    logger.info("Wrote tailored resume PDF to %s", pdf_path)

    engine = get_engine()
    outreach_id = upsert_outreach_result(
        engine,
        job_id=args.job_id,
        ats_result=result,
        resume_pdf_path=str(pdf_path),
        recruiter_id=args.recruiter_id,
    )
    logger.info("Upserted fact_outreach.outreach_id=%s", outreach_id)


if __name__ == "__main__":
    main()
