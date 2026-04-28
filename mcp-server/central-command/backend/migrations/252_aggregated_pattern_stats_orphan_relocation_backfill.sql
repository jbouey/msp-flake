-- Migration 252: Relocate orphan aggregated_pattern_stats rows.
--
-- Closes the data drift surfaced by user report 2026-04-28: the
-- "Approve" dashboard button on candidate 253985
-- (ransomware_indicator:RB-WIN-STG-002 for North Valley Dental)
-- returned 500. Root cause traced to candidate's site_id pointing
-- at the OLD orphan site `physical-appliance-pilot-1aea78` which
-- had been relocated to `north-valley-branch-2` per Session 210
-- but `aggregated_pattern_stats` was never moved alongside it.
-- CLAUDE.md "Site rename is a multi-table migration" lists this
-- exact table among the five that must move on relocate.
--
-- Pre-migration state (verified 2026-04-28 16:43 UTC):
--   * 122 rows under site_id='physical-appliance-pilot-1aea78'
--     (1 with promotion_eligible=true)
--   * 27 rows under site_id='north-valley-branch-2'
--   * 0 rows in site_appliances for the orphan site (already moved)
--
-- The unique key is (site_id, pattern_signature). A bare UPDATE
-- site_id would collide on any pattern_signature that exists under
-- both site_ids. We merge stats first (sum counts, GREATEST timestamps,
-- OR booleans) then delete the merged orphans, then bare-UPDATE the
-- non-colliding rest.
--
-- Idempotent: running twice updates 0 rows the second time.
-- Forward-only: there is no down migration. The orphan site_id is
-- dead — no path can re-create it.

BEGIN;

-- Step 1: merge stats from orphan rows into existing target rows
-- (where pattern_signature matches under both site_ids).
UPDATE aggregated_pattern_stats target
SET total_occurrences = target.total_occurrences + orphan.total_occurrences,
    l1_resolutions    = target.l1_resolutions + orphan.l1_resolutions,
    l2_resolutions    = target.l2_resolutions + orphan.l2_resolutions,
    l3_resolutions    = target.l3_resolutions + orphan.l3_resolutions,
    success_count     = target.success_count + orphan.success_count,
    total_resolution_time_ms = target.total_resolution_time_ms + orphan.total_resolution_time_ms,
    -- Recompute success_rate from the new sums (avoid weighted-by-row drift)
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
    -- Keep target's recommended_action; orphan's is older.
    recommended_action = COALESCE(target.recommended_action, orphan.recommended_action),
    check_type = COALESCE(target.check_type, orphan.check_type)
FROM aggregated_pattern_stats orphan
WHERE target.site_id = 'north-valley-branch-2'
  AND orphan.site_id = 'physical-appliance-pilot-1aea78'
  AND target.pattern_signature = orphan.pattern_signature;

-- Step 2: delete the orphan rows that were just merged.
DELETE FROM aggregated_pattern_stats orphan
 WHERE orphan.site_id = 'physical-appliance-pilot-1aea78'
   AND EXISTS (
     SELECT 1 FROM aggregated_pattern_stats target
      WHERE target.site_id = 'north-valley-branch-2'
        AND target.pattern_signature = orphan.pattern_signature
   );

-- Step 3: rename the rest (no collision) to the new site_id.
UPDATE aggregated_pattern_stats
   SET site_id = 'north-valley-branch-2',
       last_synced_at = NOW()
 WHERE site_id = 'physical-appliance-pilot-1aea78';

-- Step 4: audit-log the cleanup so future archaeology can find it.
INSERT INTO admin_audit_log (action, target, username, details, created_at)
VALUES (
    'site.aggregated_pattern_stats.orphan_relocation',
    'site:physical-appliance-pilot-1aea78',
    'migration:252',
    jsonb_build_object(
        'destination_site_id', 'north-valley-branch-2',
        'reason', 'Session 210 appliance relocate did not migrate aggregated_pattern_stats — backfilled per user report 2026-04-28 candidate 253985'
    ),
    NOW()
);

COMMIT;
