-- Migration: 003_partner_infrastructure.sql
-- Partner/Reseller White-Label Distribution System
--
-- This migration adds support for the Datto-style partner model:
-- - Partners (MSPs/Resellers) sign up and get white-label branding
-- - Partners create provision codes (QR) for appliances
-- - Appliances claim provision codes during setup
-- - Sites are associated with partners for billing/isolation

-- =============================================================================
-- PARTNERS TABLE (MSP / IT Guy / Reseller)
-- =============================================================================
CREATE TABLE IF NOT EXISTS partners (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(100) UNIQUE NOT NULL,  -- for white-label subdomain (e.g., "acme" -> acme.osiriscare.net)
    contact_email VARCHAR(255) NOT NULL,
    contact_phone VARCHAR(50),

    -- White-label branding
    brand_name VARCHAR(255),  -- "Acme IT Solutions" displayed to their clients
    logo_url TEXT,
    primary_color VARCHAR(7) DEFAULT '#4F46E5',

    -- Business terms
    revenue_share_percent INTEGER DEFAULT 40,  -- Partner gets 40%, OsirisCare 60%

    -- Authentication
    api_key_hash VARCHAR(255) NOT NULL,  -- For API access

    -- Status tracking
    status VARCHAR(20) DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'churned')),

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- PARTNER USERS TABLE (Partner staff who manage their clients)
-- =============================================================================
CREATE TABLE IF NOT EXISTS partner_users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    partner_id UUID NOT NULL REFERENCES partners(id) ON DELETE CASCADE,
    email VARCHAR(255) NOT NULL,
    name VARCHAR(255),
    password_hash VARCHAR(255),  -- For dashboard login
    role VARCHAR(50) DEFAULT 'admin' CHECK (role IN ('admin', 'tech', 'billing')),
    magic_token VARCHAR(255),  -- For passwordless login links
    magic_token_expires TIMESTAMPTZ,
    last_login TIMESTAMPTZ,
    status VARCHAR(20) DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(partner_id, email)
);

-- =============================================================================
-- APPLIANCE PROVISIONS TABLE (QR code based provisioning)
-- =============================================================================
CREATE TABLE IF NOT EXISTS appliance_provisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    partner_id UUID NOT NULL REFERENCES partners(id) ON DELETE CASCADE,

    -- Provision code (QR code content)
    provision_code VARCHAR(32) UNIQUE NOT NULL,  -- Short code for QR

    -- Target assignment
    target_site_id VARCHAR(255),  -- If pre-assigned to a site
    target_client_name VARCHAR(255),  -- Hint for partner's client name

    -- Status tracking
    status VARCHAR(20) DEFAULT 'pending' CHECK (status IN ('pending', 'claimed', 'expired', 'revoked')),

    -- Claim tracking
    claimed_at TIMESTAMPTZ,
    claimed_by_mac VARCHAR(17),  -- MAC address of appliance that claimed it
    claimed_appliance_id VARCHAR(255),

    -- Expiration
    expires_at TIMESTAMPTZ DEFAULT (NOW() + INTERVAL '30 days'),

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- PARTNER INVOICES TABLE (Revenue tracking)
-- =============================================================================
CREATE TABLE IF NOT EXISTS partner_invoices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    partner_id UUID NOT NULL REFERENCES partners(id) ON DELETE CASCADE,

    -- Billing period
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,

    -- Amounts (in cents to avoid floating point)
    total_mrr_cents INTEGER NOT NULL,  -- Total MRR from partner's clients
    partner_share_cents INTEGER NOT NULL,  -- Partner's cut
    osiris_share_cents INTEGER NOT NULL,  -- OsirisCare's cut

    -- Client breakdown (JSON array)
    line_items JSONB DEFAULT '[]',  -- [{site_id, client_name, tier, amount_cents}]

    -- Status
    status VARCHAR(20) DEFAULT 'draft' CHECK (status IN ('draft', 'sent', 'paid', 'void')),

    -- Payment tracking
    paid_at TIMESTAMPTZ,
    payment_reference VARCHAR(255),

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    sent_at TIMESTAMPTZ
);

-- =============================================================================
-- ADD PARTNER_ID TO SITES TABLE
-- =============================================================================
ALTER TABLE sites ADD COLUMN IF NOT EXISTS partner_id UUID REFERENCES partners(id);

-- =============================================================================
-- INDEXES FOR PERFORMANCE
-- =============================================================================
CREATE INDEX IF NOT EXISTS idx_partners_slug ON partners(slug);
CREATE INDEX IF NOT EXISTS idx_partners_status ON partners(status);
CREATE INDEX IF NOT EXISTS idx_partner_users_partner ON partner_users(partner_id);
CREATE INDEX IF NOT EXISTS idx_partner_users_email ON partner_users(email);
CREATE INDEX IF NOT EXISTS idx_appliance_provisions_partner ON appliance_provisions(partner_id);
CREATE INDEX IF NOT EXISTS idx_appliance_provisions_code ON appliance_provisions(provision_code);
CREATE INDEX IF NOT EXISTS idx_appliance_provisions_status ON appliance_provisions(status);
CREATE INDEX IF NOT EXISTS idx_partner_invoices_partner ON partner_invoices(partner_id);
CREATE INDEX IF NOT EXISTS idx_partner_invoices_period ON partner_invoices(period_start, period_end);
CREATE INDEX IF NOT EXISTS idx_sites_partner ON sites(partner_id);

-- =============================================================================
-- SEED: Create OsirisCare as "default" partner (for direct sales)
-- =============================================================================
INSERT INTO partners (name, slug, contact_email, brand_name, revenue_share_percent, api_key_hash, status)
VALUES (
    'OsirisCare Direct',
    'osiriscare',
    'support@osiriscare.net',
    'OsirisCare',
    100,  -- Direct sales = 100% to OsirisCare
    'direct-no-api-key',
    'active'
) ON CONFLICT (slug) DO NOTHING;
