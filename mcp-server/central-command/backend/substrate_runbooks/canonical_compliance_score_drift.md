# canonical_compliance_score_drift

**Severity:** sev2
**Display name:** Customer-facing compliance score diverges from canonical helper

## What this means (plain English)

A customer-facing endpoint returned a `compliance_score` value that differs from the canonical helper's output for the same input by more than 0.5. The substrate engine samples 10% of customer-facing requests into `canonical_metric_samples` (Phase 2b sampler decorator), then verifies the sample matches what `compute_compliance_score` produces with `_skip_cache=True` and the captured helper_input. A delta >0.5 indicates the endpoint went through a non-canonical computation path.

Pairs with the static AST gate `test_canonical_metrics_registry.py` (Counsel Rule 1 Phase 0+1, already shipped). The static gate catches **non-canonical-delegation drift** (code that doesn't go through the helper). This invariant catches **non-canonical-value drift** (code that takes the right path but produces a different number — e.g., a stale fork of the algorithm, a forgotten kwarg, a missing site_id in the aggregation).

## Root cause categories

- **`migrate`-class allowlist entry not yet drive-down'd** — the endpoint uses one of the `canonical_metrics.py` allowlist callsites (`db_queries.get_compliance_scores_for_site`, `frameworks.get_compliance_scores`, etc.) which has its own inline formula. Phase 3 drive-down task removes these one at a time.
- **`include_incidents` mismatch** — the endpoint passes `include_incidents=True` but the sampler captured `False` (or vice-versa). Phase 2c v3 P0-E4 fix captures the kwarg; verify the sampler call at the endpoint matches the helper call.
- **`window_days` mismatch** — same shape; sampler must capture the actual `window_days` passed to the helper.
- **Helper has been forked locally** — a partial copy of `compute_compliance_score` lives in some module; the endpoint uses the fork instead of the canonical. Rule 1 violation; needs fix.

## Immediate action

This is an operator-facing alert. **DO NOT surface to clinic-facing channels** — substrate-internal Rule-1 compliance state is not customer-relevant per Session 218 task #42 opaque-mode parity rule.

1. **Read `details.endpoint_path`** — names the surface (e.g., `/api/client/dashboard`, `f1:attestation_letter`).
2. **Read `details.captured_value` + `details.canonical_value` + `details.delta`** — see how far off and in which direction.
3. **Read `details.interpretation` + `details.remediation`** — auto-generated narrative + suggested drive-down PR.
4. **Locate the code path:**
   - For API endpoints: grep the file for the endpoint decorator.
   - For PDF generators: check `client_attestation_letter.py` (F1), partner PDFs, etc.
5. **Verify the call path goes through `compute_compliance_score`** — if YES, check the kwargs match what the sampler captured. If NO, this is a Rule 1 violation — the endpoint must delegate.
6. **Check `canonical_metrics.py` allowlist** — if the offending file:line is in the `migrate`-class allowlist, this is the Phase 3 drive-down work surfacing in production. Open a drive-down task.

## Verification

- Panel: invariant row clears after the next 60s tick where the sample-recompute delta drops back below 0.5.
- CLI (after fix deploys):
  ```sql
  SELECT * FROM canonical_metric_samples
   WHERE metric_class = 'compliance_score'
     AND endpoint_path = '<offending-path>'
     AND captured_at > NOW() - INTERVAL '15 minutes'
   ORDER BY captured_at DESC LIMIT 10;
  ```
  Recompute via the helper using the captured `helper_input` and confirm match.

## False-positive guard

- **Sample-then-recompute window-shift:** the sample was captured at t=0; the substrate invariant recomputes at t=N seconds later. The NOW-anchored window has slid by N. Tolerance 0.5 accommodates small numeric drift at the window boundary. Larger deltas are real non-canonical-path drift.
- **Cache bypass:** the invariant calls `compute_compliance_score(..., _skip_cache=True)` so the 60s TTL cache doesn't collapse the comparison. Without this, a sample captured at t=0 would be compared against the cached version of itself within the cache TTL — false negative.
- **Empty `site_ids`:** sample rows with empty `helper_input.site_ids` are skipped (no comparison possible).
- **`captured_value IS NULL`:** sample rows with NULL captured_value are skipped (e.g., F1 PDF for an org with no compliance bundles).

## Related runbooks

- `unbridged_telemetry_runbook_ids.md` — Rule 1 sibling for runbook_id canonicality (`assertions.py:1051`).
- `l2_resolution_without_decision_record.md` — Rule 1 sibling for L2-resolution-tier canonicality (`assertions.py:1101`).

## Change log

- 2026-05-13 — initial — Phase 2c invariant shipped alongside `_skip_cache` kwarg on `compute_compliance_score` (Task #64, Counsel Rule 1 runtime half).
