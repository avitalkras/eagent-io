-- =============================================================================
-- Eagent.io — Phase 1: Populate dim_dates (next ~2 years)
-- =============================================================================
-- Run AFTER 01_schema.sql:
--   psql -d eagent -f sql/02_populate_dim_dates.sql
--
-- CONCEPT — generate_series():
--   Postgres can generate a set of rows on the fly. We ask it for every DATE
--   from today up to 2 years out, one row per day, then DERIVE the calendar
--   attributes (day/month/quarter/year/is_weekend) from each date.
--
-- CONCEPT — idempotency (again!):
--   ON CONFLICT (date_id) DO NOTHING means you can safely re-run this script.
--   Days that already exist are skipped instead of causing a duplicate-key error.
-- =============================================================================

INSERT INTO dim_dates (date_id, date, day, month, quarter, year, is_weekend)
SELECT
    -- date_id as the YYYYMMDD "smart integer" (e.g. 2026-09-08 -> 20260908).
    -- TO_CHAR formats the date, then we cast the text to INTEGER.
    TO_CHAR(d, 'YYYYMMDD')::INTEGER            AS date_id,
    d::DATE                                    AS date,
    EXTRACT(DAY     FROM d)::SMALLINT          AS day,
    EXTRACT(MONTH   FROM d)::SMALLINT          AS month,
    EXTRACT(QUARTER FROM d)::SMALLINT          AS quarter,
    EXTRACT(YEAR    FROM d)::SMALLINT          AS year,
    -- ISODOW: Monday=1 ... Sunday=7. So 6 or 7 => weekend.
    (EXTRACT(ISODOW FROM d) IN (6, 7))         AS is_weekend
FROM generate_series(
        CURRENT_DATE,                          -- start: today
        CURRENT_DATE + INTERVAL '2 years',     -- end: 2 years from today
        INTERVAL '1 day'                       -- step: one day at a time
     ) AS gs(d)
ON CONFLICT (date_id) DO NOTHING;

-- Quick sanity check (uncomment to see what got loaded):
-- SELECT MIN(date) AS first_day, MAX(date) AS last_day, COUNT(*) AS total_days
-- FROM dim_dates;
