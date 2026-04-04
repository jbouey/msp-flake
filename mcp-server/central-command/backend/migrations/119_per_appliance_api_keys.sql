-- Migration 119: Per-appliance API keys
--
-- Moves API key ownership from site-level to appliance-level.
-- Each appliance gets its own key so rekey on one appliance
-- doesn't invalidate siblings on the same site.

-- Add appliance_id column (nullable for backward compat during transition)
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS appliance_id TEXT;

-- Create index for auth lookups by appliance
CREATE INDEX IF NOT EXISTS idx_api_keys_appliance_active
    ON api_keys (appliance_id, active) WHERE active = true;

-- Backfill: assign existing active keys to the most-recently-checked-in
-- appliance for each site. This handles the common case of 1 appliance per site.
-- For multi-appliance sites, the first appliance to rekey will get its own key.
UPDATE api_keys ak
SET appliance_id = (
    SELECT sa.appliance_id
    FROM site_appliances sa
    WHERE sa.site_id = ak.site_id
    ORDER BY sa.last_checkin DESC NULLS LAST
    LIMIT 1
)
WHERE ak.appliance_id IS NULL
  AND ak.active = true;
