-- Migration 272: email_send_failures dead-letter queue
--
-- Session pickup 2026-05-04 (post Session 215). Email infrastructure
-- previously had no visibility into final-failure deliveries — when
-- _send_smtp_with_retry exhausted its 3-retry budget, the failure was
-- only logged to stdout. Now: every final failure writes a row here.
--
-- Used by:
--   - email_alerts._send_smtp_with_retry (final-failure hook)
--   - substrate invariant `email_dlq_growing` (sev2; fires when
--     >5 unresolved rows accumulate in 24h, signaling SMTP outage
--     or auth break)
--
-- Schema design:
--   - Append-only (no DELETE blocking trigger — operational table,
--     not audit-class per CLAUDE.md "audit-trigger allowlist")
--   - resolved_at NULL until operator triages or autoresolve sweep
--     marks it observed
--   - PHI-free: recipients are operator/customer addresses (ALERT_
--     EMAIL, client_users.email, partner emails) — already approved
--     in the existing email path. error_class + error_message are
--     SMTPException class names + truncated messages.

CREATE TABLE IF NOT EXISTS email_send_failures (
    id              BIGSERIAL PRIMARY KEY,
    label           TEXT NOT NULL,
    -- Event/operator class string (operator alert, digest, magic-link,
    -- consent request, etc.). Comes from the `label` arg passed to
    -- _send_smtp_with_retry. Used for grouping in the substrate
    -- invariant + operator triage UI.
    recipient_count INTEGER NOT NULL,
    -- Recipients are NOT stored individually — privacy + minimization.
    -- The label + count + timestamp is enough to scope the failure;
    -- if an operator needs the actual addresses, they can correlate
    -- with shipper logs via `failed_at` ± a few seconds.
    error_class     TEXT NOT NULL,
    error_message   TEXT,
    retry_count     INTEGER NOT NULL DEFAULT 0,
    failed_at       TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    resolved_at     TIMESTAMP WITH TIME ZONE,
    resolution_note TEXT
);

-- Index for substrate invariant: scan by failed_at + WHERE resolved_at
-- IS NULL. The "rolling 24h unresolved count" is the canonical query.
CREATE INDEX IF NOT EXISTS idx_email_send_failures_unresolved
    ON email_send_failures (failed_at DESC)
    WHERE resolved_at IS NULL;

-- Index for label-grouped triage: "show me all unresolved failures
-- for the operator-alert pipeline" — used by future admin UI.
CREATE INDEX IF NOT EXISTS idx_email_send_failures_label
    ON email_send_failures (label, failed_at DESC)
    WHERE resolved_at IS NULL;

COMMENT ON TABLE email_send_failures IS
'Email DLQ — appended on final-failure send via _send_smtp_with_retry. '
'Operator visibility into SMTP outages, auth breaks, recipient bounces. '
'NOT audit-class — operational. Resolution sweep marks rows resolved '
'when the substrate invariant clears or operator manually triages.';

COMMENT ON COLUMN email_send_failures.label IS
'Send-site identifier passed to _send_smtp_with_retry; used for '
'invariant grouping (e.g. "operator alert: <event_type>", '
'"digest to <addr>", "consent request to <addr>").';

COMMENT ON COLUMN email_send_failures.recipient_count IS
'Number of intended recipients. We do NOT store the addresses '
'themselves — privacy + minimization. Correlate with shipper '
'logs via failed_at if specific addresses are needed.';
