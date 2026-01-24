-- Migration: 029_client_portal.sql
-- Description: Client portal tables for healthcare practice direct access
-- Created: 2025-01-24

-- =============================================================================
-- Client Organizations (Healthcare Practices)
-- =============================================================================
CREATE TABLE IF NOT EXISTS client_orgs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,

    -- Contact info
    primary_email VARCHAR(255) NOT NULL,
    primary_phone VARCHAR(50),
    address_line1 VARCHAR(255),
    address_line2 VARCHAR(255),
    city VARCHAR(100),
    state VARCHAR(50),
    postal_code VARCHAR(20),

    -- Business details
    npi_number VARCHAR(20),  -- National Provider Identifier
    tax_id VARCHAR(20),
    practice_type VARCHAR(100),  -- e.g., 'dental', 'medical', 'mental_health'
    provider_count INTEGER DEFAULT 1,

    -- Current MSP relationship
    current_partner_id UUID REFERENCES partners(id) ON DELETE SET NULL,
    partner_assigned_at TIMESTAMPTZ,

    -- Billing (Phase 3)
    stripe_customer_id VARCHAR(255),
    billing_email VARCHAR(255),

    -- Status
    status VARCHAR(50) NOT NULL DEFAULT 'active',  -- active, suspended, churned
    onboarded_at TIMESTAMPTZ,

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_client_orgs_partner ON client_orgs(current_partner_id);
CREATE INDEX idx_client_orgs_status ON client_orgs(status);
CREATE UNIQUE INDEX idx_client_orgs_email ON client_orgs(primary_email);

-- =============================================================================
-- Client Users (Portal Access)
-- =============================================================================
CREATE TABLE IF NOT EXISTS client_users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_org_id UUID NOT NULL REFERENCES client_orgs(id) ON DELETE CASCADE,

    -- Identity
    email VARCHAR(255) NOT NULL,
    name VARCHAR(255),

    -- Auth - magic link primary, password optional
    password_hash VARCHAR(255),  -- bcrypt hash, NULL = magic link only
    magic_token VARCHAR(255),
    magic_token_expires_at TIMESTAMPTZ,

    -- Role: owner (full control), admin (manage users), viewer (read-only)
    role VARCHAR(50) NOT NULL DEFAULT 'viewer',

    -- Status
    is_active BOOLEAN NOT NULL DEFAULT true,
    email_verified BOOLEAN NOT NULL DEFAULT false,
    last_login_at TIMESTAMPTZ,

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT valid_role CHECK (role IN ('owner', 'admin', 'viewer'))
);

CREATE UNIQUE INDEX idx_client_users_email ON client_users(email);
CREATE INDEX idx_client_users_org ON client_users(client_org_id);
CREATE INDEX idx_client_users_magic_token ON client_users(magic_token) WHERE magic_token IS NOT NULL;

-- =============================================================================
-- Client Sessions (httpOnly cookie-based)
-- =============================================================================
CREATE TABLE IF NOT EXISTS client_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES client_users(id) ON DELETE CASCADE,

    -- Token stored as HMAC-SHA256 hash
    token_hash VARCHAR(64) NOT NULL,

    -- Session metadata
    user_agent TEXT,
    ip_address INET,

    -- 30-day sessions
    expires_at TIMESTAMPTZ NOT NULL,
    last_activity_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_client_sessions_token ON client_sessions(token_hash);
CREATE INDEX idx_client_sessions_user ON client_sessions(user_id);
CREATE INDEX idx_client_sessions_expires ON client_sessions(expires_at);

-- =============================================================================
-- Client Notifications (Direct Alerts from OsirisCare)
-- =============================================================================
CREATE TABLE IF NOT EXISTS client_notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_org_id UUID NOT NULL REFERENCES client_orgs(id) ON DELETE CASCADE,

    -- Content
    type VARCHAR(50) NOT NULL,  -- compliance_alert, monthly_report, audit_reminder, system
    severity VARCHAR(20) NOT NULL DEFAULT 'info',  -- info, warning, critical
    title VARCHAR(255) NOT NULL,
    message TEXT NOT NULL,

    -- Optional link
    action_url VARCHAR(500),
    action_label VARCHAR(100),

    -- Read tracking (per-org, not per-user)
    is_read BOOLEAN NOT NULL DEFAULT false,
    read_at TIMESTAMPTZ,
    read_by_user_id UUID REFERENCES client_users(id),

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT valid_severity CHECK (severity IN ('info', 'warning', 'critical'))
);

CREATE INDEX idx_client_notifications_org ON client_notifications(client_org_id);
CREATE INDEX idx_client_notifications_unread ON client_notifications(client_org_id, is_read) WHERE NOT is_read;
CREATE INDEX idx_client_notifications_created ON client_notifications(created_at DESC);

-- =============================================================================
-- Client Invites (User Invitation Tokens)
-- =============================================================================
CREATE TABLE IF NOT EXISTS client_invites (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_org_id UUID NOT NULL REFERENCES client_orgs(id) ON DELETE CASCADE,

    -- Invite details
    email VARCHAR(255) NOT NULL,
    role VARCHAR(50) NOT NULL DEFAULT 'viewer',

    -- Token (stored as hash)
    token_hash VARCHAR(64) NOT NULL,

    -- Invited by
    invited_by_user_id UUID NOT NULL REFERENCES client_users(id),

    -- Status
    expires_at TIMESTAMPTZ NOT NULL,
    accepted_at TIMESTAMPTZ,

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT valid_invite_role CHECK (role IN ('admin', 'viewer'))
);

CREATE UNIQUE INDEX idx_client_invites_token ON client_invites(token_hash);
CREATE INDEX idx_client_invites_org ON client_invites(client_org_id);
CREATE INDEX idx_client_invites_email ON client_invites(email);

-- =============================================================================
-- Partner Transfer Requests (Phase 3 - Power Move)
-- =============================================================================
CREATE TABLE IF NOT EXISTS partner_transfer_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_org_id UUID NOT NULL REFERENCES client_orgs(id) ON DELETE CASCADE,

    -- Transfer from/to
    from_partner_id UUID REFERENCES partners(id),
    to_partner_id UUID REFERENCES partners(id),  -- NULL = direct OsirisCare relationship

    -- Request details
    reason TEXT,
    requested_by_user_id UUID NOT NULL REFERENCES client_users(id),

    -- Status: pending, approved, rejected, completed, cancelled
    status VARCHAR(50) NOT NULL DEFAULT 'pending',

    -- Processing
    reviewed_at TIMESTAMPTZ,
    reviewed_by VARCHAR(255),  -- OsirisCare staff
    review_notes TEXT,
    completed_at TIMESTAMPTZ,

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT valid_transfer_status CHECK (status IN ('pending', 'approved', 'rejected', 'completed', 'cancelled'))
);

CREATE INDEX idx_transfer_requests_org ON partner_transfer_requests(client_org_id);
CREATE INDEX idx_transfer_requests_status ON partner_transfer_requests(status);
CREATE INDEX idx_transfer_requests_from ON partner_transfer_requests(from_partner_id);

-- =============================================================================
-- Link Sites to Client Orgs
-- =============================================================================
ALTER TABLE sites ADD COLUMN IF NOT EXISTS client_org_id UUID REFERENCES client_orgs(id);
CREATE INDEX IF NOT EXISTS idx_sites_client_org ON sites(client_org_id);

-- =============================================================================
-- Monthly Compliance Reports (Client-facing)
-- =============================================================================
CREATE TABLE IF NOT EXISTS client_monthly_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_org_id UUID NOT NULL REFERENCES client_orgs(id) ON DELETE CASCADE,

    -- Report period
    report_month DATE NOT NULL,  -- First day of month

    -- Generated content
    pdf_path VARCHAR(500),  -- MinIO path
    pdf_hash VARCHAR(64),   -- SHA-256 for verification

    -- Summary stats (cached for quick display)
    overall_score DECIMAL(5,2),
    controls_passed INTEGER,
    controls_failed INTEGER,
    controls_total INTEGER,
    incidents_count INTEGER,
    incidents_auto_healed INTEGER,

    -- Generation metadata
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT unique_monthly_report UNIQUE (client_org_id, report_month)
);

CREATE INDEX idx_monthly_reports_org ON client_monthly_reports(client_org_id);
CREATE INDEX idx_monthly_reports_month ON client_monthly_reports(report_month DESC);

-- =============================================================================
-- Updated timestamp trigger
-- =============================================================================
CREATE OR REPLACE FUNCTION update_client_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_client_orgs_updated_at
    BEFORE UPDATE ON client_orgs
    FOR EACH ROW
    EXECUTE FUNCTION update_client_updated_at();

CREATE TRIGGER trigger_client_users_updated_at
    BEFORE UPDATE ON client_users
    FOR EACH ROW
    EXECUTE FUNCTION update_client_updated_at();

CREATE TRIGGER trigger_transfer_requests_updated_at
    BEFORE UPDATE ON partner_transfer_requests
    FOR EACH ROW
    EXECUTE FUNCTION update_client_updated_at();

-- =============================================================================
-- Comments for documentation
-- =============================================================================
COMMENT ON TABLE client_orgs IS 'Healthcare practices with direct OsirisCare relationship';
COMMENT ON TABLE client_users IS 'Portal users for client organizations';
COMMENT ON TABLE client_sessions IS 'Cookie-based sessions with 30-day expiry';
COMMENT ON TABLE client_notifications IS 'Direct alerts and notifications to clients';
COMMENT ON TABLE client_invites IS 'Pending user invitations';
COMMENT ON TABLE partner_transfer_requests IS 'Workflow for changing MSP partners';
COMMENT ON TABLE client_monthly_reports IS 'Cached monthly compliance report summaries';
COMMENT ON COLUMN sites.client_org_id IS 'Links site to owning client organization';
