# Phase 5b — Dashboard Layout & Approval UX

> **Goal of this doc:** teach you *why* the report is laid out this way and
> — the harder part — *why* Power BI can't just "write back to the
> database" on a button click, and what the real, working options are.

> ⚠️ **Same honesty note as `docs/05-power-bi-data-model.md`:** the wireframes
> and DAX below are authored against the schema, not built and screenshotted
> in a real `.pbix` (Power BI Desktop can't run in this environment). The
> **approval mechanism**, however, is different — `src/eagent/api.py` is a
> real, running, tested FastAPI service (see `tests/test_api.py` and
> `tests/test_api_integration.py`), not just a description of one. Where a
> claim in this doc is backed by a passing test, it says so.

---

## 1. Page 1 — Operational Application CRM

### 1a. Wireframe

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  Eagent.io — Operational CRM                              [Page 1] [Page 2]  │
├──────────────────────────────────────────────────────────────────────────────┤
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ │
│  │ Total    │ │ Pending  │ │ Approval │ │ Sent     │ │ Response │ │ Avg    │ │
│  │ Postings │ │ Approvals│ │ Rate %   │ │ Outreach │ │ Rate %   │ │ ATS    │ │
│  │  1,204   │ │    37    │ │  61.2%   │ │   412    │ │  18.4%   │ │  76.3  │ │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘ └────────┘ │
├────────────┬───────────────────────────────────────────────────────────────┤
│  SLICERS   │  MASTER MATRIX                                                 │
│            │  ┌───────────┬────────────┬───────────┬───────┬────┬────┬────┐│
│ Tech Stack │  │ Company   │ Job        │ Recruiter  │ ATS   │ ✓? │ St │ Ac ││
│ [ ] python │  │           │            │ Email      │ Score │    │atus│tion││
│ [ ] sql    │  ├───────────┼────────────┼───────────┼───────┼────┼────┼────┤│
│ [ ] airflow│  │ Acme      │ Sr. Data   │ jane@...   │  92 ▓ │ ✓  │Sent│... ││
│ [ ] dbt    │  │ Analytics │ Engineer   │            │       │    │    │    ││
│ [ ] ...    │  ├───────────┼────────────┼───────────┼───────┼────┼────┼────┤│
│            │  │ Bright    │ Data       │ careers@.. │  74 ▓ │ ✗  │Draf│Appr││
│ Status     │  │ Retail    │ Engineer   │            │       │    │ted │/Rej││
│ ( ) All    │  ├───────────┼────────────┼───────────┼───────┼────┼────┼────┤│
│ ( ) Drafted│  │ ...       │ ...        │ ...        │  ...  │... │... │... ││
│ ( ) Approv.│  └───────────┴────────────┴───────────┴───────┴────┴────┴────┘│
│ ( ) Sent   │                                                                │
│ ( ) ...    │                                                                │
│            │                                                                │
│ Approval   │                                                                │
│ State      │                                                                │
│ ( ) All    │                                                                │
│ ( ) Pending│                                                                │
│ ( ) Approv.│                                                                │
│            │                                                                │
│ Score Range│                                                                │
│ [====|===] │                                                                │
│  0    100  │                                                                │
└────────────┴───────────────────────────────────────────────────────────────┘
```

### 1b. KPI Banner

Six Card visuals, full-width row, ~90px tall: `Total Postings Scraped`,
`Pending Approvals Count`, `Approval Rate %`, `Sent Outreach Count`,
`Response Rate %`, `Average ATS Score`. All six measures already exist in
`powerbi/eagent_measures.dax` (Phase 5).

**Design correction worth stating explicitly:** plain Power BI Card visuals
are **not clickable filters** — there's no native "click the Pending
Approvals number to filter the matrix to just those rows" behavior on a
Card. (Getting this wrong is a common mistake when sketching a BI layout
from a wireframe alone.) The banner is **purely informational**. The actual
filtering mechanism for the matrix is the **Status** and **Approval State**
slicers in the left rail (§1d) — clicking `Drafted` there does what a
"clickable KPI" wireframe might suggest clicking the Pending Approvals card
should do. If you want a literally clickable KPI later, that requires a
bookmark+button pattern or swapping the Card for a slicer-like visual — out
of scope here, but worth knowing it's a deliberate, more-work choice, not
a Card default.

### 1c. Master Matrix

**Visual type:** Matrix (not Table) — Matrix supports row grouping (e.g. by
Company) with expand/collapse, which a flat Table doesn't.

| Column | Source | Notes |
|---|---|---|
| Company | `dim_companies[name]` | Row-group level |
| Job | `dim_jobs[title]` | |
| Recruiter Email | `dim_recruiters[email]` | Blank if no recruiter enriched yet (Phase 2, `RecruiterEnricher` fallback still applies) |
| ATS Score | `fact_outreach[ats_score]` | Conditional formatting, see below |
| is_approved | `fact_outreach[is_approved]` | Conditional formatting (icon), see below |
| Status | `fact_outreach[outreach_status]` | Conditional formatting (color), see below |
| Actions | `Approve Action URL`, `Reject Action URL` (new calculated columns, `powerbi/eagent_measures.dax`) | Two Web-URL-categorized columns — see §3 |

**Conditional formatting rules:**

- **ATS Score** — Background color scale (Format → Conditional formatting →
  Background color, "Color scale"): red at 0, yellow at ~70, green at 100.
  This mirrors `ATS Tier` (Low/Medium/High) visually without needing the
  tier as a separate column here — a continuous gradient reads faster in a
  dense matrix than three discrete color blocks would.
- **is_approved** — Icons (Format → Conditional formatting → Icons): ✓ for
  `TRUE`, ✗ for `FALSE`. Rules: `is_approved = TRUE` → checkmark,
  `is_approved = FALSE` → X.
- **Status** — Background color, rules-based (not a scale — these are
  discrete categories, not a range): `Drafted` → gray, `Approved` → blue,
  `Sent` → purple, `Replied` → orange, `Interview` → green, `Rejected` → red.

**Cross-filtering / interactions:** the slicers (§1d) filter the matrix, and
the matrix filters nothing else on this page (there's nothing else on this
page to filter — the KPI cards are informational only, per §1b). Set the
matrix's own "Edit interactions" for the slicers to **Filter**, the default.

### 1d. Slicers

Left rail, ~200px wide, stacked vertically:

1. **Tech Stack** — a list slicer bound to the exploded `bridge_job_skills`
   query's `skill` column (see §2b — the same bridge table the Tech Stack
   Demand chart on Page 2 uses; one Power Query step, two consumers).
   Multi-select enabled.
2. **Status** — a list slicer on `fact_outreach[outreach_status]`. Single or
   multi-select.
3. **Approval State** — a list slicer on `fact_outreach[is_approved]`,
   relabeled via a calculated column if you want "Pending"/"Approved" text
   instead of `TRUE`/`FALSE`:
   ```dax
   Approval State Label =
   IF ( fact_outreach[is_approved], "Approved", "Pending" )
   ```
4. **Score Range** — a "Between" slicer (Format → slider style) bound to
   `fact_outreach[ats_score]`, range 0–100.

All four use the default **Single** cross-filter direction into the matrix
— consistent with the "dims filter fact" architecture from
`docs/05-power-bi-data-model.md` §1.

---

## 2. Page 2 — Market & Funnel Analytics

### 2a. Wireframe

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  Eagent.io — Market & Funnel Analytics                    [Page 1] [Page 2]  │
├───────────────────────────────────────┬──────────────────────────────────────┤
│  CONVERSION FUNNEL                     │  TECH STACK DEMAND                   │
│                                         │                                      │
│   Scraped    ████████████████  1,204   │  python   ████████████████  812      │
│   Relevant   ████████████       890    │  sql      ██████████████    734      │
│   Approved   ██████             545    │  airflow  █████████         501      │
│   Sent       █████              412    │  dbt      ███████           390      │
│   Replied    ██                  76    │  spark    █████             288      │
│   Interview  █                   14    │  ... (Top N, sorted desc)            │
│                                         │                                      │
├───────────────────────────────┬───────┴──────────────────────────────────────┤
│  ATS SCORE vs RESPONSE RATE    │  MISSING SKILLS RANKING                      │
│                                 │                                              │
│   Response  ●High               │  snowflake  ████████████  156               │
│   Rate %      ●Med              │  kubernetes ██████████    121               │
│            ●Low                 │  terraform  ████████       98               │
│         ATS Score (bucket) →    │  scala      █████          64               │
│   (bubble size = Sent count)    │  ... (Top N, sorted desc)                    │
└─────────────────────────────────┴──────────────────────────────────────────────┘
```

### 2b. Conversion Funnel

**Visual type:** Funnel.

- **Category:** `'Funnel Stages'[StageName]` (a small disconnected table —
  see `powerbi/eagent_measures.dax` for the exact rows to enter and the
  sort-order setup).
- **Values:** `[Funnel Value]` (the `SWITCH(SELECTEDVALUE(...))` measure in
  the same file).

**Why "Relevant" and not straight from "Scraped" to "Approved":** the task
asked for `Scraped → Relevant → Approved → Sent → Replied → Interview`.
"Relevant" isn't an existing status column — I defined it precisely rather
than leaving it vague: a scraped posting (`dim_jobs` row) becomes
**Relevant** the moment it's worth ATS-scoring, i.e. the moment a
`fact_outreach` row exists for it (Phase 3's `upsert_outreach_result`).
`Relevant Postings Count = COUNTROWS(fact_outreach)`. I deliberately did
**not** invent a score threshold (e.g. "ats_score >= 50") for this — that
would be a made-up cutoff the schema doesn't define anywhere else. Reading
it off the real pipeline stage (scraped vs. entered-the-ATS-pipeline) keeps
every funnel stage traceable to an actual table, not an arbitrary number.

### 2c. Tech Stack Demand

**Visual type:** Clustered bar chart. **Axis:** `skill`. **Values:** Count
of rows (or `DISTINCTCOUNT(job_id)` if you want to guard against any
accidental duplicate tag per posting). **Filter:** Top N, ~15, by count
descending.

**The array problem, and the fix — a direct callback to a Phase 1 trade-off:**
`dim_jobs.tech_stack` is a Postgres `TEXT[]` array (`sql/01_schema.sql`).
DAX can't `GROUP BY` inside an array cell — you can't build a bar chart
axis directly off `tech_stack` as stored. `docs/01-database-modeling-star-schema.md`
§7 flagged this exact trade-off back in Phase 1: *"if we later needed heavy
per-skill ranking/joining across the whole warehouse, the alternative is a
bridge table... we chose the array for simplicity."* This chart is that
"later." Rather than migrating the whole warehouse schema, build the bridge
as a **Power Query step** — the array only needs to be exploded for
reporting, not for the OLTP-shaped write path Phases 2–4 use.

New Power Query query, `bridge_job_skills`, referencing `dim_jobs`:

```m
// If the Postgres connector surfaces tech_stack as a Power Query List
// (common with the native "PostgreSQL database" connector):
let
    Source = dim_jobs,
    Kept = Table.SelectColumns(Source, {"job_id", "tech_stack"}),
    Exploded = Table.ExpandListColumn(Kept, "tech_stack"),
    Renamed = Table.RenameColumns(Exploded, {{"tech_stack", "skill"}}),
    Trimmed = Table.TransformColumns(Renamed, {{"skill", Text.Trim, type text}})
in
    Trimmed
```

```m
// Fallback, if it instead comes through as literal Postgres array text
// like "{python,sql,airflow}" (driver/connector-version dependent — verify
// which case you're in by inspecting one cell before picking a version):
let
    Source = dim_jobs,
    Kept = Table.SelectColumns(Source, {"job_id", "tech_stack"}),
    Stripped = Table.TransformColumns(Kept, {{"tech_stack", each Text.Trim(_, {"{", "}"}), type text}}),
    Split = Table.TransformColumns(Stripped, {{"tech_stack", each Text.Split(_, ","), type list}}),
    Exploded = Table.ExpandListColumn(Split, "tech_stack"),
    Renamed = Table.RenameColumns(Exploded, {{"tech_stack", "skill"}})
in
    Renamed
```

One row per `(job_id, skill)` pair. This is also what the Page 1 **Tech
Stack slicer** (§1d) is built on — one Power Query step, two consumers,
instead of duplicating the unpivot logic.

### 2d. ATS Score vs. Response Rate scatter plot

**A subtlety worth getting right:** a scatter plot needs one dot per
*group*, not one dot per outreach row. "Response rate" isn't a meaningful
number for a single outreach — it either got a reply or it didn't (binary),
not a rate. A rate needs a denominator bigger than one. So this has to plot
**buckets** of outreach, not individual rows.

**Visual type:** Scatter chart (bubble variant).

- **X axis:** `[Average ATS Score]`
- **Y axis:** `[Response Rate %]`
- **Legend / "Details":** `fact_outreach[ATS Tier]` — reuses the existing
  calculated column from Phase 5 (3 buckets: Low/Medium/High — simplest
  version, no new column needed). For finer resolution, swap in the new
  `ATS Score Bucket` column instead (10-point buckets, ~7–8 dots) —
  DAX for both is in `powerbi/eagent_measures.dax`.
- **Size:** `[Sent Outreach Count]` — bubble size shows the sample size
  behind each dot, so a bucket with 3 outreach doesn't visually compete
  equally with one backed by 200. Skipping this is the single most common
  mistake in this kind of chart — a tiny-sample bucket can show a
  misleadingly extreme rate.

### 2e. Missing Skills ranking

**Visual type:** Clustered bar chart, same shape as Tech Stack Demand
(§2c) and the **same array-unpivoting problem**, this time on
`fact_outreach.missing_skills` instead of `dim_jobs.tech_stack`. New Power
Query query, `bridge_outreach_missing_skills`, same M pattern as §2c but
sourced from `fact_outreach` and keyed by `outreach_id` instead of `job_id`.

**Axis:** `missing_skill`. **Values:** Count of rows. **Filter:** Top N,
~15, descending. This is the single most actionable visual on the whole
dashboard for the project's actual purpose (per `MEMORY.md`: becoming a
Data Engineer) — it's a direct, data-backed answer to "what should I
actually go learn next," derived from real job descriptions instead of a
guess.

---

## 3. Approval mechanism: how a user actually sets `is_approved = TRUE`

The task named three candidate mechanisms. Comparing them honestly first,
then explaining the one that's actually built and tested here:

| Mechanism | How it'd work | Verdict |
|---|---|---|
| **Power Apps visual (embedded)** | A canvas app embedded in the report via the "Power Apps" visual; a button click writes to a data source (typically Dataverse, or Postgres via a custom/premium connector) | Most "native" feel (no page navigation) but needs **Power Apps/Premium licensing** beyond a standard Power BI Pro seat, and a Postgres connector that isn't in the free tier. Real option for an org that already has that licensing; not buildable or demoable in this portfolio project without it. |
| **Linked Google Sheet / Excel** | Export/link the matrix to a Sheet; a reviewer ticks a checkbox there; a scheduled sync job pulls approvals back into Postgres | Lowest engineering effort, but breaks the star schema's single-source-of-truth property (`docs/01` §3–4): now there are two systems that can disagree, and a sync-lag window where they do. Also can't reuse any of Phase 4's `eagent.workflow` state-machine/locking logic — approvals would bypass `SELECT ... FOR UPDATE` entirely, reopening the concurrency risk Phase 4 specifically closed (`docs/04` §3). Weakest option; included here because it was asked about, not because it's recommended. |
| **Local webhook** (chosen) | A small local HTTP service the Actions column links to, which calls the existing `eagent.workflow` functions directly | No extra licensing, reuses every guarantee Phase 4 already built and tested (state machine, row locking, the `is_approved` defense-in-depth check), and is the only one of the three I could actually build and verify here rather than just describe. |

**Recommendation: the local webhook — and it's not hypothetical.**
`src/eagent/api.py` is a real FastAPI service, `POST /outreach/{id}/approve`
and `POST /outreach/{id}/reject`, each just calling
`eagent.workflow.approve_outreach`/`reject_outreach`. **Zero new business
logic** — if you trust Phase 4's state machine (and its tests), you trust
this. `tests/test_api.py` (9 tests, all passing, no DB required) proves the
HTTP-to-exception translation is correct; `tests/test_api_integration.py`
(DB-gated) proves a `POST` through the real API actually updates a real
Postgres row.

### Why there's a GET "confirmation page" *and* a POST endpoint

A plain Power BI Table/Matrix hyperlink column can only navigate via
**GET** — it can't send a POST with a body. But approving an outreach is a
real side effect, and HTTP GET is supposed to be safe (no side effects):
browsers prefetch links, and Power BI's own hyperlink rendering is just
navigation. If the Actions column linked straight to a GET endpoint that
approved on visit, an accidental hover-prefetch or a stray click could
silently approve outreach nobody meant to send.

The fix: the Actions column's `Approve Action URL` points at
`GET /outreach/{id}/approve-page` — a tiny HTML page with one button. The
**button's click** fires the real mutation via `fetch(..., {method: "POST"})`
to `POST /outreach/{id}/approve`. `tests/test_api.py::test_get_on_approve_page_never_calls_the_mutating_function`
proves the GET route touches nothing. One extra click; the mutating
endpoint is never reachable by a bare navigation.

### Running it

```bash
pip install -e ".[dev]"          # installs fastapi + uvicorn (added this phase)
uvicorn eagent.api:app --reload --port 8000
```

Then in Power BI, the Actions column's `Approve Action URL` /
`Reject Action URL` (`powerbi/eagent_measures.dax`) point at
`http://localhost:8000/outreach/{id}/approve-page` /
`.../reject-page` — set their Data Category to **Web URL**
(Column tools → Data category) so the matrix renders them as clickable.

**A native-Power-BI limitation worth naming:** a Web-URL-categorized column
in a Table/Matrix displays **the column's own value** as the clickable
text — there's no built-in way to show friendly text ("Approve") with a
different underlying link the way an HTML `<a href>` can. Two honest paths:

1. **Accept it** (what the DAX above does): the visible link text is the
   literal URL. Works everywhere, zero extra setup, fine for an internal
   single-operator tool.
2. **Style it properly:** the certified **"HTML Content" custom visual**
   (AppSource) can render real `<a href="...">✅ Approve</a>` markup from a
   text measure — the standard way BI teams actually solve this. Flagged
   here as the "if you want it polished" path rather than fully specified,
   since I can't verify a custom visual's exact wiring without Power BI
   Desktop to test it in (see this doc's honesty note at the top) — I'd
   rather point you at the right tool than hand you DAX for it I haven't
   verified.

### Scope: this is a local tool, not a public API

`src/eagent/api.py` has **no authentication**. That's a deliberate, stated
scope boundary for a single-operator local tool (§ design), not an
oversight — before ever exposing this beyond `localhost` (a shared server,
a real Power BI Service deployment via an on-prem gateway), add an API key
header check, or keep it behind a VPN/the gateway's own network boundary.
Naming the boundary is the point; building auth nobody asked for yet is the
kind of speculative feature this project's own conventions (see
`CLAUDE.md`) explicitly avoid.

---

## 4. Interview checklist ✅

- [ ] Why can't a Card visual be a clickable filter, and what's the actual
      filtering mechanism on Page 1 instead?
- [ ] What does the Matrix's ATS Score conditional formatting do that a
      separate `ATS Tier` column display wouldn't, in a dense grid?
- [ ] Why does the Funnel visual need a disconnected `Funnel Stages` table
      and a `SWITCH(SELECTEDVALUE(...))` measure, instead of just plotting
      6 different measures directly?
- [ ] How was "Relevant" defined for the funnel, and why not a score
      threshold?
- [ ] Why can't `tech_stack`/`missing_skills` be used directly as a chart
      axis, and what Power Query step fixes it? Which Phase 1 doc predicted
      this exact problem?
- [ ] Why does the ATS-Score-vs-Response-Rate chart need bucketing instead
      of one dot per outreach row, and why does bubble size matter here?
- [ ] Why does the Actions column need a GET confirmation page *and* a POST
      endpoint, instead of the hyperlink hitting the mutating endpoint
      directly?
- [ ] Of the three approval mechanisms compared, which one reuses Phase 4's
      concurrency-safety guarantees, and which one would silently bypass
      them?

---

### Files in this phase
- `docs/06-dashboard-layout-and-approval-ux.md` — this doc
- `powerbi/eagent_measures.dax` — extended with funnel-stage count measures,
  the `Funnel Value` SWITCH measure, `ATS Score Bucket`, and the Actions
  column URL calculated columns
- `src/eagent/api.py` — the real, tested webhook backend
- `tests/test_api.py` — unit tests (mocked `eagent.workflow`, no DB)
- `tests/test_api_integration.py` — DB-gated end-to-end test (HTTP → FastAPI → Postgres)

**Still open:** the `dim_jobs → dim_dates` gap from `docs/05` §1 — the one
schema gap this project has surfaced that hasn't been closed yet.
