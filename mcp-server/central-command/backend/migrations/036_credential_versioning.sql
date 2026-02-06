-- Migration 036: Add credential versioning to site_appliances
-- Session 89: PHI boundary enforcement - conditional credential delivery
--
-- Tracks when credentials were last provisioned to each appliance and a
-- version counter so the server can skip credential delivery when the
-- appliance already has the latest version cached locally.

-- UP
ALTER TABLE site_appliances
ADD COLUMN IF NOT EXISTS credentials_provisioned_at TIMESTAMPTZ;

ALTER TABLE site_appliances
ADD COLUMN IF NOT EXISTS credentials_version INT DEFAULT 0;

-- Track credential version on the site_credentials table too, so we can
-- compare appliance version vs current version to decide if re-provisioning
-- is needed.
ALTER TABLE site_credentials
ADD COLUMN IF NOT EXISTS version INT DEFAULT 1;

-- Index for quick lookup during checkin
CREATE INDEX IF NOT EXISTS idx_appliances_cred_version
ON site_appliances (site_id, credentials_version);

COMMENT ON COLUMN site_appliances.credentials_provisioned_at IS
'Timestamp of last credential delivery to this appliance. NULL = never provisioned.';

COMMENT ON COLUMN site_appliances.credentials_version IS
'Version of credentials currently on the appliance. Compared against site_credentials.version.';

COMMENT ON COLUMN site_credentials.version IS
'Monotonically increasing version number. Bumped on credential update to trigger re-provisioning.';

-- DOWN (for rollback)
-- ALTER TABLE site_appliances DROP COLUMN IF EXISTS credentials_provisioned_at;
-- ALTER TABLE site_appliances DROP COLUMN IF EXISTS credentials_version;
-- ALTER TABLE site_credentials DROP COLUMN IF EXISTS version;
-- DROP INDEX IF EXISTS idx_appliances_cred_version;
