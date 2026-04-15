-- Migration 213: drop the legacy `appliances` table
--
-- Phase 5 of the M1 "full" migration. After phases 2-4, every reader and
-- writer in the backend has been swapped off `appliances` and onto either
-- `v_appliances_current` (the existing view that projects site_appliances
-- into the old appliances shape) or `site_appliances` directly. The legacy
-- table has been a silent shadow since migration 202 backfilled its
-- fields onto site_appliances — at preflight time it held 2 rows against
-- the 4 live site_appliances rows the platform actually serves. No code
-- path writes to it; every SELECT now targets site_appliances.
--
-- FK audit on VPS 2026-04-15:
--   orders             . orders_appliance_id_fkey              ON DELETE CASCADE
--   incidents          . incidents_appliance_id_fkey           ON DELETE CASCADE
--   evidence_bundles   . evidence_bundles_appliance_id_fkey    ON DELETE CASCADE
--   appliance_updates  . appliance_updates_appliance_id_fkey   ON DELETE CASCADE
--   update_audit_log   . update_audit_log_appliance_id_fkey    ON DELETE SET NULL
--   discovered_devices . discovered_devices_appliance_id_fkey  ON DELETE CASCADE
--
-- Disposition: DROP all six. The referenced column `appliances.id` is a
-- legacy UUID. Every site_appliances row exposes the same UUID via
-- `legacy_uuid`, but that column is NOT UNIQUE so it cannot be the target
-- of a foreign key. Rows in the referencing tables keep their existing
-- `appliance_id` UUIDs as historical identifiers without FK enforcement —
-- acceptable because any live JOIN now goes through v_appliances_current
-- which aliases `legacy_uuid AS id`.
--
-- View recreate is NOT required: v_appliances_current is already defined
-- entirely off site_appliances. Dropping `appliances` cannot affect it.
--
-- Stored-function dependencies: FIVE functions still reference
-- `appliances` directly (set_incident_site_id, set_order_site_id,
-- set_evidence_bundle_site_id, set_discovered_device_site_id, and
-- refresh_compliance_score). This migration CREATE OR REPLACEs all
-- five to read from site_appliances.legacy_uuid instead. Without that
-- redefinition the DROP would break every future INSERT into the four
-- trigger-bound tables AND the refresh_compliance_score() RPC.
--
-- Rollback: restore from the `appliances_backup_20260415` CTAS snapshot
-- taken below, then recreate the six FK constraints + re-run the
-- original function bodies (in migrations 078 and 080). The snapshot
-- is kept alongside the drop for safety — it can be itself dropped in
-- a follow-up migration once operators are comfortable.
--
-- Row-guard: no UPDATEs on site_appliances happen here. DDL only.

BEGIN;

-- 1. Snapshot the legacy table so a manual rollback is a CTAS-rename away.
--    DROP TABLE IF EXISTS ... in case a prior migration attempt left one.
DROP TABLE IF EXISTS appliances_backup_20260415;
CREATE TABLE appliances_backup_20260415 AS TABLE appliances;

-- 2. Redefine five stored functions that still reference `appliances`
--    directly. Dropping the table without updating these would break the
--    next INSERT into incidents / orders / evidence_bundles /
--    discovered_devices (the BEFORE INSERT site_id triggers) AND the
--    refresh_compliance_score() RPC. Redirect all reads to
--    site_appliances.legacy_uuid (preserves the same UUID identity
--    used by the referencing rows). LIMIT 1 because legacy_uuid is not
--    UNIQUE on site_appliances.
CREATE OR REPLACE FUNCTION set_incident_site_id() RETURNS trigger AS $$
BEGIN
    IF NEW.site_id IS NULL AND NEW.appliance_id IS NOT NULL THEN
        SELECT site_id INTO NEW.site_id
          FROM site_appliances WHERE legacy_uuid = NEW.appliance_id LIMIT 1;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION set_order_site_id() RETURNS trigger AS $$
BEGIN
    IF NEW.site_id IS NULL AND NEW.appliance_id IS NOT NULL THEN
        SELECT site_id INTO NEW.site_id
          FROM site_appliances WHERE legacy_uuid = NEW.appliance_id LIMIT 1;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION set_evidence_bundle_site_id() RETURNS trigger AS $$
BEGIN
    IF NEW.site_id IS NULL AND NEW.appliance_id IS NOT NULL THEN
        SELECT site_id INTO NEW.site_id
          FROM site_appliances WHERE legacy_uuid = NEW.appliance_id LIMIT 1;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION set_discovered_device_site_id() RETURNS trigger AS $$
BEGIN
    IF NEW.site_id IS NULL AND NEW.appliance_id IS NOT NULL THEN
        SELECT site_id INTO NEW.site_id
          FROM site_appliances WHERE legacy_uuid = NEW.appliance_id LIMIT 1;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- refresh_compliance_score: keep the whole body, just redirect the
-- fallback SELECT from appliances → site_appliances. Everything else
-- remains byte-identical to the original definition.
CREATE OR REPLACE FUNCTION refresh_compliance_score(
    p_appliance_id UUID,
    p_framework VARCHAR
) RETURNS void AS $$
DECLARE
    v_site_id VARCHAR;
    v_score RECORD;
BEGIN
    SELECT site_id INTO v_site_id
      FROM appliance_framework_configs
     WHERE appliance_id = p_appliance_id;

    IF v_site_id IS NULL THEN
        SELECT site_id INTO v_site_id
          FROM site_appliances
         WHERE legacy_uuid = p_appliance_id
         LIMIT 1;
    END IF;

    SELECT * INTO v_score FROM calculate_compliance_score(p_appliance_id, p_framework);

    INSERT INTO compliance_scores (
        appliance_id, site_id, framework,
        total_controls, passing_controls, failing_controls, unknown_controls,
        score_percentage, is_compliant, at_risk, calculated_at
    ) VALUES (
        p_appliance_id, v_site_id, p_framework,
        v_score.total_controls, v_score.passing_controls,
        v_score.failing_controls, v_score.unknown_controls,
        COALESCE(v_score.score_percentage, 0),
        COALESCE(v_score.score_percentage, 0) >= 80,
        COALESCE(v_score.score_percentage, 0) < 70,
        NOW()
    )
    ON CONFLICT (appliance_id, framework)
    DO UPDATE SET
        total_controls = EXCLUDED.total_controls,
        passing_controls = EXCLUDED.passing_controls,
        failing_controls = EXCLUDED.failing_controls,
        unknown_controls = EXCLUDED.unknown_controls,
        score_percentage = EXCLUDED.score_percentage,
        is_compliant = EXCLUDED.is_compliant,
        at_risk = EXCLUDED.at_risk,
        calculated_at = EXCLUDED.calculated_at;
END;
$$ LANGUAGE plpgsql;

-- 3. Drop the six FK constraints pointing at appliances(id). Enumerated
--    explicitly so the audit trail in pg_event_trigger (if any) carries
--    per-constraint detail, and so a re-run is a no-op via IF EXISTS.
ALTER TABLE orders
    DROP CONSTRAINT IF EXISTS orders_appliance_id_fkey;
ALTER TABLE incidents
    DROP CONSTRAINT IF EXISTS incidents_appliance_id_fkey;
ALTER TABLE evidence_bundles
    DROP CONSTRAINT IF EXISTS evidence_bundles_appliance_id_fkey;
ALTER TABLE appliance_updates
    DROP CONSTRAINT IF EXISTS appliance_updates_appliance_id_fkey;
ALTER TABLE update_audit_log
    DROP CONSTRAINT IF EXISTS update_audit_log_appliance_id_fkey;
ALTER TABLE discovered_devices
    DROP CONSTRAINT IF EXISTS discovered_devices_appliance_id_fkey;

-- 4. Drop the legacy table. Indexes and triggers scoped to it drop with
--    it; no CASCADE needed because FKs are gone above and no view depends
--    on it. The trigger function `enforce_single_row_update_per_site()` is
--    shared with other tables and must NOT be dropped — DROP TABLE only
--    removes the trigger binding, not the function.
DROP TABLE IF EXISTS appliances;

COMMIT;
