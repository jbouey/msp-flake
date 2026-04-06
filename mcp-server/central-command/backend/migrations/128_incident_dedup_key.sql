-- Migration 128: Add cross-appliance dedup key to incidents
-- dedup_key = SHA256(site_id || ':' || incident_type || ':' || hostname)
-- Enables dedup across appliances reporting the same issue on the same host.

ALTER TABLE incidents ADD COLUMN IF NOT EXISTS dedup_key VARCHAR(64);

CREATE INDEX IF NOT EXISTS idx_incidents_dedup_key
  ON incidents(dedup_key)
  WHERE dedup_key IS NOT NULL;

-- Backfill open/resolving/escalated incidents
UPDATE incidents
SET dedup_key = encode(
  sha256(
    (COALESCE(site_id::text, '') || ':' || COALESCE(incident_type, '') || ':' || COALESCE(details->>'hostname', ''))::bytea
  ),
  'hex'
)
WHERE status IN ('open', 'resolving', 'escalated')
  AND dedup_key IS NULL;

SELECT 'Migration 128_incident_dedup_key completed successfully' AS status;
