-- Migration 203: give mcp_app ownership of appliance_status_rollup so
-- the heartbeat_rollup background loop can REFRESH it.
--
-- Session 206 regression discovered post-D2-flip. The loop errors:
--   InsufficientPrivilegeError: must be owner of materialized view
--   appliance_status_rollup
-- Because mcp (superuser, used for migrations) created the MV in
-- Migration 193, but the background loop runs as mcp_app via the
-- application pool. REFRESH requires OWNER in Postgres — not a
-- grantable privilege.
--
-- Fix: reassign ownership to mcp_app. Migration runs as mcp
-- (MIGRATION_DATABASE_URL), which has the authority to reassign.

BEGIN;

ALTER MATERIALIZED VIEW appliance_status_rollup OWNER TO mcp_app;

COMMIT;
