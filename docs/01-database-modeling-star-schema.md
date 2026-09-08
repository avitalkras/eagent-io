# Phase 1 — Database Modeling & the Star Schema

> **Goal of this doc:** teach you *why* we modeled the database the way we did, not
> just *what* the SQL says. If you can explain everything here in an interview,
> you understand dimensional modeling at a junior/mid Data Engineer level.

---

## 1. The big picture: why a "warehouse" schema is different

There are two very different jobs a database can do:

| | **OLTP** (transactional) | **OLAP** (analytical / warehouse) |
|---|---|---|
| Used by | The live app | Dashboards, reports, ML |
| Typical query | "Insert this one order" | "Sum outreach by month by industry" |
| Optimized for | Many tiny reads/writes | Few huge aggregations |
| Modeling style | **Normalized** (3NF) — no duplication | **Dimensional** (star schema) — some duplication on purpose |

Eagent.io's database is **OLAP**. It feeds **Power BI**. So we model it as a
**star schema**, the design pioneered by Ralph Kimball. This is the single most
important modeling pattern in analytics engineering.

---

## 2. What is a Star Schema?

A star schema has exactly two kinds of tables:

- **Fact table** (the center of the star) — records **events/measurements**.
  Narrow columns, but grows forever. Ours is `fact_outreach`: one row per
  outreach attempt.
- **Dimension tables** (the points of the star) — records **descriptive context**
  you filter and group by. Ours are `dim_companies`, `dim_jobs`,
  `dim_recruiters`, `dim_dates`.

```
                    ┌──────────────────┐
                    │   dim_dates      │
                    └────────▲─────────┘
                             │ date_id
         ┌──────────────┐    │    ┌────────────────┐
         │  dim_jobs    ◄────┼────►  dim_recruiters │
         └──────▲───────┘  FACT    └────────▲───────┘
                │       ┌──────────────┐    │
        company_id      │ fact_outreach│  company_id
                │       │  (events)    │    │
         ┌──────┴───────┴──────────────┴────┴───────┐
         │             dim_companies                │
         └──────────────────────────────────────────┘
```

Reading it: *"Each outreach event points to the job it was for, the recruiter it
went to, and the date it was drafted. Jobs and recruiters both belong to a
company."*

### Why not just one giant table, or fully-normalized tables?

- **One giant flat table** → massive duplication, impossible to keep consistent,
  slow to update a company name in a million rows.
- **Fully normalized (3NF like an app DB)** → correct, but analysts have to write
  10-table joins for every report, and Power BI relationships get messy.
- **Star schema** → the sweet spot. Simple joins (fact → dimension), fast
  aggregation, and Power BI models it beautifully (one fact, dimensions fan out).

---

## 3. Surrogate keys vs. natural keys

Every dimension's primary key is a **surrogate key**: a meaningless
auto-generated integer (`company_id`, `job_id`, …) that *the warehouse owns*.

```sql
company_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY
```

**Why not use the natural key** (e.g. the company name, or the job board's id)?
- Natural keys change (companies rebrand, URLs move). Surrogate keys never do.
- Natural keys may not be unique or may be huge strings (slow joins).
- Surrogate integers make fact-table joins tiny and fast.

> `GENERATED ALWAYS AS IDENTITY` is the modern SQL-standard replacement for the
> old `SERIAL`. Prefer it in new Postgres code.

We still keep the **natural key** around (e.g. `external_source` + `external_id`
on jobs) — but only as a *unique constraint*, not the primary key. That gives us
the best of both worlds: fast integer joins **and** idempotency (next section).

---

## 4. Idempotency — the concept that makes pipelines reliable

**Idempotent** = running the same operation twice has the same effect as running
it once. This is THE property that separates a toy scraper from a production
pipeline. Scrapers crash, get retried, run on a schedule — you *will* re-process
the same job posting. Without idempotency you get duplicates.

We enforce it with a **unique constraint on the natural key**:

```sql
CONSTRAINT uq_jobs_source_external UNIQUE (external_source, external_id)
```

Now the loader can safely "upsert":

```sql
INSERT INTO dim_jobs (company_id, title, external_source, external_id, job_url)
VALUES (:company_id, :title, :source, :ext_id, :url)
ON CONFLICT (external_source, external_id)
DO UPDATE SET title = EXCLUDED.title,       -- refresh if it already exists
              job_url = EXCLUDED.job_url;
```

Run it 1,000 times → still exactly one row per posting. Same idea protects
`dim_dates` (`ON CONFLICT (date_id) DO NOTHING`) and `dim_recruiters`.

---

## 5. The date dimension (`dim_dates`)

A dedicated calendar table looks redundant ("why not just use the timestamp?")
but it's a core analytics pattern:

- Pre-computes `quarter`, `year`, `is_weekend`, `month` **once**, so every report
  filters consistently and fast.
- Lets Power BI mark it as the official **Date Table**, enabling
  *time-intelligence* (Year-over-Year, Month-to-Date, running totals). Those DAX
  functions literally require a proper date dimension.

### The "smart integer" key

```
2026-09-08  →  date_id = 20260908
```

Storing the date as a `YYYYMMDD` integer (Kimball's recommendation) is compact,
sorts correctly, is human-readable in a query, and makes a clean Power BI join.
The fact table stores this same integer in `date_id`.

We populate it with `generate_series()` — Postgres generating one row per day for
2 years — see `sql/02_populate_dim_dates.sql`.

---

## 6. Facts, measures, and grain

The **grain** of a fact table is the precise meaning of one row. Defining it
first is the #1 rule of dimensional modeling.

> **`fact_outreach` grain:** *one row = one outreach attempt for one job to one
> recruiter.*

A fact table holds two things:
- **Foreign keys** to dimensions (`job_id`, `recruiter_id`, `date_id`).
- **Measures** — the numbers you aggregate: `ats_score`, plus workflow state
  (`is_approved`, `outreach_status`) and lifecycle timestamps.

The lifecycle timestamps (`drafted_at`, `sent_at`, `replied_at`) let us compute
**funnel/velocity metrics** later, e.g. *average days from Sent → Replied* or a
*Drafted → Approved → Sent → Interview* conversion funnel.

---

## 7. Data types — the choices we made and why

| Choice | Where | Why |
|---|---|---|
| `TIMESTAMPTZ` (never `TIMESTAMP`) | all timestamps | timezone-aware, stored as UTC. Naive timestamps cause brutal bugs. |
| `CITEXT` | `email`, `domain` | case-insensitive compare without `LOWER()` everywhere |
| `NUMERIC(5,2)` for `ats_score` | fact | exact decimals; `CHECK` bounds it to 0–100 |
| `NUMERIC(4,3)` for `confidence_score` | recruiters | exact 0.000–1.000 |
| `TEXT[]` for `tech_stack` / `missing_skills` | jobs / fact | simple, native array; GIN-indexed for `@>` searches |
| `ENUM` for `outreach_status` | fact | DB rejects invalid statuses at write time |
| `BIGINT` surrogate keys | all dims/fact | never run out of ids; fast joins |

> **Trade-off noted:** `tech_stack TEXT[]` is the pragmatic choice for a single
> model. If we later needed heavy per-skill ranking/joining across the whole
> warehouse, the "proper" alternative is a **bridge table**
> (`job_id, skill_id`) plus a `dim_skills`. We consciously chose the array for
> simplicity — knowing the alternative is what matters in an interview.

---

## 8. Constraints — pushing data quality into the database

The database is your **last line of defense** for data quality. Bad data caught
at write time never pollutes a dashboard. We use:

- **`NOT NULL`** — required fields can't be empty.
- **`UNIQUE`** — no duplicates (idempotency keys, domains, emails-per-company).
- **`FOREIGN KEY ... ON DELETE RESTRICT`** — you can't delete a company that
  still has jobs/recruiters/outreach pointing at it. `RESTRICT` is the safe
  default for a warehouse (vs. `CASCADE`, which would silently delete children).
- **`CHECK`** — value ranges (`ats_score` 0–100, `confidence_score` 0–1).
- **`ENUM`** — closed set of allowed statuses.

---

## 9. Indexes — making reads fast without killing writes

An index is a lookup structure (usually a B-tree) that finds rows without
scanning the whole table. Our rules of thumb:

1. **Index every foreign key** — joins and filters use them (`job_id`,
   `recruiter_id`, `date_id`, `company_id`).
2. **Index columns you filter on constantly** — dashboard slicers:
   `outreach_status`, `is_approved`, `posted_at`, `date`.
3. **Use the right index type** — arrays need a **GIN** index to search *inside*
   them (`WHERE tech_stack @> '{python}'`); a B-tree can't do that.
4. **Don't over-index** — every index makes `INSERT`/`UPDATE` slower and uses
   disk. Index for the queries you actually run.

---

## 10. Why the whole script is one transaction

`01_schema.sql` is wrapped in `BEGIN; … COMMIT;`. If any statement fails, the
entire thing **rolls back** — you never end up with half a schema. This is the
foundation of **atomic, repeatable migrations**, and the `IF NOT EXISTS` guards
make the script **safe to re-run**.

---

## 11. Interview checklist ✅

You should now be able to answer:

- [ ] What's the difference between OLTP and OLAP modeling?
- [ ] Fact vs. dimension — how do you tell them apart?
- [ ] What is the *grain* of a fact table and why define it first?
- [ ] Surrogate vs. natural key — when/why each?
- [ ] What does *idempotent* mean and how does a unique key + `ON CONFLICT`
      give it to you?
- [ ] Why a dedicated date dimension instead of raw timestamps?
- [ ] `TIMESTAMPTZ` vs `TIMESTAMP` — why does it matter?
- [ ] When do you index, and why not index everything?
- [ ] `ON DELETE RESTRICT` vs `CASCADE`?

---

### Files in this phase
- `sql/01_schema.sql` — the full DDL (heavily commented).
- `sql/02_populate_dim_dates.sql` — fills the calendar for 2 years.

**Next up → Phase 2:** the Python scraper that loads `dim_jobs` idempotently.
