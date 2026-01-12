-- Cloud Integration Tables
-- Migration: 015_cloud_integrations.sql
-- Date: 2026-01-12
-- Purpose: Secure cloud integrations (AWS, Google Workspace, Okta, Azure AD)

-- =============================================================================
-- MAIN INTEGRATIONS TABLE
-- =============================================================================

CREATE TABLE IF NOT EXISTS integrations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    site_id UUID NOT NULL REFERENCES sites(id) ON DELETE CASCADE,

    -- Provider configuration
    provider VARCHAR(50) NOT NULL,  -- aws, google_workspace, okta, azure_ad
    name VARCHAR(255) NOT NULL,
    description TEXT,

    -- Status tracking
    status VARCHAR(30) DEFAULT 'pending',  -- pending, configuring, connected, error, disabled
    health_status VARCHAR(30) DEFAULT 'unknown',  -- healthy, degraded, unhealthy, unknown
    error_message TEXT,
    error_code VARCHAR(50),

    -- Encrypted credentials (HKDF per-integration key)
    credentials_encrypted BYTEA,
    credentials_version INTEGER DEFAULT 1,

    -- OAuth-specific fields
    oauth_state_hash VARCHAR(64),  -- Hash of state token for validation
    access_token_expires_at TIMESTAMPTZ,
    refresh_token_expires_at TIMESTAMPTZ,

    -- AWS-specific fields
    aws_account_id VARCHAR(20),
    aws_role_arn TEXT,
    aws_external_id VARCHAR(100),
    aws_regions TEXT[],  -- Regions to scan

    -- Google Workspace-specific
    google_domain VARCHAR(255),
    google_customer_id VARCHAR(50),

    -- Okta-specific
    okta_domain VARCHAR(255),
    okta_org_id VARCHAR(50),

    -- Azure AD-specific
    azure_tenant_id VARCHAR(50),
    azure_subscription_ids TEXT[],

    -- Sync configuration
    sync_enabled BOOLEAN DEFAULT true,
    sync_interval_minutes INTEGER DEFAULT 60,
    enabled_resource_types TEXT[],
    last_sync_at TIMESTAMPTZ,
    last_sync_success_at TIMESTAMPTZ,
    last_sync_duration_seconds INTEGER,
    consecutive_failures INTEGER DEFAULT 0,

    -- Resource statistics (denormalized for quick access)
    total_resources INTEGER DEFAULT 0,
    compliant_resources INTEGER DEFAULT 0,
    non_compliant_resources INTEGER DEFAULT 0,

    -- Metadata
    created_by UUID,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    -- Constraints
    CONSTRAINT valid_provider CHECK (provider IN ('aws', 'google_workspace', 'okta', 'azure_ad')),
    CONSTRAINT valid_status CHECK (status IN ('pending', 'configuring', 'connected', 'error', 'disabled')),
    CONSTRAINT valid_health CHECK (health_status IN ('healthy', 'degraded', 'unhealthy', 'unknown')),
    CONSTRAINT unique_integration_name UNIQUE (site_id, provider, name)
);

-- Indexes for integrations
CREATE INDEX IF NOT EXISTS idx_integrations_site ON integrations(site_id);
CREATE INDEX IF NOT EXISTS idx_integrations_provider ON integrations(provider);
CREATE INDEX IF NOT EXISTS idx_integrations_status ON integrations(status);
CREATE INDEX IF NOT EXISTS idx_integrations_health ON integrations(health_status);
CREATE INDEX IF NOT EXISTS idx_integrations_sync ON integrations(last_sync_at);

-- =============================================================================
-- INTEGRATION RESOURCES TABLE
-- =============================================================================

CREATE TABLE IF NOT EXISTS integration_resources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    integration_id UUID NOT NULL REFERENCES integrations(id) ON DELETE CASCADE,

    -- Resource identification
    resource_type VARCHAR(100) NOT NULL,  -- iam_user, s3_bucket, ec2_instance, etc.
    resource_id VARCHAR(500) NOT NULL,    -- AWS ARN, Google ID, etc.
    resource_name VARCHAR(500),
    resource_region VARCHAR(50),

    -- Compliance state
    compliance_status VARCHAR(30) DEFAULT 'unknown',  -- compliant, non_compliant, unknown
    risk_level VARCHAR(20),  -- critical, high, medium, low
    compliance_checks JSONB DEFAULT '[]'::jsonb,  -- Array of check results
    hipaa_controls TEXT[],  -- Mapped HIPAA control IDs
    framework_controls JSONB DEFAULT '{}'::jsonb,  -- {hipaa: [...], soc2: [...]}

    -- Resource data
    raw_data JSONB,  -- Provider-specific resource data
    metadata JSONB DEFAULT '{}'::jsonb,  -- Additional metadata

    -- Timestamps
    first_seen_at TIMESTAMPTZ DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ DEFAULT NOW(),
    last_checked_at TIMESTAMPTZ,
    compliance_changed_at TIMESTAMPTZ,

    -- Constraints
    CONSTRAINT valid_compliance_status CHECK (compliance_status IN ('compliant', 'non_compliant', 'unknown')),
    CONSTRAINT valid_risk_level CHECK (risk_level IS NULL OR risk_level IN ('critical', 'high', 'medium', 'low')),
    CONSTRAINT unique_resource UNIQUE (integration_id, resource_type, resource_id)
);

-- Indexes for resources
CREATE INDEX IF NOT EXISTS idx_resources_integration ON integration_resources(integration_id);
CREATE INDEX IF NOT EXISTS idx_resources_type ON integration_resources(resource_type);
CREATE INDEX IF NOT EXISTS idx_resources_compliance ON integration_resources(compliance_status);
CREATE INDEX IF NOT EXISTS idx_resources_risk ON integration_resources(risk_level);
CREATE INDEX IF NOT EXISTS idx_resources_last_seen ON integration_resources(last_seen_at);

-- =============================================================================
-- INTEGRATION AUDIT LOG (APPEND-ONLY)
-- =============================================================================

CREATE TABLE IF NOT EXISTS integration_audit_log (
    id BIGSERIAL PRIMARY KEY,
    integration_id UUID REFERENCES integrations(id) ON DELETE SET NULL,
    site_id UUID NOT NULL,

    -- Event details
    event_type VARCHAR(100) NOT NULL,
    event_category VARCHAR(50),  -- auth, sync, credential, config, error
    event_data JSONB DEFAULT '{}'::jsonb,

    -- Actor information
    actor_user_id UUID,
    actor_username VARCHAR(255),
    actor_ip INET,
    actor_user_agent TEXT,

    -- Request context
    request_id VARCHAR(50),
    request_path TEXT,

    -- Resources affected
    resources_affected JSONB,  -- [{type, id, name}]
    resource_count INTEGER DEFAULT 0,

    -- Timestamp (immutable after insert)
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for audit log
CREATE INDEX IF NOT EXISTS idx_audit_integration ON integration_audit_log(integration_id);
CREATE INDEX IF NOT EXISTS idx_audit_site ON integration_audit_log(site_id);
CREATE INDEX IF NOT EXISTS idx_audit_type ON integration_audit_log(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_category ON integration_audit_log(event_category);
CREATE INDEX IF NOT EXISTS idx_audit_time ON integration_audit_log(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON integration_audit_log(actor_user_id);

-- Prevent updates/deletes on audit log (append-only)
CREATE OR REPLACE FUNCTION prevent_audit_modification()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Audit log is append-only. Modifications are not allowed.';
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_log_immutable ON integration_audit_log;
CREATE TRIGGER audit_log_immutable
    BEFORE UPDATE OR DELETE ON integration_audit_log
    FOR EACH ROW
    EXECUTE FUNCTION prevent_audit_modification();

-- =============================================================================
-- INTEGRATION SYNC JOBS TABLE
-- =============================================================================

CREATE TABLE IF NOT EXISTS integration_sync_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    integration_id UUID NOT NULL REFERENCES integrations(id) ON DELETE CASCADE,

    -- Job status
    status VARCHAR(30) DEFAULT 'pending',  -- pending, running, completed, failed, cancelled
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    duration_seconds INTEGER,

    -- Results
    resources_found INTEGER DEFAULT 0,
    resources_created INTEGER DEFAULT 0,
    resources_updated INTEGER DEFAULT 0,
    resources_deleted INTEGER DEFAULT 0,
    compliance_checks_run INTEGER DEFAULT 0,
    non_compliant_found INTEGER DEFAULT 0,

    -- Error handling
    error_message TEXT,
    error_type VARCHAR(100),
    retry_count INTEGER DEFAULT 0,

    -- Metadata
    triggered_by VARCHAR(50),  -- scheduled, manual, webhook
    resource_types TEXT[],

    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for sync jobs
CREATE INDEX IF NOT EXISTS idx_sync_jobs_integration ON integration_sync_jobs(integration_id);
CREATE INDEX IF NOT EXISTS idx_sync_jobs_status ON integration_sync_jobs(status);
CREATE INDEX IF NOT EXISTS idx_sync_jobs_started ON integration_sync_jobs(started_at);

-- =============================================================================
-- VIEWS
-- =============================================================================

-- Integration health summary view
CREATE OR REPLACE VIEW v_integration_health AS
SELECT
    i.id AS integration_id,
    i.site_id,
    i.provider,
    i.name,
    i.status,
    i.health_status,
    i.last_sync_at,
    i.last_sync_success_at,
    i.consecutive_failures,
    i.total_resources,
    i.compliant_resources,
    i.non_compliant_resources,
    COALESCE(
        ROUND(
            (i.compliant_resources::DECIMAL / NULLIF(i.total_resources, 0)) * 100,
            1
        ),
        0
    ) AS compliance_percentage,
    COUNT(ir.id) FILTER (WHERE ir.risk_level = 'critical') AS critical_count,
    COUNT(ir.id) FILTER (WHERE ir.risk_level = 'high') AS high_count,
    COUNT(ir.id) FILTER (WHERE ir.risk_level = 'medium') AS medium_count,
    COUNT(ir.id) FILTER (WHERE ir.risk_level = 'low') AS low_count,
    CASE
        WHEN i.status = 'error' THEN 'critical'
        WHEN i.consecutive_failures >= 5 THEN 'critical'
        WHEN i.consecutive_failures >= 3 THEN 'degraded'
        WHEN i.last_sync_at < NOW() - INTERVAL '2 hours' THEN 'stale'
        WHEN i.status = 'connected' AND i.health_status = 'healthy' THEN 'healthy'
        ELSE 'unknown'
    END AS computed_health
FROM integrations i
LEFT JOIN integration_resources ir ON ir.integration_id = i.id
GROUP BY i.id;

-- Site integration summary view
CREATE OR REPLACE VIEW v_site_integration_summary AS
SELECT
    s.id AS site_id,
    s.clinic_name AS site_name,
    COUNT(DISTINCT i.id) AS integration_count,
    COUNT(DISTINCT i.id) FILTER (WHERE i.status = 'connected') AS connected_count,
    COUNT(DISTINCT i.id) FILTER (WHERE i.health_status = 'healthy') AS healthy_count,
    COUNT(DISTINCT i.id) FILTER (WHERE i.health_status = 'unhealthy' OR i.status = 'error') AS unhealthy_count,
    SUM(i.total_resources) AS total_resources,
    SUM(i.non_compliant_resources) AS non_compliant_resources,
    MAX(i.last_sync_at) AS last_sync_at
FROM sites s
LEFT JOIN integrations i ON i.site_id = s.id
GROUP BY s.id, s.clinic_name;

-- Recent compliance changes view
CREATE OR REPLACE VIEW v_recent_compliance_changes AS
SELECT
    ir.id AS resource_id,
    ir.integration_id,
    i.site_id,
    i.provider,
    ir.resource_type,
    ir.resource_id AS external_id,
    ir.resource_name,
    ir.compliance_status,
    ir.risk_level,
    ir.compliance_changed_at,
    ir.compliance_checks
FROM integration_resources ir
JOIN integrations i ON i.id = ir.integration_id
WHERE ir.compliance_changed_at IS NOT NULL
  AND ir.compliance_changed_at > NOW() - INTERVAL '24 hours'
ORDER BY ir.compliance_changed_at DESC;

-- =============================================================================
-- FUNCTIONS
-- =============================================================================

-- Function to update integration stats
CREATE OR REPLACE FUNCTION update_integration_stats(p_integration_id UUID)
RETURNS void AS $$
BEGIN
    UPDATE integrations
    SET
        total_resources = (
            SELECT COUNT(*) FROM integration_resources
            WHERE integration_id = p_integration_id
        ),
        compliant_resources = (
            SELECT COUNT(*) FROM integration_resources
            WHERE integration_id = p_integration_id
              AND compliance_status = 'compliant'
        ),
        non_compliant_resources = (
            SELECT COUNT(*) FROM integration_resources
            WHERE integration_id = p_integration_id
              AND compliance_status = 'non_compliant'
        ),
        updated_at = NOW()
    WHERE id = p_integration_id;
END;
$$ LANGUAGE plpgsql;

-- Trigger to update integration timestamp
CREATE OR REPLACE FUNCTION update_integration_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS integration_updated ON integrations;
CREATE TRIGGER integration_updated
    BEFORE UPDATE ON integrations
    FOR EACH ROW
    EXECUTE FUNCTION update_integration_timestamp();

-- Trigger to track compliance changes
CREATE OR REPLACE FUNCTION track_compliance_change()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.compliance_status IS DISTINCT FROM NEW.compliance_status THEN
        NEW.compliance_changed_at = NOW();
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS resource_compliance_changed ON integration_resources;
CREATE TRIGGER resource_compliance_changed
    BEFORE UPDATE ON integration_resources
    FOR EACH ROW
    EXECUTE FUNCTION track_compliance_change();

-- =============================================================================
-- COMMENTS
-- =============================================================================

COMMENT ON TABLE integrations IS 'Cloud service integrations (AWS, Google, Okta, Azure) for compliance monitoring';
COMMENT ON TABLE integration_resources IS 'Resources discovered from cloud integrations with compliance status';
COMMENT ON TABLE integration_audit_log IS 'Append-only audit log for integration operations (HIPAA 164.312(b))';
COMMENT ON TABLE integration_sync_jobs IS 'Tracking for integration sync operations';

COMMENT ON COLUMN integrations.credentials_encrypted IS 'Encrypted with per-integration HKDF-derived key';
COMMENT ON COLUMN integrations.aws_external_id IS 'ExternalId for STS AssumeRole (confused deputy protection)';
COMMENT ON COLUMN integration_resources.compliance_checks IS 'JSON array of individual check results with framework mappings';
COMMENT ON COLUMN integration_resources.hipaa_controls IS 'HIPAA control IDs mapped from failed checks';

-- =============================================================================
-- GRANT PERMISSIONS
-- =============================================================================

-- Assuming 'msp' is the application user
GRANT SELECT, INSERT, UPDATE, DELETE ON integrations TO msp;
GRANT SELECT, INSERT, UPDATE, DELETE ON integration_resources TO msp;
GRANT SELECT, INSERT ON integration_audit_log TO msp;  -- No UPDATE/DELETE
GRANT SELECT, INSERT, UPDATE, DELETE ON integration_sync_jobs TO msp;
GRANT USAGE, SELECT ON SEQUENCE integration_audit_log_id_seq TO msp;

-- Grant view access
GRANT SELECT ON v_integration_health TO msp;
GRANT SELECT ON v_site_integration_summary TO msp;
GRANT SELECT ON v_recent_compliance_changes TO msp;
