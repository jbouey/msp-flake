-- Migration 320: canonical_devices appliance-bearer RLS parity (Task #85)
--
-- Gate A APPROVE-WITH-FIXES 2026-05-13 (audit/coach-canonical-devices-
-- rls-gap-gate-a-2026-05-13.md). Fork's load-bearing finding:
--
--   Mig 319 (canonical_devices) shipped 3 RLS policies:
--     - canonical_devices_admin_all       (app.is_admin)
--     - canonical_devices_tenant_org_isolation (app.current_org via rls_site_belongs_to_current_org)
--     - canonical_devices_partner_isolation    (app.current_partner_id via rls_site_belongs_to_current_partner)
--
--   But NONE of those fires for the `tenant_connection()` path under
--   appliance-bearer auth. That path sets `app.current_tenant` (the
--   site_id literal) — NOT app.current_org. Sibling discovered_devices
--   has the matching policy via mig 080:163-171:
--
--     CREATE POLICY discovered_devices_tenant_isolation ON discovered_devices
--         USING (
--             current_setting('app.is_admin', true) = 'true'
--             OR site_id = current_setting('app.current_tenant', true)
--         );
--
--   canonical_devices is missing this 4th parity policy. Without it,
--   sites.py:5644 hot-path migration (Task #75) would break the
--   pending_deploy provisioning step at every appliance checkin — the
--   appliance-bearer conn would see zero canonical_devices rows.
--
-- Fork P1: mig 319 ENABLE'd but did NOT FORCE row-level security.
-- Sibling mig 080:165 forces on discovered_devices. Mig 320 closes
-- the parity gap (additive — admin bypass policy already exists, this
-- just makes it explicit).
--
-- UNBLOCKS Task #75 sites.py:5644 hot-path canonical_devices migration.
-- Mig 320 MUST ship + verify deploy BEFORE Task #75 implements.

BEGIN;

-- P1 — FORCE row-level security parity with discovered_devices.
-- This means even table-owner queries respect RLS (the existing 3
-- policies' admin bypass still works because they USING (app.is_admin)).
ALTER TABLE canonical_devices FORCE ROW LEVEL SECURITY;

-- P0 — appliance-bearer parity policy mirrors mig 080:167-171 exactly.
-- Allows tenant_connection() reads under appliance-bearer auth to see
-- canonical_devices rows for the appliance's site (app.current_tenant
-- = site_id literal). Without this, sites.py:5644 hot-path queries
-- return zero rows + pending_deploy provisioning silently breaks.
CREATE POLICY canonical_devices_tenant_isolation
    ON canonical_devices
    FOR ALL
    USING (
        current_setting('app.is_admin', true) = 'true'
        OR site_id = current_setting('app.current_tenant', true)
    );

-- Audit-log row documenting the closure.
INSERT INTO admin_audit_log (
    user_id, username, action, target, details, ip_address
) VALUES (
    NULL,
    'system',
    'canonical_devices_rls_parity_added',
    'canonical_devices',
    jsonb_build_object(
        'migration', '320_canonical_devices_appliance_rls_parity',
        'task', '#85',
        'gate_a_verdict', 'audit/coach-canonical-devices-rls-gap-gate-a-2026-05-13.md',
        'sibling_policy', 'mig 080:167-171 discovered_devices_tenant_isolation',
        'unblocks_task', '#75 sites.py:5644 hot-path canonical_devices migration',
        'closes_gap', 'mig 319 3-policy gap: app.current_tenant variable not covered'
    ),
    NULL
);

COMMIT;
