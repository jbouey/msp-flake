# client_portal_zero_evidence_with_data

**Severity:** sev2
**Display name:** Client portal hiding evidence — RLS misalignment

## What this means (plain English)

A customer has produced compliance evidence in the last 7 days
(real bundles in the database) but their client portal view is
returning ZERO rows. Customer-facing tiles will show "Awaiting first
scan" or "—" even though their substrate is actively producing
signed bundles. This breaks customer trust on a trust-bearing
platform.

The most common cause is the same one that triggered this invariant's
creation on 2026-05-05: a new site-RLS table was added without a
parallel `tenant_org_isolation` policy, and the client portal reads
from it under `org_connection`.

## Root cause categories

- New site-RLS table added in a recent migration without the
  org-scoped policy that mig 278 applied to the rest of the set.
- A migration accidentally dropped the `tenant_org_isolation` policy
  on `compliance_bundles` or `client_user_email_change_log`.
- Someone changed `org_connection()` in `tenant_middleware.py` and
  broke the `app.current_org` GUC plumbing.
- An auth path regressed `app.is_admin` to `'false'` for a code
  path that needs admin bypass.

## Immediate action

1. Run the source-level CI gate locally — it lists the offending
   table by name:

   ```
   cd mcp-server/central-command/backend
   python3 -m pytest tests/test_org_scoped_rls_policies.py -v
   ```

2. If the gate passes but the invariant still fires, the regression
   is likely runtime not source. Manually simulate the client RLS
   context against the affected org from the violation details:

   ```
   ssh root@VPS 'docker exec -i mcp-postgres psql -U mcp -d mcp' <<'SQL'
   BEGIN;
   SET LOCAL app.current_org = '<offending_org_id>';
   SET LOCAL app.is_admin = 'false';
   SET LOCAL app.current_tenant = '';
   SELECT COUNT(*) FROM compliance_bundles cb
     WHERE cb.site_id IN (SELECT site_id FROM sites
                           WHERE client_org_id::text = '<offending_org_id>');
   ROLLBACK;
   SQL
   ```

   This count must match the admin-side count. If it returns 0, the
   RLS misalignment is live; if it matches, the bug is upstream
   (filtering / WHERE clause / pre-Stage-2-style formula).

3. If a new site-RLS table is the culprit, add a migration modeled
   on mig 278: `CREATE POLICY tenant_org_isolation ON <table> USING
   (rls_site_belongs_to_current_org(site_id::text))`.

## Verification

- Panel: invariant row should clear on the next 60s sweep tick.
- CLI: re-run the simulation in step 2; non-zero count means fixed.
- End-to-end: hit `/api/client/dashboard` from a customer session
  and confirm `kpis.total_checks > 0`.

## Escalation

This is sev2 not sev1 because cryptographic chain integrity is NOT
affected — the bundles still exist, are still Ed25519-signed and
OTS-anchored, and an auditor pulling via admin endpoints sees the
full chain. The break is in the CUSTOMER VIEW only.

That said, sustained > 1h means a customer cannot see their own
evidence on the portal and may file a support ticket questioning
whether the platform is monitoring them at all. Auto-fix is
forbidden — fixes for this class are migrations + policy edits and
must be reviewed.

If the invariant fires for ALL orgs simultaneously, that's the
2026-05-05 regression class re-occurring (RLS helper or
`org_connection()` itself broken). Page on-call.

## Related runbooks

- partition_maintainer_dry — adjacent partitioned-table health
- email_dlq_growing — adjacent operator-visibility invariant pattern

## Change log

- 2026-05-05 — created — Stage 4 closure of the
  `org_connection`/site-RLS misalignment P0; backstops the
  Stage-1 mig 278 + Stage-2 unified scoring fixes.
