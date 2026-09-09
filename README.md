# Eagent.io

> An end-to-end **Data Engineering & Analytics** platform that harvests
> data/analytics job postings, enriches recruiter contacts, scores & tailors
> resumes for ATS compatibility, runs a human-in-the-loop approval workflow, and
> surfaces market + operational intelligence in a Power BI dashboard.

**This repo is also a learning journal.** Every design decision is documented in
[`docs/`](docs/) so it doubles as a study resource on the road to becoming a
Data Engineer.

---

## 🎯 What it does (the vision)

1. **Harvest** — scrape data/analytics job postings from multiple boards.
2. **Enrich** — find recruiter contacts for each posting.
3. **Score & tailor** — use an LLM to score resume ↔ job-description match (ATS)
   and identify missing skills.
4. **Approve** — a human-in-the-loop gate before anything is "sent".
5. **Analyze** — a Power BI dashboard over a PostgreSQL **star schema**.

## 🧰 Tech stack

| Layer | Technology |
|---|---|
| Language | Python |
| Warehouse | PostgreSQL |
| BI / Dashboard | Power BI Desktop |
| Orchestration / CI | GitHub Actions |
| Containerization | Docker |
| LLM | Gemini / Groq API |
| Resume rendering | Typst / LaTeX |

---

## 📦 Project structure

```
Job interview scraping automation/
├── README.md                  ← you are here
├── .gitignore
├── pyproject.toml             ← Python package + pytest config
├── requirements.txt
├── .env.example                ← copy to .env and fill in (never commit .env)
├── docs/                       ← learning notes (the "why" behind everything)
│   ├── 00-project-roadmap.md
│   ├── 01-database-modeling-star-schema.md
│   ├── 02-ingestion-and-enrichment.md
│   ├── 03-ats-scoring-and-resume-generation.md
│   ├── 04-human-in-the-loop-approval-workflow.md
│   ├── 05-power-bi-data-model.md
│   ├── 06-dashboard-layout-and-approval-ux.md
│   └── git-and-github-basics.md
├── sql/                        ← DB migrations, applied in order
│   ├── 01_schema.sql            ← Phase 1: star schema DDL
│   ├── 02_populate_dim_dates.sql
│   ├── 03_ats_outreach_columns.sql   ← Phase 3: resume_pdf_path + UNIQUE(job_id)
│   └── 04_interviewed_at_column.sql  ← Phase 4: interviewed_at timestamp
├── data/
│   └── master_resume.example.json    ← sample structured resume (fabricated data)
├── src/eagent/
│   ├── schema.py                 ← shared SQLAlchemy Core Table mappings (single source of truth)
│   ├── models.py                 ← Phase 2: Pydantic models (JobPosting, RecruiterContact)
│   ├── config.py                 ← env/DB config loading
│   ├── loader.py                 ← Phase 2: idempotent SQLAlchemy Core upserts
│   ├── enrichment.py             ← Phase 2: RecruiterEnricher + HunterIOProvider
│   ├── workflow.py                ← Phase 4: approval state machine + transitions
│   ├── api.py                     ← Phase 5b: FastAPI webhook backend for the Actions column
│   ├── scrapers/
│   │   ├── base.py                ← BaseScraper ABC
│   │   └── remoteok.py            ← RemoteOKScraper
│   └── ats/                      ← Phase 3: ATS scoring + resume generation
│       ├── models.py               ← MasterResume, ATSAnalysisResult, hallucination guard
│       ├── llm.py                  ← GroqProvider, GeminiProvider, analyze_and_tailor()
│       ├── render.py               ← Typst template + PDF compilation
│       ├── loader.py               ← idempotent fact_outreach upsert
│       └── ats_analysis_result.schema.json   ← generated JSON Schema
├── scripts/
│   ├── run_ingestion.py        ← Phase 2: scrape → load → enrich, wired end-to-end
│   ├── run_ats_pipeline.py     ← Phase 3: score → tailor → render → save, wired end-to-end
│   ├── export_ats_schema.py    ← regenerates the JSON Schema artifact
│   └── manage_outreach.py      ← Phase 4: review queue + funnel-advancing CLI
├── powerbi/
│   └── eagent_measures.dax     ← Phase 5: copy-paste DAX (measures, ATS Tier, funnel, Actions URLs)
└── tests/                      ← unit tests (mocked) + DB-gated integration tests
```

---

## 🗺️ Roadmap

| Phase | Topic | Status |
|---|---|---|
| **1** | Database Modeling & Star Schema (DDL) | ✅ Done |
| **2** | Python scraper + idempotent load + recruiter enrichment | ✅ Done |
| **3** | LLM ATS scoring + resume tailoring | ✅ Done |
| **4** | Human-in-the-loop approval workflow | ✅ Done |
| **5** | Power BI data model + DAX (built ahead of Phase 4) | ✅ Done |
| **5b** | Dashboard layout wireframes + approval-mechanism webhook | ✅ Done |

See [`docs/00-project-roadmap.md`](docs/00-project-roadmap.md) for details.

---

## 🚀 Running it locally

### Phase 1 — build the database

You need PostgreSQL. Then:

```bash
createdb eagent
psql -d eagent -f sql/01_schema.sql              # tables, constraints, indexes
psql -d eagent -f sql/02_populate_dim_dates.sql   # calendar dimension, 2 years
psql -d eagent -c "SELECT COUNT(*) FROM dim_dates;"
```

### Phase 2 — ingest jobs & enrich recruiters

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env          # then edit .env: set DATABASE_URL, optional HUNTER_API_KEY

python scripts/run_ingestion.py --limit 25
```

### Phase 3 — ATS score, tailor, and generate a PDF

You also need the [`typst`](https://github.com/typst/typst) CLI for PDF
generation (`brew install typst`, or download a release binary), plus an API
key for Groq or Gemini:

```bash
psql -d eagent -f sql/03_ats_outreach_columns.sql   # Migration #3, run once

python scripts/run_ats_pipeline.py \
    --master-resume data/master_resume.example.json \
    --job-description-file path/to/some_job_description.txt \
    --job-id 1 \
    --provider groq \
    --api-key "$GROQ_API_KEY"
```

### Phase 4 — review and approve outreach

```bash
psql -d eagent -f sql/04_interviewed_at_column.sql   # Migration #4, run once

python scripts/manage_outreach.py review --limit 10   # interactive approve/reject queue

# advance an already-approved outreach through the rest of the funnel:
python scripts/manage_outreach.py sent 42
python scripts/manage_outreach.py replied 42
python scripts/manage_outreach.py interview 42
```

Run the tests:

```bash
pytest                         # unit tests — fast, fully offline (mocked HTTP + real typst compile)

# integration tests — need a real scratch database with all 4 migrations applied:
createdb eagent_test
psql -d eagent_test -f sql/01_schema.sql
psql -d eagent_test -f sql/02_populate_dim_dates.sql
psql -d eagent_test -f sql/03_ats_outreach_columns.sql
psql -d eagent_test -f sql/04_interviewed_at_column.sql
DATABASE_URL=postgresql+psycopg2://localhost:5432/eagent_test pytest -m integration
```

### Phase 5 — Power BI data model & DAX

1. Get Data → PostgreSQL database in Power BI Desktop, import `dim_companies`,
   `dim_jobs`, `dim_recruiters`, `dim_dates`, `fact_outreach`.
2. Build the relationships described in
   [`docs/05-power-bi-data-model.md`](docs/05-power-bi-data-model.md) §1
   (all single-direction, dims → fact; mark `dim_dates` as the Date Table).
3. Create a hidden `_Measures` table and paste in every measure from
   [`powerbi/eagent_measures.dax`](powerbi/eagent_measures.dax).
4. Add the `ATS Tier` / `ATS Tier Sort Order` calculated columns on
   `fact_outreach` from the same file.

> This phase's DAX was authored against the schema, not executed in Power BI
> Desktop (unavailable in this environment) — see the honesty note at the top
> of `docs/05-power-bi-data-model.md` before trusting it blindly.

### Phase 5b — dashboard layout + the approval webhook

The wireframes/visual specs in
[`docs/06-dashboard-layout-and-approval-ux.md`](docs/06-dashboard-layout-and-approval-ux.md)
are, like Phase 5, authored against the schema and not built in Power BI
Desktop here. The approval webhook is different — it's real and tested:

```bash
uvicorn eagent.api:app --reload --port 8000
```

Then point the Master Matrix's `Approve Action URL` / `Reject Action URL`
columns (`powerbi/eagent_measures.dax`, Data Category = Web URL) at
`http://localhost:8000/outreach/{id}/approve-page` / `.../reject-page`.

The webhook's own tests run as part of the same `pytest` / `pytest -m
integration` commands under Phase 4 above — no separate test command needed.

---

## 📚 Learning docs

- [Star schema & database modeling](docs/01-database-modeling-star-schema.md) —
  the core of Phase 1.
- [Ingestion & recruiter enrichment](docs/02-ingestion-and-enrichment.md) —
  the core of Phase 2 (ABCs, dependency injection, idempotent upserts,
  Pydantic validation, testing strategy).
- [ATS scoring & resume generation](docs/03-ats-scoring-and-resume-generation.md) —
  the core of Phase 3 (anti-hallucination guardrails, structured LLM output,
  the retry-with-feedback pattern, Typst PDF rendering).
- [Human-in-the-loop approval workflow](docs/04-human-in-the-loop-approval-workflow.md) —
  the core of Phase 4 (state machines as data, row-level locking for
  concurrency safety, defense-in-depth invariant checks, closing a gap a
  downstream phase found).
- [Power BI data model & DAX](docs/05-power-bi-data-model.md) — the core of
  Phase 5 (relationship cardinality/direction, `DIVIDE`/`FILTER` patterns,
  calculated column vs. measure, and how Phase 4 closed one of the two
  schema gaps this design surfaced).
- [Dashboard layout & approval UX](docs/06-dashboard-layout-and-approval-ux.md) —
  the core of Phase 5b (wireframes for both report pages, Power Query
  array-unpivoting, why GET must stay side-effect-free, and a real
  side-by-side of three approval-mechanism architectures with one actually
  built and tested).
- [Git & GitHub basics](docs/git-and-github-basics.md) — the workflow used to
  build this repo.
