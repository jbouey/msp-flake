-- Migration 266 — HOT-FIX for P0.2 deploy regression (2026-05-01).
--
-- Migration 264 added 'auto_recovered' to the resolution_tier CHECK
-- constraint, but the column is VARCHAR(10) — 'auto_recovered' is 14
-- chars, so every evidence-ingest call attempted to write a value
-- longer than the storage type and raised
-- `StringDataRightTruncationError: value too long for type character
-- varying(10)`. The whole evidence chain stalled (substrate's
-- own evidence_chain_stalled invariant fired sev1 within 15 minutes
-- of the deploy — the meta-invariant working as designed).
--
-- Fix: extend the column to VARCHAR(32). 'auto_recovered' fits in 14
-- chars; the wider buffer accommodates future tiers without another
-- alter. The CHECK constraint from mig 264 still gates which strings
-- are valid.
--
-- Idempotent: ALTER TABLE ALTER COLUMN TYPE is a no-op if the column
-- is already wider.
--
-- Verification post-deploy: evidence-ingest 500 rate drops to 0;
-- new incidents with `resolution_tier='auto_recovered'` start
-- appearing in forensic queries.

BEGIN;

ALTER TABLE incidents
    ALTER COLUMN resolution_tier TYPE VARCHAR(32);

-- Audit-log
INSERT INTO admin_audit_log (username, action, target, details, created_at)
VALUES (
    'migration:266',
    'schema.alter',
    'incidents.resolution_tier',
    jsonb_build_object(
        'reason', 'HOT-FIX: VARCHAR(10) too narrow for auto_recovered tier value',
        'audit_block', 'Session-214 Block-3 P0.2 regression',
        'pre_fix', 'VARCHAR(10) — auto_recovered (14 chars) raised StringDataRightTruncationError',
        'post_fix', 'VARCHAR(32) — accommodates current + future tier names',
        'shipped', '2026-05-01'
    ),
    NOW()
)
ON CONFLICT DO NOTHING;

COMMIT;
