-- Migration 080: RLS on remaining tables (Phase 4 P2)
--
-- Tables covered:
--   orders              — add site_id, backfill from appliances, RLS
--   evidence_bundles    — add site_id, backfill from appliances, RLS
--   discovered_devices  — add site_id, backfill from appliances, RLS
--   device_compliance_details — add site_id, backfill via discovered_devices→appliances, RLS
--   fleet_orders        — admin-only RLS (no site_id — fleet-wide by design)
--
-- Depends on: 078 (RLS infrastructure), 079 (mcp_app role)

-- ============================================================================
-- 1. Add site_id columns
-- ============================================================================

ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS site_id TEXT;

ALTER TABLE evidence_bundles
    ADD COLUMN IF NOT EXISTS site_id TEXT;

ALTER TABLE discovered_devices
    ADD COLUMN IF NOT EXISTS site_id TEXT;

ALTER TABLE device_compliance_details
    ADD COLUMN IF NOT EXISTS site_id TEXT;

-- ============================================================================
-- 2. Backfill site_id from appliances
-- ============================================================================

-- orders: appliance_id → appliances.site_id
UPDATE orders o
SET site_id = a.site_id
FROM appliances a
WHERE o.appliance_id = a.id
  AND o.site_id IS NULL;

-- evidence_bundles: appliance_id → appliances.site_id
UPDATE evidence_bundles eb
SET site_id = a.site_id
FROM appliances a
WHERE eb.appliance_id = a.id
  AND eb.site_id IS NULL;

-- discovered_devices: appliance_id → appliances.site_id
UPDATE discovered_devices dd
SET site_id = a.site_id
FROM appliances a
WHERE dd.appliance_id = a.id
  AND dd.site_id IS NULL;

-- device_compliance_details: discovered_device_id → discovered_devices → appliances
UPDATE device_compliance_details dcd
SET site_id = a.site_id
FROM discovered_devices dd
JOIN appliances a ON dd.appliance_id = a.id
WHERE dcd.discovered_device_id = dd.id
  AND dcd.site_id IS NULL;

-- ============================================================================
-- 3. Auto-populate triggers (new rows get site_id automatically)
-- ============================================================================

-- orders: look up site_id from appliances on INSERT
CREATE OR REPLACE FUNCTION set_order_site_id()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.site_id IS NULL AND NEW.appliance_id IS NOT NULL THEN
        SELECT site_id INTO NEW.site_id
        FROM appliances WHERE id = NEW.appliance_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS auto_set_order_site_id ON orders;
CREATE TRIGGER auto_set_order_site_id
    BEFORE INSERT ON orders
    FOR EACH ROW
    EXECUTE FUNCTION set_order_site_id();

-- evidence_bundles: look up site_id from appliances on INSERT
CREATE OR REPLACE FUNCTION set_evidence_bundle_site_id()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.site_id IS NULL AND NEW.appliance_id IS NOT NULL THEN
        SELECT site_id INTO NEW.site_id
        FROM appliances WHERE id = NEW.appliance_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS auto_set_evidence_bundle_site_id ON evidence_bundles;
CREATE TRIGGER auto_set_evidence_bundle_site_id
    BEFORE INSERT ON evidence_bundles
    FOR EACH ROW
    EXECUTE FUNCTION set_evidence_bundle_site_id();

-- discovered_devices: look up site_id from appliances on INSERT
CREATE OR REPLACE FUNCTION set_discovered_device_site_id()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.site_id IS NULL AND NEW.appliance_id IS NOT NULL THEN
        SELECT site_id INTO NEW.site_id
        FROM appliances WHERE id = NEW.appliance_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS auto_set_discovered_device_site_id ON discovered_devices;
CREATE TRIGGER auto_set_discovered_device_site_id
    BEFORE INSERT ON discovered_devices
    FOR EACH ROW
    EXECUTE FUNCTION set_discovered_device_site_id();

-- device_compliance_details: look up site_id via discovered_devices → appliances on INSERT
CREATE OR REPLACE FUNCTION set_device_compliance_site_id()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.site_id IS NULL AND NEW.discovered_device_id IS NOT NULL THEN
        SELECT a.site_id INTO NEW.site_id
        FROM discovered_devices dd
        JOIN appliances a ON dd.appliance_id = a.id
        WHERE dd.id = NEW.discovered_device_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS auto_set_device_compliance_site_id ON device_compliance_details;
CREATE TRIGGER auto_set_device_compliance_site_id
    BEFORE INSERT ON device_compliance_details
    FOR EACH ROW
    EXECUTE FUNCTION set_device_compliance_site_id();

-- ============================================================================
-- 4. Enable RLS + policies
-- ============================================================================

-- --- orders ---
ALTER TABLE orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE orders FORCE ROW LEVEL SECURITY;

CREATE POLICY orders_tenant_isolation ON orders
    USING (
        current_setting('app.is_admin', true) = 'true'
        OR site_id = current_setting('app.current_tenant', true)
    );

-- --- evidence_bundles ---
ALTER TABLE evidence_bundles ENABLE ROW LEVEL SECURITY;
ALTER TABLE evidence_bundles FORCE ROW LEVEL SECURITY;

CREATE POLICY evidence_bundles_tenant_isolation ON evidence_bundles
    USING (
        current_setting('app.is_admin', true) = 'true'
        OR site_id = current_setting('app.current_tenant', true)
    );

-- --- discovered_devices ---
ALTER TABLE discovered_devices ENABLE ROW LEVEL SECURITY;
ALTER TABLE discovered_devices FORCE ROW LEVEL SECURITY;

CREATE POLICY discovered_devices_tenant_isolation ON discovered_devices
    USING (
        current_setting('app.is_admin', true) = 'true'
        OR site_id = current_setting('app.current_tenant', true)
    );

-- --- device_compliance_details ---
ALTER TABLE device_compliance_details ENABLE ROW LEVEL SECURITY;
ALTER TABLE device_compliance_details FORCE ROW LEVEL SECURITY;

CREATE POLICY device_compliance_details_tenant_isolation ON device_compliance_details
    USING (
        current_setting('app.is_admin', true) = 'true'
        OR site_id = current_setting('app.current_tenant', true)
    );

-- --- fleet_orders (admin-only — no site_id, fleet-wide operations) ---
ALTER TABLE fleet_orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE fleet_orders FORCE ROW LEVEL SECURITY;

CREATE POLICY fleet_orders_admin_only ON fleet_orders
    USING (
        current_setting('app.is_admin', true) = 'true'
    );

-- ============================================================================
-- 5. Indexes for RLS performance
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_orders_site_id ON orders(site_id);
CREATE INDEX IF NOT EXISTS idx_evidence_bundles_site_id ON evidence_bundles(site_id);
CREATE INDEX IF NOT EXISTS idx_discovered_devices_site_id ON discovered_devices(site_id);
CREATE INDEX IF NOT EXISTS idx_device_compliance_details_site_id ON device_compliance_details(site_id);

-- ============================================================================
-- 6. Record migration
-- ============================================================================

INSERT INTO schema_migrations (version, name, applied_at, checksum, execution_time_ms)
VALUES ('080', 'rls_remaining_tables', NOW(), 'phase4-p2-v1', 0)
ON CONFLICT (version) DO NOTHING;
