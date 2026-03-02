-- Migration 069: Organization-level shared credentials
-- Org credentials are inherited by all sites in the org.
-- Site-level credentials can override org credentials.

CREATE TABLE IF NOT EXISTS org_credentials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_org_id UUID NOT NULL REFERENCES client_orgs(id) ON DELETE CASCADE,
    credential_name VARCHAR(255) NOT NULL,
    credential_type VARCHAR(50) NOT NULL,  -- domain_admin, local_admin, winrm, ssh_password, ssh_key
    encrypted_data TEXT,  -- JSON: {host, username, password, domain, port, ...}
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_org_credentials_org ON org_credentials(client_org_id);

-- Track which site credentials override an org credential
ALTER TABLE site_credentials ADD COLUMN IF NOT EXISTS overrides_org_credential_id UUID REFERENCES org_credentials(id);
