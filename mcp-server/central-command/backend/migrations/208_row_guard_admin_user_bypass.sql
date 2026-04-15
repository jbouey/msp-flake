-- Migration 208: row-guard refactor — bypass for admin DB user
--
-- Migration 192 introduced enforce_single_row_update_per_site() to
-- block the site-wide UPDATE footgun that made phantom appliances
-- look online for 3 days (Session 206 incident). The trigger refuses
-- any UPDATE > 1 row per site unless the caller sets
-- `SET LOCAL app.allow_multi_row='true'` inside the same transaction.
--
-- The flag-based bypass works for application code paths but is a
-- footgun for migrations: I forgot it on Migration 206 tonight,
-- the deploy fail-closed, and the new code didn't roll out for ~30
-- minutes until I noticed.
--
-- Fix: also bypass the guard when the executing role is `mcp` (the
-- admin/migration runner). Application code runs as `mcp_app` (the
-- tenant-RLS-active role) and continues to need the per-tx flag if
-- it ever wants to do a bulk update — which it shouldn't.
--
-- Net effect:
--   * mcp_app  → must use per-row filter or explicit SET LOCAL
--   * mcp      → bulk ops just work (migrations, manual cleanup)
--
-- This eliminates the entire class of "I forgot the bypass in
-- migration N" CI failures that have happened twice in 24 hours.

BEGIN;

CREATE OR REPLACE FUNCTION enforce_single_row_update_per_site()
RETURNS TRIGGER AS $$
DECLARE
    max_per_site INTEGER;
    sample_site TEXT;
BEGIN
    -- Bypass 1 (new in 208): admin/migrator role. Application
    -- code never runs as mcp; only the migration runner and
    -- one-off DBA sessions do.
    IF current_user = 'mcp' THEN
        RETURN NULL;
    END IF;

    -- Bypass 2 (legacy from 192): explicit per-tx flag for the
    -- rare case application code legitimately needs a bulk op.
    IF current_setting('app.allow_multi_row', TRUE) = 'true' THEN
        RETURN NULL;
    END IF;

    -- Count affected rows per site_id. Reject if any site is touched
    -- by more than one row in a single UPDATE.
    SELECT COUNT(*), site_id INTO max_per_site, sample_site
    FROM new_table
    GROUP BY site_id
    ORDER BY COUNT(*) DESC
    LIMIT 1;

    IF max_per_site > 1 THEN
        RAISE EXCEPTION
            'Site-wide UPDATE on % blocked: % rows at site_id=% would be modified. '
            'Add a per-row filter (appliance_id/host_id/mac_address), '
            'or set LOCAL app.allow_multi_row=''true'' if this is truly a bulk op.',
            TG_TABLE_NAME, max_per_site, sample_site
            USING ERRCODE = 'raise_exception',
                  HINT = 'Session 206 invariant — see Migration 192/208.';
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION enforce_single_row_update_per_site() IS
    'Migration 208: rejects multi-row UPDATEs by mcp_app (tenant role). '
    'Admin role (mcp) bypasses for migrations. Application bypass via '
    'SET LOCAL app.allow_multi_row=''true''.';

COMMIT;

SELECT 'Migration 208 row-guard admin bypass complete' AS status;
