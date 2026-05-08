-- Migration 293: Partner→client-portal magic-link mint table
-- (Sprint-N+2 D4, round-table 2026-05-08)
--
-- Lisa-the-MSP-MD's "open this clinic's portal as the practice
-- owner" workflow. Partner mints a 15-min single-use magic link
-- to the client portal scoped to a specific site. Each mint is
-- chain-attested (`partner_client_portal_link_minted` event in
-- privileged_access_attestation::ALLOWED_EVENTS); this table is
-- the operational record for token validation + single-use
-- enforcement, while the cryptographic record lives in
-- compliance_bundles.
--
-- Why a new table vs. extending an existing magic-link table:
--   * privileged_access_magic_links is for HMAC-signed approval
--     tokens with session-binding semantics (the recipient
--     consumes via session-auth) — wrong shape.
--   * portal.PortalSessionManager.set_magic_link is in-memory /
--     Redis with a flat token→site_id map; no minter
--     attribution, no chain link, no per-partner rate-limiter
--     scope.
--   * This table records (partner_id, partner_user_id, site_id)
--     attribution + attestation_bundle_id chain link + single-
--     use semantics in the durable store, matching the
--     enterprise-grade default.
--
-- RLS: mirrors mig 290 partner_baa_roster shape — partner_self_
-- isolation policy + admin bypass.

BEGIN;

CREATE TABLE IF NOT EXISTS partner_client_portal_links (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    partner_id UUID NOT NULL REFERENCES partners(id) ON DELETE RESTRICT,
    partner_user_id UUID REFERENCES partner_users(id) ON DELETE SET NULL,

    -- Site this link is scoped to. Free-form text to match
    -- existing site_id columns elsewhere (sites.site_id is text).
    site_id TEXT NOT NULL,

    -- Opaque token rendered in the URL. UNIQUE so any leak +
    -- brute force still single-use-bounded by uniqueness.
    token TEXT NOT NULL UNIQUE,

    -- Lifecycle
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,

    -- Forensics
    minted_by_email TEXT,           -- caller's authenticated email
    minted_by_ip TEXT,              -- request.client.host
    consumed_by_ip TEXT,            -- IP that hit the consume endpoint
    consumed_by_ua TEXT,            -- UA truncated 512

    -- Reason (≥20 chars) — recorded in attestation; mirrored here
    -- for operational queries without compliance_bundles join.
    reason TEXT NOT NULL,

    -- Cryptographic chain link.
    attestation_bundle_id UUID,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Reason length parity with privileged-access ≥20 char convention.
    CONSTRAINT pcpl_reason_minlen CHECK (LENGTH(reason) >= 20),

    -- Used-at can only be NULL or in the past (a future used_at
    -- means a clock-skew or mutation bug; reject at the DB).
    CONSTRAINT pcpl_used_at_sane CHECK (
        used_at IS NULL OR used_at <= NOW()
    ),

    -- Expiry must be after created_at.
    CONSTRAINT pcpl_expiry_after_created CHECK (
        expires_at > created_at
    )
);

-- Lookup paths
CREATE INDEX IF NOT EXISTS idx_pcpl_partner_active
    ON partner_client_portal_links(partner_id, created_at DESC)
    WHERE used_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_pcpl_site_recent
    ON partner_client_portal_links(site_id, created_at DESC);

-- Token lookup is the consume hot path; UNIQUE constraint already
-- gives us a btree index, so no separate index needed.

-- RLS — partner-scoped. Mirrors mig 290.
ALTER TABLE partner_client_portal_links ENABLE ROW LEVEL SECURITY;

CREATE POLICY partner_self_isolation ON partner_client_portal_links
    FOR ALL
    USING (
        partner_id::text = current_setting('app.current_partner', true)
    );

CREATE POLICY admin_full_access ON partner_client_portal_links
    FOR ALL
    USING (
        current_setting('app.is_admin', true) = 'true'
    );

COMMIT;
