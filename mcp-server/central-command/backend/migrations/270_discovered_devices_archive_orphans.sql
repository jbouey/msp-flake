-- Migration 270 — archive orphan + soft-deleted-appliance discovered
-- devices (BUG 3 Path D round-table 2026-05-01).
--
-- discovered_devices on prod has 38 stale rows for north-valley:
--   * 7 belong to soft-deleted physical-appliance-pilot-1aea78
--     (Session 210-B relocate source — devices never migrated to the
--     new appliance_id, lingered in the table)
--   * 31 are FULL ORPHANS (appliance_id refers to a legacy_uuid that
--     doesn't match any row in site_appliances at all)
--
-- v_appliances_current already filters them out via JOIN, so dashboard
-- counts are correct — but the table is bloating. Mirror the Session
-- 210-B mig 244 archive pattern: move stale rows to
-- discovered_devices_archive and DELETE from the live table.
--
-- Round-table 2026-05-01 (BUG 3 fork a48dd10968aaf583c, Steve SRE):
-- "Path D safe to ship anytime — JOIN already filters them, so
-- removing them changes only the underlying table size, not any
-- live count."
--
-- Idempotent: archive table created with IF NOT EXISTS; DELETE
-- skips already-archived rows via NOT EXISTS subquery.

BEGIN;

-- Archive table mirrors discovered_devices schema + adds archive
-- metadata. Mirrors mig 244's discovered_devices_archive pattern
-- (Session 210-B GC).
CREATE TABLE IF NOT EXISTS discovered_devices_archive_orphans (
    id BIGINT NOT NULL,
    appliance_id UUID NOT NULL,
    local_device_id TEXT,
    hostname TEXT,
    ip_address TEXT,
    mac_address TEXT,
    device_type TEXT,
    device_status TEXT,
    compliance_status TEXT,
    medical_device BOOLEAN,
    first_seen_at TIMESTAMPTZ,
    last_seen_at TIMESTAMPTZ,
    archived_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    archive_reason TEXT NOT NULL,
    PRIMARY KEY (id, archived_at)
);

CREATE INDEX IF NOT EXISTS idx_dd_archive_orphans_appliance
    ON discovered_devices_archive_orphans (appliance_id);

-- Step 1: archive ORPHANS (appliance_id matches NO row in site_appliances)
INSERT INTO discovered_devices_archive_orphans (
    id, appliance_id, local_device_id, hostname, ip_address, mac_address,
    device_type, device_status, compliance_status, medical_device,
    first_seen_at, last_seen_at, archive_reason
)
SELECT
    d.id, d.appliance_id, d.local_device_id, d.hostname, d.ip_address,
    d.mac_address, d.device_type, d.device_status, d.compliance_status,
    d.medical_device, d.first_seen_at, d.last_seen_at,
    'orphan: appliance_id has no matching row in site_appliances'
  FROM discovered_devices d
 WHERE NOT EXISTS (
     SELECT 1 FROM site_appliances sa WHERE sa.legacy_uuid = d.appliance_id
 )
   AND NOT EXISTS (
     SELECT 1 FROM discovered_devices_archive_orphans a WHERE a.id = d.id
 );

-- Step 2: archive devices belonging to SOFT-DELETED appliances
INSERT INTO discovered_devices_archive_orphans (
    id, appliance_id, local_device_id, hostname, ip_address, mac_address,
    device_type, device_status, compliance_status, medical_device,
    first_seen_at, last_seen_at, archive_reason
)
SELECT
    d.id, d.appliance_id, d.local_device_id, d.hostname, d.ip_address,
    d.mac_address, d.device_type, d.device_status, d.compliance_status,
    d.medical_device, d.first_seen_at, d.last_seen_at,
    'soft-deleted appliance: site_appliances.deleted_at IS NOT NULL'
  FROM discovered_devices d
 WHERE EXISTS (
     SELECT 1 FROM site_appliances sa
      WHERE sa.legacy_uuid = d.appliance_id
        AND sa.deleted_at IS NOT NULL
 )
   AND NOT EXISTS (
     SELECT 1 FROM discovered_devices_archive_orphans a WHERE a.id = d.id
 );

-- Step 3: DELETE from live table (rows are now in archive)
-- Allow multi-row admin operation for this maintenance task.
SET LOCAL app.allow_multi_row = 'true';

DELETE FROM discovered_devices d
 WHERE NOT EXISTS (
     SELECT 1 FROM site_appliances sa WHERE sa.legacy_uuid = d.appliance_id
 )
    OR EXISTS (
     SELECT 1 FROM site_appliances sa
      WHERE sa.legacy_uuid = d.appliance_id
        AND sa.deleted_at IS NOT NULL
 );

-- Audit-log (idempotent ON CONFLICT)
INSERT INTO admin_audit_log (username, action, target, details, created_at)
VALUES (
    'migration:270',
    'data.archive',
    'discovered_devices (orphans + soft-deleted)',
    jsonb_build_object(
        'reason', 'Stale rows for soft-deleted/relocated/missing appliances; bloating discovered_devices table',
        'audit_block', 'BUG 3 Path D round-table 2026-05-01',
        'archive_table', 'discovered_devices_archive_orphans',
        'sibling_path_c', 'mig 269 deprecates compliance_status column',
        'pattern_mirror', 'Session 210-B mig 244 (data_hygiene_gc archive pattern)',
        'shipped', '2026-05-01'
    ),
    NOW()
)
ON CONFLICT DO NOTHING;

COMMIT;
