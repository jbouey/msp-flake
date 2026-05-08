-- Migration 288: Compliance Attestation Letters (F1, round-table 2026-05-06)
--
-- Closes Maria's customer-round-table finding: "what do I actually
-- hand my insurance carrier when they ask for HIPAA proof?" Today
-- the answer is the auditor kit ZIP, which the carrier can't open.
-- F1 produces a one-page branded PDF Maria forwards to Brian (her
-- Erie Insurance agent), Brian's underwriter, her board, etc.
--
-- DESIGN POSTURE (white-label survivability — Diane CPA finding):
--   "If Maria fires her MSP next year, does the historical April 2026
--    attestation stay branded the way it was issued, or does it
--    retroactively re-skin? It must stay as-issued. Audit trails that
--    mutate are not audit trails."
-- → presenter_brand_snapshot, presenter_partner_id_snapshot,
--   presenter_contact_line_snapshot are FROZEN at issue time. Future
--   re-renders produce byte-identical PDFs (matching the kit's
--   determinism contract).
--
-- DESIGN POSTURE (vendor continuity — Diane CPA finding):
--   The footer reference to "BAA executed [date]" pulls from
--   baa_signatures (Migration 224 append-only table). Without a BAA
--   on file, the letter REFUSES to render — Carol contract: "the
--   whole letter is worthless if Maria's disclosing PHI metadata to
--   a vendor with no BAA on file."
--
-- DESIGN POSTURE (validity window — Brian agent finding):
--   "I need to know when to ask for a fresh one at renewal." Default
--   issue → expire = 90 days. Letter PDF renders the expiration date
--   inline. Public /verify endpoint returns expired=true past the
--   window so a stale letter forwarded to a carrier is detectable.
--
-- DESIGN POSTURE (load-bearing F2 link — Carol contract):
--   privacy_officer_designation_id is NOT NULL. F1's letter cannot
--   exist without an active Privacy Officer designation at issue
--   time. The signature line on the rendered PDF is the designee's
--   name + title + accepted_at + explainer_version. Revoking the
--   designation does NOT invalidate prior letters (those are frozen
--   evidence) — but it DOES block new issuances until a new
--   designation is in place.

BEGIN;

CREATE TABLE IF NOT EXISTS compliance_attestation_letters (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_org_id UUID NOT NULL REFERENCES client_orgs(id) ON DELETE RESTRICT,

    -- Period covered by the letter. Default 30 days back from issue.
    period_start TIMESTAMPTZ NOT NULL,
    period_end   TIMESTAMPTZ NOT NULL,

    -- Operational summary at issue time (frozen).
    sites_covered_count INTEGER NOT NULL,
    appliances_count INTEGER NOT NULL,
    workstations_count INTEGER NOT NULL,
    overall_score INTEGER,  -- 0-100; nullable if no_data
    bundle_count INTEGER NOT NULL,

    -- Privacy Officer linkage (LOAD-BEARING per F2 contract).
    -- The PO designation in effect at issue time. Snapshot fields
    -- below preserve the rendered identity even if the underlying
    -- designation is later revoked — the issued letter itself is
    -- frozen evidence.
    privacy_officer_designation_id UUID NOT NULL
        REFERENCES privacy_officer_designations(id) ON DELETE RESTRICT,
    privacy_officer_name_snapshot TEXT NOT NULL,
    privacy_officer_title_snapshot TEXT NOT NULL,
    privacy_officer_email_snapshot TEXT NOT NULL,
    privacy_officer_explainer_version_snapshot TEXT NOT NULL,

    -- BAA-on-file linkage (Diane CPA contract).
    baa_signature_id UUID,  -- FK soft-linked; baa_signatures may not
                            -- be in tenant schema in all envs
    baa_dated_at TIMESTAMPTZ NOT NULL,
    baa_practice_name_snapshot TEXT NOT NULL,

    -- White-label snapshot (frozen at issue time; never re-skin).
    presenter_brand_snapshot TEXT NOT NULL DEFAULT 'OsirisCare',
    presenter_partner_id_snapshot UUID,
    presenter_contact_line_snapshot TEXT NOT NULL DEFAULT '',

    -- Cryptographic identity.
    -- attestation_hash: SHA-256 of canonical-JSON content (the
    -- rendered facts, NOT the PDF bytes — PDF bytes are frozen by
    -- the deterministic Jinja2 + WeasyPrint pipeline). The /verify/
    -- {hash} public endpoint takes this hash, looks up the row,
    -- returns OCR-grade payload (designee name + period + control
    -- count + BAA boolean).
    attestation_hash TEXT NOT NULL UNIQUE,
    -- Ed25519 signature over canonical JSON of the attested facts.
    -- Same key the auditor-kit chain uses; same signing_backend
    -- abstraction (file/Vault Transit).
    ed25519_signature TEXT NOT NULL,
    -- Optional link to the chain bundle if the issuance also wrote
    -- a privileged_access attestation (designed for future symmetry
    -- with F2's chain entries).
    attestation_bundle_id UUID,

    -- Lifecycle timestamps.
    issued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    valid_until TIMESTAMPTZ NOT NULL,  -- default issued_at + 90 days

    -- Generation context (forensics).
    issued_by_user_id UUID REFERENCES client_users(id) ON DELETE SET NULL,
    issued_by_email TEXT,

    -- Soft-delete posture. Letters are evidence — we don't delete.
    -- Superseded letters carry a superseded_by_id pointer (denormalized
    -- chain head pointer for the most-recent letter per org).
    superseded_by_id UUID REFERENCES compliance_attestation_letters(id) ON DELETE SET NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Sanity invariants
    CONSTRAINT cal_period_order CHECK (period_end > period_start),
    CONSTRAINT cal_validity_order CHECK (valid_until > issued_at),
    CONSTRAINT cal_sites_nonneg CHECK (sites_covered_count >= 0),
    CONSTRAINT cal_score_range CHECK (overall_score IS NULL OR (overall_score >= 0 AND overall_score <= 100)),
    CONSTRAINT cal_baa_practice_name_nonempty CHECK (LENGTH(baa_practice_name_snapshot) > 0)
);

-- Lookup paths
CREATE INDEX IF NOT EXISTS idx_cal_org_issued
    ON compliance_attestation_letters(client_org_id, issued_at DESC);
-- attestation_hash is already UNIQUE; no extra index.
CREATE INDEX IF NOT EXISTS idx_cal_org_active
    ON compliance_attestation_letters(client_org_id, valid_until DESC)
    WHERE superseded_by_id IS NULL;
CREATE INDEX IF NOT EXISTS idx_cal_designation
    ON compliance_attestation_letters(privacy_officer_designation_id);

-- RLS — letters are tenant-scoped (client_org_id). Public /verify
-- queries use a SECURITY DEFINER function to bypass RLS for
-- hash-keyed reads only (audited separately).
ALTER TABLE compliance_attestation_letters ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_org_isolation ON compliance_attestation_letters
    FOR ALL
    USING (
        client_org_id::text = current_setting('app.current_org', true)
    );

CREATE POLICY admin_full_access ON compliance_attestation_letters
    FOR ALL
    USING (
        current_setting('app.is_admin', true) = 'true'
    );

-- SECURITY DEFINER function for the F4 public /verify/{hash}
-- endpoint. The function returns ONLY the OCR-grade fields the
-- public verify page needs — does NOT leak full row internals
-- (no client_org_id, no internal IDs, no audit metadata).
CREATE OR REPLACE FUNCTION public_verify_attestation_letter(p_hash TEXT)
RETURNS TABLE (
    attestation_hash TEXT,
    issued_at TIMESTAMPTZ,
    valid_until TIMESTAMPTZ,
    is_expired BOOLEAN,
    is_superseded BOOLEAN,
    period_start TIMESTAMPTZ,
    period_end TIMESTAMPTZ,
    bundle_count INTEGER,
    sites_covered_count INTEGER,
    privacy_officer_name TEXT,
    privacy_officer_title TEXT,
    baa_dated_at TIMESTAMPTZ,
    baa_practice_name TEXT,
    presenter_brand TEXT,
    overall_score INTEGER
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT
        l.attestation_hash,
        l.issued_at,
        l.valid_until,
        (NOW() > l.valid_until) AS is_expired,
        (l.superseded_by_id IS NOT NULL) AS is_superseded,
        l.period_start,
        l.period_end,
        l.bundle_count,
        l.sites_covered_count,
        l.privacy_officer_name_snapshot AS privacy_officer_name,
        l.privacy_officer_title_snapshot AS privacy_officer_title,
        l.baa_dated_at,
        l.baa_practice_name_snapshot AS baa_practice_name,
        l.presenter_brand_snapshot AS presenter_brand,
        l.overall_score
    FROM compliance_attestation_letters l
    WHERE l.attestation_hash = p_hash;
$$;

REVOKE ALL ON FUNCTION public_verify_attestation_letter(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public_verify_attestation_letter(TEXT) TO mcp_app;

COMMIT;
