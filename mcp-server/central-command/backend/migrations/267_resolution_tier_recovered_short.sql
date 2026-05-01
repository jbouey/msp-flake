-- Migration 267 — replace 'auto_recovered' (14 chars) with 'recovered'
-- (9 chars) in the resolution_tier CHECK so it fits the existing
-- VARCHAR(10) column. Round-table-mandated hot-fix path
-- (2026-05-01).
--
-- Context: Migration 264 added 'auto_recovered' to the CHECK list
-- but the column is VARCHAR(10) — every INSERT raised
-- StringDataRightTruncationError. Two ALTER COLUMN TYPE attempts
-- (mig 266 v1 + v2) failed because dependent views/materialized
-- views block the type change. Round-table consensus: shortening
-- the value to fit is bounded-scope and bounded-time; column-width
-- extension is a calm-session followup.
--
-- Idempotent: drops + re-adds the CHECK constraint with the new
-- value list. Mig 264's prior CHECK with 'auto_recovered' already
-- applied to prod (no rows ever written successfully because of
-- the truncation error, so no orphaned values to migrate).
--
-- Forensic contract still distinguishable:
--   L1/L2/L3        — healing pipeline closed via remediation
--   monitoring      — 7-day stale sweep gave up
--   recovered       — fail→pass transition on bundle ingest (NEW)
--   NULL            — not yet resolved, or pre-tier-tracking row
--
-- Audit-chain integrity unchanged (status='resolved' + resolved_at
-- + new short tier still distinguishes from manual/sweep/healing).

BEGIN;

ALTER TABLE incidents
    DROP CONSTRAINT IF EXISTS incidents_resolution_tier_check;

ALTER TABLE incidents
    ADD CONSTRAINT incidents_resolution_tier_check
    CHECK (
        resolution_tier IS NULL
        OR resolution_tier IN ('L1', 'L2', 'L3', 'monitoring', 'recovered')
    );

COMMENT ON COLUMN incidents.resolution_tier IS
    'L1=deterministic rule, L2=LLM planner, L3=human escalation, '
    'monitoring=7d stale sweep, recovered=fail→pass transition '
    'on bundle ingest (mig 267, replaces auto_recovered abandoned '
    'in mig 264/266 due to VARCHAR(10) width constraint).';

-- Audit-log
INSERT INTO admin_audit_log (username, action, target, details, created_at)
VALUES (
    'migration:267',
    'schema.alter',
    'incidents.resolution_tier_check',
    jsonb_build_object(
        'reason', 'Replace auto_recovered (14ch) with recovered (9ch) to fit VARCHAR(10)',
        'audit_block', 'Session-214 Block-3 P0.2 third hot-fix (shortened-value path)',
        'pre_state', 'mig 264 added auto_recovered which never persisted',
        'shipped', '2026-05-01'
    ),
    NOW()
)
ON CONFLICT DO NOTHING;

COMMIT;
