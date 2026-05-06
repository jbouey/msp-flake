-- Migration 282: dual-admin governance for attestation-gated flag-flip
--
-- Outside HIPAA counsel adversarial review (2026-05-06): the
-- feature-flag toggle is the legal-sensitivity choke point of the
-- entire RT21 design. A single admin enabling a legally sensitive
-- capability — even with attested reason and audit-log row — sits
-- outside the cryptographic chain we market as our strongest
-- evidentiary layer. Counsel's recommended hardening: dual control.
-- Two distinct admins: one PROPOSES, the other APPROVES. Same admin
-- cannot self-approve.
--
-- This migration adds proposal-side columns to feature_flags and
-- tightens the CHECK constraint accordingly. The endpoint splits
-- into propose-enable + approve-enable (two API calls, two distinct
-- admin sessions). The DB-layer CHECK is the last-line-of-defense:
-- even if application code is compromised, the schema rejects a row
-- where approver = proposer.

ALTER TABLE feature_flags
    ADD COLUMN IF NOT EXISTS enable_proposed_by_email TEXT,
    ADD COLUMN IF NOT EXISTS enable_proposed_at TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS enable_proposed_reason TEXT;

-- Drop the old single-admin enable CHECK + replace with the dual-admin
-- shape. Same length-floor (≥40 chars on the approver's reason field
-- where the legal-opinion identifier lives) + new constraint that
-- the approver MUST be a different person from the proposer.
ALTER TABLE feature_flags DROP CONSTRAINT IF EXISTS feature_flags_check;

-- Postgres autogenerates the constraint name as feature_flags_check
-- when an inline CHECK has no explicit name. We dropped it; redefine
-- with the dual-admin shape.
ALTER TABLE feature_flags
    ADD CONSTRAINT feature_flags_dual_admin_check CHECK (
        enabled = false
        OR (
            -- approve side
            enabled_by_email IS NOT NULL
            AND enable_reason IS NOT NULL
            AND length(enable_reason) >= 40
            AND enabled_at IS NOT NULL
            -- propose side
            AND enable_proposed_by_email IS NOT NULL
            AND enable_proposed_at IS NOT NULL
            AND enable_proposed_reason IS NOT NULL
            AND length(enable_proposed_reason) >= 20
            -- distinct admins (dual control). Counsel's hardening.
            AND lower(enabled_by_email) <> lower(enable_proposed_by_email)
        )
    );

COMMENT ON COLUMN feature_flags.enable_proposed_by_email IS
    'Migration 282: first admin who PROPOSED enabling this flag. Must be '
    'distinct from enabled_by_email (the approver) per the dual-admin '
    'CHECK. Set by the propose-enable endpoint; cleared on disable.';

COMMENT ON COLUMN feature_flags.enable_proposed_at IS
    'Migration 282: timestamp the first admin proposed enabling this flag.';

COMMENT ON COLUMN feature_flags.enable_proposed_reason IS
    'Migration 282: reason supplied by the proposer (>=20 chars). The '
    'separate approver reason on `enable_reason` carries the legal-opinion '
    'identifier (>=40 chars).';
