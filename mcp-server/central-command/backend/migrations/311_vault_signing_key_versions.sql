-- Migration 311: vault_signing_key_versions registry table.
-- Vault Phase C P0 #2 (Gate A audit/coach-vault-phase-c-gate-a-2026-05-12.md).
--
-- INV-SIGNING-BACKEND-VAULT (P0 #1, sibling migration) reads from this
-- table on every container startup. The startup invariant fails-closed
-- if the Vault Transit key version observed at boot doesn't match the
-- last operator-approved (`known_good=TRUE`) version. Defense against
-- an attacker who compromises the Vault host + rotates the key to one
-- they control: mcp-server refuses to start until an operator inspects
-- the new version + sets `known_good=TRUE`.
--
-- Bootstrap pattern:
--   * First key version observed at startup INSERTs a row with
--     `known_good=FALSE`.
--   * Operator runs a one-off SQL to flip the first row to TRUE after
--     verifying the Vault host's audit log matches what they expect.
--   * Subsequent startups compare observed version against the
--     known_good row. Mismatch → startup fails.
--
-- Substrate sibling invariant: signing_backend_drifted_from_vault
-- (sev2, assertions.py) reads this table to detect drift between the
-- container's SIGNING_BACKEND env value and the observed signing
-- activity in fleet_orders.signing_method (P0 #3 write path).
--
-- Table is admin-only (no RLS — global registry, not site-scoped).
-- UPDATE permitted ONLY on `known_good` and `last_observed_at` columns
-- (enforced by trigger). DELETE rejected unconditionally — history is
-- audit-class. Mig 310's lesson applied: the table is NOT site_id-
-- bearing, so it does NOT belong in `_rename_site_immutable_tables()`.

BEGIN;

CREATE TABLE IF NOT EXISTS vault_signing_key_versions (
    id BIGSERIAL PRIMARY KEY,
    key_name TEXT NOT NULL,
    key_version INT NOT NULL,
    pubkey_hex TEXT NOT NULL,
    pubkey_b64 TEXT NOT NULL,
    first_observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    known_good BOOLEAN NOT NULL DEFAULT FALSE,
    approved_by TEXT,                  -- operator email when known_good flipped to TRUE
    approved_at TIMESTAMPTZ,           -- timestamp of approval
    UNIQUE (key_name, key_version)
);

CREATE INDEX IF NOT EXISTS idx_vault_signing_key_versions_known_good
    ON vault_signing_key_versions(key_name) WHERE known_good = TRUE;

-- Block UPDATE of immutable columns. Only known_good, approved_by,
-- approved_at, and last_observed_at may change after INSERT.
CREATE OR REPLACE FUNCTION vault_signing_key_versions_reject_immutable_update()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.key_name IS DISTINCT FROM NEW.key_name THEN
        RAISE EXCEPTION 'vault_signing_key_versions.key_name is immutable';
    END IF;
    IF OLD.key_version IS DISTINCT FROM NEW.key_version THEN
        RAISE EXCEPTION 'vault_signing_key_versions.key_version is immutable';
    END IF;
    IF OLD.pubkey_hex IS DISTINCT FROM NEW.pubkey_hex THEN
        RAISE EXCEPTION 'vault_signing_key_versions.pubkey_hex is immutable';
    END IF;
    IF OLD.pubkey_b64 IS DISTINCT FROM NEW.pubkey_b64 THEN
        RAISE EXCEPTION 'vault_signing_key_versions.pubkey_b64 is immutable';
    END IF;
    IF OLD.first_observed_at IS DISTINCT FROM NEW.first_observed_at THEN
        RAISE EXCEPTION 'vault_signing_key_versions.first_observed_at is immutable';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_vault_key_versions_reject_immutable_update ON vault_signing_key_versions;
CREATE TRIGGER trg_vault_key_versions_reject_immutable_update
    BEFORE UPDATE ON vault_signing_key_versions
    FOR EACH ROW EXECUTE FUNCTION vault_signing_key_versions_reject_immutable_update();

-- Block DELETE outright — history is forensic.
CREATE OR REPLACE FUNCTION vault_signing_key_versions_reject_delete()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'DELETE denied on vault_signing_key_versions — forensic key-rotation history is audit-class';
END;
$$;

DROP TRIGGER IF EXISTS trg_vault_key_versions_reject_delete ON vault_signing_key_versions;
CREATE TRIGGER trg_vault_key_versions_reject_delete
    BEFORE DELETE ON vault_signing_key_versions
    FOR EACH ROW EXECUTE FUNCTION vault_signing_key_versions_reject_delete();

COMMENT ON TABLE vault_signing_key_versions IS
    'Vault Transit key-version registry. INV-SIGNING-BACKEND-VAULT '
    'startup invariant compares observed key version against '
    'known_good=TRUE row; unauthorized rotation fails startup. '
    'Append-only by trigger; UPDATE limited to known_good/approved_*/'
    'last_observed_at; DELETE rejected. Not site_id-bearing — does '
    'NOT belong in _rename_site_immutable_tables() (lesson from mig 310).';

COMMIT;

-- Audit log entry capturing the Gate A driven creation.
INSERT INTO admin_audit_log (action, target, username, details, created_at)
VALUES (
    'migration_311_vault_signing_key_versions',
    'vault_signing_key_versions',
    'jeff',
    jsonb_build_object(
        'migration', '311',
        'reason', 'Vault Phase C Gate A P0 #2. Registry table feeds INV-SIGNING-BACKEND-VAULT startup invariant + signing_backend_drifted_from_vault substrate invariant. Bootstrap pattern: first observed row inserts with known_good=FALSE; operator approval flips it.',
        'audit_ref', 'audit/coach-vault-phase-c-gate-a-2026-05-12.md',
        'siblings', jsonb_build_array(
            'INV-SIGNING-BACKEND-VAULT (startup_invariants.py)',
            'signing_method write path (fleet_orders inserts)',
            'signing_backend_drifted_from_vault (assertions.py sev2)'
        )
    ),
    NOW()
);
