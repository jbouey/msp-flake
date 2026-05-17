# bearer_revoked_without_attestation

**Severity:** sev1
**Display name:** Bearer revoked without chain-of-custody attestation

## What this means (plain English)

A `site_appliances` row has `bearer_revoked = TRUE` but no
preceding `compliance_bundles` row anchors the revocation. Per
Counsel Rule 3 + §164.308(a)(4) workforce-access requirement,
every bearer revocation must be auditor-traceable to a named
human + reason.

The canonical path is the admin endpoint `POST /api/admin/sites/
{site_id}/appliances/revoke-bearers` (Sub-B, #123) which writes
both:
1. `bearer_revoked = TRUE` on `site_appliances`
2. A `compliance_bundles` row with `check_type='privileged_
   access'` + `event_type='bulk_bearer_revoke'` + the actor/
   reason

Both writes happen in the same `admin_transaction`. The invariant
fires when (1) exists without (2).

## Why sev1

- §164.308(a)(4) workforce-access controls require attestation
  of every credential revocation — no exception class
- Bearer revocation immediately disconnects the daemon; if it
  was unintended, the operator needs the audit trail to recover
- Pre-mig 324 only the load-test path (sites.synthetic=TRUE)
  wrote `bearer_revoked = TRUE` — that path is carved out of the
  invariant. ANY other writer is a regression / unattested action

## Carve-outs

- `sites.synthetic = TRUE` — load-test infrastructure (load_test_
  api.py:415-449). Load harness teardown legitimately revokes
  its own synthetic bearers without attestation.
- `site_appliances.deleted_at IS NOT NULL` — soft-deleted
  appliances are out of scope; revocation on a deleted appliance
  is moot

## Root cause categories

1. **Direct DB UPDATE bypassing the endpoint** — operator ran
   `UPDATE site_appliances SET bearer_revoked = TRUE` via psql
   during incident response. Best-intent shortcut but skips
   attestation. Fix: re-run via the admin endpoint with named
   actor + reason (the endpoint is idempotent on the bearer_
   revoked=TRUE side; it'll write the missing attestation
   without flipping the already-TRUE column).

2. **A new code path introduced a writer** — `git log -S
   'bearer_revoked' --since=24h` to find. Apply the
   admin_transaction + attestation pattern to the new writer
   OR remove the writer.

3. **Mig backfill missed the carve-out** — a future migration
   set `bearer_revoked = TRUE` on existing rows without writing
   attestations. Rare; if surfaces, escalate to scope-cleanup
   task.

4. **Auth path inconsistency** — `shared.py:614-640` reads
   `bearer_revoked` to short-circuit 401. If a future refactor
   moves the read to a different table without updating this
   invariant's SQL, the invariant would silently never fire.
   Pin: a test that confirms `shared.py` reads from
   `site_appliances.bearer_revoked` (not a moved table).

## Immediate action

1. **Identify the offending row:**
   ```sql
   SELECT sa.appliance_id, sa.site_id, sa.hostname,
          s.synthetic, sa.deleted_at,
          (SELECT MAX(cb.created_at)
             FROM compliance_bundles cb
            WHERE cb.site_id = sa.site_id
              AND cb.check_type = 'privileged_access'
              AND cb.summary::jsonb->>'event_type' = 'bulk_bearer_revoke'
              AND (cb.summary::jsonb->'target_appliance_ids') ?
                  sa.appliance_id::text
          ) AS most_recent_attestation
     FROM site_appliances sa
     JOIN sites s ON s.site_id = sa.site_id
    WHERE sa.bearer_revoked = TRUE
      AND COALESCE(s.synthetic, FALSE) = FALSE
      AND sa.deleted_at IS NULL
    ORDER BY sa.site_id, sa.appliance_id LIMIT 50;
   ```

2. **Emit retroactive attestation** via the admin endpoint:
   ```
   POST /api/admin/sites/<site_id>/appliances/revoke-bearers
   {
     "appliance_ids": ["<aid_1>", "<aid_2>"],
     "actor_email": "named.human@company.com",
     "reason": "Retroactive attestation for unattested revocation on YYYY-MM-DD per substrate invariant alert N",
     "incident_correlation_id": "<incident-uuid>"
   }
   ```
   The endpoint is idempotent on the already-TRUE column; it
   writes the missing `compliance_bundles` row + admin_audit_log
   row, the invariant clears on the next 60s tick.

3. **Recover the appliance** (if revocation was unintended):
   Issue `signing_key_rotation` privileged order for the
   appliance to re-mint its bearer + signature key. The daemon
   picks up the new bearer on next checkin.

## Verification

- Invariant clears on next 60s tick once the attestation row
  exists OR `bearer_revoked` is reset to FALSE
- Test: `tests/test_bearer_revoked_attestation_invariant.py`
  pins the SQL shape + sev1 registration + carve-outs

## Escalation

- **>5 unattested revocations in 24h:** sev0 — likely a code
  path regressed; page CISO + freeze deploys until the writer
  is identified
- **Revocation was during an incident-response sweep:** treat as
  evidence-gap for that incident; loop in counsel if any
  customer-facing artifact was downloaded post-revocation

## Related runbooks

- `vault_key_version_approved_without_attestation.md` (sev1 —
  same chain-of-custody class for Vault key approval)
- `load_test_chain_contention_site_orphan.md` (sev2 — synthetic-
  site orphan detection sibling)

## Change log

- 2026-05-17 — initial — #123 Sub-A closure. Companion to mig 329
  (4-list lockstep extension). Gate A:
  audit/coach-123-batch-bearer-revocation-gate-a-2026-05-17.md.
