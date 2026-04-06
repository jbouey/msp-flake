-- Add display_name to site_appliances for unique, human-friendly appliance naming.
-- When multiple appliances share the same OS hostname (e.g., all "osiriscare"),
-- the backend auto-generates iterative display names on checkin.

ALTER TABLE site_appliances
  ADD COLUMN IF NOT EXISTS display_name VARCHAR(128);

-- Backfill: for sites with multiple appliances sharing the same hostname,
-- assign iterative names (hostname, hostname-2, hostname-3, ...)
WITH ranked AS (
  SELECT
    appliance_id,
    hostname,
    site_id,
    ROW_NUMBER() OVER (PARTITION BY site_id, hostname ORDER BY first_checkin, appliance_id) AS rn,
    COUNT(*) OVER (PARTITION BY site_id, hostname) AS cnt
  FROM site_appliances
)
UPDATE site_appliances sa
SET display_name = CASE
  WHEN r.cnt = 1 THEN r.hostname
  WHEN r.rn = 1 THEN r.hostname
  ELSE r.hostname || '-' || r.rn
END
FROM ranked r
WHERE sa.appliance_id = r.appliance_id
  AND sa.display_name IS NULL;
