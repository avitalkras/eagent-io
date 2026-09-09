"""
The human-in-the-loop approval workflow — Phase 4.

Nothing in this project ever emails a recruiter automatically. Every
outreach starts as `'Drafted'` (Phase 3's `upsert_outreach_result`) and can
only reach `'Sent'` after a human explicitly approves it — `is_approved`
exists in the schema for exactly this ("human-in-the-loop gate", Phase 1
comment in sql/01_schema.sql). This module is that gate, plus the rest of
the outreach lifecycle state machine: Drafted -> Approved -> Sent -> Replied
-> Interview, with Rejected reachable from anywhere.

DESIGN: ONE TABLE IS THE SINGLE SOURCE OF TRUTH FOR THE STATE MACHINE.
    `ALLOWED_TRANSITIONS` below is the only place the workflow graph is
    defined. Every transition function (`approve_outreach`, `mark_sent`,
    ...) derives its allowed starting states FROM that table via
    `_predecessors_of()`, rather than each function hardcoding its own
    "allowed_from" set. Two independent lists that are supposed to describe
    the same graph WILL drift eventually; one table can't disagree with
    itself. Same instinct as `eagent.schema` consolidating what used to be
    three separate partial `fact_outreach` Table definitions.

DESIGN: ROW-LEVEL LOCKING FOR CONCURRENCY SAFETY.
    Every transition does `SELECT ... FOR UPDATE` before checking the
    current status. Without it, two concurrent calls (a human clicking
    "Approve" twice, or a review UI and a webhook handler racing) could both
    read the same starting status, both pass validation, and both write —
    silently corrupting the funnel (e.g. two `sent_at` timestamps racing,
    or a double-send). `FOR UPDATE` makes the second transaction wait for
    the first to commit, then re-read the now-updated status and correctly
    reject the now-invalid transition.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import func, select, update
from sqlalchemy.engine import Engine

from eagent.schema import dim_companies, dim_jobs, fact_outreach

logger = logging.getLogger(__name__)


class InvalidTransitionError(Exception):
    """Raised when a requested status change isn't legal from the outreach's current state."""


# =============================================================================
# The state machine — single source of truth (see module docstring).
# =============================================================================
# Rejected has no outgoing edges: it's a terminal state. Every other status
# can move to Rejected — a recruiter can decline at any stage of the funnel,
# not just at the start.
ALLOWED_TRANSITIONS: Dict[str, Set[str]] = {
    "Drafted":   {"Approved", "Rejected"},
    "Approved":  {"Sent", "Rejected"},
    "Sent":      {"Replied", "Interview", "Rejected"},  # "Interview" direct: some recruiters invite without an explicit reply first
    "Replied":   {"Interview", "Rejected"},
    "Interview": {"Rejected"},
    "Rejected":  set(),
}


def can_transition(from_status: str, to_status: str) -> bool:
    """Pure predicate over ALLOWED_TRANSITIONS — no DB access, easy to unit test."""
    return to_status in ALLOWED_TRANSITIONS.get(from_status, set())


def _predecessors_of(to_status: str) -> Set[str]:
    """Every status ALLOWED_TRANSITIONS permits moving *into* to_status from."""
    return {frm for frm, tos in ALLOWED_TRANSITIONS.items() if to_status in tos}


def _transition(
    engine: Engine,
    outreach_id: int,
    allowed_from: Set[str],
    to_status: str,
    extra_values: Optional[Dict[str, Any]] = None,
    require_is_approved: bool = False,
) -> None:
    """Shared implementation for every public transition function below.

    Locks the target row (`FOR UPDATE`), validates the current status is in
    `allowed_from`, optionally checks `is_approved` (defense in depth for
    `mark_sent` — see its docstring), then updates `outreach_status` plus
    whatever lifecycle timestamp the caller passed in `extra_values`, all in
    one transaction.
    """
    extra_values = dict(extra_values or {})

    with engine.begin() as conn:
        row = conn.execute(
            select(fact_outreach.c.outreach_status, fact_outreach.c.is_approved)
            .where(fact_outreach.c.outreach_id == outreach_id)
            .with_for_update()
        ).first()

        if row is None:
            raise ValueError(f"No fact_outreach row with outreach_id={outreach_id}")

        current_status = row.outreach_status
        if current_status not in allowed_from:
            raise InvalidTransitionError(
                f"outreach_id={outreach_id}: cannot move from '{current_status}' to "
                f"'{to_status}' (allowed only from {sorted(allowed_from)})"
            )

        if require_is_approved and not row.is_approved:
            raise InvalidTransitionError(
                f"outreach_id={outreach_id}: cannot mark as '{to_status}' — is_approved is "
                f"False despite status='{current_status}' (data integrity check failed)"
            )

        conn.execute(
            update(fact_outreach)
            .where(fact_outreach.c.outreach_id == outreach_id)
            .values(outreach_status=to_status, **extra_values)
        )

    logger.info("outreach_id=%s: %s -> %s", outreach_id, current_status, to_status)


# =============================================================================
# Public API — one function per funnel stage.
# =============================================================================


def approve_outreach(engine: Engine, outreach_id: int) -> None:
    """The human-in-the-loop gate itself: Drafted -> Approved.

    Sets `is_approved = TRUE` atomically with the status change — the two
    columns are updated together, on purpose, so they can never observably
    disagree (no "Approved but is_approved=False" state to reason about).
    """
    _transition(
        engine, outreach_id,
        allowed_from=_predecessors_of("Approved"),
        to_status="Approved",
        extra_values={"is_approved": True},
    )


def reject_outreach(engine: Engine, outreach_id: int) -> None:
    """Terminal: any non-terminal status -> Rejected."""
    _transition(
        engine, outreach_id,
        allowed_from=_predecessors_of("Rejected"),
        to_status="Rejected",
    )


def mark_sent(engine: Engine, outreach_id: int) -> None:
    """Approved -> Sent, stamping `sent_at`.

    `require_is_approved=True` is belt-and-suspenders: under normal
    operation, status can only reach 'Approved' via `approve_outreach`,
    which always sets `is_approved=True` in the same transaction, so this
    check should never actually fire. It exists as a second, independent
    check against the literal requirement "before anything is sent" — this
    function trusts `is_approved`, not just the status label, before ever
    stamping a send.
    """
    _transition(
        engine, outreach_id,
        allowed_from=_predecessors_of("Sent"),
        to_status="Sent",
        extra_values={"sent_at": func.now()},
        require_is_approved=True,
    )


def mark_replied(engine: Engine, outreach_id: int) -> None:
    """Sent -> Replied, stamping `replied_at`."""
    _transition(
        engine, outreach_id,
        allowed_from=_predecessors_of("Replied"),
        to_status="Replied",
        extra_values={"replied_at": func.now()},
    )


def mark_interview(engine: Engine, outreach_id: int) -> None:
    """Sent or Replied -> Interview, stamping `interviewed_at`.

    `interviewed_at` (Migration #4, sql/04_interviewed_at_column.sql) closes
    a gap Phase 5's Power BI design work surfaced: without it,
    `Interview Rate %` had no choice but to filter on the current
    `outreach_status`, which undercounts any outreach that later moves on to
    'Rejected'. See docs/05-power-bi-data-model.md for the DAX-side fix.
    """
    _transition(
        engine, outreach_id,
        allowed_from=_predecessors_of("Interview"),
        to_status="Interview",
        extra_values={"interviewed_at": func.now()},
    )


# =============================================================================
# The review queue — what a human actually looks at before approving.
# =============================================================================


def get_pending_approvals(engine: Engine, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Every Drafted-and-not-yet-approved outreach, with enough job/company
    context to make an approve/reject decision, best ATS match first.
    """
    query = (
        select(
            fact_outreach.c.outreach_id,
            dim_jobs.c.title,
            dim_companies.c.name.label("company_name"),
            dim_jobs.c.job_url,
            fact_outreach.c.ats_score,
            fact_outreach.c.missing_skills,
            fact_outreach.c.resume_pdf_path,
            fact_outreach.c.drafted_at,
        )
        .select_from(
            fact_outreach
            .join(dim_jobs, fact_outreach.c.job_id == dim_jobs.c.job_id)
            .join(dim_companies, dim_jobs.c.company_id == dim_companies.c.company_id)
        )
        .where(
            fact_outreach.c.outreach_status == "Drafted",
            fact_outreach.c.is_approved.is_(False),
        )
        .order_by(fact_outreach.c.ats_score.desc().nulls_last())
    )
    if limit is not None:
        query = query.limit(limit)

    with engine.connect() as conn:
        return [dict(row._mapping) for row in conn.execute(query)]
