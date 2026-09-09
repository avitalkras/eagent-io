#!/usr/bin/env python3
"""
The human-in-the-loop entry point — Phase 4.

    review                          interactive queue: approve/reject every
                                     Drafted-and-unapproved outreach, best
                                     ATS match first
    sent OUTREACH_ID                Approved -> Sent
    replied OUTREACH_ID             Sent -> Replied
    interview OUTREACH_ID           Sent/Replied -> Interview
    reject OUTREACH_ID              any non-terminal status -> Rejected

`sent`/`replied`/`interview` model events that would normally arrive from
elsewhere (an email-tracking webhook, a calendar integration) — exposed here
as direct CLI commands so the whole funnel is drivable and testable without
those integrations existing yet.

Usage:
    python scripts/manage_outreach.py review --limit 10
    python scripts/manage_outreach.py sent 42
"""
from __future__ import annotations

import argparse
import logging

from eagent.config import get_engine
from eagent.workflow import (
    InvalidTransitionError,
    approve_outreach,
    get_pending_approvals,
    mark_interview,
    mark_replied,
    mark_sent,
    reject_outreach,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("manage_outreach")

_TRANSITION_ACTIONS = {
    "sent": mark_sent,
    "replied": mark_replied,
    "interview": mark_interview,
    "reject": reject_outreach,
}


def run_review_queue(engine, limit) -> None:
    pending = get_pending_approvals(engine, limit=limit)
    if not pending:
        print("No pending approvals.")
        return

    for row in pending:
        print("-" * 72)
        print(f"outreach_id={row['outreach_id']}   ats_score={row['ats_score']}")
        print(f"{row['title']} @ {row['company_name']}")
        print(f"job_url: {row['job_url']}")
        print(f"missing_skills: {', '.join(row['missing_skills'] or []) or '(none)'}")
        print(f"resume_pdf_path: {row['resume_pdf_path']}")

        choice = input("[a]pprove / [r]eject / [s]kip / [q]uit: ").strip().lower()
        if choice == "a":
            approve_outreach(engine, row["outreach_id"])
            print("Approved.")
        elif choice == "r":
            reject_outreach(engine, row["outreach_id"])
            print("Rejected.")
        elif choice == "q":
            break
        # anything else (including "s") -> leave it Drafted, move to the next one


def main() -> None:
    parser = argparse.ArgumentParser(description="Review and advance outreach through the funnel.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    review_parser = subparsers.add_parser("review", help="Interactively approve/reject pending outreach.")
    review_parser.add_argument("--limit", type=int, default=None)

    for action in _TRANSITION_ACTIONS:
        action_parser = subparsers.add_parser(action, help=f"Mark one outreach as {action}.")
        action_parser.add_argument("outreach_id", type=int)

    args = parser.parse_args()
    engine = get_engine()

    try:
        if args.command == "review":
            run_review_queue(engine, args.limit)
        else:
            _TRANSITION_ACTIONS[args.command](engine, args.outreach_id)
            print(f"outreach_id={args.outreach_id} -> {args.command}")
    except InvalidTransitionError as exc:
        parser.exit(status=1, message=f"Error: {exc}\n")


if __name__ == "__main__":
    main()
