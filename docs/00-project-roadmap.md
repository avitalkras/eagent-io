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

## Phase 4 — Human-in-the-Loop Approval Workflow ⬜
- Review queue for Drafted outreach; approve/reject before anything is sent;
  advance `outreach_status` through the Approved -> Sent -> Replied -> Interview
  funnel.
- **Skills:** workflow state machines, approval gates, funnel/velocity metrics.

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
  this design pass (no `dim_jobs -> dim_dates` link, no `interviewed_at`
  timestamp) are documented for a future migration.
- **Skills:** star-schema relationships in Power BI (cardinality,
  cross-filter direction), DAX (`DIVIDE`/`BLANK`/`FILTER`/`SWITCH`,
  `VAR`/`RETURN`, measure reuse), calculated column vs. measure trade-offs,
  designing a semantic layer against a schema contract before every producer
  of that schema exists.
- **Docs:** [`05-power-bi-data-model.md`](05-power-bi-data-model.md)

---

## Cross-cutting (learned throughout)
- **Git & GitHub** — branching, commits, PRs → see
  [`git-and-github-basics.md`](git-and-github-basics.md).
- **Docker** — containerizing Postgres + the app.
- **GitHub Actions** — CI: lint SQL/Python, run migrations on a test DB.
