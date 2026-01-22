-- Migration: 026_partner_approval.sql
-- Partner OAuth Approval Workflow
--
-- New OAuth signups require admin approval before accessing dashboard.
-- Admins get notified via email (same as L3 alerts).

-- =============================================================================
-- ADD APPROVAL FIELDS TO PARTNERS TABLE
-- =============================================================================

-- Pending approval status (true = waiting for admin approval)
ALTER TABLE partners ADD COLUMN IF NOT EXISTS pending_approval BOOLEAN DEFAULT FALSE;

-- Who approved this partner (NULL if auto-approved or pending)
ALTER TABLE partners ADD COLUMN IF NOT EXISTS approved_by UUID REFERENCES admin_users(id);

-- When was this partner approved
ALTER TABLE partners ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ;

-- Domain allowlist for auto-approval (JSON array of domains)
-- If partner's email domain matches, they're auto-approved
-- Example: ['trustedmsp.com', 'partnerdomain.net']
ALTER TABLE partners ADD COLUMN IF NOT EXISTS auto_approved_domain BOOLEAN DEFAULT FALSE;

-- =============================================================================
-- PARTNER APPROVAL CONFIG TABLE
-- =============================================================================
CREATE TABLE IF NOT EXISTS partner_oauth_config (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Domain allowlist (partners from these domains are auto-approved)
    allowed_domains TEXT[] DEFAULT '{}',

    -- Require admin approval for new OAuth signups
    require_approval BOOLEAN DEFAULT TRUE,

    -- Allow consumer Gmail accounts (not just Workspace)
    allow_consumer_gmail BOOLEAN DEFAULT TRUE,

    -- Email addresses to notify on new partner signup
    -- Falls back to ALERT_EMAIL env var if empty
    notify_emails TEXT[] DEFAULT '{}',

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Insert default config if not exists
INSERT INTO partner_oauth_config (id, allowed_domains, require_approval, allow_consumer_gmail)
VALUES (gen_random_uuid(), '{}', TRUE, TRUE)
ON CONFLICT DO NOTHING;

-- =============================================================================
-- INDEXES
-- =============================================================================
CREATE INDEX IF NOT EXISTS idx_partners_pending ON partners(pending_approval) WHERE pending_approval = TRUE;

-- =============================================================================
-- COMMENTS
-- =============================================================================
COMMENT ON COLUMN partners.pending_approval IS 'True if partner is waiting for admin approval';
COMMENT ON COLUMN partners.approved_by IS 'Admin user who approved this partner';
COMMENT ON COLUMN partners.approved_at IS 'Timestamp when partner was approved';
COMMENT ON TABLE partner_oauth_config IS 'Configuration for partner OAuth signup workflow';
