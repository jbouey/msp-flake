-- Migration 290: Partner BAA roster + BA Compliance Attestation
-- (P-F6, partner round-table 2026-05-08)
--
-- Tony-the-MSP-HIPAA-lead's customer-round-table finding (2026-05-08):
--   "The three-party BAA chain is invisible. I am a Business
--    Associate to 14 covered entities and a downstream of
--    OsirisCare. My auditor will ask: (a) show me the
--    OsirisCare→MSP subcontractor BAA, (b) show me each
--    MSP→clinic BAA executed and current, (c) show me evidence
--    I'm performing my BA obligations. Today PartnerAgreements
--    stores the OsirisCare BAA. Per-clinic BAAs are nowhere."
--
-- This migration adds the per-clinic-BAA roster Tony needs. The
-- BA Compliance Attestation Letter (P-F6) renders the roster and
-- cross-references each entry with monitored sites under that
-- partner.
--
-- Two counterparty kinds:
--   1. counterparty_org_id NOT NULL — the clinic is an active
--      OsirisCare client (sites.client_org_id matches). The Letter
--      cross-references monitored sites for evidence-of-performance.
--   2. counterparty_practice_name NOT NULL — the clinic is on
--      Tony's BAA roster but NOT yet onboarded to OsirisCare
--      (legacy/non-OsirisCare clinic Tony serves as MSP). The
--      Letter renders the BAA entry without a sites cross-ref.
--      Tony's auditor sees the MSP performs BA duties broadly,
--      not just for OsirisCare-monitored clinics.

BEGIN;

CREATE TABLE IF NOT EXISTS partner_baa_roster (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    partner_id UUID NOT NULL REFERENCES partners(id) ON DELETE RESTRICT,

    -- Counterparty identity. Exactly one of these MUST be set.
    counterparty_org_id UUID REFERENCES client_orgs(id) ON DELETE RESTRICT,
    counterparty_practice_name TEXT,

    -- BAA execution metadata
    executed_at TIMESTAMPTZ NOT NULL,
    expiry_at TIMESTAMPTZ,  -- NULL = no fixed expiry
    scope TEXT NOT NULL,     -- short scope description (≥20 chars)
    doc_sha256 TEXT,         -- optional: SHA-256 of the executed PDF
    signer_name TEXT NOT NULL,
    signer_title TEXT NOT NULL,
    signer_email TEXT,

    -- Forensics + lifecycle
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    uploaded_by_user_id UUID REFERENCES partner_users(id) ON DELETE SET NULL,
    uploaded_by_email TEXT,

    -- Soft-revoke (replacement). NULL = currently active.
    revoked_at TIMESTAMPTZ,
    revoked_by_user_id UUID REFERENCES partner_users(id) ON DELETE SET NULL,
    revoked_by_email TEXT,
    revoked_reason TEXT,

    -- Cryptographic chain linkage (Ed25519 attestation per insert
    -- + per revoke, via privileged_access_attestation).
    attestation_bundle_id UUID,
    revoked_attestation_bundle_id UUID,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- One-of constraint: org_id XOR practice_name (exactly one set).
    CONSTRAINT pbr_one_counterparty CHECK (
        (counterparty_org_id IS NOT NULL AND counterparty_practice_name IS NULL)
        OR
        (counterparty_org_id IS NULL AND counterparty_practice_name IS NOT NULL)
    ),

    -- Scope length (matches privileged-access ≥20-char convention).
    CONSTRAINT pbr_scope_minlen CHECK (LENGTH(scope) >= 20),

    -- Revocation field consistency.
    CONSTRAINT pbr_revoke_fields_consistent CHECK (
        (revoked_at IS NULL AND revoked_by_user_id IS NULL
            AND revoked_by_email IS NULL AND revoked_reason IS NULL)
        OR
        (revoked_at IS NOT NULL AND revoked_reason IS NOT NULL)
    ),

    CONSTRAINT pbr_revoked_reason_minlen CHECK (
        revoked_reason IS NULL OR LENGTH(revoked_reason) >= 20
    ),

    -- Practice name length (only relevant when org_id is NULL).
    CONSTRAINT pbr_practice_name_minlen CHECK (
        counterparty_practice_name IS NULL OR LENGTH(counterparty_practice_name) >= 2
    ),

    -- Email shape on signer if provided.
    CONSTRAINT pbr_signer_email_shape CHECK (
        signer_email IS NULL OR signer_email ~ '^[^@]+@[^@]+\.[^@]+$'
    )
);

-- Lookup paths
CREATE INDEX IF NOT EXISTS idx_pbr_partner_active
    ON partner_baa_roster(partner_id, executed_at DESC)
    WHERE revoked_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_pbr_partner_counterparty_org
    ON partner_baa_roster(partner_id, counterparty_org_id)
    WHERE revoked_at IS NULL AND counterparty_org_id IS NOT NULL;

-- One ACTIVE BAA per (partner, counterparty_org) — replacement
-- requires explicit revoke + re-insert.
CREATE UNIQUE INDEX IF NOT EXISTS idx_pbr_one_active_per_pair
    ON partner_baa_roster(partner_id, counterparty_org_id)
    WHERE revoked_at IS NULL AND counterparty_org_id IS NOT NULL;

-- RLS — partner-scoped.
ALTER TABLE partner_baa_roster ENABLE ROW LEVEL SECURITY;

CREATE POLICY partner_self_isolation ON partner_baa_roster
    FOR ALL
    USING (
        partner_id::text = current_setting('app.current_partner', true)
    );

CREATE POLICY admin_full_access ON partner_baa_roster
    FOR ALL
    USING (
        current_setting('app.is_admin', true) = 'true'
    );

COMMIT;
