-- Migration 081: Flip app.is_admin default to 'false'
--
-- This activates RLS enforcement for all queries that don't explicitly
-- SET LOCAL app.is_admin = 'true'.
--
-- All endpoints now use either:
--   tenant_connection(pool, site_id) -> is_admin='false', scoped to site
--   admin_connection(pool)           -> is_admin='true', sees all rows
--
-- Without SET LOCAL, queries default to is_admin='false' -> RLS enforced.
-- This is the fail-closed security posture.
--
-- Rollback: ALTER DATABASE mcp SET app.is_admin = 'true';

ALTER DATABASE mcp SET app.is_admin = 'false';

INSERT INTO schema_migrations (version, name, applied_at, checksum, execution_time_ms)
VALUES ('081', 'flip_is_admin_default', NOW(), 'phase4-p2-v1', 0)
ON CONFLICT (version) DO NOTHING;
