-- Migration 196: deprecate `appliances` table (#M1 starter).
--
-- Background: `appliances` is a legacy single-appliance-per-site table
-- from pre-Session-196 (before backend-authoritative multi-appliance
-- mesh). It still has 5+ writers that site-update by site_id only,
-- which was part of the Session 206 phantom-liveness bug (fixed per-host
-- in e51884f + 6930292 + e752967).
--
-- Full retirement requires migrating all readers/writers to
-- site_appliances. That's a multi-PR effort. This migration:
--   1. Comments the table as DEPRECATED so future code sees the warning
--   2. Adds a compatibility VIEW `v_appliances_current` that projects
--      from site_appliances, for new code to read instead of the legacy
--      table. When all readers/writers are migrated, DROP the table.

BEGIN;

COMMENT ON TABLE appliances IS
    'DEPRECATED (Session 206 M1). Legacy single-appliance-per-site table. '
    'New code should read v_appliances_current which projects from '
    'site_appliances (per-appliance, honest last_checkin via heartbeats). '
    'Retirement tracked as task #149. Writers identified: '
    '(1) sites.py STEP 3.5, (2) main.py /checkin, (3) agent_api.py, '
    '(4) reconciliation_loop, (5) device_sync.py. All fixed as of 6930292 '
    'to use host_id scoping, but the table itself is still a single-row '
    'anti-pattern per site.';

CREATE OR REPLACE VIEW v_appliances_current AS
SELECT
    sa.appliance_id                             AS host_id,
    sa.site_id,
    sa.hostname,
    sa.mac_address,
    sa.last_checkin,
    sa.agent_version,
    sa.nixos_version,
    sa.status,
    sa.first_checkin                            AS created_at,
    sa.last_checkin                             AS updated_at,
    sa.ip_addresses,
    sa.deleted_at
FROM site_appliances sa
WHERE sa.deleted_at IS NULL;

COMMENT ON VIEW v_appliances_current IS
    'Session 206 M1: compatibility projection over site_appliances for code '
    'that still reads the legacy appliances table. Excludes soft-deleted '
    'rows. Migrate readers one-by-one, then DROP TABLE appliances.';

COMMIT;
