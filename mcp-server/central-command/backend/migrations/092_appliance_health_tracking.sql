-- Migration 092: Appliance health tracking columns
-- Adds offline detection and notification tracking to site_appliances

ALTER TABLE site_appliances ADD COLUMN IF NOT EXISTS offline_since TIMESTAMPTZ;
ALTER TABLE site_appliances ADD COLUMN IF NOT EXISTS offline_notified BOOLEAN DEFAULT false;

-- Index for the health monitor query (find appliances that stopped checking in)
CREATE INDEX IF NOT EXISTS idx_site_appliances_last_checkin
    ON site_appliances (last_checkin)
    WHERE last_checkin IS NOT NULL;

COMMENT ON COLUMN site_appliances.offline_since IS 'Timestamp when appliance was first detected offline (NULL = online)';
COMMENT ON COLUMN site_appliances.offline_notified IS 'Whether offline notification has been sent for current offline period';
