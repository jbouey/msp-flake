-- Migration 221: per-appliance break-glass passphrases (Phase R)
--
-- Replaces the MAC-derived emergency password
--   osiris-<sha256("osiriscare-emergency-<MAC>")[:8]>
-- which is a permanent 32-bit backdoor discoverable from any LAN
-- ARP table or a sticker on the box. Phase R generates a random
-- 32-byte passphrase at first boot, sets it on the `msp` user, and
-- ships an encrypted-at-rest copy to the backend so an operator can
-- retrieve it through the privileged chain for physical-console
-- break-glass.
--
-- Storage: encrypted_passphrase is Fernet-wrapped (AES-128-CBC +
-- HMAC-SHA256) via credential_crypto.encrypt_credential using the
-- CREDENTIAL_ENCRYPTION_KEY already provisioned for site_credentials.
-- The `rotated_at` column tracks the most recent re-generation; the
-- watchdog can trigger rotation via a (future) `watchdog_rotate_
-- breakglass` order. Table is UPDATE-able (unlike audit logs) because
-- the whole point is that the value rotates.
--
-- Retrieval: /api/admin/appliance/{aid}/break-glass (admin auth +
-- actor_email + reason ≥ 20 chars, full privileged chain). Every
-- retrieval writes to admin_audit_log so the customer's privileged-
-- action feed (Phase H6) shows who pulled the passphrase and why.

BEGIN;

CREATE TABLE IF NOT EXISTS appliance_breakglass_passphrases (
    site_id               VARCHAR(50)   NOT NULL,
    appliance_id          VARCHAR(255)  NOT NULL,
    encrypted_passphrase  BYTEA         NOT NULL,
    passphrase_version    INTEGER       NOT NULL DEFAULT 1,
    generated_at          TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    rotated_at            TIMESTAMPTZ,
    last_retrieved_at     TIMESTAMPTZ,
    retrieval_count       INTEGER       NOT NULL DEFAULT 0,
    PRIMARY KEY (appliance_id)
);

CREATE INDEX IF NOT EXISTS idx_breakglass_site
    ON appliance_breakglass_passphrases (site_id);

COMMENT ON TABLE appliance_breakglass_passphrases IS
    'Session 207 Phase R. Per-appliance break-glass passphrase, '
    'Fernet-encrypted at rest via credential_crypto. Replaces the '
    'MAC-derived emergency password baked by msp-first-boot in '
    'pre-v32 ISOs. Retrieval is privileged + audit-logged.';

COMMIT;
