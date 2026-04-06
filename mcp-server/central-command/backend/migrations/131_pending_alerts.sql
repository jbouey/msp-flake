-- Migration 131: Pending alerts digest buffer
-- Alerts enqueued here, batched into digest emails per org on a 4-hour cycle.

CREATE TABLE IF NOT EXISTS pending_alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES client_orgs(id) ON DELETE CASCADE,
    site_id VARCHAR NOT NULL REFERENCES sites(site_id) ON DELETE CASCADE,
    alert_type VARCHAR(50) NOT NULL,
    severity VARCHAR(20) DEFAULT 'medium',
    summary TEXT NOT NULL,
    incident_id UUID REFERENCES incidents(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_at TIMESTAMPTZ,
    dismissed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_pending_alerts_unsent
  ON pending_alerts(org_id, created_at)
  WHERE sent_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_pending_alerts_org_recent
  ON pending_alerts(org_id, created_at DESC);

-- ============================================================================
-- RLS (matches project pattern — org_id tenant isolation)
-- ============================================================================

ALTER TABLE pending_alerts ENABLE ROW LEVEL SECURITY;
ALTER TABLE pending_alerts FORCE ROW LEVEL SECURITY;

-- Admin bypass
CREATE POLICY pending_alerts_admin ON pending_alerts
  FOR ALL
  USING (current_setting('app.is_admin', true) = 'true');

-- Tenant isolation
CREATE POLICY pending_alerts_org ON pending_alerts
  FOR ALL
  USING (org_id::text = current_setting('app.current_org', true));

-- ============================================================================
-- PERMISSIONS
-- ============================================================================

GRANT ALL ON pending_alerts TO mcp;
GRANT SELECT, INSERT, UPDATE ON pending_alerts TO mcp_app;

SELECT 'Migration 131_pending_alerts completed successfully' AS status;
