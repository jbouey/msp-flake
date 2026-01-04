-- Migration: 004_discovery_and_credentials.sql
-- Adds network discovery, asset management, and credential storage
--
-- Depends on: 003_partner_infrastructure.sql

BEGIN;

-- ============================================================
-- ADDITIONAL COLUMNS FOR SITES
-- ============================================================

ALTER TABLE sites ADD COLUMN IF NOT EXISTS client_name VARCHAR(255);
ALTER TABLE sites ADD COLUMN IF NOT EXISTS client_contact_email VARCHAR(255);
ALTER TABLE sites ADD COLUMN IF NOT EXISTS monthly_price_cents INTEGER DEFAULT 0;
ALTER TABLE sites ADD COLUMN IF NOT EXISTS notes TEXT;
ALTER TABLE sites ADD COLUMN IF NOT EXISTS hardware_id VARCHAR(255);
ALTER TABLE sites ADD COLUMN IF NOT EXISTS public_key TEXT;

-- ============================================================
-- ADDITIONAL COLUMNS FOR APPLIANCE_PROVISIONS
-- ============================================================

ALTER TABLE appliance_provisions ADD COLUMN IF NOT EXISTS client_contact_email VARCHAR(255);
ALTER TABLE appliance_provisions ADD COLUMN IF NOT EXISTS network_range VARCHAR(50);
ALTER TABLE appliance_provisions ADD COLUMN IF NOT EXISTS notes TEXT;
ALTER TABLE appliance_provisions ADD COLUMN IF NOT EXISTS claimed_hardware_id VARCHAR(255);
ALTER TABLE appliance_provisions ADD COLUMN IF NOT EXISTS claimed_by_site_id UUID REFERENCES sites(id);

-- Rename columns to match expected schema
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'appliance_provisions' AND column_name = 'target_client_name') THEN
        ALTER TABLE appliance_provisions RENAME COLUMN target_client_name TO client_name;
    END IF;
END $$;

-- Add client_name if rename didn't happen (column didn't exist)
ALTER TABLE appliance_provisions ADD COLUMN IF NOT EXISTS client_name VARCHAR(255);

-- ============================================================
-- SITE CREDENTIALS (encrypted storage)
-- ============================================================

CREATE TABLE IF NOT EXISTS site_credentials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    site_id UUID NOT NULL REFERENCES sites(id) ON DELETE CASCADE,

    -- Credential info
    name VARCHAR(100) NOT NULL,  -- "Primary Domain Creds", "Backup Service Account"
    credential_type VARCHAR(30) NOT NULL,  -- domain_admin, service_account, local_admin

    -- Values (encrypt in application layer with Fernet)
    domain VARCHAR(100),
    username VARCHAR(255) NOT NULL,
    password_encrypted TEXT NOT NULL,  -- Fernet encrypted

    -- Validation status
    is_primary BOOLEAN DEFAULT false,
    last_validated_at TIMESTAMPTZ,
    validation_status VARCHAR(20) DEFAULT 'untested',  -- untested, valid, partial, invalid
    validation_details JSONB,  -- {can_read_ad: true, servers_accessible: [...]}

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- DISCOVERED ASSETS (from network scans)
-- ============================================================

CREATE TABLE IF NOT EXISTS discovered_assets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    site_id UUID NOT NULL REFERENCES sites(id) ON DELETE CASCADE,

    -- Discovery info
    ip_address INET NOT NULL,
    hostname VARCHAR(255),
    mac_address VARCHAR(17),

    -- Classification
    asset_type VARCHAR(50),  -- domain_controller, file_server, sql_server, backup_server, workstation, unknown
    os_info VARCHAR(255),  -- "Windows Server 2019 Standard"
    confidence FLOAT DEFAULT 0.5,  -- 0.0-1.0 confidence in classification
    discovery_method VARCHAR(50),  -- port_scan, ad_query, dns_srv, netbios, manual

    -- Service detection
    open_ports INTEGER[],
    detected_services JSONB,  -- {"389": "ldap", "1433": "mssql"}

    -- Monitoring
    monitoring_status VARCHAR(20) DEFAULT 'discovered',  -- discovered, monitored, ignored, unreachable
    credential_id UUID REFERENCES site_credentials(id),

    -- Health
    last_seen_at TIMESTAMPTZ,
    last_check_at TIMESTAMPTZ,
    last_check_status VARCHAR(20),  -- healthy, degraded, critical, unknown

    -- AD integration
    ad_info JSONB,  -- {ou: "...", description: "...", spns: [...]}

    first_seen_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(site_id, ip_address)
);

-- ============================================================
-- DISCOVERY SCANS (history and status)
-- ============================================================

CREATE TABLE IF NOT EXISTS discovery_scans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    site_id UUID NOT NULL REFERENCES sites(id) ON DELETE CASCADE,

    scan_type VARCHAR(20) NOT NULL,  -- full, incremental, ad_only, port_only
    triggered_by VARCHAR(20) DEFAULT 'scheduled',  -- scheduled, manual, provision

    started_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    status VARCHAR(20) DEFAULT 'running',  -- running, completed, failed

    -- Results
    network_range_scanned VARCHAR(50),
    assets_found INTEGER DEFAULT 0,
    new_assets INTEGER DEFAULT 0,
    changed_assets INTEGER DEFAULT 0,
    missing_assets INTEGER DEFAULT 0,

    -- Details
    scan_log JSONB,
    error_message TEXT
);

-- ============================================================
-- ADDITIONAL PARTNER COLUMNS
-- ============================================================

ALTER TABLE partners ADD COLUMN IF NOT EXISTS contact_name VARCHAR(255);
ALTER TABLE partners ADD COLUMN IF NOT EXISTS billing_email VARCHAR(255);
ALTER TABLE partners ADD COLUMN IF NOT EXISTS api_key VARCHAR(64) UNIQUE;
ALTER TABLE partners ADD COLUMN IF NOT EXISTS onboarded_at TIMESTAMPTZ;

-- ============================================================
-- ADDITIONAL PARTNER_USERS COLUMNS
-- ============================================================

-- Rename column if needed
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'partner_users' AND column_name = 'magic_token_expires') THEN
        ALTER TABLE partner_users RENAME COLUMN magic_token_expires TO magic_token_expires_at;
    END IF;
END $$;

ALTER TABLE partner_users ADD COLUMN IF NOT EXISTS magic_token_expires_at TIMESTAMPTZ;
ALTER TABLE partner_users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ;

-- ============================================================
-- INDEXES FOR PERFORMANCE
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_discovered_assets_site ON discovered_assets(site_id);
CREATE INDEX IF NOT EXISTS idx_discovered_assets_status ON discovered_assets(monitoring_status);
CREATE INDEX IF NOT EXISTS idx_discovered_assets_type ON discovered_assets(asset_type);
CREATE INDEX IF NOT EXISTS idx_credentials_site ON site_credentials(site_id);
CREATE INDEX IF NOT EXISTS idx_discovery_scans_site ON discovery_scans(site_id);
CREATE INDEX IF NOT EXISTS idx_discovery_scans_status ON discovery_scans(status);

COMMIT;
