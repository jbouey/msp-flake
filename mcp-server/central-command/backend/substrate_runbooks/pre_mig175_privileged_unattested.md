# pre_mig175_privileged_unattested

**Severity:** sev3 (INFORMATIONAL)
**Display name:** Pre-mig-175 privileged orders unattested (disclosed)

## What this means (plain English)

This invariant surfaces three legacy `fleet_orders` rows on
`north-valley-branch-2` that pre-date migration 175's chain-of-
custody trigger and therefore lack `attestation_bundle_id`. The
rows are listed in detail in the public security advisory
`docs/security/SECURITY_ADVISORY_2026-04-13_PRIVILEGED_PRE_TRIGGER.md`,
which ships in every auditor-kit ZIP under `disclosures/`.

This is **sev3 informational** — NOT actionable. The invariant
exists so future operators see the disclosure surface from the
substrate dashboard rather than discovering it via archaeology.
New violations are STRUCTURALLY blocked by
`trg_enforce_privileged_chain` (mig 175); zero new rows have been
added since 2026-04-13 09:01 UTC.

## Root cause categories

There is exactly one root cause and it is historical: migration 175
went live AFTER three privileged emergency-access orders had
already been inserted. The trigger is a pre-INSERT guard; it
cannot retroactively heal historical rows.

The 2026-05-08 round-table (Carol/Sarah/Steve/Maya 4-of-4) chose
public disclosure over retroactive backfill on these grounds:
1. Synthesizing attestation rows for historical orders would
   create a chain that *appears* to satisfy the inviolable rule
   but doesn't — a forgery pattern.
2. One-shot backfill scripts attract calls for "well, one more
   case" and become a chain-laundering vector.
3. Disclosure is honest, append-only, durable.

## Immediate action

**None.** This is informational. If you arrived at this runbook
because you saw the invariant on the dashboard, you have done
the right thing — you are now aware of the disclosure.

If you are an auditor:
1. Read
   `docs/security/SECURITY_ADVISORY_2026-04-13_PRIVILEGED_PRE_TRIGGER.md`
   for the full row-by-row disclosure, dates, types, and
   independent-verification queries.
2. Cross-check the three order IDs against `admin_audit_log` and
   the appliance's local `journal_upload_events` to corroborate
   that the orders did not execute.

If you are an engineer onboarding the codebase:
1. Read CLAUDE.md "Privileged-Access Chain of Custody" section.
2. Read migration 175 to understand the trigger's REJECT shape.
3. Note that `trg_enforce_privileged_chain` cannot be bypassed —
   this exact class of orphan row cannot be created today.

## Verification

This invariant resolves when the count drops to zero, which would
happen only if one of:
- A future migration explicitly grandfathers these rows (requires
  round-table approval) — unlikely; disclosure is preferred.
- The rows are deleted (impossible; `fleet_orders` has audit-class
  retention).

In practice, this invariant will fire forever as a passive
disclosure marker.

## Escalation

If the count GROWS beyond 3, that is a P0 — the trigger has been
disabled or bypassed. Investigate immediately:

```sql
-- Confirm trigger is enabled
SELECT tgenabled FROM pg_trigger WHERE tgname = 'trg_enforce_privileged_chain';
-- Confirm trigger function exists
SELECT EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'enforce_privileged_order_attestation');
```

A `tgenabled='D'` (disabled) result is a security incident —
restore the trigger and audit how it was disabled.

## Related runbooks

- *(none — this is a singleton informational invariant)*

## Related

- Audit: `audit/coach-e2e-attestation-audit-2026-05-08.md` F-P0-2
- Round-table verdict: `audit/round-table-verdict-2026-05-08.md` RT-1.2
- Public disclosure: `docs/security/SECURITY_ADVISORY_2026-04-13_PRIVILEGED_PRE_TRIGGER.md`
- Migration: `migrations/175_*.sql` (`trg_enforce_privileged_chain`)
- CLAUDE.md "Privileged-Access Chain of Custody"

## Change log

- **2026-05-08:** Created. Closes RT-1.2 from the E2E attestation
  audit. Substrate-engine surface for the disclosed pre-mig-175
  orphan privileged orders. Disclosure path chosen over backfill
  per round-table 4-of-4 vote.
