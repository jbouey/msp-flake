# load_test_marker_in_compliance_bundles

**Severity:** sev1
**Display name:** Synthetic marker in evidence chain — CHAIN INTEGRITY

## What this means (plain English)

A row in `compliance_bundles` carries `details->>'synthetic'='load_test'`.
This is a hard violation of the auditor-kit determinism contract:

- `compliance_bundles` rows are Ed25519-signed by the server +
  hash-chained per site + OTS-anchored via Merkle batches.
- The auditor kit ZIP pins to bundle content; any synthetic row
  flips the kit hash between consecutive downloads — visible as
  a tamper-evidence violation to the customer + auditor.

The CI gate `tests/test_no_load_test_marker_in_compliance_bundles.py`
scans backend `INSERT INTO compliance_bundles` callsites for
proximity to `'load_test'` markers and blocks at build time. If
this invariant fires, the static scan was bypassed — either by
dynamic SQL construction, an ORM raw-execute, or a fork branch
that hadn't run CI.

## Root cause categories

- New code path doing `INSERT INTO compliance_bundles` via dynamic
  SQL (string interpolation) that escaped the source-shape scan
- ORM raw-execute / `exec_driver_sql` bypassing the CI gate
- Test fixture leaking into prod (unlikely — test fixtures use
  isolated test DB)
- Deliberate tampering — pause + investigate immediately

## Immediate action

1. **Quarantine the row** — capture `bundle_id`, `site_id`,
   `check_type`, `created_at`, full row. Do NOT delete (auditor
   evidence). Mark the chain segment as invalidated.
2. **Notify on-call security** — this is a chain-integrity event.
3. **Find the writer** — `git log -S 'load_test' --since=<days>`
   then grep for INSERT INTO compliance_bundles in those PRs.
4. **Re-run the chain verifier** for the affected site to
   determine the blast radius.

## Verification

- Panel: invariant clears on next 60s tick once the row is
  quarantined (or moved to a `quarantine_compliance_bundles`
  side-table).
- Auditor kit hash: download two consecutive kits for affected
  site; SHA256 MUST match post-quarantine.

## Escalation

This is a **sev1 chain-integrity event** — page immediately. Even
one synthetic row across the entire customer base is a hard
auditor finding. Loop in counsel if a customer auditor has
already downloaded a kit containing the row.

## Related runbooks

- `synthetic_traffic_marker_orphan.md` (sibling, wider class)

## Change log

- 2026-05-16 — initial — Task #62 v2.1 Commit 5a / Gate A P0-2 + P1-6
