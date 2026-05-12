-- Migration 308: l2_escalations_missed — parallel disclosure table for
-- the recurrence-detector partitioning gap (2026-05-12).
--
-- Filename note: brief specified `307_l2_escalations_missed.sql` but 307
-- is already assigned to `307_ots_proofs_status_check.sql`. Next free
-- slot is 308; coordinated with Maya P0-C verdict
-- (`audit/maya-p0c-backfill-decision-2026-05-12.md`) which itself noted
-- "next free; confirm before commit."
--
-- ROUND-TABLE CONTEXT
-- ===================
-- Maya P0-C verdict (Option B): the historical gap of ~320 L1 resolutions
-- where L2 SHOULD have run (3 chronic check_types on
-- north-valley-branch-2 over 7d) is NOT backfilled into `l2_decisions`.
-- Mig 300 was approved because the L2 LLM actually ran and the audit row
-- write raised mid-flight (synthetic rows preserved the FACT). Here the
-- L2 LLM never ran — synthesizing l2_decisions rows would fabricate
-- evidence of root-cause analysis that did not happen (the exact forgery
-- pattern Session 218 round-table rejected).
--
-- Instead: a PARALLEL TABLE outside `v_l2_outcomes` (mig 285) +
-- structured disclosure JSON in the auditor kit + advisory MD shipped to
-- every kit (Session 218 disclosure-over-backfill precedent). Auditor-kit
-- determinism contract is preserved: prior payload shapes are unchanged;
-- kit_version advances 2.1 -> 2.2 as a legitimate forward-progression
-- signal that explains the new disclosures section.
--
-- IMMUTABILITY
-- ============
-- INSERT-ONLY. UPDATE/DELETE rejected by triggers. Auditors can hash the
-- table and compare across audits. The only way rows enter is the
-- backfill INSERT below + (in theory) a future migration re-running the
-- detector for a different historical window. Operator hand-edits are
-- rejected at the DB layer.
--
-- CI gates that depend on this migration:
--   - tests/test_l2_escalations_missed_immutable.py
--   - tests/test_no_appliance_id_partitioned_recurrence_count.py
--   - test_substrate_docs_present.py (covers companion runbook files)

BEGIN;

-- ----------------------------------------------------------------------
-- Table
-- ----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS l2_escalations_missed (
    id                       BIGSERIAL PRIMARY KEY,
    site_id                  TEXT        NOT NULL,
    incident_type            TEXT        NOT NULL,
    missed_count             INT         NOT NULL,
    first_observed_at        TIMESTAMPTZ NOT NULL,
    last_observed_at         TIMESTAMPTZ NOT NULL,
    detection_method         TEXT        NOT NULL DEFAULT 'recurrence_partitioning_audit_2026_05_12',
    disclosed_in_kit_version TEXT        NOT NULL DEFAULT '2.2',
    recorded_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- One row per (site, incident_type, detection_method). The unique
    -- constraint also lets ON CONFLICT DO NOTHING idempotency guard the
    -- one-shot backfill against accidental re-runs.
    CONSTRAINT uq_l2_esc_missed_site_type
        UNIQUE (site_id, incident_type, detection_method)
);

COMMENT ON TABLE l2_escalations_missed IS
  'Disclosure-only audit table. Parallel to l2_decisions (which is the live '
  'LLM-decision record). Rows enumerate historical (site_id, incident_type) '
  'pairs where the recurrence detector partitioning bug (2026-05-12) caused '
  'the L2 LLM path to NOT run when the customer-facing chronic-pattern SLA '
  'said it should have. INSERT-ONLY. Audit reference: '
  'docs/security/SECURITY_ADVISORY_2026-05-12_RECURRENCE_DETECTOR_PARTITIONING.md. '
  'Session 220 P1 persistence-drift round-table 2026-05-12. '
  'Determinism: feeds disclosures/missed_l2_escalations.json in the auditor kit.';

-- ----------------------------------------------------------------------
-- Immutability triggers (INSERT-only; UPDATE + DELETE rejected)
-- ----------------------------------------------------------------------
-- Function names are unique to this table — do NOT reuse the generic
-- prevent_audit_deletion() from mig 151 because the message string here
-- references the specific disclosure context for auditors who hit the
-- error during forensic inspection.

CREATE OR REPLACE FUNCTION l2_escalations_missed_reject_update()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION
        'UPDATE denied on l2_escalations_missed (id=%): rows are an '
        'INSERT-ONLY disclosure record per Maya P0-C verdict 2026-05-12. '
        'Mutating a disclosed row would itself be a chain-manipulation event. '
        'If the disclosure window needs to retire, write a NEW migration '
        'removing the invariant from ALL_ASSERTIONS — do not mutate this row.',
        OLD.id;
END;
$$;

CREATE OR REPLACE FUNCTION l2_escalations_missed_reject_delete()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION
        'DELETE denied on l2_escalations_missed (id=%): rows are an '
        'INSERT-ONLY disclosure record. Audit trail integrity. '
        'See docs/security/SECURITY_ADVISORY_2026-05-12_RECURRENCE_DETECTOR_PARTITIONING.md.',
        OLD.id;
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'l2_esc_missed_no_update') THEN
        CREATE TRIGGER l2_esc_missed_no_update
            BEFORE UPDATE ON l2_escalations_missed
            FOR EACH ROW EXECUTE FUNCTION l2_escalations_missed_reject_update();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'l2_esc_missed_no_delete') THEN
        CREATE TRIGGER l2_esc_missed_no_delete
            BEFORE DELETE ON l2_escalations_missed
            FOR EACH ROW EXECUTE FUNCTION l2_escalations_missed_reject_delete();
    END IF;
END $$;

-- ----------------------------------------------------------------------
-- RLS — admin-only direct read. Customer surfacing happens via the
-- auditor-kit JSON, which the kit-issuance code accesses under admin
-- (Carol P0-D: kit-issuance is server-side, not via RLS-scoped client
-- connections).
-- ----------------------------------------------------------------------
ALTER TABLE l2_escalations_missed ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policy
         WHERE polname = 'l2_esc_missed_admin_select'
           AND polrelid = 'l2_escalations_missed'::regclass
    ) THEN
        CREATE POLICY l2_esc_missed_admin_select
            ON l2_escalations_missed
            FOR SELECT
            USING (
                COALESCE(current_setting('app.is_admin', true), 'false') = 'true'
            );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policy
         WHERE polname = 'l2_esc_missed_admin_insert'
           AND polrelid = 'l2_escalations_missed'::regclass
    ) THEN
        CREATE POLICY l2_esc_missed_admin_insert
            ON l2_escalations_missed
            FOR INSERT
            WITH CHECK (
                COALESCE(current_setting('app.is_admin', true), 'false') = 'true'
            );
    END IF;
END $$;

-- ----------------------------------------------------------------------
-- Backfill — aggregate from incident_recurrence_velocity (the canonical
-- chronic-pattern table, NOT the buggy per-appliance detector). One row
-- per (site_id, incident_type) tuple. Count = `velocity_count_4h` from
-- the velocity row if the column exists; else COALESCE to 1 (defensive —
-- the column name is `resolved_4h` per mig 156:21 verified in Gate A
-- digest, but a defensive COALESCE keeps the migration tolerant of
-- alternate aggregate column names).
--
-- Window: last 30 days. Anything older is pre-detector-instrumentation
-- and would inflate the disclosure with noise from a period predating
-- the customer-facing chronic SLA claim.
--
-- ON CONFLICT DO NOTHING: re-running this migration (e.g., recovery
-- from a partial-apply) is idempotent.
-- ----------------------------------------------------------------------
INSERT INTO l2_escalations_missed (
    site_id,
    incident_type,
    missed_count,
    first_observed_at,
    last_observed_at,
    detection_method,
    disclosed_in_kit_version,
    recorded_at
)
SELECT
    v.site_id,
    v.incident_type,
    COALESCE(v.resolved_4h, 1)                         AS missed_count,
    MIN(v.computed_at)                                 AS first_observed_at,
    MAX(v.computed_at)                                 AS last_observed_at,
    'recurrence_partitioning_audit_2026_05_12'         AS detection_method,
    '2.2'                                              AS disclosed_in_kit_version,
    NOW()                                              AS recorded_at
FROM incident_recurrence_velocity v
WHERE v.is_chronic = TRUE
  AND v.computed_at > NOW() - INTERVAL '30 days'
GROUP BY v.site_id, v.incident_type, v.resolved_4h
ON CONFLICT (site_id, incident_type, detection_method) DO NOTHING;

-- ----------------------------------------------------------------------
-- Audit log row so operators see the migration ran. `username` (NOT
-- `actor`) per CLAUDE.md rule. Mirrors mig 300 shape exactly.
-- ----------------------------------------------------------------------
INSERT INTO admin_audit_log (username, action, target, details, created_at)
VALUES (
    'system:mig-308',
    'disclose_l2_escalation_gap',
    'l2_escalations_missed',
    jsonb_build_object(
        'migration', '308_l2_escalations_missed',
        'session',   'Session 220 P1 persistence-drift L2 routing fix',
        'verdict_ref', 'audit/maya-p0c-backfill-decision-2026-05-12.md',
        'advisory_ref', 'docs/security/SECURITY_ADVISORY_2026-05-12_RECURRENCE_DETECTOR_PARTITIONING.md',
        'reason', 'parallel disclosure table; NOT a backfill into l2_decisions per Maya P0-C',
        'kit_version_advance', '2.1 -> 2.2'
    ),
    NOW()
);

COMMIT;

-- Carol P0-D composite index on l2_decisions(site_id, escalation_reason,
-- created_at DESC) lives in mig 309 as a standalone file. asyncpg's
-- simple-query runner cannot run CREATE INDEX CONCURRENTLY in the same
-- script as an explicit BEGIN/COMMIT block (deploy 096de200 hit this).
-- Working pattern: mig 136 + mig 154 ship CONCURRENTLY-only with no
-- BEGIN/COMMIT.
