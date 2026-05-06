-- Migration 283: BAA receipt-authorization signature for cross-org relocate
--
-- Outside HIPAA counsel approved the cross-org relocate feature
-- (2026-05-06) contingent on five conditions, four of which were
-- already engineering-shipped. Condition #2 is the one this migration
-- hardens:
--
--   "the receiving organization's BAA or addendum expressly
--    authorizes receipt and continuity of transferred site
--    compliance records/evidence"
--
-- Today's `client_orgs.baa_on_file BOOLEAN` (mig 124) confirms a BAA
-- exists. Counsel's #2 wants confirmation that the BAA EXPRESSLY
-- covers received-site continuity — a stronger predicate that
-- requires per-org contracts-team review.
--
-- This migration adds a signature-id column. It points at a row in
-- the existing `baa_signatures` table (mig 224) that was signed AFTER
-- contracts confirmed the receiving-site language is present
-- (either in the standard BAA or in a signed addendum). The
-- target-accept + execute endpoints refuse to advance if this column
-- is NULL on the target org.
--
-- Why a signature_id (not just a boolean):
--   - Audit trail: contracts-team flip is anchored to a specific
--     signed document with timestamp + IP + UA + sha256 of the
--     BAA text (the existing baa_signatures.signature_id contract).
--   - Auditors walking a relocate completion can trace all the way
--     back to the signed authorization that licensed the receipt.
--   - Matches the existing `signup_sessions.baa_signature_id`
--     pattern (mig 224 line 33). PARITY rule.

ALTER TABLE client_orgs
    ADD COLUMN IF NOT EXISTS baa_relocate_receipt_signature_id TEXT
        REFERENCES baa_signatures(signature_id);

ALTER TABLE client_orgs
    ADD COLUMN IF NOT EXISTS baa_relocate_receipt_authorized_at
        TIMESTAMP WITH TIME ZONE;

-- Brief explanation: contracts-team reviewer who flipped the column.
ALTER TABLE client_orgs
    ADD COLUMN IF NOT EXISTS baa_relocate_receipt_authorized_by_email TEXT;

-- Optional pointer to addendum-specific signature (if the standard
-- BAA didn't cover receipt + an addendum was signed afterward).
-- Same FK target — the addendum lives in the same baa_signatures
-- table with its own SHA256 of the addendum text.
ALTER TABLE client_orgs
    ADD COLUMN IF NOT EXISTS baa_relocate_receipt_addendum_signature_id TEXT
        REFERENCES baa_signatures(signature_id);

COMMENT ON COLUMN client_orgs.baa_relocate_receipt_signature_id IS
    'Migration 283 (counsel approval contingency #2, 2026-05-06): '
    'signature_id of the BAA (or addendum) that EXPRESSLY authorizes '
    'this org to receive a transferred site under cross-org relocate. '
    'NULL means "BAA on file but not reviewed for receipt language" — '
    'the relocate flow refuses to set this org as a target until '
    'contracts-team review completes. Different from `baa_on_file` '
    '(mig 124) which only confirms a BAA exists.';

COMMENT ON COLUMN client_orgs.baa_relocate_receipt_authorized_at IS
    'Migration 283: timestamp when contracts-team flipped this org '
    'into "authorized to receive transferred sites" state.';

COMMENT ON COLUMN client_orgs.baa_relocate_receipt_authorized_by_email IS
    'Migration 283: contracts-team reviewer who confirmed the BAA '
    'language and flipped the authorization.';

COMMENT ON COLUMN client_orgs.baa_relocate_receipt_addendum_signature_id IS
    'Migration 283: optional pointer to an addendum-specific '
    'signature, when receipt-authorization came from an addendum '
    'rather than the standard BAA. NULL when standard BAA covered '
    'receipt language. Both signature_ids land in baa_signatures.';

-- Selective index — most orgs will not be relocate targets.
CREATE INDEX IF NOT EXISTS idx_client_orgs_baa_relocate_receipt_signed
    ON client_orgs (baa_relocate_receipt_signature_id)
    WHERE baa_relocate_receipt_signature_id IS NOT NULL;
