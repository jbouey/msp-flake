-- Migration 228: partner_agreements — MSA + Subcontractor BAA + Reseller Addendum
--
-- The three legal artifacts that establish the non-operator partner chain
-- (see memory/feedback_non_operator_partner_posture.md):
--
--   msa                 — Master Software License + Services Agreement
--                         (tool vendor scope, liability capped at 12mo fees,
--                          no patient-harm indemnification)
--   subcontractor_baa   — narrow Subcontractor BAA with the MSP (the MSP
--                         holds the direct BAA with its clinic; OsirisCare
--                         is subcontractor to the MSP, NEVER direct-to-CE).
--                         Scope: "protected evidence metadata" only; PHI
--                         scrubbed at appliance egress via phiscrub package.
--   reseller_addendum   — licensing, margin, brand usage, termination,
--                         client-data portability at off-boarding.
--
-- Mirrors the baa_signatures pattern (Migration 224): append-only, SHA256
-- committed text, 7-year retention, IP + UA captured. All three artifacts
-- MUST be signed before partner can invite clinics (Batch C enforcement).
--
-- Non-operator posture is enforced at FOUR layers:
--   1. contracts (this migration)
--   2. code (phiscrub at appliance, subcontractor-scope API semantics)
--   3. UI language (monitor/attest/help-detect, NEVER enforce/guarantee)
--   4. business flow (MSP owns clinic relationship, OsirisCare bills the MSP)

BEGIN;

-- ─── partner_agreements ──────────────────────────────────────────────
-- One row per (partner_id, agreement_type, version) combination.
-- A partner re-signs on version bump — old signatures remain bound to
-- the hash they saw, so we can always prove what they agreed to.

CREATE TABLE IF NOT EXISTS partner_agreements (
    agreement_id         TEXT         PRIMARY KEY,                   -- UUID
    partner_id           UUID         NOT NULL,
    agreement_type       TEXT         NOT NULL,
    version              TEXT         NOT NULL,                      -- e.g., "msa-v1.0-2026-04-17"
    text_sha256          TEXT         NOT NULL,                      -- hex digest of the agreement text shown
    signer_name          TEXT         NOT NULL,                      -- typed name in the e-sign form
    signer_email         TEXT         NOT NULL,                      -- must match partner_users.email
    signer_ip            TEXT,
    signer_user_agent    TEXT,
    signer_role          TEXT,                                       -- partner role at time of signing
    metadata             JSONB        NOT NULL DEFAULT '{}'::jsonb,
    signed_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    effective_until      TIMESTAMPTZ,                                -- null = perpetual, set on termination

    CONSTRAINT partner_agreements_type_ck CHECK (
        agreement_type IN ('msa','subcontractor_baa','reseller_addendum')
    ),
    CONSTRAINT partner_agreements_sha256_ck CHECK (length(text_sha256) = 64),
    CONSTRAINT partner_agreements_partner_fk FOREIGN KEY (partner_id)
        REFERENCES partners(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_partner_agreements_partner
    ON partner_agreements (partner_id, agreement_type, signed_at DESC);
CREATE INDEX IF NOT EXISTS idx_partner_agreements_type
    ON partner_agreements (agreement_type, signed_at DESC);
CREATE INDEX IF NOT EXISTS idx_partner_agreements_effective
    ON partner_agreements (partner_id, agreement_type)
    WHERE effective_until IS NULL;

-- Append-only: DELETE + UPDATE blocked by trigger. 7-year HIPAA retention.
-- UPDATE of effective_until is permitted ONLY via a scoped bypass
-- (`SET LOCAL app.allow_partner_agreement_termination='true'`) so that the
-- "mark terminated" admin action is auditable but not silent.
CREATE OR REPLACE FUNCTION prevent_partner_agreement_modification()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    -- Permit UPDATE only when the scoped bypass is set AND only effective_until changed
    IF TG_OP = 'UPDATE' THEN
        IF current_setting('app.allow_partner_agreement_termination', true) = 'true'
           AND NEW.agreement_id = OLD.agreement_id
           AND NEW.partner_id = OLD.partner_id
           AND NEW.agreement_type = OLD.agreement_type
           AND NEW.version = OLD.version
           AND NEW.text_sha256 = OLD.text_sha256
           AND NEW.signer_name = OLD.signer_name
           AND NEW.signed_at = OLD.signed_at THEN
            RETURN NEW;  -- only effective_until (and maybe metadata) changed
        END IF;
    END IF;
    RAISE EXCEPTION
        'partner_agreements is append-only (HIPAA §164.316(b)(2)(i) 7-year retention). '
        'Attempted % on agreement_id=%. '
        'Terminations must set app.allow_partner_agreement_termination=true and '
        'only mutate effective_until.',
        TG_OP, COALESCE(OLD.agreement_id, NEW.agreement_id);
END;
$$;

DROP TRIGGER IF EXISTS trg_partner_agreements_no_modify ON partner_agreements;
CREATE TRIGGER trg_partner_agreements_no_modify
    BEFORE UPDATE OR DELETE ON partner_agreements
    FOR EACH ROW EXECUTE FUNCTION prevent_partner_agreement_modification();

COMMENT ON TABLE partner_agreements IS
    'MSA + Subcontractor BAA + Reseller Addendum signatures for partners. '
    'Append-only, SHA256-committed, 7-year retention. Partner cannot invite '
    'clinics until ALL THREE are signed with current versions (enforced by '
    'require_active_partner_agreements() in partners.py).';

-- Helper view: partner's currently-effective agreements (one row per type)
CREATE OR REPLACE VIEW v_partner_active_agreements AS
SELECT DISTINCT ON (partner_id, agreement_type)
       agreement_id,
       partner_id,
       agreement_type,
       version,
       text_sha256,
       signer_name,
       signer_email,
       signed_at,
       effective_until
  FROM partner_agreements
 WHERE effective_until IS NULL OR effective_until > NOW()
 ORDER BY partner_id, agreement_type, signed_at DESC;

COMMIT;
