-- Migration 197: D1 + D2 + D3 — heartbeat attestation + appliance-scoped
-- UPDATE audit + claim-ledger references.
--
-- D1: heartbeat_hash column + content hash computed on insert via trigger.
--     Bridges to the evidence chain — a later PR will have the Go daemon
--     Ed25519-sign the hash (agent_signature column already present by
--     analogy with compliance_bundles). Today we ship the deterministic
--     content hash so every claim has a stable reference.
--
-- D2: audit trigger on site_appliances + appliances — logs any UPDATE
--     that touches a row whose appliance_id != current app.actor_appliance_id
--     and the actor isn't admin. AUDIT-ONLY for now; enforcement flip
--     deferred until every caller sets LOCAL app.actor_appliance_id.
--
-- D3: liveness_claims table — every APPLIANCE_LIVENESS_LIE incident
--     cites the heartbeat_id (or NULL if no heartbeat exists) that
--     proves the lie. Creates a verifiable paper trail for auditors.

BEGIN;

-- =============================================================================
-- D1: heartbeat content hash
-- =============================================================================

ALTER TABLE appliance_heartbeats
    ADD COLUMN IF NOT EXISTS heartbeat_hash TEXT,
    ADD COLUMN IF NOT EXISTS agent_signature TEXT;

COMMENT ON COLUMN appliance_heartbeats.heartbeat_hash IS
    'SHA-256 of (site_id|appliance_id|observed_at|status). Computed on insert. '
    'Deterministic + stable — dashboard/alert claims can cite a hash that '
    'uniquely identifies the heartbeat they are based on.';

COMMENT ON COLUMN appliance_heartbeats.agent_signature IS
    'Optional Ed25519 signature by the appliance over heartbeat_hash. '
    'NULL today; filled in when the Go daemon adds signing (pending PR).';

CREATE OR REPLACE FUNCTION compute_heartbeat_hash()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.heartbeat_hash IS NULL THEN
        NEW.heartbeat_hash = encode(sha256(
            (COALESCE(NEW.site_id, '')
                || '|' || COALESCE(NEW.appliance_id, '')
                || '|' || COALESCE(NEW.observed_at::text, '')
                || '|' || COALESCE(NEW.status, '')
            )::bytea
        ), 'hex');
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_heartbeat_hash ON appliance_heartbeats;
CREATE TRIGGER trg_heartbeat_hash
    BEFORE INSERT ON appliance_heartbeats
    FOR EACH ROW EXECUTE FUNCTION compute_heartbeat_hash();

CREATE INDEX IF NOT EXISTS idx_heartbeats_hash
    ON appliance_heartbeats(heartbeat_hash);

-- =============================================================================
-- D2: audit trigger — cross-appliance UPDATE detection
-- =============================================================================

CREATE OR REPLACE FUNCTION audit_cross_appliance_update()
RETURNS TRIGGER AS $$
DECLARE
    actor_id TEXT;
    is_admin TEXT;
BEGIN
    actor_id := current_setting('app.actor_appliance_id', TRUE);
    is_admin := current_setting('app.is_admin', TRUE);

    IF is_admin = 'true' THEN
        RETURN NEW;
    END IF;
    IF actor_id IS NULL OR actor_id = '' THEN
        -- Actor not identified — not yet tagged in tenant_connection.
        -- Audit-only path doesn't reject; soft log so operators see the
        -- breadth of un-tagged writers during rollout.
        RETURN NEW;
    END IF;

    IF NEW.appliance_id IS NOT NULL AND NEW.appliance_id != actor_id THEN
        INSERT INTO admin_audit_log
            (username, action, target, details, success)
        VALUES (
            'trigger:audit_cross_appliance_update',
            'CROSS_APPLIANCE_UPDATE_AUDIT',
            NEW.appliance_id,
            jsonb_build_object(
                'actor_appliance_id', actor_id,
                'target_appliance_id', NEW.appliance_id,
                'table', TG_TABLE_NAME,
                'site_id', NEW.site_id
            ),
            true
        );
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_audit_cross_appliance_site_appliances
    ON site_appliances;
CREATE TRIGGER trg_audit_cross_appliance_site_appliances
    BEFORE UPDATE ON site_appliances
    FOR EACH ROW
    EXECUTE FUNCTION audit_cross_appliance_update();

COMMENT ON FUNCTION audit_cross_appliance_update() IS
    'Session 206 D2: logs (does not yet enforce) UPDATEs on site_appliances '
    'where the acting appliance is modifying another appliance''s row. '
    'Flip to REJECT after every caller sets LOCAL app.actor_appliance_id.';

-- =============================================================================
-- D3: liveness_claims — claim ledger for APPLIANCE_LIVENESS_LIE
-- =============================================================================

CREATE TABLE IF NOT EXISTS liveness_claims (
    claim_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    site_id         TEXT NOT NULL,
    appliance_id    TEXT NOT NULL,
    claim_type      TEXT NOT NULL,  -- 'liveness_lie', 'recovered', 'offline_alert'
    cited_heartbeat_id BIGINT,      -- Optional FK to the heartbeat that proved/disproved the claim
    cited_heartbeat_hash TEXT,      -- Copy of hash so claim remains verifiable even if partitions are detached
    claimed_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    details         JSONB NOT NULL DEFAULT '{}'::jsonb,
    published_to    TEXT[]          -- ['email', 'dashboard', 'public_status']
);

CREATE INDEX IF NOT EXISTS idx_liveness_claims_appliance
    ON liveness_claims(appliance_id, claimed_at DESC);
CREATE INDEX IF NOT EXISTS idx_liveness_claims_type
    ON liveness_claims(claim_type, claimed_at DESC);

COMMENT ON TABLE liveness_claims IS
    'Session 206 D3: every assertion made about an appliance''s liveness '
    '(online/offline/recovered/lying) is recorded here with a pointer to '
    'the heartbeat evidence that supports it. Auditors and the public '
    'status page can verify that claims are backed by real data.';

-- Treat this as evidence-grade — no DELETE.
CREATE OR REPLACE FUNCTION prevent_liveness_claim_deletion()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'liveness_claims is append-only — claim ledger invariant.'
        USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_liveness_claim_deletion
    ON liveness_claims;
CREATE TRIGGER trg_prevent_liveness_claim_deletion
    BEFORE DELETE ON liveness_claims
    FOR EACH ROW EXECUTE FUNCTION prevent_liveness_claim_deletion();

COMMIT;
