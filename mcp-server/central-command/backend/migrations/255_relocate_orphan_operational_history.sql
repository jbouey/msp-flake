-- Migration 255: Relocate orphan operational history for the
-- 2026-04-25 appliance relocation (physical-appliance-pilot-1aea78
-- → north-valley-branch-2).
--
-- Migrations 252 + 254 cleaned up `aggregated_pattern_stats` but
-- missed the upstream source: `_flywheel_promotion_loop` in main.py
-- aggregates `execution_telemetry` by site_id every 30 min, which
-- means the orphan rows in aggregated_pattern_stats get RECREATED
-- on every loop tick. Cleanups land, then 30 minutes later the
-- aggregator regenerates them. (Forensic timestamps confirmed:
-- migration 254 ran at 09:36:41Z, target rows got that timestamp;
-- 10 min later at 09:47:08Z, both target AND orphan rows got
-- updated by the next flywheel tick — pattern_signatures matching
-- between the two site_ids.)
--
-- This migration moves the upstream operational tables. Per CLAUDE.md
-- "Site rename is a multi-table migration" — the rule cites
-- `aggregated_pattern_stats`, but `execution_telemetry`, `incidents`,
-- and `l2_decisions` were missed. This adds them.
--
-- INTENTIONALLY NOT TOUCHING: `compliance_bundles`. Those are
-- HIPAA §164.316(b)(2)(i) signed evidence — the Ed25519 signature
-- and OTS proof bind the site_id. Updating site_id would break the
-- chain. Compliance bundles correctly stay under their original
-- site_id forever; auditor kits can reconcile via the appliance
-- relocation event.
--
-- Pre-state (verified 2026-04-29 10:07Z):
--   * execution_telemetry: 19,063 rows under orphan
--   * incidents: 533 rows under orphan
--   * l2_decisions: 31 rows under orphan
--   * compliance_bundles: 137,168 rows under orphan (NOT MIGRATED)
--
-- Forward-only. No down migration. The orphan site_id is dead.

-- execution_telemetry: the flywheel aggregator's source. After this
-- runs, the next flywheel tick aggregates rows under the canonical
-- site_id and the aggregated_pattern_stats orphan-row recreation
-- stops. PK on (id), unique on execution_id — neither involves
-- site_id, so the bulk UPDATE is collision-free.
UPDATE execution_telemetry
   SET site_id = 'north-valley-branch-2'
 WHERE site_id = 'physical-appliance-pilot-1aea78';

-- incidents: per-appliance operational history. Preserve continuity
-- so the relocated appliance's incident timeline doesn't gap.
UPDATE incidents
   SET site_id = 'north-valley-branch-2'
 WHERE site_id = 'physical-appliance-pilot-1aea78';

-- l2_decisions: planner audit. Same rationale.
UPDATE l2_decisions
   SET site_id = 'north-valley-branch-2'
 WHERE site_id = 'physical-appliance-pilot-1aea78';

-- After moving the source rows, run aggregated_pattern_stats Step
-- 1+2+3 ONE MORE TIME — the rows the flywheel recreated between
-- 09:36:41 and now need final cleanup. Idempotent.
UPDATE aggregated_pattern_stats target
SET total_occurrences = target.total_occurrences + orphan.total_occurrences,
    l1_resolutions    = target.l1_resolutions + orphan.l1_resolutions,
    l2_resolutions    = target.l2_resolutions + orphan.l2_resolutions,
    l3_resolutions    = target.l3_resolutions + orphan.l3_resolutions,
    success_count     = target.success_count + orphan.success_count,
    total_resolution_time_ms = target.total_resolution_time_ms + orphan.total_resolution_time_ms,
    success_rate = CASE
        WHEN (target.total_occurrences + orphan.total_occurrences) > 0
        THEN (target.success_count + orphan.success_count)::float
             / (target.total_occurrences + orphan.total_occurrences)
        ELSE 0.0
    END,
    avg_resolution_time_ms = CASE
        WHEN (target.total_occurrences + orphan.total_occurrences) > 0
        THEN (target.total_resolution_time_ms + orphan.total_resolution_time_ms)
             / (target.total_occurrences + orphan.total_occurrences)
        ELSE 0.0
    END,
    last_seen = GREATEST(target.last_seen, orphan.last_seen),
    last_synced_at = NOW(),
    first_seen = LEAST(target.first_seen, orphan.first_seen),
    promotion_eligible = target.promotion_eligible OR orphan.promotion_eligible,
    recommended_action = COALESCE(target.recommended_action, orphan.recommended_action),
    check_type = COALESCE(target.check_type, orphan.check_type)
FROM aggregated_pattern_stats orphan
WHERE target.site_id = 'north-valley-branch-2'
  AND orphan.site_id = 'physical-appliance-pilot-1aea78'
  AND target.pattern_signature = orphan.pattern_signature;

DELETE FROM aggregated_pattern_stats orphan
 WHERE orphan.site_id = 'physical-appliance-pilot-1aea78'
   AND EXISTS (
     SELECT 1 FROM aggregated_pattern_stats target
      WHERE target.site_id = 'north-valley-branch-2'
        AND target.pattern_signature = orphan.pattern_signature
   );

UPDATE aggregated_pattern_stats
   SET site_id = 'north-valley-branch-2',
       last_synced_at = NOW()
 WHERE site_id = 'physical-appliance-pilot-1aea78';

-- Audit-log the upstream cleanup. Idempotent guard.
INSERT INTO admin_audit_log (action, target, username, details, created_at)
SELECT
    'site.operational_history.orphan_relocation',
    'site:physical-appliance-pilot-1aea78',
    'migration:255',
    jsonb_build_object(
        'destination_site_id', 'north-valley-branch-2',
        'tables_migrated', ARRAY['execution_telemetry', 'incidents', 'l2_decisions', 'aggregated_pattern_stats'],
        'tables_intentionally_skipped', ARRAY['compliance_bundles'],
        'skip_rationale', 'compliance_bundles are signed Ed25519 + OTS-anchored — site_id is part of the cryptographic binding; updating breaks the chain',
        'related_migrations', ARRAY['252', '254', '255'],
        'reason', 'Closes the upstream orphan-row recreation cycle that defeated migrations 252+254. CLAUDE.md "Site rename is a multi-table migration" extended to include execution_telemetry/incidents/l2_decisions.'
    ),
    NOW()
WHERE NOT EXISTS (
    SELECT 1 FROM admin_audit_log
     WHERE target = 'site:physical-appliance-pilot-1aea78'
       AND action = 'site.operational_history.orphan_relocation'
);
