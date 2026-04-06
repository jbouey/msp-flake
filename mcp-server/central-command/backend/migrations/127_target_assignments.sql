-- Track server-side target assignments per appliance.
ALTER TABLE site_appliances
  ADD COLUMN IF NOT EXISTS assigned_targets JSONB DEFAULT '[]',
  ADD COLUMN IF NOT EXISTS assignment_epoch BIGINT DEFAULT 0;
