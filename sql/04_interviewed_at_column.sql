-- =============================================================================
-- Eagent.io — Phase 4 migration: fact_outreach.interviewed_at
-- =============================================================================
-- Purpose : add the missing lifecycle timestamp for the 'Interview' stage.
-- Author  : Avital Krasovitski
--
-- HOW TO RUN:
--   psql -d eagent -f sql/04_interviewed_at_column.sql
--
-- WHY THIS MIGRATION EXISTS:
--   Phase 1 gave fact_outreach lifecycle timestamps for Drafted, Sent, and
--   Replied (drafted_at, sent_at, replied_at) but not for Interview — the
--   gap was caught during Phase 5's Power BI design work
--   (docs/05-power-bi-data-model.md §1) when `Interview Rate %` had to fall
--   back to `outreach_status = 'Interview'`, a point-in-time snapshot that
--   silently undercounts any outreach that later moves on to 'Rejected'.
--   Same fix shape as Migration #3 (sql/03_ats_outreach_columns.sql): an
--   additive column on an already-shipped table, in its own numbered file.
-- =============================================================================

BEGIN;

ALTER TABLE fact_outreach
    ADD COLUMN IF NOT EXISTS interviewed_at TIMESTAMPTZ;

COMMIT;

-- =============================================================================
-- eagent.workflow.mark_interview() sets this column going forward.
-- Power BI's `Interview Rate %` measure now filters on
-- NOT ISBLANK(interviewed_at) instead of the outreach_status snapshot — see
-- powerbi/eagent_measures.dax and docs/05-power-bi-data-model.md.
-- =============================================================================
