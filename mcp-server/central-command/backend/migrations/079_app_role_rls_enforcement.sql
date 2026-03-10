-- Migration 079: Create non-superuser app role for RLS enforcement
--
-- Problem: The `mcp` role is a superuser, which bypasses ALL row-level
-- security policies regardless of FORCE ROW LEVEL SECURITY.
-- PostgreSQL spec: superusers are always exempt from RLS.
--
-- Solution: Create `mcp_app` role (non-superuser, NOBYPASSRLS) for
-- application connections. Keep `mcp` as superuser for migrations only.
--
-- The application's DATABASE_URL must be updated to use mcp_app.
-- Rollback: DROP ROLE mcp_app; (and revert DATABASE_URL)

-- ============================================================================
-- 1. Create the application role
-- ============================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'mcp_app') THEN
        CREATE ROLE mcp_app LOGIN PASSWORD 'mcp' NOSUPERUSER NOBYPASSRLS;
    END IF;
END
$$;

-- ============================================================================
-- 2. Grant privileges — mcp_app needs full DML on all tables
-- ============================================================================

-- Schema usage
GRANT USAGE ON SCHEMA public TO mcp_app;

-- All existing tables: SELECT, INSERT, UPDATE, DELETE
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO mcp_app;

-- All existing sequences (for serial/identity columns)
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO mcp_app;

-- Future tables created by mcp (migrations) auto-grant to mcp_app
ALTER DEFAULT PRIVILEGES FOR ROLE mcp IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO mcp_app;

ALTER DEFAULT PRIVILEGES FOR ROLE mcp IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO mcp_app;

-- ============================================================================
-- 3. Grant GUC set permission (for SET LOCAL app.*)
-- ============================================================================

-- mcp_app needs to set the tenant context GUCs within transactions.
-- In PostgreSQL 15+, custom GUCs (app.*) can be set by any role.
-- For older versions, the ALTER DATABASE SET above already defines defaults.
-- No explicit GRANT needed for SET LOCAL on custom GUCs.

-- ============================================================================
-- 4. Record migration
-- ============================================================================

INSERT INTO schema_migrations (version, name, applied_at, checksum, execution_time_ms)
VALUES ('079', 'app_role_rls_enforcement', NOW(), 'phase4-p1-v3', 0)
ON CONFLICT (version) DO NOTHING;
