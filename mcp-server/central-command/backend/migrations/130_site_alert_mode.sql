-- Migration 130: Per-site alert mode override
-- NULL = inherit from org

ALTER TABLE sites
  ADD COLUMN IF NOT EXISTS client_alert_mode VARCHAR(20);

SELECT 'Migration 130_site_alert_mode completed successfully' AS status;
