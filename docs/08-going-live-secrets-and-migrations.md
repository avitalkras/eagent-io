# Phase 7 — Going Live: Secrets, Migrations, and the Daily Harvest

> **Goal of this doc:** the exact operational checklist for turning
> `.github/workflows/daily_harvest.yml` (PR #9) from a file sitting in the
> repo into an actually-running scheduled pipeline — every secret, the
> exact commands to run once against the new database, and one operational
> gotcha "going live" introduces that a local one-off script never had to
> worry about.

---

## 1. GitHub Secrets — exact names, exact purpose

Settings → Secrets and variables → Actions → New repository secret, for each:

| Secret name | Required? | What it's for |
|---|---|---|
| `PROD_DATABASE_URL` | **Required** | SQLAlchemy connection string to the Neon Postgres instance. See §3 for the exact format Neon needs. |
| `GROQ_API_KEY` | **Required** (given `LLM_PROVIDER: "groq"` in the workflow) | ATS scoring calls (`eagent.ats.llm.GroqProvider`). Get one at console.groq.com. |
| `HUNTER_API_KEY` | Optional | Recruiter enrichment. If unset, `RecruiterEnricher` runs in fallback-only mode (generic `careers@domain` guesses) — the pipeline still runs, just without real recruiter lookups. See `src/eagent/enrichment.py`. |
| `TELEGRAM_BOT_TOKEN` | Optional, but needed for alerting to do anything | See §2 for how to get one. If unset, `scripts/notify.py` logs a warning and exits 0 — the harvest run itself never fails because alerting isn't configured. |
| `TELEGRAM_CHAT_ID` | Optional, paired with the token above | Same as above. |

**If you switch providers later** (`GEMINI_API_KEY` instead of Groq): add the secret, then change the single `LLM_PROVIDER: "groq"` line near the top of `daily_harvest.yml` to `"gemini"` — `scripts/score_unscored_jobs.py`'s `build_provider()` reads that one env var and fails fast with a clear error if the matching key is missing, so a typo here is caught before any DB/network work starts, not partway through a batch.

## 2. Getting a Telegram bot token + chat ID

1. Message **@BotFather** on Telegram, send `/newbot`, follow the prompts. It gives you a token like `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`. That's `TELEGRAM_BOT_TOKEN`.
2. Send your new bot **any** message first (Telegram bots can't message you until you've messaged them).
3. Visit `https://api.telegram.org/bot<your-token>/getUpdates` in a browser — the JSON response contains `"chat":{"id": ...}`. That number is `TELEGRAM_CHAT_ID`.

## 3. Provisioning Neon and applying migrations

1. Create a project at neon.tech (free tier). Copy the connection string it gives you — Neon requires SSL, so make sure `sslmode=require` is present:
   ```
   postgresql://<user>:<password>@<endpoint>.neon.tech/<dbname>?sslmode=require
   ```
2. That's the value for the `PROD_DATABASE_URL` **secret** — as a SQLAlchemy URL (note the `+psycopg2`):
   ```
   postgresql+psycopg2://<user>:<password>@<endpoint>.neon.tech/<dbname>?sslmode=require
   ```
3. Apply all 4 migrations, **in order**, using plain `psql` against the same connection string (drop the `+psycopg2` — that's a SQLAlchemy-only annotation, not part of the real DSN):
   ```bash
   NEON_URL="postgresql://<user>:<password>@<endpoint>.neon.tech/<dbname>?sslmode=require"

   psql "$NEON_URL" -f sql/01_schema.sql
   psql "$NEON_URL" -f sql/02_populate_dim_dates.sql
   psql "$NEON_URL" -f sql/03_ats_outreach_columns.sql
   psql "$NEON_URL" -f sql/04_interviewed_at_column.sql
   ```
   Every one of these is written to be safe to re-run (`IF NOT EXISTS` guards throughout, per Phase 1's design) — if you're ever unsure whether a migration already applied, running it again is harmless.
4. Sanity check:
   ```bash
   psql "$NEON_URL" -c "\dt"
   psql "$NEON_URL" -c "SELECT COUNT(*) FROM dim_dates;"
   ```

## 4. A real operational gotcha "going live" introduces

`sql/02_populate_dim_dates.sql` populates `dim_dates` for **2 years forward from whenever it's run** (its own header comment says this plainly). Every prior phase ran this once, locally, for a one-off demo — the 2-year window was never going to matter.

A daily cron job is different: it runs indefinitely. Every `fact_outreach` row `score_unscored_jobs.py` writes needs a `date_id` that exists in `dim_dates` (a real `FOREIGN KEY`, not a soft reference — see `sql/01_schema.sql`), and `today_date_id()` computes "today" from whatever day it actually is when the workflow runs. **Two years from whenever you first run step 3 above, `dim_dates` runs out, and the daily workflow starts failing on that foreign key** — a real, if distant, expiration date this phase's design didn't have to account for before. Put a reminder somewhere (a calendar note, a GitHub issue with a due date) to re-run `sql/02_populate_dim_dates.sql` again before that window closes — it's additive and safe to re-run, so there's no migration complexity here, just something that needs to happen again eventually and easily won't if nothing reminds you.

---

## 5. What running `daily_harvest.yml` actually does, step by step

See the workflow file's own header comment for the full reasoning (why every step after setup runs with `if: always()`, why this is a separate file from `ci.yml`). Short version:

1. Scrape RemoteOK, idempotently load new postings (`scripts/run_ingestion.py`) — safe to re-run, `ON CONFLICT` throughout.
2. Score every `dim_jobs` row with no `fact_outreach` row yet (`scripts/score_unscored_jobs.py`, PR #10) — one bad job never stops the batch (see that script's own docstring for exactly why).
3. Upload whatever PDFs got generated as a GitHub Actions build artifact — a **stopgap**, not the real answer. The runner's disk is destroyed the moment the job ends; without this step the PDFs would simply vanish. PR #11 (real object storage — Supabase Storage or R2, per your decision) replaces this with a real, persistent URL written into `resume_pdf_path` instead of a runner-local path nobody can ever open again.
4. Notify on today's matches above 85% via Telegram (`scripts/notify.py`) — runs even if step 2 had partial failures, so a few bad jobs in the batch don't suppress hearing about the good ones.

---

### Files in this phase
- `scripts/score_unscored_jobs.py` — batch ATS scoring with per-row error isolation (PR #10)
- `scripts/notify.py` — Telegram high-score digest
- `.github/workflows/daily_harvest.yml` — the scheduled workflow (PR #9)
- `docs/08-going-live-secrets-and-migrations.md` — this doc

**Still open:** PR #11 (real PDF object storage), the `dim_recruiters` persistence gap noted in `score_unscored_jobs.py`'s own docstring, and everything else from the original roadmap's PRs #12–15.
