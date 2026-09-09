"""
The "local webhook" backend for Power BI's Actions column — Phase 5b.

WHAT THIS IS FOR:
    The Master Matrix on the Power BI CRM page (docs/06-dashboard-layout-and-
    approval-ux.md §1b) has an Actions column with Approve/Reject links per
    row. Power BI itself can't write back to Postgres — it's a reporting
    tool, not an app platform. This tiny FastAPI service is what those
    links actually call. It contains NO new business logic: every endpoint
    is a thin wrapper around the already-tested state machine in
    `eagent.workflow` (Phase 4). If you trust workflow.py, you trust this.

WHY BOTH A POST ENDPOINT *AND* A GET "-page" ENDPOINT PER ACTION:
    HTTP GET is supposed to be safe (no side effects) — browsers,
    link-prefetchers, and Power BI's own hyperlink navigation can all issue
    a GET without the user "meaning" to trigger it. Approving/rejecting an
    outreach is a real side effect, so the mutation itself lives behind a
    POST (`/outreach/{id}/approve`) — correct REST semantics.

    But a plain Power BI Table/Matrix hyperlink column can ONLY navigate via
    GET — it can't issue a POST with a body. So each action also gets a GET
    "confirmation page" (`/outreach/{id}/approve-page`) that renders a tiny
    HTML page with a button; clicking that button is what fires the real
    POST via JavaScript `fetch()`. This is the standard "GET opens a
    confirm step, POST does the mutation" pattern for exactly this
    situation — one extra click, but the mutating action itself is never
    reachable by a bare GET. See docs/06 §3 for the full write-up and the
    trade-off against a single-GET shortcut.

SCOPE: this is a local, single-operator tool, not a public API. No
    authentication is implemented — see docs/06 §3 for what you'd add
    (an API key header, or running it only behind a VPN/Power BI Gateway)
    before ever exposing it beyond localhost.
"""
from __future__ import annotations

from typing import Callable

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.engine import Engine

from eagent.config import get_engine
from eagent.workflow import InvalidTransitionError, approve_outreach, reject_outreach

app = FastAPI(
    title="Eagent.io Outreach Webhook",
    description="Local webhook backend for the Power BI Actions column. Wraps eagent.workflow — no business logic here.",
)


def get_db_engine() -> Engine:
    """FastAPI dependency, overridden with a fake engine in tests.

    Same dependency-injection instinct as `http_get`/`http_post` in the
    scrapers and LLM providers (Phase 2/3): the real thing here is
    `eagent.config.get_engine()`, swapped out at the boundary for tests.
    """
    return get_engine()


def _confirmation_page(action_label: str, outreach_id: int, post_path: str) -> str:
    """One small HTML page, shared by both the approve-page and reject-page
    routes — just the label and target path differ."""
    return f"""<!doctype html>
<html>
<head><meta charset="utf-8"><title>{action_label} outreach #{outreach_id}</title></head>
<body style="font-family: sans-serif; max-width: 480px; margin: 4rem auto; text-align: center;">
  <h2>{action_label} outreach #{outreach_id}?</h2>
  <button id="confirm-btn" style="font-size: 1.1rem; padding: 0.6rem 1.4rem;">
    Confirm {action_label}
  </button>
  <p id="result" style="margin-top: 1rem;"></p>
  <script>
    document.getElementById("confirm-btn").addEventListener("click", async () => {{
      const res = await fetch("{post_path}", {{ method: "POST" }});
      const body = await res.json().catch(() => ({{}}));
      document.getElementById("result").textContent =
        res.ok ? "Done: " + JSON.stringify(body) : "Error: " + (body.detail || res.statusText);
    }});
  </script>
</body>
</html>"""


def _run_transition(fn: Callable[[Engine, int], None], engine: Engine, outreach_id: int, action: str) -> dict:
    """Shared error translation: eagent.workflow's exceptions become the
    right HTTP status codes instead of a generic 500."""
    try:
        fn(engine, outreach_id)
    except ValueError as exc:
        # eagent.workflow raises plain ValueError for "no such outreach_id".
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"outreach_id": outreach_id, "action": action}


# --- Approve -----------------------------------------------------------------


@app.post("/outreach/{outreach_id}/approve")
def approve(outreach_id: int, engine: Engine = Depends(get_db_engine)) -> dict:
    return _run_transition(approve_outreach, engine, outreach_id, "approved")


@app.get("/outreach/{outreach_id}/approve-page", response_class=HTMLResponse)
def approve_page(outreach_id: int) -> str:
    return _confirmation_page("Approve", outreach_id, f"/outreach/{outreach_id}/approve")


# --- Reject --------------------------------------------------------------


@app.post("/outreach/{outreach_id}/reject")
def reject(outreach_id: int, engine: Engine = Depends(get_db_engine)) -> dict:
    return _run_transition(reject_outreach, engine, outreach_id, "rejected")


@app.get("/outreach/{outreach_id}/reject-page", response_class=HTMLResponse)
def reject_page(outreach_id: int) -> str:
    return _confirmation_page("Reject", outreach_id, f"/outreach/{outreach_id}/reject")
