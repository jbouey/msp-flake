-- Migration 200: expand v_appliances_current so readers of `appliances` can
-- migrate to it without schema surprises.
--
-- Session 206 M1 — the compatibility view shipped in Migration 196 projected
-- only a handful of fields. Real readers of `appliances` depend on 7 more:
--   id UUID, deployment_mode, reseller_id, policy_version,
--   public_key, current_version, previous_version, last_update_at
--
-- site_appliances doesn't carry all of them. For the ones that ARE stored on
-- site_appliances (public key → agent_public_key, etc.) we project directly.
-- For the genuine gaps (deployment_mode, reseller_id, policy_version,
-- current_version, previous_version, last_update_at) the view returns NULL —
-- readers MUST handle NULL during migration and the view makes the gap
-- surface-visible rather than silently hidden.
--
-- After all 20 reader sites migrate to `v_appliances_current`, the `appliances`
-- table itself can be DROPped (tracked as task #151-ish follow-up).

BEGIN;

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
    sa.deleted_at,
    -- Per-appliance Ed25519 key (Session 196). The legacy appliances.public_key
    -- was the same field — rename survives via this alias.
    sa.agent_public_key                         AS public_key,
    -- The following are NOT tracked in site_appliances today. Readers that
    -- need them MUST check for NULL and degrade gracefully during M1 migration.
    -- The path forward is: move these onto site_appliances (or a new
    -- appliance_versions table) + backfill from `appliances` once, then DROP.
    NULL::uuid                                  AS id,
    NULL::text                                  AS deployment_mode,
    NULL::text                                  AS reseller_id,
    NULL::text                                  AS policy_version,
    NULL::text                                  AS current_version,
    NULL::text                                  AS previous_version,
    NULL::timestamptz                           AS last_update_at
FROM site_appliances sa
WHERE sa.deleted_at IS NULL;

COMMENT ON VIEW v_appliances_current IS
    'Session 206 M1 (expanded Migration 200): compatibility projection over '
    'site_appliances. Fields absent in the source return NULL — readers '
    'must tolerate that during migration. After all readers (20+ query '
    'sites in fleet.py, sensors.py, etc.) switch to this view, the '
    '`appliances` table can be dropped. Current reader count: ~20.';

COMMIT;
