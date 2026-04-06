-- Per-appliance signing keys for multi-appliance evidence verification.
-- Previously: single agent_public_key on sites table, last checkin wins.
-- Now: each appliance registers its own key during checkin.

ALTER TABLE site_appliances
  ADD COLUMN IF NOT EXISTS agent_public_key VARCHAR(128);

-- Backfill: copy the site-level key to the most recently active appliance
UPDATE site_appliances sa
SET agent_public_key = s.agent_public_key
FROM sites s
WHERE sa.site_id = s.site_id
  AND s.agent_public_key IS NOT NULL
  AND sa.agent_public_key IS NULL
  AND sa.last_checkin = (
    SELECT MAX(last_checkin) FROM site_appliances
    WHERE site_id = sa.site_id
  );
