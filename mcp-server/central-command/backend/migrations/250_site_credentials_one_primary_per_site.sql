-- Migration 250: Enforce single primary credential per site.
--
-- Migration 249 added `is_primary BOOLEAN DEFAULT FALSE` to
-- site_credentials but no constraint stops a partner from flagging
-- two credentials primary simultaneously. DBA round-table flagged
-- this as a P2 follow-up.
--
-- Partial unique index: at most one row per site_id can have
-- is_primary = TRUE. Rows with is_primary = FALSE (or NULL during
-- the brief post-migration gap before any partner picks a primary)
-- are unaffected.
--
-- Backfill safety: existing 5 rows in prod were created via
-- non-partner-UI paths (admin / fleet_cli) and have is_primary
-- defaulting to FALSE. Zero conflict expected.

CREATE UNIQUE INDEX IF NOT EXISTS site_credentials_one_primary_per_site
    ON site_credentials (site_id)
    WHERE is_primary = TRUE;
