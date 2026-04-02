-- Migration 113: Add missing indexes for device sync performance
-- The POST /api/devices/sync endpoint runs 12+ queries per device.
-- These indexes cut the hot-path query time from ~52-65s to <5s.

-- go_agents lookup: per-device agent-active check uses site_id + status + hostname/ip
CREATE INDEX IF NOT EXISTS idx_go_agents_site_status_host
ON go_agents(site_id, status) INCLUDE (hostname, ip_address);

-- discovered_devices IP fallback: when local_device_id doesn't match, falls back to IP
CREATE INDEX IF NOT EXISTS idx_discovered_devices_appliance_ip
ON discovered_devices(appliance_id, ip_address);

-- discovered_devices dedup: self-join DELETE on (appliance_id, ip_address, id)
CREATE INDEX IF NOT EXISTS idx_discovered_devices_appliance_ip_id
ON discovered_devices(appliance_id, ip_address, id);
