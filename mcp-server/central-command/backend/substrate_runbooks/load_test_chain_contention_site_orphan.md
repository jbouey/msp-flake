# load_test_chain_contention_site_orphan

**Severity:** sev2
**Display name:** Load-test site orphan bundle (synthetic infra)

## What this means (plain English)

A compliance_bundles row exists for site_id='load-test-chain-
contention-site' OUTSIDE any active load_test_runs window. This
site is synthetic load-test infrastructure (seeded by mig 325 for
#117 chain-contention soak); bundles are EXPECTED on it ONLY during
k6 soak runs. A bundle without a covering load_test_runs row means
a production writer is accidentally targeting the load-test site.

Why sev2: this is the Counsel Rule 4 "no silent orphan coverage"
class applied to synthetic infrastructure. Not sev1 because:
(a) no customer data is on this site (`client_org_id IS NULL`),
(b) no Ed25519 chain corruption (the bundles ARE genuinely chained
within the load-test site's own chain), (c) auditor-kit determinism
is N/A (no auditor will ever download this site's kit).

Why not sev3: silent contamination of load-test infrastructure
means future soak measurements are tainted (the runner can't
distinguish k6-generated load from leaked production traffic).
sev3 falls below operator-attention threshold for this class.

## Root cause categories

1. **Production writer accidentally targeting the seed site** —
   most common. A code path constructs `site_id='load-test-chain-
   contention-site'` from a constant + a typo or a fixture variable
   leaks into prod. Grep the writer's source for the literal.

2. **k6 soak metadata clock-skew** — the load_test_runs row's
   started_at/completed_at was clocked ahead/behind the actual
   bundle creation. Verify the k6 wrapper's clock vs. the backend's
   clock; the invariant gives a 4h grace COALESCE buffer per the
   #117 design max-soak-duration.

3. **load_test_runs row was deleted post-hoc** — admin op deleted
   the runs ledger row but bundles persisted. Restore the ledger
   row via the runbook below.

4. **Manual psql INSERT mistake** — operator ran an INSERT
   targeting the synthetic site for debugging. Verify
   admin_audit_log for recent manual writes.

## Immediate action

1. **Identify the writer:**
   ```sql
   SELECT bundle_id, check_type, created_at, chain_position
     FROM compliance_bundles
    WHERE site_id = 'load-test-chain-contention-site'
      AND created_at > NOW() - INTERVAL '7 days'
    ORDER BY created_at DESC LIMIT 20;
   ```

2. **Check for a covering load_test_runs row:**
   ```sql
   SELECT run_id, status, started_at, completed_at, scenario_sha
     FROM load_test_runs
    WHERE started_at <= '<bundle.created_at>'
      AND COALESCE(completed_at, started_at + INTERVAL '4h')
          >= '<bundle.created_at>'
    ORDER BY started_at DESC;
   ```

   - If found: backfill the load_test_runs row (the invariant will
     clear on the next 60s tick).
   - If not found: continue.

3. **Find the writer in source:**
   ```bash
   git log -S 'load-test-chain-contention-site' --since=30d \
       -- mcp-server/central-command/backend/
   ```

4. **Quarantine the row — DO NOT DELETE:**
   §164.316(b)(2)(i) 7-year retention + mig 151 `trg_prevent_audit_
   deletion` make deletion of compliance_bundles a SECURITY
   INCIDENT. Mark for forensic review instead:
   ```sql
   UPDATE compliance_bundles
      SET notes = COALESCE(notes, '') ||
        ' [QUARANTINE: orphan load-test bundle, ticket #...]'
    WHERE bundle_id = '<bundle_id>';
   ```

## Verification

- Invariant clears on next 60s tick once one of:
  - load_test_runs row backfilled to cover the bundle's window
  - 7 days pass (rolling window slides past)
  - sites.load_test_chain_contention flipped to FALSE on the seed
    site (NOT recommended — would also disable #117 invariant
    coverage for legitimate soak runs)

## Escalation

- **>5 orphan bundles in 24h:** sev1 escalation — a production
  writer is consistently leaking. Pause k6 runs + freeze the
  affected code path until root cause is closed.
- **Bundle's check_type implies customer-facing impact:** if
  check_type ∈ {'evidence', 'privileged_access',
  'remediation_attestation'}, escalate to sev1 + page on-call +
  loop in counsel — a customer-facing event was misrouted to
  synthetic infrastructure (chain-of-custody concern even though
  the customer's own chain is intact).

## Related runbooks

- `bundle_chain_position_gap.md` (sev1 — chain corruption sibling)
- `load_test_marker_in_compliance_bundles.md` (sev1 — explicit
  marker class; the load-test site is the carve-out destination)
- `synthetic_traffic_marker_orphan.md` (sev2 — wider class on
  customer-facing aggregation tables)

## Change log

- 2026-05-16 — initial — #117 Sub-commit B (mig 325) closure.
  Companion invariant to mig 325's site seed. Gate A:
  audit/coach-117-chain-contention-load-gate-a-2026-05-16.md
  (Option C, P0-2d binding).
