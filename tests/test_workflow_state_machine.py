"""
Pure, DB-free tests for eagent.workflow's state machine — ALLOWED_TRANSITIONS,
can_transition(), and _predecessors_of(). No engine, no mocking: this is
plain data and pure functions, exactly the kind of logic that should be
fully unit-testable without touching a database.
"""
from __future__ import annotations

from eagent.workflow import ALLOWED_TRANSITIONS, _predecessors_of, can_transition

ALL_STATUSES = {"Drafted", "Approved", "Sent", "Replied", "Interview", "Rejected"}


def test_every_status_has_an_entry_in_the_transition_table():
    assert set(ALLOWED_TRANSITIONS.keys()) == ALL_STATUSES


def test_rejected_is_terminal():
    assert ALLOWED_TRANSITIONS["Rejected"] == set()
    assert can_transition("Rejected", "Drafted") is False
    assert can_transition("Rejected", "Approved") is False


def test_every_non_terminal_status_can_reach_rejected():
    for status in ALL_STATUSES - {"Rejected"}:
        assert can_transition(status, "Rejected") is True, f"{status} -> Rejected should be legal"


def test_happy_path_funnel_is_legal_step_by_step():
    assert can_transition("Drafted", "Approved") is True
    assert can_transition("Approved", "Sent") is True
    assert can_transition("Sent", "Replied") is True
    assert can_transition("Replied", "Interview") is True


def test_sent_can_reach_interview_directly_without_a_reply():
    # Some recruiters invite to interview without an explicit "Replied" step.
    assert can_transition("Sent", "Interview") is True


def test_cannot_skip_the_approval_gate():
    assert can_transition("Drafted", "Sent") is False


def test_cannot_move_backwards():
    assert can_transition("Sent", "Drafted") is False
    assert can_transition("Approved", "Drafted") is False
    assert can_transition("Interview", "Sent") is False


def test_no_self_transitions():
    for status in ALL_STATUSES:
        assert can_transition(status, status) is False


def test_predecessors_of_approved_is_only_drafted():
    assert _predecessors_of("Approved") == {"Drafted"}


def test_predecessors_of_rejected_is_every_non_terminal_status():
    assert _predecessors_of("Rejected") == ALL_STATUSES - {"Rejected"}


def test_predecessors_of_matches_can_transition_both_directions():
    """Cross-check: _predecessors_of and can_transition must never disagree
    — they're derived from the exact same ALLOWED_TRANSITIONS table."""
    for to_status in ALL_STATUSES:
        preds = _predecessors_of(to_status)
        for from_status in ALL_STATUSES:
            assert (from_status in preds) == can_transition(from_status, to_status)
