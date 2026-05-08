-- Migration 289: Partner Portfolio Attestations (P-F5, partner round-table 2026-05-08)
--
-- The MSP partner's Portfolio Attestation Letter — the partner-side
-- analog of F1's Compliance Attestation Letter. Greg-the-MSP-owner
-- prints this for sales, his website trust badge, and the
-- chamber-of-commerce lunch. Anna-the-sales-lead hands the public
-- /verify URL to a prospect.
--
-- Aggregate-only — NO clinic names, NO PHI, NO per-site detail.
-- Counts + chain-roots only. The portfolio shape proves "MSP X
-- runs N HIPAA-grade clinics on the OsirisCare substrate" without
-- leaking which clinics or any clinic-specific data.
--
-- Mirrors F1 (mig 288) shape:
--   * One ACTIVE attestation per partner at a time (partial unique
--     idx on superseded_by_id IS NULL)
--   * Ed25519 signature over canonical JSON
--   * SECURITY DEFINER public-verify function returning OCR-grade
--     payload (no partner_id leak)
--   * 90-day default validity
--   * presenter snapshots frozen at issue time

BEGIN;

CREATE TABLE IF NOT EXISTS partner_portfolio_attestations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    partner_id UUID NOT NULL REFERENCES partners(id) ON DELETE RESTRICT,

    -- Period covered. Default 30 days back from issue.
    period_start TIMESTAMPTZ NOT NULL,
    period_end   TIMESTAMPTZ NOT NULL,

    -- Aggregate operational facts at issue time. NO clinic-level
    -- details — only counts.
    site_count INTEGER NOT NULL,
    appliance_count INTEGER NOT NULL,
    workstation_count INTEGER NOT NULL,
    control_count INTEGER NOT NULL,
    bundle_count INTEGER NOT NULL,

    -- OTS anchor coverage % (0-100) — what fraction of bundles in
    -- the period are anchor-confirmed in Bitcoin. The remainder
    -- are pending per the standard OTS schedule.
    ots_anchored_pct REAL NOT NULL,

    -- chain_root_hex: SHA-256 of concatenated chain heads
    -- (latest bundle hash per site, sorted by site_id). An
    -- auditor can independently recompute this from per-site
    -- auditor-kits and compare. Aggregate fingerprint without
    -- leaking individual sites.
    chain_root_hex TEXT NOT NULL,

    -- White-label snapshot — partner's own brand. FROZEN at issue
    -- time so re-rendering preserves historical accuracy.
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

    -- Soft-delete via supersede pointer (mirrors mig 288 chain-head
    -- denormalization).
    superseded_by_id UUID REFERENCES partner_portfolio_attestations(id) ON DELETE SET NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Sanity invariants
    CONSTRAINT ppa_period_order CHECK (period_end > period_start),
    CONSTRAINT ppa_validity_order CHECK (valid_until > issued_at),
    CONSTRAINT ppa_counts_nonneg CHECK (
        site_count >= 0
        AND appliance_count >= 0
        AND workstation_count >= 0
        AND control_count >= 0
        AND bundle_count >= 0
    ),
    CONSTRAINT ppa_ots_pct_range CHECK (
        ots_anchored_pct >= 0 AND ots_anchored_pct <= 100
    ),
    CONSTRAINT ppa_chain_root_shape CHECK (
        chain_root_hex ~ '^[0-9a-f]{64}$'
    )
);

-- Lookup paths
CREATE INDEX IF NOT EXISTS idx_ppa_partner_issued
    ON partner_portfolio_attestations(partner_id, issued_at DESC);

-- Steve P1-B (round-table 2026-05-06) shape: one ACTIVE letter
-- per partner. Concurrent issue races trip this rather than
-- producing two non-superseded rows.
CREATE UNIQUE INDEX IF NOT EXISTS idx_ppa_one_active_per_partner
    ON partner_portfolio_attestations(partner_id)
    WHERE superseded_by_id IS NULL;

-- RLS — partner_portfolio_attestations is partner-scoped (NOT
-- client_org-scoped). Partners read their own; admin sees all.
ALTER TABLE partner_portfolio_attestations ENABLE ROW LEVEL SECURITY;

CREATE POLICY partner_self_isolation ON partner_portfolio_attestations
    FOR ALL
    USING (
        partner_id::text = current_setting('app.current_partner', true)
    );

CREATE POLICY admin_full_access ON partner_portfolio_attestations
    FOR ALL
    USING (
        current_setting('app.is_admin', true) = 'true'
    );

-- SECURITY DEFINER public-verify function — F4 pattern. Returns
-- ONLY the OCR-grade payload (NO partner_id, NO internal IDs, NO
-- ed25519_signature exposure). The chain_root_hex is the
-- independently-verifiable fingerprint.
CREATE OR REPLACE FUNCTION public_verify_partner_portfolio(p_hash TEXT)
RETURNS TABLE (
    attestation_hash TEXT,
    issued_at TIMESTAMPTZ,
    valid_until TIMESTAMPTZ,
    is_expired BOOLEAN,
    is_superseded BOOLEAN,
    period_start TIMESTAMPTZ,
    period_end TIMESTAMPTZ,
    site_count INTEGER,
    appliance_count INTEGER,
    workstation_count INTEGER,
    control_count INTEGER,
    bundle_count INTEGER,
    ots_anchored_pct REAL,
    chain_root_hex TEXT,
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
        a.period_start,
        a.period_end,
        a.site_count,
        a.appliance_count,
        a.workstation_count,
        a.control_count,
        a.bundle_count,
        a.ots_anchored_pct,
        a.chain_root_hex,
        a.presenter_brand_snapshot AS presenter_brand
    FROM partner_portfolio_attestations a
    WHERE a.attestation_hash = p_hash;
$$;

REVOKE ALL ON FUNCTION public_verify_partner_portfolio(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public_verify_partner_portfolio(TEXT) TO mcp_app;

COMMIT;
