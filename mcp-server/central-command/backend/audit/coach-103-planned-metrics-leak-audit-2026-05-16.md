# PLANNED_METRICS leak audit (3 classes)

## historical_period_compliance_score
- Known callsites (operator_only, per canonical_metrics.py):
  - `compliance_packet.py:CompliancePacket._calculate_compliance_score` (PDF generator, period-bounded via `_period_start`/`_period_end`)
- Leaks found (direct period-bounded queries NOT in compliance_packet.py):
  - `client_quarterly_summary.py:370` — reads `compliance_bundles` with `checked_at >= $2 AND checked_at < $3` bounds, computes per-control mapping per framework (NOT compliance-score class, but similar temporal semantic)
  - **VERDICT: KNOWN-OK** — `client_quarterly_summary.py` computes per-control status, not per-site historical compliance score. Different metric class (control-level state, not aggregate score).

## category_weighted_compliance_score
- Known callsites (operator_only, per canonical_metrics.py):
  - `db_queries.py:606 get_compliance_scores_for_site` — per-category `(pass + 0.5*warn)/total*100` with HIPAA_CATEGORY_WEIGHTS
  - `db_queries.py:832 get_all_compliance_scores` — batched variant of same methodology
- Leaks found (inline per-category scoring with HIPAA weights NOT delegating to db_queries helpers):
  - **`routes.py:5733-5751` in `@router.get("/sites/{site_id}/compliance-health")`** — computes per-category breakdown with identical formula `(pass + 0.5*warn)/total*100` and HIPAA_CATEGORY_WEIGHTS inline (lines 5743-5747), customer-facing admin endpoint
  - **`client_portal.py:1162-1176` in `@auth_router.get("/sites/{site_id}/compliance-health")`** — computes per-category breakdown with identical formula inline (line 1169), customer-facing client endpoint (though headline delegates to `compute_compliance_score`)
- **VERDICT: LEAK×2** — Two customer-facing endpoints (`routes.py` admin + `client_portal.py` client) compute category_weighted_compliance_score inline instead of delegating to `db_queries.get_compliance_scores_for_site()`. Both use the telltale `(cat_pass + 0.5*cat_warn)/total*100` formula with HIPAA_CATEGORY_WEIGHTS.

## per_framework_compliance_score
- Known callsites (operator_only, per canonical_metrics.py):
  - `frameworks.py:216 get_compliance_scores` — reads `compliance_scores` denormalized table with per-framework rollup
  - `frameworks.py:425 get_appliance_compliance_scores` — FastAPI endpoint wrapper around above
- Leaks found (direct reads of `compliance_scores` table NOT in frameworks.py):
  - **None found** — only callsite is frameworks.py:248 (`FROM compliance_scores cs JOIN appliance_framework_configs`), which is already registered as `operator_only` in the allowlist.
- **VERDICT: KNOWN-OK** — per_framework_compliance_score is gated to frameworks.py. No leaks detected.

## Summary
- **Total leaks: 2**
- **Metrics with exposure: 1 of 3** (category_weighted_compliance_score)
- **Recommendations:**
  1. Refactor `routes.py:5733-5751` to delegate to `db_queries.get_compliance_scores_for_site()` instead of recomputing inline.
  2. Refactor `client_portal.py:1162-1176` to delegate to `db_queries.get_compliance_scores_for_site()` instead of recomputing inline.
  3. Add CI gate to forbid imports of `HIPAA_CATEGORY_WEIGHTS` or the per-category formula pattern outside of `db_queries.py` and `routes.py` (once fixed). This will catch future drift.
  4. Reclassify `category_weighted_compliance_score` from `PLANNED_METRICS` to `CANONICAL_METRICS` once the leaks are plugged, per Counsel Rule 1 (every exposed metric must have a canonical helper).
