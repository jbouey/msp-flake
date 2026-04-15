-- Migration 210: provisioning claim events + ISO release CA registry + revocations
--
-- Week 1 of the composed identity/authorization/attestation stack.
-- Forward-compatible schema:
--   * Columns for hash-chain + OTS anchoring exist, NULL-allowed for
--     Week 1; Week 3 migration populates and adds NOT NULL constraints.
--   * claim_signature_b64 is NULL-allowed on bootstrap (existing
--     appliances that haven't re-enrolled yet); post-soak migration
--     backfills + enforces.
--
-- Tables:
--   provisioning_claim_events  — append-only ledger of every claim/
--                                enrollment/rotation event. One row
--                                per (mac + key) binding transition.
--   iso_release_ca_pubkeys     — registry of Ed25519 CAs that have
--                                authority to sign claim certs. One row
--                                per ISO release; revoke by setting
--                                revoked_at.
--   claim_revocations          — admin-initiated key revocations. Per-
--                                appliance; signed by an operator via
--                                the privileged-access chain.
--
-- Triggers:
--   prevent_claim_event_deletion          — classic append-only guard
--   enforce_claim_event_immutability      — UPDATE allowed only when
--                                           claim_signature_b64 IS NULL
--                                           (bootstrap window) OR
--                                           populating chain_hash /
--                                           ots_bundle_id first time.
--
-- Admin role (current_user='mcp') bypasses the Migration 192 row-guard
-- on site_appliances per Migration 208.

BEGIN;

-- =========================================================================
-- provisioning_claim_events — the authoritative ledger of identity binding
-- =========================================================================

CREATE TABLE IF NOT EXISTS provisioning_claim_events (
    id                         BIGSERIAL PRIMARY KEY,
    site_id                    VARCHAR(50)  NOT NULL,
    mac_address                VARCHAR(17)  NOT NULL,
    agent_pubkey_hex           VARCHAR(64)  NOT NULL,
    agent_pubkey_fingerprint   VARCHAR(16)  NOT NULL,
    iso_build_sha              VARCHAR(64),
    hardware_id                VARCHAR(255),
    claim_signature_b64        TEXT,
    claimed_at                 TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    supersedes_id              BIGINT REFERENCES provisioning_claim_events(id),
    ots_bundle_id              TEXT,
    chain_prev_hash            VARCHAR(64),
    chain_hash                 VARCHAR(64),
    source                     VARCHAR(32)  NOT NULL DEFAULT 'enrollment'
                                             CHECK (source IN ('claim','enrollment','rotation','backfill')),
    notes                      JSONB        NOT NULL DEFAULT '{}'::jsonb
);

COMMENT ON TABLE  provisioning_claim_events IS
    'Append-only ledger of every appliance identity binding transition. '
    'One row per (mac, pubkey) pair ever observed. Future reads span '
    'the whole chain; current-binding lookup uses the view.';
COMMENT ON COLUMN provisioning_claim_events.source IS
    'claim = ISO-CA-signed first boot; enrollment = existing appliance '
    'first-time keypair register via soak-mode api_key bridge; '
    'rotation = operator-initiated rekey; backfill = schema migration.';
COMMENT ON COLUMN provisioning_claim_events.chain_prev_hash IS
    'Week 3: extends the compliance_bundles hash chain. NULL during '
    'Week 1-2 bootstrap.';

-- Fast lookup: "what is the current binding for this MAC?"
CREATE INDEX IF NOT EXISTS provisioning_claim_events_site_mac_idx
    ON provisioning_claim_events (site_id, mac_address, claimed_at DESC);

-- Fast lookup by fingerprint (reverse auth — "which appliance does this
-- sig belong to?")
CREATE INDEX IF NOT EXISTS provisioning_claim_events_fingerprint_idx
    ON provisioning_claim_events (agent_pubkey_fingerprint);

-- OTS worker queue: rows pending anchor.
CREATE INDEX IF NOT EXISTS provisioning_claim_events_pending_ots_idx
    ON provisioning_claim_events (claimed_at)
    WHERE ots_bundle_id IS NULL;

-- =========================================================================
-- v_current_appliance_identity — "current binding" view
-- =========================================================================
-- One row per (site_id, mac_address) with the most recent claim event.
-- Used by signature_auth.verify_appliance_signature to resolve the
-- expected pubkey.

CREATE OR REPLACE VIEW v_current_appliance_identity AS
SELECT DISTINCT ON (site_id, mac_address)
       site_id,
       mac_address,
       agent_pubkey_hex,
       agent_pubkey_fingerprint,
       claimed_at        AS bound_at,
       source,
       iso_build_sha
  FROM provisioning_claim_events
 ORDER BY site_id, mac_address, claimed_at DESC;

COMMENT ON VIEW v_current_appliance_identity IS
    'Current identity binding per appliance. Latest row wins; historical '
    'rows remain in provisioning_claim_events for audit.';

GRANT SELECT ON v_current_appliance_identity TO mcp_app;

-- =========================================================================
-- Append-only + immutability triggers
-- =========================================================================

CREATE OR REPLACE FUNCTION prevent_claim_event_deletion()
RETURNS TRIGGER AS $$
BEGIN
    -- Admin role bypass: allows a DBA to hand-correct via psql
    -- (audited outside this path — Session 205 invariant).
    IF current_user = 'mcp' AND
       current_setting('app.allow_claim_mutation', TRUE) = 'true' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION
        'DELETE blocked on provisioning_claim_events (append-only ledger). '
        'Set LOCAL app.allow_claim_mutation=''true'' as admin role if '
        'truly necessary (audit required).',
        USING ERRCODE = 'raise_exception',
              HINT = 'Migration 210 — the identity chain is immutable by design.';
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION enforce_claim_event_immutability()
RETURNS TRIGGER AS $$
BEGIN
    -- Permit UPDATE in two narrow bootstrap cases:
    --   * claim_signature_b64 was NULL and is being populated
    --     (Week 1 enrollment → Week 2 post-hoc signing, rare)
    --   * chain_prev_hash / chain_hash / ots_bundle_id being populated
    --     (Week 3 hash-chain backfill, Merkle anchor workers)
    --
    -- Otherwise reject. Changes to site_id/mac/pubkey are tampering.

    IF OLD.site_id                 IS DISTINCT FROM NEW.site_id
    OR OLD.mac_address             IS DISTINCT FROM NEW.mac_address
    OR OLD.agent_pubkey_hex        IS DISTINCT FROM NEW.agent_pubkey_hex
    OR OLD.agent_pubkey_fingerprint IS DISTINCT FROM NEW.agent_pubkey_fingerprint
    OR OLD.claimed_at              IS DISTINCT FROM NEW.claimed_at
    OR OLD.source                  IS DISTINCT FROM NEW.source
    OR OLD.supersedes_id           IS DISTINCT FROM NEW.supersedes_id
    THEN
        RAISE EXCEPTION
            'UPDATE on provisioning_claim_events is restricted to post-hoc '
            'signature, hash-chain, and OTS anchor columns. Core identity '
            'fields are immutable.',
            USING ERRCODE = 'raise_exception',
                  HINT = 'Migration 210.';
    END IF;

    -- claim_signature_b64 may only transition NULL → non-NULL.
    IF OLD.claim_signature_b64 IS NOT NULL
       AND OLD.claim_signature_b64 IS DISTINCT FROM NEW.claim_signature_b64 THEN
        RAISE EXCEPTION
            'claim_signature_b64 is write-once on provisioning_claim_events',
            USING ERRCODE = 'raise_exception';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_claim_event_deletion ON provisioning_claim_events;
CREATE TRIGGER trg_prevent_claim_event_deletion
    BEFORE DELETE ON provisioning_claim_events
    FOR EACH ROW
    EXECUTE FUNCTION prevent_claim_event_deletion();

DROP TRIGGER IF EXISTS trg_enforce_claim_event_immutability ON provisioning_claim_events;
CREATE TRIGGER trg_enforce_claim_event_immutability
    BEFORE UPDATE ON provisioning_claim_events
    FOR EACH ROW
    EXECUTE FUNCTION enforce_claim_event_immutability();

-- =========================================================================
-- iso_release_ca_pubkeys — registry of ISO release CAs
-- =========================================================================
-- Each ISO release mints a short-lived Ed25519 CA. Public half is
-- registered here; claim certificates presented during first-boot
-- provisioning must be signed by a non-revoked CA in its validity
-- window.

CREATE TABLE IF NOT EXISTS iso_release_ca_pubkeys (
    id                 BIGSERIAL PRIMARY KEY,
    iso_release_sha    VARCHAR(64)  NOT NULL UNIQUE,
    ca_pubkey_hex      VARCHAR(64)  NOT NULL,
    valid_from         TIMESTAMPTZ  NOT NULL,
    valid_until        TIMESTAMPTZ  NOT NULL,
    revoked_at         TIMESTAMPTZ,
    revoked_reason     TEXT,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    notes              JSONB        NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT iso_ca_validity_window_sensible
        CHECK (valid_until > valid_from)
);

COMMENT ON TABLE iso_release_ca_pubkeys IS
    'Ed25519 CAs authorized to sign claim certs. Empty until Week 2 '
    'ships the ISO CA plumbing. Revoke a release by setting revoked_at.';

CREATE INDEX IF NOT EXISTS iso_release_ca_pubkeys_active_idx
    ON iso_release_ca_pubkeys (valid_until)
    WHERE revoked_at IS NULL;

-- =========================================================================
-- claim_revocations — admin-initiated key revocations
-- =========================================================================

CREATE TABLE IF NOT EXISTS claim_revocations (
    id                          BIGSERIAL PRIMARY KEY,
    appliance_id                VARCHAR(80)  NOT NULL,
    agent_pubkey_fingerprint    VARCHAR(16)  NOT NULL,
    agent_pubkey_hex            VARCHAR(64)  NOT NULL,
    revoked_at                  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    revoked_by                  VARCHAR(100) NOT NULL,
    reason                      TEXT         NOT NULL,
    revocation_signature_b64    TEXT,
    ots_bundle_id               TEXT
);

COMMENT ON TABLE claim_revocations IS
    'Admin-initiated key revocations. Substrate invariant fires if a '
    'revoked key ever produces a valid signature afterwards (indicates '
    'leaked private key).';

CREATE INDEX IF NOT EXISTS claim_revocations_appliance_idx
    ON claim_revocations (appliance_id, revoked_at DESC);

CREATE INDEX IF NOT EXISTS claim_revocations_fingerprint_idx
    ON claim_revocations (agent_pubkey_fingerprint);

-- Same append-only treatment
DROP TRIGGER IF EXISTS trg_prevent_claim_revocation_deletion ON claim_revocations;
CREATE TRIGGER trg_prevent_claim_revocation_deletion
    BEFORE DELETE ON claim_revocations
    FOR EACH ROW
    EXECUTE FUNCTION prevent_claim_event_deletion();

-- =========================================================================
-- Permissions
-- =========================================================================

GRANT SELECT, INSERT ON provisioning_claim_events TO mcp_app;
GRANT UPDATE (claim_signature_b64, chain_prev_hash, chain_hash, ots_bundle_id, notes)
    ON provisioning_claim_events TO mcp_app;
GRANT USAGE, SELECT ON SEQUENCE provisioning_claim_events_id_seq TO mcp_app;

GRANT SELECT ON iso_release_ca_pubkeys TO mcp_app;
-- INSERT/UPDATE on iso_release_ca_pubkeys is admin-only; Week 2
-- ship-pipeline runs as `mcp` via migrate.py.

GRANT SELECT, INSERT ON claim_revocations TO mcp_app;
GRANT UPDATE (revocation_signature_b64, ots_bundle_id) ON claim_revocations TO mcp_app;
GRANT USAGE, SELECT ON SEQUENCE claim_revocations_id_seq TO mcp_app;

COMMIT;

SELECT 'Migration 210 provisioning_claim_events_schema complete' AS status;
