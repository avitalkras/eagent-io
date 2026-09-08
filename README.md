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
Eagent.io/
├── README.md                  ← you are here
├── .gitignore
├── docs/                      ← learning notes (the "why" behind everything)
│   ├── 00-project-roadmap.md
│   ├── 01-database-modeling-star-schema.md
│   └── git-and-github-basics.md
└── sql/                       ← Phase 1 deliverables
    ├── 01_schema.sql          ← star schema DDL
    └── 02_populate_dim_dates.sql
```

---

## 🗺️ Roadmap

| Phase | Topic | Status |
|---|---|---|
| **1** | Database Modeling & Star Schema (DDL) | ✅ Done |
| 2 | Python scraper → idempotent load into `dim_jobs` | ⬜ Planned |
| 3 | Recruiter enrichment | ⬜ Planned |
| 4 | LLM ATS scoring + resume tailoring | ⬜ Planned |
| 5 | Power BI dashboard | ⬜ Planned |

See [`docs/00-project-roadmap.md`](docs/00-project-roadmap.md) for details.

---

## 🚀 Running Phase 1 locally

You need PostgreSQL. Then:

```bash
# 1. create the database
createdb eagent

# 2. build the schema (tables, constraints, indexes)
psql -d eagent -f sql/01_schema.sql

# 3. populate the calendar dimension for the next 2 years
psql -d eagent -f sql/02_populate_dim_dates.sql
```

Verify:

```bash
psql -d eagent -c "\dt"                       # list tables
psql -d eagent -c "SELECT COUNT(*) FROM dim_dates;"
```

---

## 📚 Learning docs

- [Star schema & database modeling](docs/01-database-modeling-star-schema.md) —
  the core of Phase 1.
- [Git & GitHub basics](docs/git-and-github-basics.md) — the workflow used to
  build this repo.
