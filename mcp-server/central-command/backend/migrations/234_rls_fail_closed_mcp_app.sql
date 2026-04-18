-- Migration 234: fail-closed RLS default for the application role.
--
-- Audit finding (P0 #36): ALTER DATABASE mcp SET app.is_admin = 'true'
-- (migration 082) means every SQLAlchemy session that forgets to set
-- tenant context sees every tenant's rows on RLS-protected tables. Any
-- new endpoint written against `get_db()` that forgot to filter by site
-- or org_id was a data-leak one typo away.
--
-- Why we fix it at the ROLE level, not the DB level:
--   Migration 082 already documents that flipping the *database* default
--   broke every SQLAlchemy admin path because those paths don't call
--   SET LOCAL — they rely on the default being 'true'. The correct
--   surgical fix is to keep the DB default intact (so migrations and
--   out-of-band admin ops via the `mcp` superuser keep working) while
--   flipping the default for the app role (`mcp_app`) that handles
--   runtime traffic. After this migration:
--
--     - `mcp` role  (migrator, manual admin SQL):   app.is_admin = 'true'
--     - `mcp_app`   (FastAPI via PgBouncer):        app.is_admin = 'false'
--
--   Any admin-level asyncpg path (admin_connection) must now explicitly
--   opt into `SET app.is_admin = 'true'`. Any SQLAlchemy session must do
--   the same via an engine-level begin listener (see shared.py). This
--   was wired in the same commit; the migration MUST ship with those
--   code changes or the app goes blind on startup.
--
--   PgBouncer transaction pooling's default `server_reset_query = DISCARD
--   ALL` discards session-level SET between client borrows, so a prior
--   connection's `SET app.is_admin = 'true'` does not leak to the next
--   borrower. This is the property that lets us use session-level SET
--   in `admin_connection` without transaction poisoning.
--
-- Rollback: ALTER ROLE mcp_app RESET app.is_admin; and revert the code
-- changes in shared.py + tenant_middleware.py. The migration itself is
-- reversible via `ALTER ROLE mcp_app RESET app.is_admin` with no data
-- impact (settings are lazy — they apply to NEW connections from the
-- role, not retroactively).

BEGIN;

-- The narrow flip: only the app role defaults to fail-closed.
ALTER ROLE mcp_app SET app.is_admin = 'false';

-- Belt-and-suspenders: pin the migrator role default explicitly rather
-- than relying on the database default to survive a future edit.
ALTER ROLE mcp SET app.is_admin = 'true';

-- Sanity: record the flip in the audit trail.
INSERT INTO admin_audit_log (username, action, target, details, created_at)
VALUES (
    current_user,
    'rls.app_is_admin.fail_closed',
    'role:mcp_app',
    jsonb_build_object(
        'migration', '234',
        'note', 'mcp_app role default app.is_admin flipped true->false',
        'mcp_role_default', 'true (unchanged)',
        'mcp_app_role_default', 'false (new)'
    ),
    NOW()
) ON CONFLICT DO NOTHING;

COMMIT;
