-- Migration 123: Compliance attestations for administrative/physical HIPAA controls
-- Organizations attest to controls that cannot be verified by automated scanning.
-- The compliance packet records attestation status for each reporting period.

CREATE TABLE IF NOT EXISTS compliance_attestations (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    site_id VARCHAR(50) REFERENCES sites(site_id) ON DELETE CASCADE,
    control_id VARCHAR(50) NOT NULL,
    control_name VARCHAR(200),
    attested_by VARCHAR(200),
    attested_at TIMESTAMPTZ DEFAULT NOW(),
    evidence_notes TEXT,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_attestations_site_control
    ON compliance_attestations(site_id, control_id, attested_at DESC);

-- RLS
ALTER TABLE compliance_attestations ENABLE ROW LEVEL SECURITY;
ALTER TABLE compliance_attestations FORCE ROW LEVEL SECURITY;

CREATE POLICY attestations_tenant ON compliance_attestations
    USING (
        CASE WHEN current_setting('app.is_admin', true) = 'true' THEN true
        ELSE site_id = current_setting('app.current_tenant', true) END
    );

GRANT SELECT, INSERT, UPDATE, DELETE ON compliance_attestations TO mcp_app;
