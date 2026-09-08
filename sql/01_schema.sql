-- =============================================================================
-- Eagent.io — Phase 1: Star Schema DDL (PostgreSQL)
-- =============================================================================
-- Purpose : Production-grade dimensional model for analytics + Power BI.
-- Pattern : Kimball Star Schema (facts in the center, dimensions around it).
-- Author  : Avital Krasovitski
--
-- HOW TO RUN:
--   psql -d eagent -f sql/01_schema.sql
--
-- READ ME FIRST:
--   Every design decision below has an inline comment explaining WHY.
--   See docs/01-database-modeling-star-schema.md for the full teaching notes.
-- =============================================================================

-- Wrap everything in a transaction. If ANY statement fails, the WHOLE script
-- rolls back and the database is left untouched. This is "atomic migration" —
-- you never end up with a half-created schema. (BEST PRACTICE)
BEGIN;

-- -----------------------------------------------------------------------------
-- 0. EXTENSIONS
-- -----------------------------------------------------------------------------
-- citext = "case-insensitive text". We use it for emails/domains so that
-- 'Recruiter@Google.com' and 'recruiter@google.com' are treated as equal
-- WITHOUT us having to LOWER() everything by hand. Cleaner + fewer bugs.
CREATE EXTENSION IF NOT EXISTS citext;


-- -----------------------------------------------------------------------------
-- 1. ENUM TYPE — outreach lifecycle
-- -----------------------------------------------------------------------------
-- WHY an ENUM instead of a plain TEXT column?
--   * The database itself REJECTS any status that isn't in this list. You can
--     never insert a typo like 'Aproved'. This is data integrity at the source.
--   * It documents the allowed pipeline stages in one place.
-- TRADE-OFF: adding a new value later needs ALTER TYPE ... ADD VALUE. That's
--   fine for a stable, well-known lifecycle like this one.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'outreach_status_enum') THEN
        CREATE TYPE outreach_status_enum AS ENUM (
            'Drafted',    -- resume/message generated, not yet human-reviewed
            'Approved',   -- passed the human-in-the-loop approval gate
            'Sent',       -- outreach delivered to the recruiter
            'Replied',    -- recruiter responded
            'Interview',  -- advanced to an interview
            'Rejected'    -- closed / declined
        );
    END IF;
END$$;


-- =============================================================================
-- 2. DIMENSION TABLES
-- =============================================================================
-- A DIMENSION describes the "who / what / where / when" — the descriptive
-- context you slice and filter reports by. Dimensions are relatively wide
-- (many descriptive columns) but shallow (fewer rows than facts).
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 2.1 dim_companies
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dim_companies (
    -- SURROGATE KEY: a meaningless auto-generated integer that the warehouse
    -- owns. We join on this, NOT on the company name, because names change and
    -- aren't unique. GENERATED ... AS IDENTITY is the modern SQL-standard way
    -- (preferred over the older SERIAL). BIGINT = room to grow past 2.1B rows.
    company_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    name         TEXT    NOT NULL,                 -- display name, e.g. "Google"
    domain       CITEXT,                           -- e.g. "google.com" (case-insensitive)
    industry     TEXT,                             -- e.g. "Technology"
    location     TEXT,                             -- HQ or primary location

    -- AUDIT TIMESTAMPS: every table gets these. TIMESTAMPTZ stores the instant
    -- in UTC and is timezone-aware — never use naive TIMESTAMP in a warehouse.
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- A company's domain is its natural unique identity. Enforcing uniqueness
    -- here prevents duplicate company rows during enrichment/scraping.
    CONSTRAINT uq_companies_domain UNIQUE (domain)
);


-- -----------------------------------------------------------------------------
-- 2.2 dim_jobs
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dim_jobs (
    job_id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- FOREIGN KEY to the company that posted the job. ON DELETE RESTRICT means
    -- "you cannot delete a company while jobs still point to it" — protects
    -- referential integrity. (RESTRICT is the safe default for a warehouse.)
    company_id       BIGINT NOT NULL
                     REFERENCES dim_companies (company_id) ON DELETE RESTRICT,

    title            TEXT   NOT NULL,              -- e.g. "Senior Data Engineer"

    -- IDEMPOTENCY KEYS: where the row came from.
    --   external_source = which board, e.g. 'linkedin' / 'greenhouse'
    --   external_id     = that board's own id for the posting
    external_source  TEXT   NOT NULL,
    external_id      TEXT   NOT NULL,

    job_url          TEXT,
    location         TEXT,

    -- tech_stack as a TEXT[] (native Postgres array). We store extracted skills
    -- like {python,sql,airflow}. Arrays keep it simple for a single-source model;
    -- a normalized bridge table would be the alternative if we needed to join/
    -- rank individual skills heavily (documented as a trade-off in the notes).
    tech_stack       TEXT[] DEFAULT '{}',

    raw_description  TEXT,                          -- full JD text (for LLM/ATS scoring)

    posted_at        TIMESTAMPTZ,                   -- when the board posted it
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- =====================================================================
    -- IDEMPOTENCY CONSTRAINT (required): the same posting from the same source
    -- can only exist ONCE. This is what makes our scraper safe to re-run — an
    -- "upsert" (INSERT ... ON CONFLICT) will update instead of duplicating.
    -- This single line is the backbone of a reliable, re-runnable pipeline.
    -- =====================================================================
    CONSTRAINT uq_jobs_source_external UNIQUE (external_source, external_id)
);


-- -----------------------------------------------------------------------------
-- 2.3 dim_recruiters
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dim_recruiters (
    recruiter_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    company_id        BIGINT NOT NULL
                      REFERENCES dim_companies (company_id) ON DELETE RESTRICT,

    full_name         TEXT,
    email             CITEXT,                       -- case-insensitive
    linkedin_url      TEXT,
    enrichment_source TEXT,                          -- e.g. 'apollo', 'hunter'

    -- confidence_score: how sure the enrichment tool is (0.00–1.00). We CHECK
    -- the range so bad data (e.g. 5.0 or -1) is rejected at write time.
    confidence_score  NUMERIC(4,3)
                      CHECK (confidence_score >= 0 AND confidence_score <= 1),

    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Same email at the same company = same recruiter. Prevents duplicates.
    -- (NULLs are allowed and treated as distinct, which is what we want.)
    CONSTRAINT uq_recruiters_company_email UNIQUE (company_id, email)
);


-- -----------------------------------------------------------------------------
-- 2.4 dim_dates  (the classic "date dimension")
-- -----------------------------------------------------------------------------
-- WHY have a date table at all instead of just using timestamps?
--   In Power BI / analytics you constantly slice by "quarter", "is weekend",
--   "month name", "year". Pre-computing those once here means:
--     * fast, consistent filtering everywhere,
--     * a single source of truth for the calendar,
--     * Power BI can mark it as the official Date Table for time-intelligence
--       (YoY, MoM, running totals) to work correctly.
CREATE TABLE IF NOT EXISTS dim_dates (
    -- SMART INTEGER KEY: store the date as YYYYMMDD (e.g. 20260908). This is a
    -- Kimball best practice — compact, human-readable, and great for Power BI
    -- relationships. The facts store this same integer.
    date_id     INTEGER PRIMARY KEY,

    date        DATE    NOT NULL UNIQUE,
    day         SMALLINT NOT NULL,                  -- day of month (1–31)
    month       SMALLINT NOT NULL,                  -- 1–12
    quarter     SMALLINT NOT NULL,                  -- 1–4
    year        SMALLINT NOT NULL,
    is_weekend  BOOLEAN  NOT NULL
);


-- =============================================================================
-- 3. FACT TABLE
-- =============================================================================
-- A FACT records an EVENT/measurement — here, one outreach attempt. Fact tables
-- are narrow (mostly FKs + numeric measures) but DEEP (they grow forever). All
-- the descriptive context lives in the dimensions we join to.
-- =============================================================================

CREATE TABLE IF NOT EXISTS fact_outreach (
    outreach_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- FOREIGN KEYS to the three dimensions — the "points of the star".
    job_id          BIGINT  NOT NULL REFERENCES dim_jobs (job_id)         ON DELETE RESTRICT,
    recruiter_id    BIGINT           REFERENCES dim_recruiters (recruiter_id) ON DELETE RESTRICT,
    date_id         INTEGER NOT NULL REFERENCES dim_dates (date_id),       -- the "drafted" day

    -- MEASURES (the numbers you aggregate/average in the dashboard):
    ats_score       NUMERIC(5,2)                    -- 0.00–100.00 resume/JD match
                    CHECK (ats_score >= 0 AND ats_score <= 100),
    missing_skills  TEXT[] DEFAULT '{}',            -- skills the resume lacks vs JD

    -- WORKFLOW STATE:
    is_approved     BOOLEAN NOT NULL DEFAULT FALSE, -- human-in-the-loop gate
    outreach_status outreach_status_enum NOT NULL DEFAULT 'Drafted',

    -- LIFECYCLE TIMESTAMPS: when each stage happened. Nullable because a row
    -- may not have reached that stage yet. (Useful for funnel/velocity metrics:
    -- e.g. avg days from Sent -> Replied.)
    drafted_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at         TIMESTAMPTZ,
    replied_at      TIMESTAMPTZ
);


-- =============================================================================
-- 4. INDEXES
-- =============================================================================
-- WHY indexes? They are lookup structures that let the DB find rows WITHOUT
-- scanning the whole table. Rule of thumb for a warehouse:
--   * index every FOREIGN KEY (joins use them),
--   * index columns you FILTER on a lot (WHERE / Power BI slicers),
--   * don't over-index write-heavy tables (each index slows down inserts).
-- =============================================================================

-- Foreign-key indexes on the fact table (make star-schema JOINs fast):
CREATE INDEX IF NOT EXISTS ix_fact_outreach_job_id       ON fact_outreach (job_id);
CREATE INDEX IF NOT EXISTS ix_fact_outreach_recruiter_id ON fact_outreach (recruiter_id);
CREATE INDEX IF NOT EXISTS ix_fact_outreach_date_id      ON fact_outreach (date_id);

-- Status filters (dashboards constantly filter the pipeline by stage/approval):
CREATE INDEX IF NOT EXISTS ix_fact_outreach_status      ON fact_outreach (outreach_status);
CREATE INDEX IF NOT EXISTS ix_fact_outreach_is_approved ON fact_outreach (is_approved);

-- Foreign-key indexes on the dimension tables:
CREATE INDEX IF NOT EXISTS ix_jobs_company_id       ON dim_jobs (company_id);
CREATE INDEX IF NOT EXISTS ix_recruiters_company_id ON dim_recruiters (company_id);

-- Power BI relationship / common-filter helpers:
CREATE INDEX IF NOT EXISTS ix_jobs_posted_at ON dim_jobs (posted_at);
CREATE INDEX IF NOT EXISTS ix_dates_date     ON dim_dates (date);

-- GIN index for array "contains" searches, e.g. WHERE tech_stack @> '{python}'.
-- A normal B-tree index can't search inside an array; GIN is built for it.
CREATE INDEX IF NOT EXISTS ix_jobs_tech_stack_gin ON dim_jobs USING GIN (tech_stack);


COMMIT;

-- =============================================================================
-- End of schema. Run sql/02_populate_dim_dates.sql next to fill the calendar.
-- =============================================================================
