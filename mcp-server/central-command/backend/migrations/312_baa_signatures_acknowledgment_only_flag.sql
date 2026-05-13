-- Migration 312: baa_signatures.is_acknowledgment_only flag
--
-- Counsel TOP-PRIORITY P0 (Task #56) — 2026-05-13.
--
-- BACKGROUND: prior to 2026-05-13 v1.0-INTERIM master BAA, the platform
-- collected customer signatures on a 5-bullet click-through acknowledgment
-- statement (`SignupBaa.tsx:11-15` ACKNOWLEDGMENT_TEXT). Per outside
-- counsel's 2026-05-13 review, those acknowledgments "likely constitute
-- evidence of intent and part performance, but are insufficient as a
-- complete HIPAA BAA." There is a "term certainty gap" vs §164.504(e).
--
-- The v1.0-INTERIM master BAA (docs/legal/MASTER_BAA_v1.0_INTERIM.md)
-- supersedes the prior acknowledgment via Article 8 (Bridge Clause).
-- Customers re-sign within 30 days of v1.0-INTERIM effective date.
--
-- This migration adds a flag to baa_signatures so platform-internal
-- claim-logic ("BAA on file" assertions in audit_report.py +
-- client_attestation_letter.py + partner_portfolio_attestation.py +
-- client_portal.py + partner_baa_roster) can distinguish:
--   - is_acknowledgment_only = TRUE  →  pre-v2.0 click-through;
--                                       claim-logic must NOT assert "BAA on file"
--   - is_acknowledgment_only = FALSE →  v2.0+ formal BAA signature;
--                                       claim-logic asserts "BAA on file"
--
-- Backfill: every existing row with baa_version='v1.0-2026-04-15'
-- (the click-through acknowledgment era) is set to TRUE. New rows
-- with baa_version >= 'v2.0-2026-05-13' default FALSE.
--
-- IMPORTANT: this migration is platform-internal. It does NOT change
-- customer-facing surfaces by itself. The claim-logic updates that
-- consume this flag are landed in a separate commit per the bedrock
-- TWO-GATE protocol.

BEGIN;

-- Add the flag with DEFAULT TRUE. PostgreSQL's ALTER TABLE ... ADD
-- COLUMN ... DEFAULT semantics populate the new column with TRUE for
-- every existing row WITHOUT issuing UPDATE statements (metadata-only
-- backfill since PG11). This is intentional: `baa_signatures` is
-- append-only via `prevent_baa_signature_modification()` (mig 224) to
-- enforce HIPAA §164.316(b)(2)(i) 7-year retention. Any explicit
-- UPDATE would be REJECTED by the trigger.
--
-- Per outside counsel 2026-05-13: every row with
-- baa_version='v1.0-2026-04-15' represents a click-through
-- acknowledgment, NOT a formal HIPAA BAA signature. The DEFAULT TRUE
-- correctly classifies all pre-v2.0 rows.
ALTER TABLE baa_signatures
    ADD COLUMN IF NOT EXISTS is_acknowledgment_only BOOLEAN NOT NULL DEFAULT TRUE;

-- Flip the column default to FALSE going forward. New rows must be
-- explicit about which class they are; default-FALSE ensures any
-- v2.0+ signature path that doesn't set the flag explicitly is
-- treated as a real BAA signature (the safer-when-omitted direction
-- once v2.0 is live). This is a metadata change — does NOT touch
-- existing rows, so does NOT trigger the append-only block.
--
-- Two-step rationale: (1) the column had to land with DEFAULT TRUE
-- so the backfill is no-op-safe; (2) flipping default to FALSE
-- ensures NEW v2.0+ rows are treated as formal-BAA-signed by
-- default — the claim-logic in audit_report.py + 4 other modules
-- treats acknowledgment_only=TRUE as "do not assert BAA on file."
ALTER TABLE baa_signatures
    ALTER COLUMN is_acknowledgment_only SET DEFAULT FALSE;

-- Index for claim-logic queries that filter by "real BAA signers only."
CREATE INDEX IF NOT EXISTS idx_baa_signatures_acknowledgment_only
    ON baa_signatures (email, is_acknowledgment_only);

-- Audit trail row documenting the migration's intent for any future
-- replay or compliance review.
INSERT INTO admin_audit_log (
    user_id, username, action, target, details, ip_address
) VALUES (
    NULL,
    'system',
    'baa_signatures_schema_update',
    'baa_signatures.is_acknowledgment_only',
    jsonb_build_object(
        'migration', '312_baa_signatures_acknowledgment_only_flag',
        'reason', 'Counsel TOP-PRIORITY P0 — distinguish click-through acknowledgments from v2.0 formal BAA signatures',
        'effective_date', '2026-05-13',
        'baa_version_marker', 'v1.0-2026-04-15',
        'baa_version_v2_target', 'v2.0-2026-05-13',
        'counsel_review_artifact', 'audit/outside-counsel-review-baa-drafting-2026-05-13.md',
        'master_baa_location', 'docs/legal/MASTER_BAA_v1.0_INTERIM.md'
    ),
    NULL
);

COMMIT;
