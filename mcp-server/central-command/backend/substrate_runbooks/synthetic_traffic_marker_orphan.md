# synthetic_traffic_marker_orphan

**Severity:** sev2
**Display name:** Synthetic marker in customer aggregation

## What this means (plain English)

Rows in a customer-facing aggregation table (incidents,
l2_decisions, evidence_bundles, aggregated_pattern_stats) are
tied to a **synthetic site** (i.e., `sites.synthetic = TRUE` —
mig 315). Load-harness or MTTR-soak traffic that should have been
filtered out by the universal synthetic-site filter leaked into
a customer-facing surface.

The authority is `sites.synthetic = TRUE` — NOT a per-row details
marker (most of these tables lack a `details` JSONB column
entirely). A secondary check catches the MTTR-soak shape
specifically: `incidents.details->>'soak_test' = 'true'` (real
marker per mig 303, indexed) tagged on a NON-synthetic site —
that's a writer-side mis-routing bug distinct from the wider
synthetic-site leak.

The sibling sev1 invariant `load_test_marker_in_compliance_bundles`
covers the crypto-chain table separately — chain corruption is
strictly worse than visibility leak.

Gate B C5a-rev1 (2026-05-16): the prior implementation queried
`details->>'synthetic' IN ('load_test','mttr_soak')` on all 4
tables. Only `incidents` has a `details` column; the other 3
silently skipped via `except asyncpg.PostgresError: continue`.
AND the real MTTR-soak marker is `details.soak_test='true'` per
mig 303, NOT `synthetic='mttr_soak'`. The invariant covered 0 of
its 4 declared tables. Per fork verdict
`audit/coach-c5a-pha-94-closure-gate-b-2026-05-16.md` §P0-2.

## Root cause categories

- A new INSERT/UPDATE path missing the `IS NOT TRUE` filter on
  `details.synthetic`
- A SELECT-aggregate path that doesn't filter synthetic rows out
  before computing the customer-visible metric
- Synthetic-marker dropped or renamed without updating the writer

## Immediate action

1. Identify the writer: `git grep -n "INSERT INTO <table>" backend/`
   then check each callsite for the synthetic filter.
2. Add the universal filter pattern:
   ```python
   WHERE (details->>'synthetic') IS DISTINCT FROM 'load_test'
     AND (details->>'synthetic') IS DISTINCT FROM 'mttr_soak'
   ```
3. Re-aggregate the customer-facing metric for the affected site
   to scrub any leaked values.

## Verification

- Panel: invariant row drops as offending rows are quarantined.
- Customer-facing read of the affected aggregation should return
  the same value pre/post-quarantine for non-synthetic data.

## Escalation

- If hit_count is >100 OR persists across multiple 60s ticks,
  pause the load harness + MTTR soak until the writer path is
  fixed. The chaos lab + MTTR soak markers can also trigger this
  invariant if their filters regress — verify which is the source.

## Related runbooks

- `load_test_marker_in_compliance_bundles.md` (sev1 sibling)
- `load_test_run_stuck_active.md`

## Change log

- 2026-05-16 — initial — Task #62 v2.1 Commit 5a
