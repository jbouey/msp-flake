-- Partner weekly digest opt-in (Session 206 round-table P2).
--
-- Adds an opt-in flag so partners can turn off the Friday digest. Defaults
-- to TRUE for all existing partners — they're opted in by default, which
-- is the sales-team's ask. Partners turn off via /me/digest-prefs endpoint.

BEGIN;

ALTER TABLE partners
    ADD COLUMN IF NOT EXISTS digest_enabled BOOLEAN DEFAULT TRUE;

-- Default the existing NULL values to true for back-compat
UPDATE partners SET digest_enabled = TRUE WHERE digest_enabled IS NULL;

COMMIT;
