-- Migration 132: Client approval audit trail
-- Every approve/dismiss/acknowledge action by a client user is recorded here
-- for HIPAA accountability.

CREATE TABLE IF NOT EXISTS client_approvals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES client_orgs(id) ON DELETE CASCADE,
    site_id VARCHAR NOT NULL REFERENCES sites(site_id) ON DELETE CASCADE,
    incident_id UUID REFERENCES incidents(id) ON DELETE SET NULL,
    alert_id UUID NOT NULL REFERENCES pending_alerts(id) ON DELETE CASCADE,
    action VARCHAR(20) NOT NULL,
    acted_by UUID NOT NULL,
    acted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_client_approvals_incident
  ON client_approvals(incident_id);

CREATE INDEX IF NOT EXISTS idx_client_approvals_org
  ON client_approvals(org_id, acted_at DESC);

-- ============================================================================
-- RLS (matches project pattern — org_id tenant isolation)
-- ============================================================================

ALTER TABLE client_approvals ENABLE ROW LEVEL SECURITY;
ALTER TABLE client_approvals FORCE ROW LEVEL SECURITY;

-- Admin bypass
CREATE POLICY client_approvals_admin ON client_approvals
  FOR ALL
  USING (current_setting('app.is_admin', true) = 'true');

-- Tenant isolation
CREATE POLICY client_approvals_org ON client_approvals
  FOR ALL
  USING (org_id::text = current_setting('app.current_org', true));

-- ============================================================================
-- PERMISSIONS
-- ============================================================================

GRANT ALL ON client_approvals TO mcp;
GRANT SELECT, INSERT, UPDATE ON client_approvals TO mcp_app;

SELECT 'Migration 132_client_approvals completed successfully' AS status;
