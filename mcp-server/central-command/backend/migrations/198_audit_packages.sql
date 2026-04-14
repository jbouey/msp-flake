-- Migration 198: audit_packages — the client-facing audit deliverable.
--
-- #150 product: a single-click ZIP a client hands to their HIPAA auditor.
-- Contains cover letter, compliance packets, auditor kit, random sample,
-- controls matrix, and a tamper-evident signature manifest.
--
-- Append-only. 7-year retention per HIPAA §164.316(b)(2)(i). Deterministic
-- regeneration — 5 years from now, the same period produces byte-identical
-- output. That's the proof-of-non-tampering story.

BEGIN;

CREATE TABLE IF NOT EXISTS audit_packages (
    package_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    site_id             TEXT NOT NULL,
    period_start        DATE NOT NULL,
    period_end          DATE NOT NULL,
    generated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    generated_by        TEXT NOT NULL,        -- client_user email or 'auto:monthly'
    -- Content snapshot hashes so we can prove determinism later.
    bundles_count       INTEGER NOT NULL,
    bundles_hash_root   TEXT NOT NULL,        -- sha256 of sorted bundle_ids in period
    packets_count       INTEGER NOT NULL,
    -- The ZIP itself — stored on disk (path), hashed + signed here.
    zip_path            TEXT NOT NULL,
    zip_sha256          TEXT NOT NULL,
    zip_size_bytes      BIGINT NOT NULL,
    manifest_signature  TEXT NOT NULL,        -- Ed25519 over zip_sha256 + metadata
    -- Delivery tracking — who downloaded, when.
    download_count      INTEGER NOT NULL DEFAULT 0,
    last_downloaded_at  TIMESTAMPTZ,
    delivered_to_email  TEXT,                 -- set when operator sends to auditor
    delivered_at        TIMESTAMPTZ,
    -- Retention: 7 years minimum.
    retain_until        DATE NOT NULL DEFAULT (CURRENT_DATE + INTERVAL '7 years')::date,
    -- Framework context (HIPAA default; SOC 2 / PCI once multi-framework ships).
    framework           TEXT NOT NULL DEFAULT 'hipaa',
    CONSTRAINT audit_packages_period_valid
        CHECK (period_end >= period_start),
    CONSTRAINT audit_packages_framework_valid
        CHECK (framework IN ('hipaa', 'soc2', 'pci_dss', 'nist_csf', 'cis', 'sox',
                             'gdpr', 'cmmc', 'iso_27001', 'nist_800_171'))
);

CREATE INDEX IF NOT EXISTS idx_audit_packages_site_period
    ON audit_packages(site_id, period_end DESC);
CREATE INDEX IF NOT EXISTS idx_audit_packages_framework
    ON audit_packages(framework, period_end DESC);
-- Full index (not partial) — Postgres rejects CURRENT_DATE in predicates
-- as not immutable. Retention-cleanup jobs filter at query time instead.
CREATE INDEX IF NOT EXISTS idx_audit_packages_retain
    ON audit_packages(retain_until);

COMMENT ON TABLE audit_packages IS
    'Session 206 #150: client-facing audit package handoffs to HIPAA / SOC 2 / '
    'PCI auditors. Append-only, 7y retention. Determinism is enforced at '
    'generation: bundles_hash_root lets us prove later re-runs produce identical '
    'content. manifest_signature is Ed25519 over zip_sha256+metadata so the ZIP '
    'can be verified independently of the download channel.';

COMMENT ON COLUMN audit_packages.bundles_hash_root IS
    'sha256 of newline-joined sorted bundle_ids captured at generation. If a '
    '5-year-later regeneration produces a different root, something has been '
    'tampered with or retention was violated.';

-- Delete trigger — evidence-grade, no row removal before retain_until.
-- (Matches the pattern established by liveness_claims + compliance_bundles.)
CREATE OR REPLACE FUNCTION prevent_audit_package_deletion()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.retain_until > CURRENT_DATE THEN
        RAISE EXCEPTION 'audit_packages row % protected until %',
            OLD.package_id, OLD.retain_until
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_audit_package_deletion ON audit_packages;
CREATE TRIGGER trg_prevent_audit_package_deletion
    BEFORE DELETE ON audit_packages
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_package_deletion();

-- Download log — who fetched the ZIP, when, from what IP. Append-only evidence
-- that a specific audit firm received a specific package. Useful if a client
-- later claims "my auditor never got it."
CREATE TABLE IF NOT EXISTS audit_package_downloads (
    download_id     BIGSERIAL PRIMARY KEY,
    package_id      UUID NOT NULL REFERENCES audit_packages(package_id),
    downloaded_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    downloader      TEXT NOT NULL,      -- email / 'auditor-link' / client_user_email
    ip_address      INET,
    user_agent      TEXT,
    referrer        TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_package_downloads_package
    ON audit_package_downloads(package_id, downloaded_at DESC);

COMMIT;
