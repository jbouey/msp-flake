-- Migration 232: per-partner email-from display name + reply-to.
--
-- Ships the code surface for white-labeled partner email in a DKIM/SPF-safe
-- posture. The SMTP envelope sender (and therefore DKIM-signing identity)
-- remains OsirisCare's authorized sender — we do NOT let a partner spoof an
-- arbitrary From address without owning the domain. What we DO allow, and
-- what real MSPs actually ask for by name:
--
--   1. Custom display name. The recipient sees "Scranton IT Services" in
--      their inbox instead of "OsirisCare Compliance." This is pure RFC 5322
--      display-name handling; no DKIM/SPF impact.
--
--   2. Custom Reply-To header. Replies route to the MSP's own support alias,
--      so the MSP owns the customer conversation end-to-end.
--
-- Future (intentionally not in this migration): custom envelope From
-- requires per-partner DKIM keys + SPF include records + DMARC alignment.
-- That's the Postmark integration from the onboarding-rationale PDF
-- (Tier 2 — 6-to-8-week horizon, not a day-1 P1 item).

BEGIN;

ALTER TABLE partners
    ADD COLUMN IF NOT EXISTS email_from_display_name TEXT,
    ADD COLUMN IF NOT EXISTS email_reply_to_address TEXT;

COMMENT ON COLUMN partners.email_from_display_name IS
    'RFC 5322 display name shown in recipient inbox for partner-originated '
    'email. Envelope From stays on OsirisCare SMTP identity for DKIM/SPF '
    'alignment. NULL = fall back to global OsirisCare display name.';

COMMENT ON COLUMN partners.email_reply_to_address IS
    'Reply-To header for partner-originated email. Lets the MSP own the '
    'customer conversation while we send through our authorized SMTP. '
    'NULL = fall back to SMTP_FROM.';

-- Guard: reply-to must look like an email if set. Loose RFC 5322 compliance
-- is not worth the CHECK constraint complexity; bare sanity check only.
ALTER TABLE partners
    DROP CONSTRAINT IF EXISTS partners_email_reply_to_shape;
ALTER TABLE partners
    ADD CONSTRAINT partners_email_reply_to_shape CHECK (
        email_reply_to_address IS NULL
        OR email_reply_to_address ~* '^[^@\s]+@[^@\s]+\.[^@\s]+$'
    );

COMMIT;
