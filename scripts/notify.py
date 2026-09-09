#!/usr/bin/env python3
"""
High-score Telegram digest — Phase 7, decision #4 ("Telegram Bot webhook for
high-score notifications (>85%)").

Finds every `fact_outreach` row scored **today**, above the threshold, and
sends one digest message to a Telegram chat via the Bot API. Run as the
final step of `.github/workflows/daily_harvest.yml`, after
`scripts/score_unscored_jobs.py`.

WHY "drafted_at IS today" CORRECTLY MEANS "SCORED IN TODAY'S RUN":
    `eagent.ats.loader.upsert_outreach_result` only sets `drafted_at` on the
    first INSERT for a job — it's left untouched on every subsequent
    UPDATE (see that function's docstring). `score_unscored_jobs.py` only
    ever scores jobs that have **no** existing `fact_outreach` row, i.e.
    every row it writes is a fresh INSERT. Put together: every row with
    `drafted_at::date = today` was necessarily scored by today's run, not
    an older row that happened to get touched again. This is a real
    invariant this query leans on, not a coincidence — if a future change
    ever makes `score_unscored_jobs.py` re-score existing rows, this
    filter would need to change with it.

WHY WORKFLOW-FAILURE ALERTING ISN'T HERE TOO:
    GitHub Actions already emails the repo owner on a failed scheduled
    workflow run, by default, for free, with zero code. Duplicating that
    into a second Telegram failure-path wasn't asked for and would just be
    two systems to keep in sync for the same signal. This script's only
    job is the thing that isn't already free: "tell me about a good match."

CREDENTIALS: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID — both environment-sourced.
    If either is unset, this script logs a warning and exits 0 rather than
    failing the whole workflow — alerting being unconfigured yet shouldn't
    take down the harvest run itself.

Usage:
    python scripts/notify.py --threshold 85
"""
from __future__ import annotations

import argparse
import logging
import os
from typing import Any, Callable, Dict, List, Optional

import requests
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from eagent.config import get_engine
from eagent.schema import dim_companies, dim_jobs, fact_outreach

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("notify")

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"

HttpPost = Callable[..., "requests.Response"]


def get_todays_high_scorers(engine: Engine, threshold: float) -> List[Dict[str, Any]]:
    """Every fact_outreach row scored today with ats_score above threshold,
    best match first. See module docstring for why `drafted_at::date =
    today` correctly means "scored in today's run" for this pipeline."""
    query = (
        select(
            fact_outreach.c.outreach_id,
            dim_jobs.c.title,
            dim_companies.c.name.label("company_name"),
            fact_outreach.c.ats_score,
            dim_jobs.c.job_url,
        )
        .select_from(
            fact_outreach
            .join(dim_jobs, fact_outreach.c.job_id == dim_jobs.c.job_id)
            .join(dim_companies, dim_jobs.c.company_id == dim_companies.c.company_id)
        )
        .where(
            fact_outreach.c.ats_score > threshold,
            func.date(fact_outreach.c.drafted_at) == func.current_date(),
        )
        .order_by(fact_outreach.c.ats_score.desc())
    )
    with engine.connect() as conn:
        return [dict(row._mapping) for row in conn.execute(query)]


def build_digest_message(rows: List[Dict[str, Any]], threshold: float) -> str:
    """Pure formatting function — data in, Telegram-ready text out. Kept
    separate from the network call so the message format is testable
    without mocking HTTP."""
    header = f"🎯 {len(rows)} new match{'es' if len(rows) != 1 else ''} above {threshold:.0f}% today"
    lines = [header, ""]
    for row in rows:
        line = f"• {row['ats_score']:.0f}% — {row['title']} @ {row['company_name']}"
        if row["job_url"]:
            line += f"\n  {row['job_url']}"
        lines.append(line)
    return "\n".join(lines)


def send_telegram_message(token: str, chat_id: str, text: str, http_post: Optional[HttpPost] = None) -> None:
    post = http_post or requests.post
    response = post(
        TELEGRAM_API_URL.format(token=token),
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        timeout=10,
    )
    response.raise_for_status()


def main() -> None:
    parser = argparse.ArgumentParser(description="Send a Telegram digest of today's high-scoring matches.")
    parser.add_argument("--threshold", type=float, default=85.0)
    args = parser.parse_args()

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.warning("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set — skipping notification (not a failure).")
        return

    engine = get_engine()
    rows = get_todays_high_scorers(engine, args.threshold)

    if not rows:
        logger.info("No matches above %.0f%% found today — nothing to notify.", args.threshold)
        return

    message = build_digest_message(rows, args.threshold)
    send_telegram_message(token, chat_id, message)
    logger.info("Sent digest for %d match(es) above %.0f%%.", len(rows), args.threshold)


if __name__ == "__main__":
    main()
