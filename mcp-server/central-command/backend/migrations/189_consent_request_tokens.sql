-- Migration 189 — consent request tokens (Phase 4 UI).
--
-- Partners initiate consent flows by asking the client to approve via
-- a single-use magic link. Tokens are hashed-at-rest (sha256) — the
-- raw token never persists. Expires in 72h if unused.
--
-- Lifecycle:
--   1. partner POST /api/partners/me/consent/request  → row inserted
--   2. client receives email with link containing raw token
--   3. client POST /api/consent/approve/{token}        → consumed_at set
--   4. 72h expiry loop marks unconsumed stale tokens   → consumed_at NULL + expires_at in past
--
-- Retention: rows persist even after consumption — auditors need to
-- see the full consent-request lineage. Delete is blocked by the
-- existing audit trigger pattern (add to migration 151 allowlist in
-- Phase 4 hardening).

BEGIN;

CREATE TABLE IF NOT EXISTS consent_request_tokens (
    token_hash            TEXT PRIMARY KEY,                 -- sha256(raw token)
    site_id               TEXT NOT NULL REFERENCES sites(site_id) ON DELETE CASCADE,
    class_id              TEXT NOT NULL REFERENCES runbook_classes(class_id),
    requested_by_email    TEXT NOT NULL,                    -- partner asking
    requested_for_email   TEXT NOT NULL,                    -- customer who will approve
    requested_ttl_days    INT DEFAULT 365,                  -- TTL requested by partner
    expires_at            TIMESTAMPTZ NOT NULL,             -- default NOW() + 72h
    consumed_at           TIMESTAMPTZ,
    consumed_consent_id   UUID REFERENCES runbook_class_consent(consent_id),
    created_at            TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_consent_request_tokens_site
    ON consent_request_tokens(site_id);
CREATE INDEX IF NOT EXISTS ix_consent_request_tokens_expires
    ON consent_request_tokens(expires_at) WHERE consumed_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_consent_request_tokens_partner
    ON consent_request_tokens(requested_by_email);

-- Append-only audit invariant — DELETE is blocked at the DB layer.
-- Mirror of the pattern in migration 151 (prevent_audit_deletion).
-- Keeps the consent-request lineage complete for auditors.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'prevent_audit_deletion') THEN
        DROP TRIGGER IF EXISTS trg_no_delete_consent_tokens ON consent_request_tokens;
        CREATE TRIGGER trg_no_delete_consent_tokens
            BEFORE DELETE ON consent_request_tokens
            FOR EACH ROW EXECUTE FUNCTION prevent_audit_deletion();
    END IF;
END $$;

COMMIT;
