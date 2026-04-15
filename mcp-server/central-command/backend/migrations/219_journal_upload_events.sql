-- Migration 219: tamper-evident journal-upload ledger
--
-- Session 207 Phase H4. Every appliance periodically ships a batch of
-- journalctl lines to /api/journal/upload. Each batch lands here as
-- ONE append-only row (not one row per line — that would explode
-- cardinality). Per-appliance hash chain via (chain_prev_hash,
-- chain_hash) so any retroactive tampering with historical rows
-- breaks the chain and is detectable by walking forward from the
-- genesis hash.
--
-- Payload is jsonb containing:
--   {
--     "batch_start":  "<RFC3339 timestamp of oldest line>",
--     "batch_end":    "<RFC3339 timestamp of newest line>",
--     "line_count":   <int>,
--     "compressed":   "<zstd+base64 of newline-joined journal entries>",
--     "sha256":       "<hex sha256 of the uncompressed text>",
--     "scrubbed":     true
--   }
--
-- PHI scrubbing: appliance-side (per CLAUDE.md Session 204 rule) via
-- the phiscrub Go package before compression. Backend trusts but
-- verifies — the daemon-journal tail tends to carry hostnames that
-- could be PHI-adjacent ("PATIENT-ROOM-201-PC"), and the scrub is
-- the privacy guarantee. The "scrubbed":true field is an explicit
-- claim that operations auditors can point to.
--
-- Retention: 90-day default (keeps the rolling window cheap), with
-- an auditor-extended-retention flag that bumps to 6 years for any
-- appliance under an active compliance attestation. Retention
-- enforcement is a follow-up migration; this one just ships the
-- ledger.

BEGIN;

CREATE TABLE IF NOT EXISTS journal_upload_events (
    id              BIGSERIAL PRIMARY KEY,
    site_id         VARCHAR(50)   NOT NULL,
    appliance_id    VARCHAR(255)  NOT NULL,
    batch_start     TIMESTAMPTZ   NOT NULL,
    batch_end       TIMESTAMPTZ   NOT NULL,
    line_count      INTEGER       NOT NULL CHECK (line_count >= 0),
    payload_bytes   INTEGER       NOT NULL CHECK (payload_bytes >= 0),
    payload         JSONB         NOT NULL,
    chain_prev_hash TEXT,
    chain_hash      TEXT          NOT NULL,
    received_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_journal_upload_appliance_received
    ON journal_upload_events (appliance_id, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_journal_upload_batch_time
    ON journal_upload_events (appliance_id, batch_end DESC);
CREATE INDEX IF NOT EXISTS idx_journal_upload_site_received
    ON journal_upload_events (site_id, received_at DESC);

-- Append-only via the shared prevent_audit_deletion() function.
DROP TRIGGER IF EXISTS prevent_journal_event_mutation ON journal_upload_events;
CREATE TRIGGER prevent_journal_event_mutation
    BEFORE UPDATE OR DELETE ON journal_upload_events
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_deletion();

COMMENT ON TABLE journal_upload_events IS
    'Tamper-evident journal-upload ledger. Each row is one 15-min '
    'batch from an appliance. Per-appliance hash chain. Append-only.';

COMMIT;
