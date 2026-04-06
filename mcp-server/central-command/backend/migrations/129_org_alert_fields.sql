-- Migration 129: Add alert routing fields to client_orgs
-- alert_email: where client-tier alerts are sent
-- cc_email: secondary recipient
-- client_alert_mode: self_service | informed | silent (default: informed)
-- welcome_email_sent_at: tracks one-time onboarding email

ALTER TABLE client_orgs
  ADD COLUMN IF NOT EXISTS alert_email VARCHAR(255),
  ADD COLUMN IF NOT EXISTS cc_email VARCHAR(255),
  ADD COLUMN IF NOT EXISTS client_alert_mode VARCHAR(20) DEFAULT 'informed',
  ADD COLUMN IF NOT EXISTS welcome_email_sent_at TIMESTAMPTZ;

-- Seed alert_email from existing primary_email where set
UPDATE client_orgs
SET alert_email = primary_email
WHERE alert_email IS NULL AND primary_email IS NOT NULL;

SELECT 'Migration 129_org_alert_fields completed successfully' AS status;
