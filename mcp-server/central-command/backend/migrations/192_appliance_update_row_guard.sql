-- Migration 192: BEFORE UPDATE row-count guard on site_appliances + appliances.
--
-- Context: Session 206 audit exposed THREE site-wide UPDATE bugs that
-- propagated one appliance's state onto every row at the site. The
-- dashboard showed phantom appliances as "online" for 3+ days because
-- the UPDATEs matched by site_id alone (no host_id/appliance_id filter)
-- while the tables now hold multiple rows per site (multi-appliance
-- architecture shipped Session 196).
--
-- Bugs shipped between those sessions:
--   * sites.py STEP 3.5     — UPDATE appliances WHERE site_id = $1
--   * reconciliation_loop   — UPDATE site_appliances FROM appliances WHERE sa.site_id = a.site_id
--   * device_sync.py        — UPDATE site_appliances SET status='online' WHERE site_id = $1
--
-- Permanent fix (this migration): a STATEMENT-level BEFORE UPDATE trigger
-- that counts affected rows per site_id. If any UPDATE affects >1 row for
-- the same site_id without the caller explicitly declaring bulk intent
-- via SET LOCAL app.allow_multi_row='true', the statement is REJECTED.
--
-- Legitimate bulk operations (site-transfer, operator-driven cleanup) MUST
-- set the flag inside their transaction. Drive-by accidents cannot bypass
-- the trigger — the flag is per-transaction and local to the operator's
-- deliberate call, not a global escape hatch.

BEGIN;

-- =============================================================================
-- Reusable guard function — applied to both site_appliances + appliances.
-- =============================================================================

CREATE OR REPLACE FUNCTION enforce_single_row_update_per_site()
RETURNS TRIGGER AS $$
DECLARE
    max_per_site INTEGER;
    sample_site TEXT;
BEGIN
    -- Allow explicit bulk operations to bypass. Flag must be set inside
    -- the same transaction, scoped with SET LOCAL.
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
                  HINT = 'Session 206 invariant — see Migration 192.';
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION enforce_single_row_update_per_site() IS
    'Session 206: reject UPDATEs affecting multiple rows per site_id unless '
    'caller sets LOCAL app.allow_multi_row=''true''. Prevents the site-wide '
    'UPDATE footgun that made phantom appliances look online for 3 days.';

-- =============================================================================
-- Install on site_appliances
-- =============================================================================

DROP TRIGGER IF EXISTS trg_enforce_single_row_per_site_appliances
    ON site_appliances;

CREATE TRIGGER trg_enforce_single_row_per_site_appliances
    AFTER UPDATE ON site_appliances
    REFERENCING NEW TABLE AS new_table
    FOR EACH STATEMENT
    EXECUTE FUNCTION enforce_single_row_update_per_site();

-- =============================================================================
-- Install on appliances (legacy table, still has writers)
-- =============================================================================

DROP TRIGGER IF EXISTS trg_enforce_single_row_per_site_legacy_appliances
    ON appliances;

CREATE TRIGGER trg_enforce_single_row_per_site_legacy_appliances
    AFTER UPDATE ON appliances
    REFERENCING NEW TABLE AS new_table
    FOR EACH STATEMENT
    EXECUTE FUNCTION enforce_single_row_update_per_site();

COMMIT;
