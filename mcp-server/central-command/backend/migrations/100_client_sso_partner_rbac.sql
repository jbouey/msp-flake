-- Migration 100: Client Portal OIDC SSO + Partner RBAC Infrastructure

-- Per-org OIDC SSO configuration (one SSO config per client org)
CREATE TABLE IF NOT EXISTS client_org_sso (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    client_org_id UUID NOT NULL UNIQUE REFERENCES client_orgs(id) ON DELETE CASCADE,
    issuer_url TEXT NOT NULL,
    client_id TEXT NOT NULL,
    client_secret_encrypted BYTEA NOT NULL,
    allowed_domains TEXT[] NOT NULL DEFAULT '{}',
    sso_enforced BOOLEAN NOT NULL DEFAULT false,
    created_by_partner_id UUID REFERENCES partners(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- OIDC state tokens for PKCE flow (10-min TTL, single-use)
CREATE TABLE IF NOT EXISTS client_oauth_state (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    state_hash TEXT NOT NULL UNIQUE,
    code_verifier TEXT NOT NULL,
    nonce TEXT NOT NULL,
    client_org_id UUID NOT NULL REFERENCES client_orgs(id),
    redirect_uri TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_client_oauth_state_hash ON client_oauth_state(state_hash);

-- Link partner sessions to individual staff members for RBAC
ALTER TABLE partner_sessions ADD COLUMN IF NOT EXISTS partner_user_id UUID REFERENCES partner_users(id);

-- RLS: client_org_sso — admin bypass + tenant isolation by org
ALTER TABLE client_org_sso ENABLE ROW LEVEL SECURITY;
ALTER TABLE client_org_sso FORCE ROW LEVEL SECURITY;
DO $$ BEGIN
    CREATE POLICY admin_bypass_client_org_sso ON client_org_sso FOR ALL
        USING (current_setting('app.is_admin', true) = 'true');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
    CREATE POLICY tenant_client_org_sso ON client_org_sso FOR ALL
        USING (client_org_id::text = current_setting('app.current_org', true));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- RLS: client_oauth_state — admin-only (server-side consumption only)
ALTER TABLE client_oauth_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE client_oauth_state FORCE ROW LEVEL SECURITY;
DO $$ BEGIN
    CREATE POLICY admin_bypass_client_oauth_state ON client_oauth_state FOR ALL
        USING (current_setting('app.is_admin', true) = 'true');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Grant permissions to app role
GRANT SELECT, INSERT, UPDATE, DELETE ON client_org_sso TO mcp_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON client_oauth_state TO mcp_app;
