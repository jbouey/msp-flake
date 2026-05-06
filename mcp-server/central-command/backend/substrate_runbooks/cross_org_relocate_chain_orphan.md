# cross_org_relocate_chain_orphan

**Severity:** sev1
**Display name:** Cross-org relocate without attestation — chain orphan

## What this means (plain English)

A site has `sites.prior_client_org_id` set (indicating it was moved
from one client_org to another), but there is NO completed row in
`cross_org_site_relocate_requests` attesting the move. The proper
flow writes BOTH columns AND a 6-event attestation chain. If only the
column is set, some code path bypassed the relocate state machine —
likely a direct UPDATE on `sites.client_org_id` from a DBA shortcut,
an accidental backfill, or a regression in another endpoint.

This is sev1 because §164.528 disclosure-accounting integrity is on
the line: an auditor reading the chain expects to see all three actor
attestations (initiator, source-owner-release, target-owner-accept,
admin-execute). A move without that chain is unattested PHI custody
transfer — exactly the class of event RT21 was built to prevent.

## Root cause categories

- Direct `UPDATE sites SET client_org_id = ...` from psql or a script.
- A regression in another endpoint (org_management, sites.py, etc.)
  that mutates `sites.client_org_id` without going through
  `cross_org_site_relocate.execute_relocate`.
- A backfill migration that set `prior_client_org_id` for historical
  data without writing matching relocate-request rows.
- A test fixture leaking into production state.

## Immediate action

1. Identify the offending site:

   ```sql
   SELECT s.site_id, s.client_org_id, s.prior_client_org_id
     FROM sites s
    LEFT JOIN cross_org_site_relocate_requests r
      ON r.site_id = s.site_id
     AND r.source_org_id = s.prior_client_org_id
     AND r.target_org_id = s.client_org_id
     AND r.status = 'completed'
    WHERE s.prior_client_org_id IS NOT NULL
      AND r.id IS NULL;
   ```

2. Determine WHEN the move happened. Cross-reference:
   - `admin_audit_log` for any rows targeting the site_id.
   - `compliance_bundles.checked_at` jumps that bracket the move.
   - `git log` for any commit that touched `sites.client_org_id`
     UPDATE statements.

3. Decide whether the move was authorized:
   - **Yes, authorized:** write a post-hoc `cross_org_site_relocate_
     requests` row with `status='completed'` and the actor names,
     reasons, and timestamps reconstructed from `admin_audit_log`.
     Document the post-hoc attestation in the row's `cancel_reason`
     column with a "post-hoc reconstruction" prefix so an auditor can
     distinguish it from natively-attested rows.
   - **No, unauthorized:** REVERSE the change immediately by setting
     `sites.client_org_id` back to `sites.prior_client_org_id` and
     `sites.prior_client_org_id = NULL`. Open a P0 incident and
     investigate the bypass path.

4. Either way, identify HOW the bypass happened and close the gap.
   The substrate invariant exists precisely to surface this class.

## Verification

After remediation, this invariant should clear within one
`substrate_assertions_loop` iteration (~60s). Confirm via the
substrate-health admin panel.

## Escalation

If the bypass path can't be identified within 1 hour, this is a P0
chain-of-custody incident. Page the on-call substrate-engineer +
notify the privacy officer (per `docs/security/emergency-access-
policy.md`). Document under what circumstance prior_client_org_id
was set without a relocate row — this is the kind of finding that
justifies a HIPAA breach disclosure analysis.

## Related runbooks

- `client_portal_zero_evidence_with_data.md` — the org-RLS class.
- `audit_chain_continuity_*` (when added) — chain-gap parent class.

## Related

- Round-table: `.agent/plans/21-cross-org-site-relocate-roundtable-2026-05-05.md`
- Module: `cross_org_site_relocate.py` (the canonical flow)
- Migrations: 279 (table), 280 (sites column), 281 (feature flag)
- CI gate: `tests/test_cross_org_relocate_contract.py`

## Change log

- 2026-05-05: invariant introduced alongside RT21 cross-org relocate
  module. Sev1 because §164.528 chain-of-custody integrity.
