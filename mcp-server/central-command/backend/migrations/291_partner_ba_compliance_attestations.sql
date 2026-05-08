-- Migration 291: Partner BA Compliance Attestations (P-F6 fix-up,
-- coach retroactive sweep 2026-05-08)
--
-- Coach found P-F6 shipped (in working tree) without parallel
-- structure to F1/P-F5: SHA-256 only, no Ed25519, no public verify
-- route. This migration closes the gap BEFORE first ship.
--
-- The MSP partner's BA Compliance Attestation Letter — Tony's
-- three-party-BAA-chain visibility artifact for downstream
-- counterparties (insurance carriers, board chairs, counsel).
-- Persists the issued attestation so a recipient can independently
-- verify the cryptographic identity at /api/verify/ba-attestation/{hash[:32]}.
--
-- Mirrors mig 289 (P-F5 partner_portfolio_attestations) shape:
--   * One ACTIVE attestation per partner at a time (partial
--     unique idx on superseded_by_id IS NULL)
--   * Ed25519 signature over canonical JSON
--   * SECURITY DEFINER public-verify function returning
--     OCR-grade payload only (NO partner_id leak, NO roster
--     PHI, NO counterparty-org-name leak — aggregate counts
--     only)
--   * 90-day default validity
--   * presenter snapshots frozen at issue time
--   * Hash-prefix lookup with 32-char floor + ambiguity detection
--     handled at the API layer (per F1+P-F5 pattern)

BEGIN;

CREATE TABLE IF NOT EXISTS partner_ba_compliance_attestations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    partner_id UUID NOT NULL REFERENCES partners(id) ON DELETE RESTRICT,

    -- Subcontractor BAA snapshot — when MSP signed BAA with
    -- OsirisCare. Frozen at issue time so historical re-render
    -- preserves accuracy.
    subcontractor_baa_dated_at TIMESTAMPTZ NOT NULL,

    -- Aggregate roster facts at issue time. NO counterparty
    -- names, NO clinic-identifying detail — only counts. The
    -- rendered letter shows roster detail only via authenticated
    -- partner-portal access; the public verify endpoint never
    -- discloses counterparty identity.
    roster_count INTEGER NOT NULL,
    total_monitored_sites INTEGER NOT NULL,
    onboarded_counterparty_count INTEGER NOT NULL,

    -- White-label snapshot — partner's own brand, frozen.
    presenter_brand_snapshot TEXT NOT NULL,
    support_email_snapshot TEXT NOT NULL DEFAULT '',
    support_phone_snapshot TEXT NOT NULL DEFAULT '',

    -- Cryptographic identity
    attestation_hash TEXT NOT NULL UNIQUE,
    ed25519_signature TEXT NOT NULL,

    -- Lifecycle
    issued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    valid_until TIMESTAMPTZ NOT NULL,

    -- Forensics
    issued_by_user_id UUID REFERENCES partner_users(id) ON DELETE SET NULL,
    issued_by_email TEXT,

    -- Soft-delete via supersede pointer
    superseded_by_id UUID REFERENCES partner_ba_compliance_attestations(id) ON DELETE SET NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Sanity invariants
    CONSTRAINT pbca_validity_order CHECK (valid_until > issued_at),
    CONSTRAINT pbca_counts_nonneg CHECK (
        roster_count >= 0
        AND total_monitored_sites >= 0
        AND onboarded_counterparty_count >= 0
    ),
    CONSTRAINT pbca_counterparty_le_roster CHECK (
        onboarded_counterparty_count <= roster_count + 0  -- defense-in-depth: stays sane
    ),
    -- Steve P1-D / Maya P1-A (round-table 2026-05-06): hash floor
    -- ≥32 chars + canonical 64-hex shape.
    CONSTRAINT pbca_hash_shape CHECK (
        attestation_hash ~ '^[0-9a-f]{64}$'
    )
);

-- Lookup paths
CREATE INDEX IF NOT EXISTS idx_pbca_partner_issued
    ON partner_ba_compliance_attestations(partner_id, issued_at DESC);

-- Steve P1-B shape: one ACTIVE letter per partner. Concurrent
-- issue races trip this rather than producing two non-superseded
-- rows.
CREATE UNIQUE INDEX IF NOT EXISTS idx_pbca_one_active_per_partner
    ON partner_ba_compliance_attestations(partner_id)
    WHERE superseded_by_id IS NULL;

-- RLS — partner-scoped (NOT client_org-scoped).
ALTER TABLE partner_ba_compliance_attestations ENABLE ROW LEVEL SECURITY;

CREATE POLICY partner_self_isolation ON partner_ba_compliance_attestations
    FOR ALL
    USING (
        partner_id::text = current_setting('app.current_partner', true)
    );

CREATE POLICY admin_full_access ON partner_ba_compliance_attestations
    FOR ALL
    USING (
        current_setting('app.is_admin', true) = 'true'
    );

-- SECURITY DEFINER public-verify function — F4 / P-F5 pattern.
-- Returns ONLY the OCR-grade payload. Crucially, NO counterparty
-- identity, NO partner_id, NO ed25519_signature exposure. Roster
-- detail is partner-portal-only.
CREATE OR REPLACE FUNCTION public_verify_partner_ba_attestation(p_hash TEXT)
RETURNS TABLE (
    attestation_hash TEXT,
    issued_at TIMESTAMPTZ,
    valid_until TIMESTAMPTZ,
    is_expired BOOLEAN,
    is_superseded BOOLEAN,
    subcontractor_baa_dated_at TIMESTAMPTZ,
    roster_count INTEGER,
    total_monitored_sites INTEGER,
    onboarded_counterparty_count INTEGER,
    presenter_brand TEXT
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT
        a.attestation_hash,
        a.issued_at,
        a.valid_until,
        (NOW() > a.valid_until) AS is_expired,
        (a.superseded_by_id IS NOT NULL) AS is_superseded,
        a.subcontractor_baa_dated_at,
        a.roster_count,
        a.total_monitored_sites,
        a.onboarded_counterparty_count,
        a.presenter_brand_snapshot AS presenter_brand
    FROM partner_ba_compliance_attestations a
    WHERE a.attestation_hash = p_hash;
$$;

REVOKE ALL ON FUNCTION public_verify_partner_ba_attestation(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public_verify_partner_ba_attestation(TEXT) TO mcp_app;

COMMIT;
