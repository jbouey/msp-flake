-- Migration 037: Evidence signing diagnostics
-- Session 89: Fix evidence signature verification + partner error handling
--
-- Tracks evidence acceptance/rejection per appliance so partners can see
-- evidence chain health on the dashboard. Reset on successful submission.

-- UP
ALTER TABLE site_appliances
ADD COLUMN IF NOT EXISTS evidence_rejection_count INT DEFAULT 0;

ALTER TABLE site_appliances
ADD COLUMN IF NOT EXISTS last_evidence_rejection TIMESTAMPTZ;

ALTER TABLE site_appliances
ADD COLUMN IF NOT EXISTS last_evidence_accepted TIMESTAMPTZ;

COMMENT ON COLUMN site_appliances.evidence_rejection_count IS
'Consecutive evidence signature rejections. Reset to 0 on successful submission.';

COMMENT ON COLUMN site_appliances.last_evidence_rejection IS
'Timestamp of last evidence signature rejection. Partners see this on dashboard.';

COMMENT ON COLUMN site_appliances.last_evidence_accepted IS
'Timestamp of last successfully verified evidence submission.';

-- DOWN (for rollback)
-- ALTER TABLE site_appliances DROP COLUMN IF EXISTS evidence_rejection_count;
-- ALTER TABLE site_appliances DROP COLUMN IF EXISTS last_evidence_rejection;
-- ALTER TABLE site_appliances DROP COLUMN IF EXISTS last_evidence_accepted;
