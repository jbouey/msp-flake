# email_dlq_growing

**Severity:** sev2
**Display name:** Email DLQ growing — outbound email pipeline failing

## What this means

More than 5 unresolved rows in `email_send_failures` (mig 272 DLQ) within the last 24h on a single label. The outbound email pipeline for that send-class has been silently failing. The cryptographic chain (admin_audit_log + Ed25519 compliance_bundles) is unaffected — those write BEFORE the SMTP send. Only the operator-visibility echoes, customer-facing notifications, and partner digests are not landing in inboxes.

## Root cause categories

- **SMTP host outage** — `mail.privateemail.com` unreachable. Affects every label simultaneously; check VPS-to-SMTP connectivity first.
- **SMTP auth break** — `SMTP_USER` / `SMTP_PASSWORD` rotated upstream, env not updated. Affects every label, error_class is SMTPAuthenticationError.
- **DKIM/SPF DNS misalignment** — recipient mail providers reject. Often label-scoped (e.g. partner-branded mail rejected by some recipients, OsirisCare mail accepted).
- **Per-recipient bounces** — typo'd customer email accumulating retries. Single label + single recipient pattern.
- **Quota exhaustion** — privateemail.com daily-send cap hit. Time-of-day correlated.

## Immediate action

Diagnostic SQL — distinguish error class:

```
ssh root@178.156.162.116 \
  'docker exec mcp-postgres psql -U mcp -d mcp -c \
   "SELECT label, error_class, error_message, retry_count, failed_at \
      FROM email_send_failures \
     WHERE resolved_at IS NULL \
       AND failed_at > NOW() - INTERVAL ''24 hours'' \
     ORDER BY failed_at DESC LIMIT 20;"'
```

Then by class:

- `SMTPException + TimeoutError`: check VPS connectivity to mail.privateemail.com:587. Wait for SMTP host recovery.
- `SMTPAuthenticationError`: rotate SMTP_PASSWORD via /opt/mcp-server/.env then `docker compose restart mcp-server`.
- `SMTPRecipientsRefused`: bisect digest recipient list to find bad address.

After root cause is fixed, mark resolved:

```
ssh root@178.156.162.116 \
  'docker exec mcp-postgres psql -U mcp -d mcp -c \
   "UPDATE email_send_failures SET resolved_at = NOW(), \
           resolution_note = ''SMTP recovered <RFC3339>'' \
     WHERE id = ANY(ARRAY[123,124]::bigint[]);"'
```

## Verification

- Re-run the diagnostic SELECT. Unresolved-count for the affected label should be 0 (or below 5 and trending down).
- Send a test email via any path (e.g. trigger a digest preview at `/api/partners/me/digest/preview` from a partner account) and confirm delivery from the recipient inbox side.
- Substrate panel `/admin/substrate-health` clears the violation row within one assertions_loop tick (60s).

## Escalation

If unresolved-count grows past 50 in a 1h window (10× normal threshold) OR if `SMTPAuthenticationError` rows accumulate while the env file shows the secret unchanged: this is incident-response class, not maintenance. Page on-call. Compromised SMTP credentials class — assume the password is leaked, rotate immediately + audit `/api/version` SHA chain to confirm no malicious deploy slipped in during the credentials window.

## Related runbooks

- `substrate_assertions_meta_silent.md` — if the email DLQ violation row never appears even though SMTP is broken, the substrate engine itself is silent.
- `bg_loop_silent.md` — `_send_smtp_with_retry` is called synchronously inside many loops; a stuck SMTP send would cause the calling loop to silent-stick.
- `compliance_packets_stalled.md` — sibling outcome-layer invariant (HIPAA monthly attestation gap).

## Change log

- 2026-05-04: Maya substrate observability follow-up after the partner-portal consistency audit. Email DLQ shipped in commit 3cd0a208 (mig 272) without a paired invariant — this runbook + assertion fills the gap. Threshold (>5 unresolved/24h/label) is conservative initial calibration; tune after first month of real traffic. Sev2 because the cryptographic chain is unaffected by email failures (audit row + Ed25519 commit BEFORE the SMTP send) — only operator-visibility echoes are delayed.
