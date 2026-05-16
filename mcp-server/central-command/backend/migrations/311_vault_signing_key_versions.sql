-- Migration 311: vault_signing_key_versions — Vault Phase C INV anchor
--
-- Task #43 / #62 Vault P0 iter-4 Commit 2 (2026-05-16). Recreate
-- after the iter-1/2/3 revert chain orphaned the table on prod
-- (mig 311 was applied 2026-05-13 02:15:40 then the FILE got
-- reverted; Path A clean-slate dropped the orphan + restored fixture
-- parity in commit 80cbd72c earlier today).
--
-- Spec: `.agent/plans/vault-p0-bundle-redesign-2026-05-13.md`
-- Gate A (iter-4): `audit/coach-vault-p0-bundle-iter4-gate-a-2026-05-16.md`
-- Gate A (original):
--   `audit/coach-vault-p0-bundle-gate-a-redo-2-2026-05-13.md` (APPROVE-FOR-EXECUTION)
-- Revert case study: `memory/feedback_vault_phase_c_revert_2026_05_12.md`
--
-- Purpose: anchor table for the INV-SIGNING-BACKEND-VAULT startup
-- invariant. On every container start, the INV probes Vault for
-- (key_name, key_version) + pubkey + bootstrap-INSERTs the observed
-- row with known_good=FALSE. Operator manually approves a row
-- (sets known_good=TRUE + approved_by + approved_at) via an admin
-- endpoint. Subsequent starts compare the observed Vault state
-- against the approved row; a mismatch FAILS the INV — surfacing
-- the case where an attacker rotated the Vault key without
-- authorization.
--
-- Design notes:
--   - known_good BOOLEAN NOT NULL DEFAULT FALSE (Gate A P0 #5) —
--     explicit NOT NULL so a future ALTER doesn't drop it +
--     the NOT-NULL-with-default avoids the UNKNOWN-evaluates-true
--     class on the CHECK below.
--   - CHECK ensures that a `known_good=TRUE` row MUST carry
--     approved_by + approved_at. Schema-level guard prevents an
--     accidental UPDATE from approving a row without operator
--     identity attribution.
--   - UNIQUE (key_name, key_version) — bootstrap-INSERT uses
--     ON CONFLICT (key_name, key_version) DO NOTHING (NOT
--     DO UPDATE — Gate A P0 #3 mandated this so first-observed
--     telemetry isn't masked by side-effect updates on every start).
--   - Index on known_good for fast "any approved version?" check.
--   - first_observed_at + last_observed_at give operators a
--     temporal signal for first-seen vs continuously-seen rows.

BEGIN;

CREATE TABLE IF NOT EXISTS vault_signing_key_versions (
    id                  BIGSERIAL    PRIMARY KEY,
    key_name            TEXT         NOT NULL,
    key_version         INTEGER      NOT NULL,
    pubkey_hex          TEXT         NOT NULL,
    pubkey_b64          TEXT         NOT NULL,
    first_observed_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_observed_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    known_good          BOOLEAN      NOT NULL DEFAULT FALSE,
    approved_by         TEXT         NULL,
    approved_at         TIMESTAMPTZ  NULL,

    CONSTRAINT vault_signing_key_versions_key_name_key_version_key
        UNIQUE (key_name, key_version),
    CONSTRAINT vault_signing_key_versions_known_good_ck
        CHECK (NOT known_good OR (approved_by IS NOT NULL AND approved_at IS NOT NULL))
);

COMMENT ON TABLE vault_signing_key_versions IS
    'Vault Phase C INV-SIGNING-BACKEND-VAULT anchor (mig 311 iter-4 '
    '2026-05-16). One row per (Vault key_name, key_version) observed '
    'at mcp-server startup. Operator approves a row by setting '
    'known_good=TRUE + approved_by + approved_at via admin endpoint. '
    'INV fails if observed Vault state has no matching known_good '
    'row — attacker-rotated-key class.';

CREATE INDEX IF NOT EXISTS idx_vault_signing_key_versions_known_good
    ON vault_signing_key_versions (known_good)
    WHERE known_good = TRUE;

COMMIT;
