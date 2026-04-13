-- Migration 173: Signature-verification fingerprint tracking (Phase 13)
--
-- Session 205 round-table finding: fleet-order signature verification is
-- failing on some appliances because the daemon's cached server pubkey
-- doesn't match the current signing key. We had no way to diagnose
-- WHICH appliances were affected without capturing the pubkey state
-- on every checkin.
--
-- This migration adds two columns to site_appliances:
--   server_pubkey_fingerprint_seen    — 16-hex-char prefix of the
--                                       server_public_key we sent on the
--                                       most recent checkin response
--   server_pubkey_fingerprint_seen_at — when that was
--
-- The checkin handler (sites.py) stamps these fields. A new Prometheus
-- gauge + admin endpoint reports divergence: appliances whose most
-- recently seen fingerprint doesn't match the current server key.
--
-- Non-goal: this does NOT track the daemon's ACTUAL cached pubkey —
-- that would require a daemon-side probe response. What we track is
-- what we DELIVERED on the last checkin. If the daemon honors checkin
-- responses correctly, these values converge. If they diverge, the
-- daemon has a bug (H3/H4 daemon hardening work).

BEGIN;

ALTER TABLE site_appliances
    ADD COLUMN IF NOT EXISTS server_pubkey_fingerprint_seen     VARCHAR(16),
    ADD COLUMN IF NOT EXISTS server_pubkey_fingerprint_seen_at  TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_site_appliances_pubkey_seen
    ON site_appliances (server_pubkey_fingerprint_seen)
    WHERE server_pubkey_fingerprint_seen IS NOT NULL;

COMMIT;
