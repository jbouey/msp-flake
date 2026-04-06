-- Partner notification table for org-health alerts and non-engagement escalation.

CREATE TABLE IF NOT EXISTS partner_notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    partner_id UUID NOT NULL REFERENCES partners(id) ON DELETE CASCADE,
    org_id UUID REFERENCES client_orgs(id) ON DELETE SET NULL,
    notification_type VARCHAR(50) NOT NULL,
    summary TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    read_at TIMESTAMPTZ,
    escalated_to_admin_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_partner_notifications_unread
  ON partner_notifications(partner_id, created_at DESC)
  WHERE read_at IS NULL;

-- RLS
ALTER TABLE partner_notifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE partner_notifications FORCE ROW LEVEL SECURITY;

CREATE POLICY partner_notifications_admin ON partner_notifications
  FOR ALL
  USING (current_setting('app.is_admin', true) = 'true');

GRANT ALL ON partner_notifications TO mcp;
GRANT SELECT, INSERT, UPDATE ON partner_notifications TO mcp_app;

SELECT 'Migration 133_partner_notifications completed successfully' AS status;
