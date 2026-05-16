# synthetic_traffic_marker_orphan

**Severity:** sev2
**Display name:** Synthetic marker in customer aggregation

## What this means (plain English)

Rows in a customer-facing aggregation table (incidents,
l2_decisions, evidence_bundles, aggregated_pattern_stats) carry
`details->>'synthetic'='load_test'` OR `'mttr_soak'`. Load-harness
or MTTR-soak traffic that should have been filtered out by the
universal `IS NOT TRUE` writer guards leaked into a customer-facing
surface.

Per v2.1 spec P0-3 (marker unification): both 'load_test' (this
spec) and 'mttr_soak' (plan-24) use the same `details.synthetic`
shape so a single invariant catches both classes.

The sibling sev1 invariant `load_test_marker_in_compliance_bundles`
covers the crypto-chain table separately — chain corruption is
strictly worse than visibility leak.

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
