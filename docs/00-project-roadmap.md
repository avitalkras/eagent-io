# Eagent.io — Project Roadmap

A portfolio-grade Data Engineering platform, built in 5 phases. Each phase is a
self-contained learning module.

---

## Phase 1 — Database Modeling & Star Schema ✅
- **Deliverables:** `sql/01_schema.sql`, `sql/02_populate_dim_dates.sql`
- **Skills:** dimensional modeling (Kimball), star schema, surrogate vs natural
  keys, idempotency, constraints, indexing, PostgreSQL types.
- **Docs:** [`01-database-modeling-star-schema.md`](01-database-modeling-star-schema.md)

## Phase 2 — Ingestion & Recruiter Enrichment ✅
- **Deliverables:** `src/eagent/` (`models.py`, `scrapers/base.py`,
  `scrapers/remoteok.py`, `loader.py`, `enrichment.py`, `config.py`),
  `scripts/run_ingestion.py`, `tests/`
- Scrapes RemoteOK's public API in Python; idempotently upserts into
  `dim_companies` / `dim_jobs` via SQLAlchemy Core; finds/guesses a recruiter
  contact per company via a mockable `RecruiterEnricher`.
- **Skills:** abstract base classes & the Strategy pattern, dependency
  injection for testability, Pydantic v2 runtime validation,
  `INSERT ... ON CONFLICT` upserts (`DO NOTHING` vs. `DO UPDATE ... RETURNING`),
  transactional batch loads, graceful degradation of flaky 3rd-party APIs,
  unit vs. integration testing strategy.
- **Docs:** [`02-ingestion-and-enrichment.md`](02-ingestion-and-enrichment.md)

## Phase 3 — ATS Scoring & Resume Tailoring ✅
- **Deliverables:** `src/eagent/ats/` (`models.py`, `llm.py`, `render.py`,
  `loader.py`), `sql/03_ats_outreach_columns.sql`,
  `scripts/run_ats_pipeline.py`, `data/master_resume.example.json`
- Scores a structured Master Resume against a job description via an LLM
  (Groq or Gemini) in strict JSON mode, tailors the resume without
  fabrication, renders a single-column ATS-safe PDF via Typst, and
  idempotently writes the result into `fact_outreach`.
- **Skills:** Pydantic-as-contract for LLM output, JSON Schema export,
  structured/schema-constrained LLM generation, the generate → validate →
  retry-with-feedback pattern, deterministic anti-hallucination checks
  (vs. an LLM judge), PDF generation from structured data, additive schema
  migrations, upserts that never clobber a human decision.
- **Docs:** [`03-ats-scoring-and-resume-generation.md`](03-ats-scoring-and-resume-generation.md)

## Phase 4 — Human-in-the-Loop Approval Workflow ✅
- **Deliverables:** `src/eagent/schema.py`, `src/eagent/workflow.py`,
  `scripts/manage_outreach.py`, `sql/04_interviewed_at_column.sql`
- Review queue for Drafted-and-unapproved outreach; approve/reject before
  anything is sent; advance `outreach_status` through the Approved -> Sent ->
  Replied -> Interview funnel, with Rejected reachable from any non-terminal
  state. `is_approved` and `outreach_status` are updated atomically so they
  can never observably disagree.
- Consolidated three separate, drifting partial `fact_outreach` (and
  `dim_jobs`/`dim_companies`) SQLAlchemy Core `Table` mappings — one each in
  Phase 2's and Phase 3's loaders, about to become a third in this phase's
  `workflow.py` — into one shared `eagent/schema.py`, the single Python-side
  source of truth for the star schema.
- **Skills:** state machines as data (one transition table, not
  hardcoded per-function checks), `SELECT ... FOR UPDATE` row locking for
  concurrency-safe transitions, defense-in-depth invariant checks
  (`require_is_approved` in `mark_sent`, independent of the status check),
  closing a gap a downstream phase (5) surfaced by name.
- **Docs:** [`04-human-in-the-loop-approval-workflow.md`](04-human-in-the-loop-approval-workflow.md)

## Phase 5 — Power BI Dashboard ✅ (data model + DAX; built ahead of Phase 4)
- **Deliverables:** `docs/05-power-bi-data-model.md`, `powerbi/eagent_measures.dax`
- Star-schema relationship architecture (single-direction, dims -> fact) over
  the Phase 1 schema; production DAX for the outreach funnel (approval,
  response, interview rates), ATS scoring, and a dynamic `ATS Tier`
  calculated column.
- Built directly against the Phase 1 schema contract, ahead of Phase 4's
  application code — the funnel measures (`Pending Approvals Count`,
  `Approval Rate %`) are ready to report on `is_approved`/`outreach_status`
  the moment Phase 4 starts writing to them. Two schema gaps surfaced during
  this design pass: the missing `interviewed_at` timestamp was closed by
  Phase 4 (`sql/04_interviewed_at_column.sql`); the missing `dim_jobs ->
  dim_dates` link is still open for a future migration.
- **Skills:** star-schema relationships in Power BI (cardinality,
  cross-filter direction), DAX (`DIVIDE`/`BLANK`/`FILTER`/`SWITCH`,
  `VAR`/`RETURN`, measure reuse), calculated column vs. measure trade-offs,
  designing a semantic layer against a schema contract before every producer
  of that schema exists.
- **Docs:** [`05-power-bi-data-model.md`](05-power-bi-data-model.md)

## Phase 5b — Dashboard Layout & Approval UX ✅
- **Deliverables:** `docs/06-dashboard-layout-and-approval-ux.md`,
  `src/eagent/api.py`, extensions to `powerbi/eagent_measures.dax`
- Full 2-page wireframes and visual specs: Page 1 Operational CRM (KPI
  banner, Master Matrix with conditional formatting, 4 slicers), Page 2
  Market & Funnel Analytics (conversion funnel, tech-stack demand, ATS
  score vs. response-rate scatter, missing-skills ranking) — including
  Power Query M code to unpivot the `tech_stack`/`missing_skills` Postgres
  arrays, a real gap Phase 1 predicted (`docs/01` §7).
- Compared three approval-mechanism architectures (Power Apps visual,
  linked Google Sheet, local webhook) and **built and tested** the chosen
  one: a small FastAPI service wrapping Phase 4's `eagent.workflow`
  functions with zero new business logic — a GET confirmation page (what a
  plain Power BI hyperlink can call) that triggers the real POST mutation
  on click, so the Actions column can never approve/reject on a bare
  navigation or link-prefetch.
- **Skills:** distinguishing what's testable from what isn't in a stack you
  can't fully run locally (Power BI Desktop) and building/testing the part
  that is anyway, REST semantics (GET-must-be-safe) applied to a real UX
  constraint, Power Query array-unpivoting, scoping a local tool's security
  boundary explicitly instead of either over-building or ignoring it.
- **Docs:** [`06-dashboard-layout-and-approval-ux.md`](06-dashboard-layout-and-approval-ux.md)

---

## Cross-cutting (learned throughout)
- **Git & GitHub** — branching, commits, PRs → see
  [`git-and-github-basics.md`](git-and-github-basics.md).
- **Docker** — containerizing Postgres + the app.
- **GitHub Actions** — CI: lint SQL/Python, run migrations on a test DB.
