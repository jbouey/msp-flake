-- Migration 254: Retry the orphan-row cleanup that migration 252 left behind.
--
-- Migration 252 (2026-04-28) wrote its audit_log row but left 115 orphan
-- rows still under site_id='physical-appliance-pilot-1aea78'. Likely
-- root cause: asyncpg's multi-statement simple-query protocol treated
-- the migration's explicit BEGIN/COMMIT as nested-no-op markers under
-- its own implicit autocommit txn, AND something in Step 1's UPDATE
-- raised an error that aborted the txn AFTER Step 1 wrote and BEFORE
-- Step 3's rename — yet asyncpg's apply_migration() saw no exception
-- because the implicit txn rollback happens silently in some asyncpg
-- versions when an error occurs inside an explicit BEGIN/COMMIT block
-- in simple-query mode. (Filed as P3 backend hardening: rewrap
-- migration runner so each migration runs inside an EXPLICIT
-- conn.transaction() block; remove explicit BEGIN/COMMIT from
-- migration .sql bodies.)
--
-- This migration runs the SAME cleanup logic as 252's Step 3 but
-- WITHOUT the explicit BEGIN/COMMIT (asyncpg's autocommit handles
-- it correctly when there's a single statement). Idempotent: running
-- on already-clean data is zero rows.
--
-- Verified pre-state (2026-04-29 09:21 UTC):
--   * 115 rows under site_id='physical-appliance-pilot-1aea78'
--   * 124 rows under site_id='north-valley-branch-2'
--   * NO collisions on (site_id, pattern_signature) — Step 2 of 252
--     did delete the colliding rows successfully (verified by query
--     that found zero shared pattern_signatures across the two sites
--     post-252).
--
-- Forward-only. No down migration. The orphan site_id is dead.

-- Step 1: merge orphan stats into colliding target rows. The 254 first-cut
-- assumed Step 1+2 of 252 had succeeded; the deploy failure on
-- AGENT-REDEPLOY-EXHAUSTED:RB-DRIFT-001 proved otherwise. Re-runs full
-- merge logic — idempotent because GREATEST/LEAST handle the
-- already-merged case correctly (a + 0 = a if orphan was already
-- merged AND deleted; here the orphan is still there with its full
-- counts).
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

-- Step 2: delete orphan rows that just got merged (collision rows).
DELETE FROM aggregated_pattern_stats orphan
 WHERE orphan.site_id = 'physical-appliance-pilot-1aea78'
   AND EXISTS (
     SELECT 1 FROM aggregated_pattern_stats target
      WHERE target.site_id = 'north-valley-branch-2'
        AND target.pattern_signature = orphan.pattern_signature
   );

-- Step 3: rename remaining (non-colliding) orphan rows. After Step 2
-- there are no orphan rows whose pattern_signature collides with
-- target — the UPDATE is now safe.
UPDATE aggregated_pattern_stats
   SET site_id = 'north-valley-branch-2',
       last_synced_at = NOW()
 WHERE site_id = 'physical-appliance-pilot-1aea78';

-- Audit-log the retry. Idempotent guard — won't duplicate if this
-- migration somehow runs twice.
INSERT INTO admin_audit_log (action, target, username, details, created_at)
SELECT
    'site.aggregated_pattern_stats.orphan_cleanup_retry',
    'site:physical-appliance-pilot-1aea78',
    'migration:254',
    jsonb_build_object(
        'destination_site_id', 'north-valley-branch-2',
        'reason', 'Migration 252 partially ran (Step 3 only worked on non-colliders, Steps 1+2 silently skipped on rows that DID collide). 254 re-runs the full Step 1+2+3 merge sequence as separate statements (no explicit BEGIN/COMMIT — asyncpg autocommit handles each cleanly).',
        'related_migrations', ARRAY['252', '254']
    ),
    NOW()
WHERE NOT EXISTS (
    SELECT 1 FROM admin_audit_log
     WHERE target = 'site:physical-appliance-pilot-1aea78'
       AND action = 'site.aggregated_pattern_stats.orphan_cleanup_retry'
);
