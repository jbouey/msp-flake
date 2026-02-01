-- Migration 034: Add SSH key provisioning support
-- Session 82: Zero-friction SSH key provisioning for appliances
--
-- Adds ssh_authorized_keys column to appliance_provisioning table
-- This allows SSH keys to be distributed during MAC-based auto-provisioning

-- UP
ALTER TABLE appliance_provisioning
ADD COLUMN IF NOT EXISTS ssh_authorized_keys TEXT[] DEFAULT '{}';

-- Also add to sites table for site-level SSH keys
ALTER TABLE sites
ADD COLUMN IF NOT EXISTS ssh_authorized_keys TEXT[] DEFAULT '{}';

-- Add index for faster lookups
CREATE INDEX IF NOT EXISTS idx_provisioning_ssh ON appliance_provisioning (mac_address)
WHERE ssh_authorized_keys IS NOT NULL AND array_length(ssh_authorized_keys, 1) > 0;

COMMENT ON COLUMN appliance_provisioning.ssh_authorized_keys IS
'SSH public keys to provision on appliance first boot. Array of authorized_keys format strings.';

COMMENT ON COLUMN sites.ssh_authorized_keys IS
'SSH public keys for all appliances at this site. Merged with appliance-specific keys.';

-- DOWN (for rollback)
-- ALTER TABLE appliance_provisioning DROP COLUMN IF EXISTS ssh_authorized_keys;
-- ALTER TABLE sites DROP COLUMN IF EXISTS ssh_authorized_keys;
-- DROP INDEX IF EXISTS idx_provisioning_ssh;
