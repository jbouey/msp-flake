-- Migration 319: canonical_devices — deduplicated device view (Task #73 Phase 1)
--
-- USER DIRECTIVE 2026-05-13: enterprise structural fix, NOT a hotfix.
-- Device Inventory at /sites/{site_id}/devices was showing the same physical
-- machine 3-7× because each appliance at a multi-appliance site ARP-scans
-- the same /24 network. Empirical state at nvb2: 36 discovered_devices rows
-- collapse to 22 canonical (ip, mac) entries — 14 duplicates from 3 appliances.
-- Customer-impact: monthly compliance packet PDFs (compliance_packet.py:1167)
-- have been emitting ~63% over-counted total_devices to clinic customers.
--
-- Design v3 + Gate A v1 (architectural) + Gate A v2 (implementation) APPROVE:
--   audit/device-dedup-architectural-design-2026-05-13.md
--   audit/coach-device-dedup-architectural-gate-a-2026-05-13.md
--   audit/coach-device-dedup-implementation-gate-a-2026-05-13.md
--
-- Counsel Rules addressed:
--   Rule 1 — device_count_per_site joins canonical_metrics.py registry
--   Rule 4 — observed_by_appliances UUID[] preserves per-appliance source-of-record
--            (no orphan-coverage-by-dedup class)
--
-- v2 implementation Gate A P0s applied:
--   P0-A — mig 319 (not 316 — collision with Task #38 + 317/318 with Task #58)
--   P0-B — CI ratchet baseline computed AFTER compliance_packet migration
--   P0-C — RLS policy parity with discovered_devices (tenant_org + partner + admin)

BEGIN;

CREATE TABLE canonical_devices (
    canonical_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    site_id         TEXT NOT NULL REFERENCES sites(site_id) ON DELETE CASCADE,
    ip_address      TEXT NOT NULL,
    mac_address     TEXT NULL,
    mac_dedup_key   TEXT GENERATED ALWAYS AS (COALESCE(mac_address, '')) STORED,
    device_type     TEXT NULL,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    observed_by_appliances UUID[] NOT NULL DEFAULT '{}',
    reconciled_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX canonical_devices_site_ip_mac_idx
    ON canonical_devices (site_id, ip_address, mac_dedup_key);

CREATE INDEX canonical_devices_site_last_seen_idx
    ON canonical_devices (site_id, last_seen_at DESC);

CREATE INDEX canonical_devices_reconciled_idx
    ON canonical_devices (reconciled_at);

-- P0-C (Carol) — RLS parity with discovered_devices.
ALTER TABLE canonical_devices ENABLE ROW LEVEL SECURITY;

-- Admin bypass (mirrors discovered_devices)
CREATE POLICY canonical_devices_admin_all
    ON canonical_devices
    USING (current_setting('app.is_admin', true) = 'true')
    WITH CHECK (current_setting('app.is_admin', true) = 'true');

-- Tenant isolation by site_id (mirrors discovered_devices.tenant_org_isolation
-- per mig 278 canonical shape — short-circuit if app.current_org is unset
-- per the rls_site_belongs_to_current_org function's contract comment).
-- Gate B P1.1 fix 2026-05-13: omitting these guards is a documented
-- contract violation.
CREATE POLICY canonical_devices_tenant_org_isolation
    ON canonical_devices FOR ALL
    USING (
        current_setting('app.current_org', true) IS NOT NULL
        AND current_setting('app.current_org', true) <> ''
        AND rls_site_belongs_to_current_org(site_id::text)
    );

-- Partner isolation by site_id (mirrors discovered_devices.partner_isolation
-- per mig 297 canonical shape).
CREATE POLICY canonical_devices_partner_isolation
    ON canonical_devices FOR ALL
    USING (
        current_setting('app.current_partner_id', true) IS NOT NULL
        AND current_setting('app.current_partner_id', true) <> ''
        AND rls_site_belongs_to_current_partner(site_id::text)
    );

COMMENT ON TABLE canonical_devices IS
    'Canonical view of physical devices per site, deduplicated from discovered_devices. '
    'Multi-appliance same-(ip,mac) observations collapse to one row via reconciliation loop. '
    'Per Counsel Rule 1 (2026-05-13), this is the canonical source for device_count_per_site. '
    'See canonical_metrics.py and tests/test_no_raw_discovered_devices_count.py.';

COMMENT ON COLUMN canonical_devices.observed_by_appliances IS
    'Array of appliance_ids that have independently observed this (ip, mac) within the '
    'reconciliation window. Length < expected_appliances_count is a Counsel-Rule-4 '
    'coverage-degradation signal.';

COMMENT ON COLUMN canonical_devices.device_type IS
    'Majority-vote winner across observing appliances. Multi-way ties broken by '
    'alphabetical UTF-8 codepoint order (deterministic). See reconciliation loop SQL.';

-- Phase 1 backfill — idempotent INSERT-SELECT from existing discovered_devices.
-- Reads from the source table directly; the reconciliation loop takes over
-- for ongoing tick-by-tick refresh.
INSERT INTO canonical_devices (site_id, ip_address, mac_address, device_type,
                               first_seen_at, last_seen_at, observed_by_appliances,
                               reconciled_at)
SELECT
    dd.site_id,
    dd.ip_address,
    (ARRAY_AGG(dd.mac_address) FILTER (WHERE dd.mac_address IS NOT NULL))[1] AS mac_address,
    (SELECT dt FROM (
        SELECT device_type AS dt, COUNT(DISTINCT appliance_id) AS vc
          FROM discovered_devices dd2
         WHERE dd2.site_id = dd.site_id
           AND dd2.ip_address = dd.ip_address
           AND COALESCE(dd2.mac_address, '') = COALESCE(dd.mac_address, '')
           AND dd2.device_type IS NOT NULL
         GROUP BY device_type
         ORDER BY vc DESC, device_type ASC
         LIMIT 1
    ) t) AS device_type,
    MIN(dd.first_seen_at) AS first_seen_at,
    MAX(dd.last_seen_at) AS last_seen_at,
    ARRAY_AGG(DISTINCT dd.appliance_id) AS observed_by_appliances,
    NOW() AS reconciled_at
FROM discovered_devices dd
GROUP BY dd.site_id, dd.ip_address, COALESCE(dd.mac_address, '')
ON CONFLICT (site_id, ip_address, mac_dedup_key) DO NOTHING;

-- Audit-log row.
INSERT INTO admin_audit_log (
    user_id, username, action, target, details, ip_address
) VALUES (
    NULL,
    'system',
    'canonical_devices_table_created',
    'canonical_devices',
    jsonb_build_object(
        'migration', '319_canonical_devices',
        'task', '#73',
        'counsel_rules_addressed', jsonb_build_array(
            'Rule 1 — canonical-source registry runtime half',
            'Rule 4 — no orphan coverage (per-appliance observation preserved)'
        ),
        'design_doc', 'audit/device-dedup-architectural-design-2026-05-13.md',
        'gate_a_v1_verdict', 'audit/coach-device-dedup-architectural-gate-a-2026-05-13.md',
        'gate_a_v2_verdict', 'audit/coach-device-dedup-implementation-gate-a-2026-05-13.md'
    ),
    NULL
);

COMMIT;
