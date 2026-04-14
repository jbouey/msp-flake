-- Migration 202: M1 structural prep — move 7 appliances-only fields onto
-- site_appliances so future reader migrations have somewhere to go.
--
-- Session 206 #151 progress. The audit (Migration 196 COMMENT + 200 view)
-- identified 7 fields tracked on `appliances` but absent from
-- `site_appliances`:
--   id UUID, deployment_mode, reseller_id, policy_version,
--   public_key, current_version, previous_version, last_update_at
--
-- Strategy:
--   1. Add columns to site_appliances (nullable, no defaults for
--      optional fields)
--   2. Backfill from appliances where host_id ↔ appliance_id
--   3. Readers can start pulling from site_appliances directly —
--      no dependency on the legacy table
--   4. Writers to `appliances` still fire (legacy), but their data
--      is duplicative once readers migrate
--   5. Final DROP TABLE appliances in a later migration after all
--      readers verified
--
-- site_appliances.agent_public_key already exists → not adding
-- `public_key` duplicate; the compat view already aliases.

BEGIN;

-- Missing fields on site_appliances.
ALTER TABLE site_appliances
    ADD COLUMN IF NOT EXISTS legacy_uuid         UUID,
    ADD COLUMN IF NOT EXISTS deployment_mode     VARCHAR(50),
    ADD COLUMN IF NOT EXISTS reseller_id         VARCHAR(255),
    ADD COLUMN IF NOT EXISTS policy_version      VARCHAR(50),
    ADD COLUMN IF NOT EXISTS current_version     VARCHAR(50),
    ADD COLUMN IF NOT EXISTS previous_version    VARCHAR(50),
    ADD COLUMN IF NOT EXISTS last_update_at      TIMESTAMPTZ;

COMMENT ON COLUMN site_appliances.legacy_uuid IS
    'Session 206 M1: legacy appliances.id UUID. Preserved so fleet_updates '
    'and other UUID-FK dependents can rewire from appliances → site_appliances '
    'without data loss.';

COMMENT ON COLUMN site_appliances.deployment_mode IS
    'Session 206 M1: migrated from legacy appliances.deployment_mode.';

-- One-shot backfill from the legacy table. Best-effort: rows that exist
-- in appliances but not in site_appliances are ignored (phantom appliance
-- rows that we don't want to resurrect). Also ignores soft-deleted rows.
UPDATE site_appliances sa SET
    legacy_uuid      = a.id,
    deployment_mode  = a.deployment_mode,
    reseller_id      = a.reseller_id,
    policy_version   = a.policy_version,
    current_version  = a.current_version,
    previous_version = a.previous_version,
    last_update_at   = a.last_update_at
FROM appliances a
WHERE sa.site_id = a.site_id
  AND sa.appliance_id = a.host_id
  AND sa.deleted_at IS NULL
  AND (sa.legacy_uuid IS NULL OR sa.legacy_uuid != a.id);

-- Index on legacy_uuid for readers who look up by the old UUID FK.
CREATE INDEX IF NOT EXISTS idx_site_appliances_legacy_uuid
    ON site_appliances(legacy_uuid)
    WHERE legacy_uuid IS NOT NULL;

-- Expand the compatibility view now that these fields exist on
-- site_appliances — no more NULL projections for the 7 fields.
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
    sa.agent_public_key                         AS public_key,
    sa.legacy_uuid                              AS id,
    sa.deployment_mode,
    sa.reseller_id,
    sa.policy_version,
    sa.current_version,
    sa.previous_version,
    sa.last_update_at
FROM site_appliances sa
WHERE sa.deleted_at IS NULL;

COMMENT ON VIEW v_appliances_current IS
    'Session 206 M1 (Migration 202): site_appliances projection with ALL '
    'legacy appliances-table fields backfilled. Readers can migrate to '
    'this view confident no fields return NULL that had real data before. '
    'Remaining work: update 20 reader sites to FROM v_appliances_current, '
    'then DROP TABLE appliances.';

COMMIT;
