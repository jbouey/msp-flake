-- Migration 114: Discovery-based scan ownership for multi-appliance deployments
--
-- When multiple appliances share a subnet, the first appliance to discover a
-- device owns it for scanning purposes. This prevents duplicate drift scans.
-- Ownership transfers if the owning appliance goes offline (>30 min no checkin).

ALTER TABLE discovered_devices
ADD COLUMN IF NOT EXISTS owner_appliance_id UUID,
ADD COLUMN IF NOT EXISTS owned_since TIMESTAMP WITH TIME ZONE;

-- Set initial ownership: current appliance_id owns all its discovered devices
UPDATE discovered_devices
SET owner_appliance_id = appliance_id::uuid,
    owned_since = created_at
WHERE owner_appliance_id IS NULL;

-- Index for ownership lookups during credential filtering
CREATE INDEX IF NOT EXISTS idx_discovered_devices_owner
ON discovered_devices(owner_appliance_id, ip_address);
