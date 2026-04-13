-- Migration 178: Magic-link approval tokens (Phase 14 T2.1)
--
-- Tracks the single-use state of each magic link issued by the
-- notifier. Tokens themselves carry HMAC-signed state; this table
-- is the used-vs-unused ledger and is consulted on every click to
-- prevent replay.

BEGIN;

CREATE TABLE IF NOT EXISTS privileged_access_magic_links (
    token_id           VARCHAR(64)  PRIMARY KEY,
    request_id         UUID         NOT NULL REFERENCES privileged_access_requests(id) ON DELETE CASCADE,
    action             VARCHAR(8)   NOT NULL,   -- 'approve' | 'reject'
    target_user_email  VARCHAR(255) NOT NULL,   -- which client admin it's for
    issued_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    expires_at         TIMESTAMPTZ  NOT NULL,
    consumed_at        TIMESTAMPTZ,             -- NULL = still usable
    consumed_by_ip     VARCHAR(45),
    consumed_by_ua     VARCHAR(512)
);

CREATE INDEX IF NOT EXISTS idx_magic_links_pending
    ON privileged_access_magic_links (request_id)
    WHERE consumed_at IS NULL;

COMMIT;
