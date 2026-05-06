# cross_org_relocate_baa_receipt_unauthorized

**Severity:** sev1
**Display name:** Cross-org relocate completed without BAA receipt-authorization

## What this means (plain English)

A row in `cross_org_site_relocate_requests` has `status='completed'`,
which means a site moved from one client_org to another. But the
target org's `client_orgs` row currently has BOTH
`baa_relocate_receipt_signature_id` AND
`baa_relocate_receipt_addendum_signature_id` set to NULL.

Outside HIPAA counsel's approval of this feature (2026-05-06) was
contingent on five conditions. Condition #2:

> "the receiving organization's BAA or addendum expressly authorizes
>  receipt and continuity of transferred site compliance records/
>  evidence"

The endpoint check (`_check_target_org_baa` in
`cross_org_site_relocate.py`) refuses to advance target-accept
unless one of the two signature columns is populated. This invariant
is the substrate-layer last-line-of-defense — it catches drift
between counsel's contingency and the org's current state.

## Root cause categories

- **Post-execute un-authorization.** Contracts team rolled back
  receipt-authorization for the org for a future business reason
  (org no longer eligible to receive new transfers). That's a valid
  forward-going decision, but should NOT erase the historical
  signature_id on a completed relocate's target org. Re-populate the
  column.
- **Code-path bypass.** A code path skipped `_check_target_org_baa`
  at target-accept (regression, conditional logic, accidentally
  exempted endpoint). Check `git log` for recent changes to
  cross_org_site_relocate.py around the target-accept endpoint.
- **Accidental NULL.** Direct UPDATE on `client_orgs` from psql or
  a script set the column NULL.
- **Migration-state drift.** Migration 283 didn't apply on this
  environment yet, OR a partial migration.

## Immediate action

1. Identify the offending row:

   ```sql
   SELECT r.id AS relocate_id,
          r.site_id,
          r.target_org_id,
          r.executed_at,
          co.baa_on_file,
          co.baa_relocate_receipt_signature_id,
          co.baa_relocate_receipt_addendum_signature_id,
          co.baa_relocate_receipt_authorized_at,
          co.baa_relocate_receipt_authorized_by_email
     FROM cross_org_site_relocate_requests r
     JOIN client_orgs co ON co.id = r.target_org_id
    WHERE r.status = 'completed'
      AND co.baa_relocate_receipt_signature_id IS NULL
      AND co.baa_relocate_receipt_addendum_signature_id IS NULL;
   ```

2. Cross-reference with `admin_audit_log` for any UPDATE on
   `client_orgs` for that target org since the relocate's
   `executed_at`. If contracts-team un-authorized the org, re-engage
   them; the historical signature_id should be preserved on
   completed-relocate targets even if forward-going authorization is
   revoked.

3. If a code-path bypass is the cause, `git log` for the timeframe
   between the target-accept of this relocate and `executed_at`.

## Verification

After remediation (re-populating the signature_id from the original
BAA review record), this invariant clears within one
`substrate_assertions_loop` iteration (~60s).

## Escalation

If the bypass cannot be identified within 1 hour AND the move
predates a counsel-approval-pending state, this is a P0 chain-of-
custody + counsel-contingency violation. Page on-call substrate
engineer + privacy officer. The relocate row must remain intact
(append-only); rolling back the move is a separate state-machine
decision that requires its own attestation.

## Related runbooks

- `cross_org_relocate_chain_orphan.md` — sibling sev1 invariant
  catching the case where the move bypassed the state machine
  entirely (different failure class).

## Related

- Round-table: `.agent/plans/21-cross-org-site-relocate-roundtable-2026-05-05.md`
- Counsel briefing packet (v2.3): `.agent/plans/21-counsel-briefing-packet-2026-05-06.md`
- Module: `cross_org_site_relocate.py::_check_target_org_baa`
- Migration: `283_baa_relocate_receipt_signature.sql`
- CI gate: `tests/test_cross_org_relocate_contract.py` (new check
  asserts the endpoint requires the signature_id at target-accept)

## Change log

- 2026-05-06: invariant introduced alongside RT21 counsel approval
  hardening (mig 283). Sev1 because counsel approval condition #2.
