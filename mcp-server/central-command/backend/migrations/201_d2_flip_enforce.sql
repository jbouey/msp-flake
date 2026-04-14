-- Migration 201: D2 enforcement flip — default cross_appliance_enforce = 'reject'.
--
-- Session 206 D2 flip. Migration 199 shipped the opt-in per-transaction
-- switch. This migration FLIPS THE GLOBAL DEFAULT so any site_appliances
-- UPDATE that touches a row belonging to an appliance other than the
-- authenticated actor will be REJECTED at the DB layer.
--
-- Safety net:
--   * tenant_connection in this codebase always sets actor_appliance_id
--     when called with it, and admin_connection uses is_admin=true
--     (which bypasses the trigger entirely).
--   * Legitimate bulk operations (domain credentials fan-out, site
--     transfer) already set LOCAL app.allow_multi_row='true' — not
--     affected by this trigger (separate Migration 192).
--   * Any writer missing actor_appliance_id AND not admin will fail
--     with "cross-appliance UPDATE blocked" — that's the invariant.
--   * Emergency rollback (if something silently depended on the old
--     behavior): operator runs
--         ALTER DATABASE mcp RESET "app.cross_appliance_enforce";
--     which reverts to the Postgres default of no setting (unset),
--     and the trigger's COALESCE(..., 'audit') kicks in.
--
-- Verification after apply:
--   SELECT name, setting FROM pg_db_role_setting s
--   JOIN pg_database d ON d.oid = s.setdatabase
--   WHERE d.datname = 'mcp' AND s.setrole = 0 AND name LIKE 'app.%';

BEGIN;

-- Set as DB-level default. All new sessions inherit 'reject' unless they
-- explicitly SET LOCAL to 'audit' for a specific transaction.
ALTER DATABASE mcp SET "app.cross_appliance_enforce" = 'reject';

COMMIT;

-- =============================================================================
-- Rollback (run manually if the flip is misbehaving — not part of migration
-- up flow, documented here so the operator can find it fast):
--
--   ALTER DATABASE mcp RESET "app.cross_appliance_enforce";
--
-- After reset, all cross-appliance UPDATEs fall back to the trigger's
-- internal default (COALESCE in audit_cross_appliance_update → 'audit').
-- Audit-only means they're LOGGED to admin_audit_log but NOT blocked —
-- same behavior as Session 206 D2 shipped.
--
-- To confirm which mode is live:
--   SELECT COALESCE(current_setting('app.cross_appliance_enforce', TRUE),
--                   '<unset: trigger defaults to audit>') AS mode;
-- =============================================================================
