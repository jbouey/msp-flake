-- Migration 211: sigauth_observations metric source for Week 4
--
-- Captures every signature-auth verification result so the substrate
-- engine can compute fail rates per site over a sliding window. One
-- row per checkin during soak; the assertions loop sweeps rows older
-- than 24h every tick to bound table size.
--
-- Volume math: 4 appliances × 1 checkin/60s × 86400s/day = 5,760
-- rows/day at present fleet size. With 24h TTL the steady-state row
-- count never exceeds ~6k. Cheap.
--
-- Schema is intentionally narrow — purpose-built for the
-- signature_verification_failures invariant. NOT a general audit
-- log; the existing admin_audit_log handles auditable events.

BEGIN;

CREATE TABLE IF NOT EXISTS sigauth_observations (
    id            BIGSERIAL PRIMARY KEY,
    site_id       VARCHAR(50),
    mac_address   VARCHAR(17),
    valid         BOOLEAN     NOT NULL,
    reason        VARCHAR(40) NOT NULL DEFAULT '',
    fingerprint   VARCHAR(16),
    observed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE sigauth_observations IS
    'Per-checkin signature verification outcomes. TTL 24h enforced '
    'by assertions_loop. Powers the signature_verification_failures '
    'substrate invariant.';

CREATE INDEX IF NOT EXISTS sigauth_observations_recent_idx
    ON sigauth_observations (observed_at DESC);

CREATE INDEX IF NOT EXISTS sigauth_observations_site_recent_idx
    ON sigauth_observations (site_id, observed_at DESC);

-- Application role can INSERT (per checkin), SELECT (assertion read),
-- DELETE (TTL sweep). Never UPDATE — observations are immutable.
GRANT SELECT, INSERT, DELETE ON sigauth_observations TO mcp_app;
GRANT USAGE, SELECT ON SEQUENCE sigauth_observations_id_seq TO mcp_app;

COMMIT;

SELECT 'Migration 211_sigauth_observations complete' AS status;
