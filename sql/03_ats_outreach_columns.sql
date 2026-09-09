-- =============================================================================
-- Eagent.io — Phase 3 migration: ATS output columns on fact_outreach
-- =============================================================================
-- Purpose : add storage for the ATS engine's PDF output, and make
--           fact_outreach upsertable per job (requirement #4).
-- Author  : Avital Krasovitski
--
-- HOW TO RUN:
--   psql -d eagent -f sql/03_ats_outreach_columns.sql
--
-- WHY A NEW FILE INSTEAD OF EDITING sql/01_schema.sql?
--   Once a migration has shipped (this one already ran against the real
--   database in Phase 1/2), you never edit it again — anyone who already
--   applied it would silently diverge from anyone who re-runs the edited
--   version. Schema changes after that point are new, numbered migration
--   files applied in order. This file is Migration #3.
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- 1. resume_pdf_path — where the generated, tailored resume PDF lives.
-- -----------------------------------------------------------------------------
-- A plain TEXT path/URI (e.g. a local path today, an S3/GCS URI later) rather
-- than storing the PDF bytes in the database. Warehouses are bad homes for
-- large binary blobs — keep Postgres for structured, queryable data and let
-- the filesystem/object storage hold the file itself.
ALTER TABLE fact_outreach
    ADD COLUMN IF NOT EXISTS resume_pdf_path TEXT;


-- -----------------------------------------------------------------------------
-- 2. UNIQUE(job_id) — the idempotency key for the ATS/resume step.
-- -----------------------------------------------------------------------------
-- Product decision: at most one active outreach draft per job posting. This
-- is what lets the ATS pipeline (eagent.ats.loader.upsert_outreach_result)
-- be safely re-run — re-scoring the same job updates its existing
-- fact_outreach row (fresh ats_score / missing_skills / PDF) instead of
-- creating a duplicate. Same idempotency principle as
-- dim_jobs.uq_jobs_source_external in Phase 1 (sql/01_schema.sql), applied
-- to the fact table this time.
--
-- Postgres has no `ADD CONSTRAINT IF NOT EXISTS`, so we guard manually by
-- checking pg_constraint first — this keeps the migration safe to re-run,
-- consistent with the IF NOT EXISTS guards used throughout Phase 1.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_fact_outreach_job_id'
    ) THEN
        ALTER TABLE fact_outreach
            ADD CONSTRAINT uq_fact_outreach_job_id UNIQUE (job_id);
    END IF;
END$$;

COMMIT;

-- =============================================================================
-- End of migration.
-- =============================================================================
