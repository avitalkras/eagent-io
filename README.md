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
│   └── git-and-github-basics.md
├── sql/                        ← Phase 1 deliverables
│   ├── 01_schema.sql           ← star schema DDL
│   └── 02_populate_dim_dates.sql
├── src/eagent/                 ← Phase 2 deliverables (installable package)
│   ├── models.py                ← Pydantic models (JobPosting, RecruiterContact)
│   ├── config.py                 ← env/DB config loading
│   ├── loader.py                 ← idempotent SQLAlchemy Core upserts
│   ├── enrichment.py             ← RecruiterEnricher + HunterIOProvider
│   └── scrapers/
│       ├── base.py                ← BaseScraper ABC
│       └── remoteok.py            ← RemoteOKScraper
├── scripts/
│   └── run_ingestion.py        ← scrape → load → enrich, wired end-to-end
└── tests/                      ← unit tests (mocked) + 1 DB-gated integration test
```

---

## 🗺️ Roadmap

| Phase | Topic | Status |
|---|---|---|
| **1** | Database Modeling & Star Schema (DDL) | ✅ Done |
| **2** | Python scraper + idempotent load + recruiter enrichment | ✅ Done |
| 3 | LLM ATS scoring + resume tailoring | ⬜ Planned |
| 4 | Human-in-the-loop approval workflow | ⬜ Planned |
| 5 | Power BI dashboard | ⬜ Planned |

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

Run the tests:

```bash
pytest                         # unit tests — fast, fully offline (mocked HTTP)

# integration test for the loader — needs a real scratch database:
createdb eagent_test && psql -d eagent_test -f sql/01_schema.sql
DATABASE_URL=postgresql+psycopg2://localhost:5432/eagent_test pytest -m integration
```

---

## 📚 Learning docs

- [Star schema & database modeling](docs/01-database-modeling-star-schema.md) —
  the core of Phase 1.
- [Ingestion & recruiter enrichment](docs/02-ingestion-and-enrichment.md) —
  the core of Phase 2 (ABCs, dependency injection, idempotent upserts,
  Pydantic validation, testing strategy).
- [Git & GitHub basics](docs/git-and-github-basics.md) — the workflow used to
  build this repo.
