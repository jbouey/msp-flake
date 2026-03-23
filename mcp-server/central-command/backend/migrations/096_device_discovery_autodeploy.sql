-- Migration 096: Auto-discovery + auto-deploy columns on discovered_devices
-- Extends device tracking with OS probing, lifecycle state, and deploy tracking

ALTER TABLE discovered_devices ADD COLUMN IF NOT EXISTS os_fingerprint TEXT;
ALTER TABLE discovered_devices ADD COLUMN IF NOT EXISTS distro TEXT;
ALTER TABLE discovered_devices ADD COLUMN IF NOT EXISTS probe_ssh BOOLEAN DEFAULT FALSE;
ALTER TABLE discovered_devices ADD COLUMN IF NOT EXISTS probe_winrm BOOLEAN DEFAULT FALSE;
ALTER TABLE discovered_devices ADD COLUMN IF NOT EXISTS probe_snmp BOOLEAN DEFAULT FALSE;
ALTER TABLE discovered_devices ADD COLUMN IF NOT EXISTS ad_joined BOOLEAN DEFAULT FALSE;
ALTER TABLE discovered_devices ADD COLUMN IF NOT EXISTS device_status TEXT DEFAULT 'discovered';
ALTER TABLE discovered_devices ADD COLUMN IF NOT EXISTS agent_deploy_error TEXT;
ALTER TABLE discovered_devices ADD COLUMN IF NOT EXISTS agent_deploy_attempted_at TIMESTAMPTZ;
ALTER TABLE discovered_devices ADD COLUMN IF NOT EXISTS deploy_attempts INTEGER DEFAULT 0;
ALTER TABLE discovered_devices ADD COLUMN IF NOT EXISTS device_tag TEXT;
ALTER TABLE discovered_devices ADD COLUMN IF NOT EXISTS last_probe_at TIMESTAMPTZ;

-- Index for finding devices needing deployment
CREATE INDEX IF NOT EXISTS idx_discovered_devices_deploy_status
ON discovered_devices (device_status) WHERE device_status IN ('pending_deploy', 'deploying', 'ad_managed');

-- Index for finding unmanaged devices per site
CREATE INDEX IF NOT EXISTS idx_discovered_devices_unmanaged
ON discovered_devices (site_id, device_tag) WHERE device_tag IS NULL AND device_status = 'take_over_available';
