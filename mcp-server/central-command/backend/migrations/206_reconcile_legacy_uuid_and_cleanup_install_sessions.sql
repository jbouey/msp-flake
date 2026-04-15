-- Migration 206: reconcile legacy_uuid + cleanup stale install_sessions
--
-- Two unrelated data-hygiene fixes bundled:
--
-- (1) site_appliances.legacy_uuid backfill.
--     The M1 transition (migration 195/200/202) introduced
--     legacy_uuid to preserve FK linkage from child tables
--     (incidents, evidence_bundles, orders, etc.) that point at
--     appliances(id). For appliances that existed in the legacy
--     appliances table, legacy_uuid was backfilled in migration 202.
--     For site_appliances rows CREATED post-cutover (appliances-
--     native), legacy_uuid stayed NULL — which silently excludes
--     them from v_appliances_current JOINs via a.id, and from any
--     report that chains through the legacy FK.
--     Observed 2026-04-14: 3 of 4 prod rows had legacy_uuid NULL.
--     Fix: populate fresh uuid where NULL. No child-FK linkage to
--     preserve (those rows never existed in the legacy table).
--
-- (2) install_sessions retention.
--     The table has an expires_at column (24h TTL from last_seen)
--     but nothing was actually deleting expired rows. Pre-existing
--     smoke-test entries (AA:BB:CC:DD:EE:FF) sat in prod for
--     weeks. Fix: delete rows whose expires_at has elapsed.
--     Idempotent — safe to re-run.

BEGIN;

-- (1) Backfill legacy_uuid.
-- Migration 192's row-guard trigger blocks any UPDATE on site_appliances
-- that touches more than one row without the explicit per-tx bypass.
-- This backfill is genuinely bulk (every NULL row across every site)
-- so we set the bypass for THIS transaction only, then it auto-clears
-- at COMMIT.
SET LOCAL app.allow_multi_row = 'true';

UPDATE site_appliances
SET legacy_uuid = gen_random_uuid()
WHERE legacy_uuid IS NULL
  AND deleted_at IS NULL;

-- (2) Drop expired install_sessions rows.
-- Uses the existing expires_at column; no hardcoded cutoff so this
-- migration is a one-time cleanup AND the rule we want to enforce
-- going forward (scheduler-driven deletes can use the same predicate).
DELETE FROM install_sessions
WHERE expires_at < NOW();

-- (3) Explicitly purge known smoke-test MACs that predate the TTL
-- column. No-op if already gone.
DELETE FROM install_sessions
WHERE mac_address IN ('AA:BB:CC:DD:EE:FF', '00:00:00:00:00:00');

COMMIT;

SELECT 'Migration 206_reconcile_legacy_uuid_and_cleanup_install_sessions completed' AS status;
