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

-- Single statement, no BEGIN/COMMIT — asyncpg autocommits cleanly.
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
        'reason', 'Retry of migration 252 Step 3 — that migration appears to have committed Step 1+2 but not Step 3; 115 orphan rows survived. Likely asyncpg simple-query + explicit BEGIN/COMMIT interaction.',
        'related_migration', '252'
    ),
    NOW()
WHERE NOT EXISTS (
    SELECT 1 FROM admin_audit_log
     WHERE target = 'site:physical-appliance-pilot-1aea78'
       AND action = 'site.aggregated_pattern_stats.orphan_cleanup_retry'
);
