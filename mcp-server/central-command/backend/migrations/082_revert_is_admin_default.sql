-- Migration 082: Revert app.is_admin default back to 'true'
--
-- Migration 081 set the database default to 'false' (fail-closed), but this
-- broke all SQLAlchemy-based endpoints (admin dashboard, settings, users, etc.)
-- because SQLAlchemy sessions don't call SET LOCAL.
--
-- Architecture clarification:
--   - asyncpg pool paths: use tenant_connection() / admin_connection() from
--     tenant_middleware.py, which SET LOCAL per-transaction. These are the
--     multi-tenant data paths and ARE properly RLS-enforced regardless of
--     the database default.
--   - SQLAlchemy paths: admin dashboard, settings, users, OAuth. These use
--     the database default. Admin endpoints SHOULD bypass RLS.
--
-- RLS enforcement is an application-layer guarantee via tenant_connection(),
-- not a database-default guarantee. The default='true' is the correct
-- posture because:
--   1. All tenant-scoped queries go through tenant_connection() → is_admin='false'
--   2. Admin queries need is_admin='true' (dashboard, settings)
--   3. The database default only affects paths that don't explicitly SET LOCAL
--
ALTER DATABASE mcp SET app.is_admin = 'true';

INSERT INTO schema_migrations (version, name, applied_at, checksum, execution_time_ms)
VALUES ('082', 'revert_is_admin_default', NOW(), 'phase4-p2-fix-v1', 0)
ON CONFLICT (version) DO NOTHING;
