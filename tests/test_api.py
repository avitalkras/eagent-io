"""
Unit tests for eagent.api — the FastAPI webhook backend for Power BI's
Actions column.

WHY MONKEYPATCH eagent.workflow's FUNCTIONS INSTEAD OF HITTING A REAL DB?
    This module deliberately contains no business logic — every endpoint
    just calls eagent.workflow and translates its exceptions to HTTP status
    codes. That translation is exactly what these tests check; the state
    machine itself is already covered by tests/test_workflow_state_machine.py
    and tests/test_workflow_integration.py. Testing it again here through a
    real database would duplicate coverage without testing anything new
    about this layer. Same "test each layer at its own boundary" principle
    as Phase 2's scraper/enrichment tests.

    The `get_db_engine` FastAPI dependency is overridden with a stand-in
    that's never actually used (since the workflow functions are patched
    out before they'd touch it) — present only so the endpoint's dependency
    injection resolves without needing DATABASE_URL set.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from eagent import api
from eagent.workflow import InvalidTransitionError


@pytest.fixture()
def client():
    api.app.dependency_overrides[api.get_db_engine] = lambda: object()
    yield TestClient(api.app)
    api.app.dependency_overrides.clear()


# --- POST /outreach/{id}/approve ---------------------------------------------


def test_approve_success_returns_200(client, monkeypatch):
    calls = []
    monkeypatch.setattr(api, "approve_outreach", lambda engine, outreach_id: calls.append(outreach_id))

    response = client.post("/outreach/42/approve")

    assert response.status_code == 200
    assert response.json() == {"outreach_id": 42, "action": "approved"}
    assert calls == [42]


def test_approve_missing_outreach_returns_404(client, monkeypatch):
    def raise_not_found(engine: Any, outreach_id: int) -> None:
        raise ValueError(f"No fact_outreach row with outreach_id={outreach_id}")

    monkeypatch.setattr(api, "approve_outreach", raise_not_found)

    response = client.post("/outreach/999/approve")

    assert response.status_code == 404
    assert "999" in response.json()["detail"]


def test_approve_illegal_transition_returns_409(client, monkeypatch):
    def raise_invalid(engine: Any, outreach_id: int) -> None:
        raise InvalidTransitionError(f"outreach_id={outreach_id}: cannot move from 'Approved' to 'Approved'")

    monkeypatch.setattr(api, "approve_outreach", raise_invalid)

    response = client.post("/outreach/42/approve")

    assert response.status_code == 409
    assert "cannot move" in response.json()["detail"]


# --- POST /outreach/{id}/reject ----------------------------------------------


def test_reject_success_returns_200(client, monkeypatch):
    calls = []
    monkeypatch.setattr(api, "reject_outreach", lambda engine, outreach_id: calls.append(outreach_id))

    response = client.post("/outreach/7/reject")

    assert response.status_code == 200
    assert response.json() == {"outreach_id": 7, "action": "rejected"}
    assert calls == [7]


def test_reject_illegal_transition_returns_409(client, monkeypatch):
    def raise_invalid(engine: Any, outreach_id: int) -> None:
        raise InvalidTransitionError("outreach_id=7: cannot move from 'Rejected' to 'Rejected'")

    monkeypatch.setattr(api, "reject_outreach", raise_invalid)

    response = client.post("/outreach/7/reject")

    assert response.status_code == 409


# --- GET confirmation pages: a plain Power BI hyperlink can only issue GET --


def test_approve_page_renders_confirmation_html(client):
    response = client.get("/outreach/42/approve-page")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Approve outreach #42" in response.text
    # the confirm button must POST, not itself mutate on the GET:
    assert 'fetch("/outreach/42/approve"' in response.text
    assert 'method: "POST"' in response.text


def test_reject_page_renders_confirmation_html(client):
    response = client.get("/outreach/7/reject-page")

    assert response.status_code == 200
    assert "Reject outreach #7" in response.text
    assert 'fetch("/outreach/7/reject"' in response.text


def test_get_on_approve_page_never_calls_the_mutating_function(client, monkeypatch):
    """The whole point of the confirm-page pattern: visiting the GET page
    (which Power BI's hyperlink navigation, a browser prefetcher, or a
    crawler could all do unintentionally) must never itself approve anything."""
    calls = []
    monkeypatch.setattr(api, "approve_outreach", lambda engine, outreach_id: calls.append(outreach_id))

    client.get("/outreach/42/approve-page")

    assert calls == []
