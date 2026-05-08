-- Migration 292: Quarterly Practice Compliance Summaries (F3, sprint 2026-05-08)
--
-- Maria's last owner-side P1 deferred from Friday: a quarter-windowed
-- compliance summary the Privacy Officer signs and the practice
-- owner files for HIPAA §164.530(j) records retention.
--
-- F3 is the TIME-WINDOWED owner-side analog of F1.
--   * F1 = current-state attestation, 90-day validity, 30-day window.
--   * F3 = previous-calendar-quarter summary, 365-day validity, fixed
--     period_start/period_end (Q1 = Jan 1 — Mar 31 inclusive UTC).
--
-- DESIGN POSTURE (frozen-at-issue — Diane CPA contract carried over
-- from F1):
--   The Privacy Officer name + title + email are SNAPSHOT into
--   *_snapshot columns. Revoking the F2 designation later does NOT
--   mutate prior summaries. Quarterly summaries are §164.530(j)
--   evidence — the owner files them; we do not retroactively re-skin.
--
-- DESIGN POSTURE (one-active per quarter — Steve P1-B carried over):
--   Re-issuing the same quarter SUPERSEDES the prior. Partial unique
--   idx (client_org_id, period_year, period_quarter) WHERE
--   superseded_by_id IS NULL closes the concurrent-issue race at the
--   schema layer. Application-layer transaction does the supersede;
--   if a race wins past the transaction, INSERT raises
--   UniqueViolationError and the API maps it to 409.
--
-- DESIGN POSTURE (load-bearing F2 link — Carol contract carried over):
--   F3 cannot be issued without an active Privacy Officer designation.
--   The application layer raises QuarterlySummaryError BEFORE this
--   table is touched; on success the PO snapshot fields are populated.
--
-- DESIGN POSTURE (no §164.528 disclosure accounting — Carol contract):
--   Disclaimer copy on the rendered PDF is byte-identical to F1 and
--   P-F6: "audit-supportive technical evidence; it is not a HIPAA
--   §164.528 disclosure accounting and does not constitute a legal
--   opinion." Pinned by tests/test_client_quarterly_summary.py.
--
-- DESIGN POSTURE (RLS posture — F1 parity):
--   tenant_org_isolation matches F1 mig 288 — current_setting('app.
--   current_org', true) — NOT the rls_site_belongs_to_current_org
--   site-scoped helper. F3 is org-scoped because the period spans
--   every site under the org. Public-verify reads bypass RLS via
--   SECURITY DEFINER function (Maya posture).
--
-- DESIGN POSTURE (OCR-grade public payload — Maya carried over):
--   public_verify_quarterly_practice_summary RETURNS TABLE excludes
--   client_org_id, privacy_officer_email_snapshot, ed25519_signature,
--   issued_by_email, issued_by_user_id. Carriers and OCR investigators
--   need: hash, period, counts, PO name+title, brand, expiry. Nothing
--   that identifies the org beyond the practice_name signed-on-letter.

BEGIN;

CREATE TABLE IF NOT EXISTS quarterly_practice_compliance_summaries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_org_id UUID NOT NULL REFERENCES client_orgs(id) ON DELETE RESTRICT,

    -- Calendar-quarter coordinate. period_year + period_quarter are
    -- the human-readable identity ("Q1 2026"); period_start +
    -- period_end are derived UTC midnights (Jan 1 00:00:00Z to
    -- Apr 1 00:00:00Z exclusive — "[start, end)" half-open).
    period_year INTEGER NOT NULL,
    period_quarter INTEGER NOT NULL,
    period_start TIMESTAMPTZ NOT NULL,
    period_end   TIMESTAMPTZ NOT NULL,

    -- Aggregate operational facts at issue time (FROZEN). Match the
    -- F1 + P-F5 shape but with a quarter-shaped period.
    bundle_count INTEGER NOT NULL,
    -- OTS coverage % (0-100) for bundles in the period.
    ots_anchored_pct REAL NOT NULL,
    -- Drift counts (incidents) opened / resolved within the window.
    drift_detected_count INTEGER NOT NULL,
    drift_resolved_count INTEGER NOT NULL,
    -- Rolling mean compliance score across the period (0-100; nullable
    -- if no_data — same shape as F1.overall_score).
    mean_score INTEGER,
    sites_count INTEGER NOT NULL,
    appliances_count INTEGER NOT NULL,
    workstations_count INTEGER NOT NULL,
    -- Count of distinct check_type rows in check_type_registry that
    -- were scored (is_scored=true). Equivalent to "controls
    -- evaluated."
    monitored_check_types_count INTEGER NOT NULL,

    -- Privacy Officer snapshot (Carol contract — see F2 mig 287).
    -- Snapshots, NOT FK-only — revoking the designation later does
    -- not invalidate this row's frozen identity record.
    privacy_officer_name_snapshot TEXT NOT NULL,
    privacy_officer_title_snapshot TEXT NOT NULL,
    privacy_officer_email_snapshot TEXT NOT NULL,

    -- White-label snapshot (Diane contract carried over from F1 +
    -- P-F5). FROZEN at issue time.
    presenter_brand_snapshot TEXT NOT NULL DEFAULT 'OsirisCare',
    presenter_partner_id_snapshot UUID,
    presenter_contact_line_snapshot TEXT NOT NULL DEFAULT '',

    -- Practice name snapshot — Maria might rename the org later;
    -- the historical summary keeps the name as it was at issue time.
    practice_name_snapshot TEXT NOT NULL,

    -- Cryptographic identity. attestation_hash binds to the canonical-
    -- JSON payload (sort_keys=True compact separators). Ed25519 sig
    -- via the same signing_backend abstraction F1 + P-F5 use.
    attestation_hash TEXT NOT NULL UNIQUE,
    ed25519_signature TEXT NOT NULL,

    -- Lifecycle. valid_until = issued_at + 365 days (HIPAA records
    -- retention is 6 years; we re-issue annually so 365 buys margin).
    issued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    valid_until TIMESTAMPTZ NOT NULL,

    -- Forensics
    issued_by_user_id UUID REFERENCES client_users(id) ON DELETE SET NULL,
    issued_by_email TEXT,

    -- Soft-delete via supersede pointer (mirrors F1 mig 288 + P-F5
    -- mig 289 chain-head denormalization).
    superseded_by_id UUID
        REFERENCES quarterly_practice_compliance_summaries(id) ON DELETE SET NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Sanity invariants
    CONSTRAINT qpcs_period_year_recent CHECK (period_year >= 2024),
    CONSTRAINT qpcs_period_quarter_range CHECK (period_quarter IN (1, 2, 3, 4)),
    CONSTRAINT qpcs_period_order CHECK (period_end > period_start),
    CONSTRAINT qpcs_validity_order CHECK (valid_until > issued_at),
    CONSTRAINT qpcs_counts_nonneg CHECK (
        bundle_count >= 0
        AND drift_detected_count >= 0
        AND drift_resolved_count >= 0
        AND sites_count >= 0
        AND appliances_count >= 0
        AND workstations_count >= 0
        AND monitored_check_types_count >= 0
    ),
    CONSTRAINT qpcs_ots_pct_range CHECK (
        ots_anchored_pct >= 0 AND ots_anchored_pct <= 100
    ),
    CONSTRAINT qpcs_score_range CHECK (
        mean_score IS NULL OR (mean_score >= 0 AND mean_score <= 100)
    ),
    CONSTRAINT qpcs_hash_shape CHECK (
        attestation_hash ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT qpcs_practice_name_nonempty CHECK (
        LENGTH(practice_name_snapshot) > 0
    )
);

-- Lookup paths
CREATE INDEX IF NOT EXISTS idx_qpcs_org_issued
    ON quarterly_practice_compliance_summaries(client_org_id, issued_at DESC);
CREATE INDEX IF NOT EXISTS idx_qpcs_org_period
    ON quarterly_practice_compliance_summaries(
        client_org_id, period_year DESC, period_quarter DESC
    );

-- Steve P1-B (round-table 2026-05-06) shape: one ACTIVE summary per
-- (org, year, quarter). Re-issuing supersedes the prior. Concurrent
-- issuances trip this rather than producing two non-superseded rows.
CREATE UNIQUE INDEX IF NOT EXISTS idx_qpcs_one_active_per_org_quarter
    ON quarterly_practice_compliance_summaries(
        client_org_id, period_year, period_quarter
    )
    WHERE superseded_by_id IS NULL;

-- RLS — org-scoped (mirrors F1 mig 288 posture exactly). Public-
-- verify reads bypass RLS via the SECURITY DEFINER function below.
ALTER TABLE quarterly_practice_compliance_summaries ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_org_isolation ON quarterly_practice_compliance_summaries
    FOR ALL
    USING (
        client_org_id::text = current_setting('app.current_org', true)
    );

CREATE POLICY admin_full_access ON quarterly_practice_compliance_summaries
    FOR ALL
    USING (
        current_setting('app.is_admin', true) = 'true'
    );

-- SECURITY DEFINER public-verify function. F1 + P-F5 pattern. Returns
-- ONLY OCR-grade fields:
--   * NO client_org_id              (would leak tenant identity)
--   * NO privacy_officer_email      (PII; carrier doesn't need it)
--   * NO ed25519_signature          (verifiable separately via hash)
--   * NO issued_by_email / user_id  (forensics, not OCR-grade)
--   * NO presenter_partner_id       (internal partner UUID)
CREATE OR REPLACE FUNCTION public_verify_quarterly_practice_summary(p_hash TEXT)
RETURNS TABLE (
    attestation_hash TEXT,
    issued_at TIMESTAMPTZ,
    valid_until TIMESTAMPTZ,
    is_expired BOOLEAN,
    is_superseded BOOLEAN,
    period_year INTEGER,
    period_quarter INTEGER,
    period_start TIMESTAMPTZ,
    period_end TIMESTAMPTZ,
    bundle_count INTEGER,
    ots_anchored_pct REAL,
    drift_detected_count INTEGER,
    drift_resolved_count INTEGER,
    mean_score INTEGER,
    sites_count INTEGER,
    appliances_count INTEGER,
    workstations_count INTEGER,
    monitored_check_types_count INTEGER,
    privacy_officer_name TEXT,
    privacy_officer_title TEXT,
    presenter_brand TEXT,
    practice_name TEXT
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT
        s.attestation_hash,
        s.issued_at,
        s.valid_until,
        (NOW() > s.valid_until) AS is_expired,
        (s.superseded_by_id IS NOT NULL) AS is_superseded,
        s.period_year,
        s.period_quarter,
        s.period_start,
        s.period_end,
        s.bundle_count,
        s.ots_anchored_pct,
        s.drift_detected_count,
        s.drift_resolved_count,
        s.mean_score,
        s.sites_count,
        s.appliances_count,
        s.workstations_count,
        s.monitored_check_types_count,
        s.privacy_officer_name_snapshot AS privacy_officer_name,
        s.privacy_officer_title_snapshot AS privacy_officer_title,
        s.presenter_brand_snapshot AS presenter_brand,
        s.practice_name_snapshot AS practice_name
    FROM quarterly_practice_compliance_summaries s
    WHERE s.attestation_hash = p_hash;
$$;

REVOKE ALL ON FUNCTION public_verify_quarterly_practice_summary(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public_verify_quarterly_practice_summary(TEXT) TO mcp_app;

COMMIT;
