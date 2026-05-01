-- Migration 266 — ABANDONED (2026-05-01).
--
-- Original intent: extend incidents.resolution_tier from VARCHAR(10)
-- to VARCHAR(32) so the new 'auto_recovered' value (14 chars) fits.
--
-- Round-table verdict 2026-05-01 ~10:20 UTC: ABANDON this path.
-- ALTER COLUMN TYPE blocked by 2 dependents (v_canonical_incidents
-- view from mig 258 + partner_site_weekly_rollup materialized view).
-- Drop+recreate of the materialized view loses stored aggregation
-- data and requires REFRESH MATERIALIZED VIEW which can take
-- minutes on prod-sized data. Production was bleeding evidence for
-- ~35 min when this was decided.
--
-- New path (mig 267): keep VARCHAR(10), shorten the new tier value
-- from 'auto_recovered' (14 chars) to 'recovered' (9 chars). Bounded
-- scope, no schema risk, single deploy to recovery.
--
-- This file is kept as a no-op so the migration runner can mark it
-- applied and move past the failure that's been blocking CI deploys.
--
-- Followup ticket tracked at .agent/claude-progress.json::
-- scheduled_followups for the column-width extension via a clean
-- 3-commit migration pattern. Defer to calm session — not under
-- production-outage time pressure.

BEGIN;

-- No-op marker so the runner records mig 266 as applied.
DO $$ BEGIN
    RAISE NOTICE 'Migration 266 abandoned per round-table 2026-05-01; replaced by 267 (value shortened to recovered).';
END $$;

-- Audit log.
INSERT INTO admin_audit_log (username, action, target, details, created_at)
VALUES (
    'migration:266',
    'migration.abandoned',
    'incidents.resolution_tier_width',
    jsonb_build_object(
        'reason', 'ALTER COLUMN TYPE blocked by 2 dependents during prod outage; round-table chose shorten-value path instead',
        'replaced_by', 'migration 267 (CHECK constraint value swap)',
        'audit_block', 'Session-214 Block-3 P0.2 second hot-fix',
        'shipped', '2026-05-01'
    ),
    NOW()
)
ON CONFLICT DO NOTHING;

COMMIT;
