-- Migration 153: Appliance soft-delete.
--
-- Hard deleting site_appliances loses deployment history (first_checkin,
-- mesh audit, fleet order completions). Soft-delete preserves the row
-- and allows re-registration when the appliance checks in again.

ALTER TABLE site_appliances ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE site_appliances ADD COLUMN IF NOT EXISTS deleted_by VARCHAR(255);

-- Index for filtering out deleted appliances in normal queries
CREATE INDEX IF NOT EXISTS idx_site_appliances_active
ON site_appliances (site_id)
WHERE deleted_at IS NULL;
