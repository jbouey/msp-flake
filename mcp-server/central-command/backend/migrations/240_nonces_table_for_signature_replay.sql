-- Migration 240 — v40.4 (2026-04-23) — create `nonces` table for
-- signature-auth anti-replay protection.
--
-- signature_auth.py _nonce_seen / _record_nonce write and read this
-- table on every signed request. The comment says it "reuses the
-- existing order-replay tracker" but that tracker was never
-- materialized as a `public.nonces` relation in this deployment.
-- Every /api/watchdog/bootstrap (and any other sigauth-gated
-- endpoint) has been raising asyncpg UndefinedTableError since the
-- code shipped, silently degrading to whatever the try/except path
-- produces — in this case, a 500 that stalls daemon watchdog
-- bootstrap permanently. Audit item #12.
--
-- Schema: composite-prefix nonce string (fingerprint-scoped in
-- signature_auth.py so different key rotations don't collide) +
-- created_at for TTL sweep. PRIMARY KEY on nonce gives us free
-- ON CONFLICT DO NOTHING for idempotent INSERT.

BEGIN;

CREATE TABLE IF NOT EXISTS nonces (
    nonce       TEXT PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_nonces_created_at ON nonces (created_at);

COMMENT ON TABLE nonces IS
    'Anti-replay nonce cache for signature-auth (signature_auth.py). '
    'Rows older than NONCE_TTL (~10 min) are safe to prune; see '
    'assertions.py + substrate_runbooks for the cleanup job.';

SELECT apply_migration(
    240,
    'nonces table for signature-auth anti-replay (fixes watchdog bootstrap 500)'
);

COMMIT;
