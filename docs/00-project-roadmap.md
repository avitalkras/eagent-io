# Eagent.io — Project Roadmap

A portfolio-grade Data Engineering platform, built in 5 phases. Each phase is a
self-contained learning module.

---

## Phase 1 — Database Modeling & Star Schema ✅
- **Deliverables:** `sql/01_schema.sql`, `sql/02_populate_dim_dates.sql`
- **Skills:** dimensional modeling (Kimball), star schema, surrogate vs natural
  keys, idempotency, constraints, indexing, PostgreSQL types.
- **Docs:** [`01-database-modeling-star-schema.md`](01-database-modeling-star-schema.md)

## Phase 2 — Scraper & Idempotent Load ⬜
- Scrape job boards in Python; upsert into `dim_companies` / `dim_jobs`.
- **Skills:** HTTP scraping, rate limiting, `INSERT ... ON CONFLICT` upserts,
  parameterized SQL, environment/secret management.

## Phase 3 — Recruiter Enrichment ⬜
- Find recruiter contacts per company; write `dim_recruiters` with a confidence
  score.
- **Skills:** working with 3rd-party enrichment APIs, data quality scoring,
  deduplication.

## Phase 4 — LLM ATS Scoring & Resume Tailoring ⬜
- Score resume ↔ JD match, extract `missing_skills`, tailor a resume, render via
  Typst/LaTeX. Write results into `fact_outreach`.
- **Skills:** LLM prompting, structured output, the human-in-the-loop approval
  gate, document generation.

## Phase 5 — Power BI Dashboard ⬜
- Connect Power BI to the star schema; build operational + market-intelligence
  reports with time-intelligence (YoY/MoM) off `dim_dates`.
- **Skills:** star-schema relationships in Power BI, DAX, data modeling for BI.

---

## Cross-cutting (learned throughout)
- **Git & GitHub** — branching, commits, PRs → see
  [`git-and-github-basics.md`](git-and-github-basics.md).
- **Docker** — containerizing Postgres + the app.
- **GitHub Actions** — CI: lint SQL/Python, run migrations on a test DB.
