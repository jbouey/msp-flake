# Class-B 7-lens Gate A — Task #67 Phase 2b rollout (canonical_metrics_sampler)

**Date:** 2026-05-13
**Author/scope:** Fork-based adversarial review (Steve / Maya / Carol / Coach / PM)
**Subject:** Integrate the "remaining 11 emit-paths" into `sample_metric_response()` for `compliance_score`.
**Pre-state:** 2 emit-paths integrated (commit `2118f04c` /api/client/dashboard + commit `9c64cd28` F1 attestation letter PDF).

---

## 200-word summary

**BLOCK — premise defect.** The brief enumerates 11 "remaining emit-paths" but source-grep of `compute_compliance_score(` in customer-facing surfaces yields **only 4 customer-facing canonical-helper callsites in the entire backend**, of which **2 are already integrated** (Phase 2b proof-of-life + F1 PDF). The actual remaining work is **2 callsites**, not 11: `client_portal.py:1221` (/api/client/sites/{id}/compliance-health, the `include_incidents=True` site) and `client_portal.py:1889` (/api/client/reports/current).

The other 9 line numbers in the brief are stale grep artifacts: 6 in `routes.py` are either inline SQL `passed/total*100` aggregations (`/fleet-posture`, `/admin/organizations/{id}`, monthly admin report) or read-from-table-column accessors; `org_management.py:1118-1203` is also inline SQL; `portal.py:1308` reads a stored value from `compliance_packets`; `client_portal.py:5796` is the F4 public verify endpoint that reads a stored letter, NOT a live compute; `client_portal.py:1934` is an unrelated healing-query line inside the reports/current endpoint (which is captured via :1889).

**Sampling a non-canonical inline aggregation defeats the entire Phase 2c drift-detection contract.** Those 9 callsites must MIGRATE to the canonical helper FIRST (Counsel Rule 1 drive-down work, task #50), THEN get sampled.

**Recommended path:** 1 commit for the 2 real targets (Batch A) + spin out a separate Class-B Gate A for the 9 migration targets.

---

## Per-lens verdicts

### Steve (Engineering) — BLOCK on premise / APPROVE on real targets

Source-grep `compute_compliance_score(` against customer-facing `.py` files (excluding test_, assertions.py, canonical_metrics*.py, compliance_score.py):

| File | Line | Endpoint | Already integrated? | Real Phase 2b target? |
|---|---|---|---|---|
| `client_portal.py` | 760 | /api/client/dashboard | YES (`2118f04c`) | — |
| `client_portal.py` | 1221 | /api/client/sites/{id}/compliance-health | **NO** | **YES** (include_incidents=True) |
| `client_portal.py` | 1889 | /api/client/reports/current | **NO** | **YES** |
| `client_attestation_letter.py` | 209 | F1 PDF | YES (`9c64cd28`) | — |

**That's it. Four total. Two real targets.** Pattern adapts mechanically — same shape as `2118f04c` + `9c64cd28`.

**Brief mismatch matrix:**

| Brief item | Reality | Verdict |
|---|---|---|
| `client_portal.py:1206` /compliance-health (include_incidents=True trigger) | Stale line number. Actual callsite is `:1221`. include_incidents=True correct. | **REAL TARGET** — integrate. |
| `client_portal.py:1745` /reports/current aggregation | Stale line number. Reports/current callsite is `:1889`. `:1745` is in verify_evidence (no score). | **REAL TARGET** (`:1889`, not `:1745`). |
| `client_portal.py:1934` /sites/{id} site detail | `:1934` is a healing-query line INSIDE /reports/current, not a separate emit-path. No `compute_compliance_score` here. | Phantom — already covered by `:1889`. |
| `client_portal.py:5796` /api/client/appliances/{id} (RT33 P2) | `:5796` is in `public_verify_attestation_letter` (F4 public verify). No live compute — reads stored letter. `/api/client/appliances` (`:923`) does NOT emit compliance_score. | NOT a compliance_score emit-path. |
| `org_management.py:1203` partner-internal org listing | Endpoint is `/api/orgs/{org_id}/compliance-packet` (admin auth `require_auth`, not partner). Inline SQL `passed/total*100` (lines 1110-1118) — Rule-1 violation, allowlist `migrate` class. | **PRE-EXISTING RULE-1 VIOLATION** — migrate to canonical helper first; sampling the inline value defeats drift detection. |
| `portal.py:1305` legacy | Reads `compliance_score` column from stored `compliance_packets` table row. No live compute. | Read-from-stored, not sampler-eligible. |
| `routes.py:3398` admin /fleet-posture | Inline SQL site_compliance CTE `passed/total*100` lines 3331-3342. Rule-1 violation. | **PRE-EXISTING RULE-1 VIOLATION** — migration required. |
| `routes.py:4875` admin /organizations/{org_id} | Uses `get_all_compliance_scores(db)` — allowlist `migrate` non-canonical helper. | **PRE-EXISTING RULE-1 VIOLATION** — migration required. |
| `routes.py:5774` admin | Inside `/api/client/sites/{id}/compliance-health` endpoint healing-fetchrow. The endpoint at this line range uses bespoke per-category aggregation lines 5710-5745 (not canonical). | Same endpoint as routes-form of compliance-health; bespoke aggregation. **PRE-EXISTING RULE-1 VIOLATION**. |
| `routes.py:5786` admin | Inside same endpoint, the canonical-devices coverage CTE. Not score. | Phantom. |
| `routes.py:7627` admin | Inside a check-results loop (no score emit). | Phantom. |
| `routes.py:7851` admin monthly-report | Inline SQL `passed/total*100` lines 7862-7872. Rule-1 violation. | **PRE-EXISTING RULE-1 VIOLATION** — migration required. |

**STEVE P0:** The brief instructs sampling INLINE values that are already known Rule-1 violations on the migrate allowlist. Recording these in `canonical_metric_samples` with `classification='customer-facing'` would (a) generate guaranteed drift-fires on every sample because the canonical recompute will differ, (b) trash the substrate invariant signal-to-noise, (c) NOT improve Rule-1 posture because the underlying violation is the inline aggregation, not the sampling gap.

**STEVE P0:** Pattern verification — checked `2118f04c` + `9c64cd28` integration shape against `client_portal.py:1221` and `:1889` call shapes:
- `:1221` passes `include_incidents=True` (line 1222) and no `window_days` (defaults to 30). Sampler `helper_input` must capture `include_incidents=True`.
- `:1889` passes no kwargs (defaults to `window_days=30`, `include_incidents=False`).
- Both inside `async with org_connection(pool, org_id=org_id) as conn` — same RLS context as the proof-of-life. Pattern adapts mechanically.

**STEVE P1:** Brief's "endpoint_path" string for the F4 verify endpoint would have been the wrong shape anyway — public hash-lookup has no `tenant_id` to scope drift detection. Even if it computed live (it doesn't), the sampler's per-tenant assertion path can't operate.

**STEVE P1:** Note that `client_quarterly_summary.py:345` is also an INLINE aggregation — but the comment explains why ("compute_compliance_score uses NOW()-window_days, which can't take a fixed past quarter"). This is a legitimate canonical-helper feature gap, not a Rule-1 violation per se. Out of scope for Phase 2b but should be tracked separately (canonical helper grows a fixed-window param).

### Maya (Database) — APPROVE on revised scope

**Reality-corrected scale math** (2 callsites, not 11):

- Customer-facing requests per site per day per real callsite: ~5 (Steve's working estimate)
- Current paying customers: 1 (North Valley) + 6 chaos-lab sites + a handful of demo orgs ≈ 10 sites worst-case in production today
- Per-callsite sample rate: 10%
- Per-day rows added by Batch A: 2 callsites × 5 req/site/day × 10 sites × 0.1 = **10 rows/day**
- Plus already-integrated 2 callsites: another ~10 rows/day
- **Net: ~20 rows/day total across all 4 customer-facing emit-paths.**

Compare to `canonical_metric_samples` capacity: monthly-partitioned per mig 314, 3-month coverage shipped. **20 rows/day × 30 = 600 rows/month** — trivial. The partial index `WHERE classification='customer-facing'` keeps the drift-scan working-set tiny.

**MAYA P1 (informational, not blocking):** If the 9 stale-grep callsites had been wired with `classification='customer-facing'`, the math would be more aggressive (~9 callsites × inline aggregations on broad WHERE clauses) — but each invocation is per-request, so the row volume still doesn't scale beyond ~70 rows/day at current paying-customer scale. Not a Maya-level concern; the Steve-level concern dominates.

**MAYA APPROVE:** Batch A insert cost is negligible. No DB issues.

### Carol (Security) — APPROVE with NOTE

The brief's Carol probe is well-framed but the answer is already shipped: per `canonical_metrics_sampler.py:_VALID_CLASSIFICATIONS` + the substrate WHERE clause at `assertions.py:6163` — only `classification='customer-facing'` rows fire drift. Three-layer defense-in-depth:

1. mig 314 CHECK constraint blocks invalid `classification` values at write time
2. Partial index `WHERE classification='customer-facing'` physically excludes operator/partner samples from drift-scan
3. The Phase 2c invariant SELECT explicitly filters to `customer-facing`

**CAROL NOTE:** Brief proposed `classification='operator-internal'` for the 6 routes.py operator paths. Per the design, those would be sampled-for-observability but excluded-from-drift-fire. **However**, since Steve's review concludes those callsites are inline Rule-1 violations needing migration FIRST, Carol's question is moot for Phase 2b — they're not getting sampler integration in the current batch.

**CAROL P2 (informational):** `org_management.py:1118` endpoint (`/api/orgs/{org_id}/compliance-packet`) — auth is `auth_module.require_auth` (admin/operator), not partner. Brief's "partner-internal classification" label is wrong — this is operator-internal. Doesn't affect verdict.

### Coach (Process/Quality) — BLOCK + redesign

**Two structural problems with the brief:**

1. **Stale-grep premise.** The brief was authored from grep output without verifying each line is actually a `compute_compliance_score` callsite. 9 of 11 brief items don't match the criterion. Coach's responsibility is to **catch premise defects before they consume execution effort.** A 2.5h estimate against an 11-item batch becomes a 5-min batch against 2 real items — but only AFTER source-walking the brief.

2. **Counsel Rule 1 ordering inversion.** Sampling INLINE non-canonical aggregations into `canonical_metric_samples` with `classification='customer-facing'` is worse than not sampling: every recompute will diverge from the captured value (because the canonical helper produces a different number than `passed/total*100` over compliance_bundles cross jsonb_array_elements), generating a flood of false-positive drift fires that mask real drift. **The Phase 2c invariant only works against samples that should equal the canonical helper output.** Migrating the 9 inline aggregations to delegate to `compute_compliance_score` is the prerequisite, not the follow-up.

**COACH P0:** Reframe the work as TWO independent tasks:

- **Task #67 (this one) — Batch A:** Integrate the 2 real remaining callsites (`client_portal.py:1221` + `:1889`). 1 commit. ~20min. Closes Task #67.
- **Task #NEW (spin out) — Counsel Rule 1 inline-aggregation migration:** Migrate 9 callsites (6 in routes.py, 1 in org_management.py:1118, plus the routes.py compliance-health bespoke aggregation, plus consider portal.py:1308 and client_quarterly_summary.py:345). Each callsite needs its OWN Gate A because windowing semantics differ (admin /fleet-posture uses 24h window; quarterly summary uses fixed past-quarter; compliance-packet uses fixed month). Some require canonical helper enhancements (fixed-window param). NOT a mechanical batch.

**COACH P1:** Batching strategy for Batch A (the real 2 callsites): Option A (one commit) is right. Both inside `client_portal.py`, both customer-facing, both mechanical. One commit + one Gate B is correct. Per Session 220 lock-in, that single Gate B must run the full pre-push CI parity sweep (not diff-only), but the sweep already exists.

**COACH P1:** Gate B must verify the `include_incidents=True` capture at `:1221` actually flows into `helper_input` — the proof-of-life pattern hard-codes `False`. A copy-paste of that snippet would be wrong for `:1221` and would generate false drift fires after Phase 2c runs against it.

### PM — Re-estimate

**Original brief:** 11 callsites × 10min + 20min Gate B = ~2.5h.
**Reality:** 2 callsites × 10min + 20min Gate B = **~40min.**
**Spin-out (separate task):** 9 inline-aggregation migrations × per-callsite Gate A + migration work ≈ multi-day. Tracked as new task, not Phase 2b.

---

## Per-callsite classification matrix

| Brief line | True line | True endpoint | True classification | In Batch A? | Notes |
|---|---|---|---|---|---|
| client_portal.py:1206 | client_portal.py:1221 | /api/client/sites/{id}/compliance-health | customer-facing | **YES** | include_incidents=True in helper_input |
| client_portal.py:1745 | client_portal.py:1889 | /api/client/reports/current | customer-facing | **YES** | defaults — include_incidents=False, window_days=30 |
| client_portal.py:1934 | (phantom) | — | — | NO | line is healing-fetchrow inside /reports/current; already covered |
| client_portal.py:5796 | client_portal.py:5728 (vicinity) | /api/verify/attestation/{hash} (public F4) | n/a | NO | reads stored letter, not live compute; no tenant scoping |
| org_management.py:1203 | org_management.py:1118 | /api/orgs/{org_id}/compliance-packet | operator-internal (admin auth) | NO | **inline SQL Rule-1 violation** — migrate first |
| portal.py:1305 | portal.py:1308 | /api/portal/site/{site_id}/home | n/a | NO | reads compliance_packets.compliance_score column |
| routes.py:3398 | routes.py:3398 | /api/fleet-posture | operator-internal | NO | **inline SQL Rule-1 violation** — migrate first |
| routes.py:4875 | routes.py:4875 | /api/organizations/{org_id} | operator-internal | NO | uses get_all_compliance_scores (allowlist migrate) |
| routes.py:5774 | routes.py:5774 | (routes-form compliance-health) | operator-internal | NO | **bespoke per-category aggregation** — migrate first |
| routes.py:5786 | (phantom) | — | — | NO | canonical-devices coverage CTE, not score |
| routes.py:7627 | (phantom) | — | — | NO | check-results loop line |
| routes.py:7851 | routes.py:7851 | admin monthly-report | operator-internal | NO | **inline SQL Rule-1 violation** — migrate first |

---

## Batching recommendation

**Option D (new):** Reduce scope to 2 real callsites + spin out the inline-aggregation work.

**Batch A (this task #67 closeout):**
- Single commit: integrate `client_portal.py:1221` + `client_portal.py:1889`
- Pattern: identical to `2118f04c` proof-of-life
- Critical: `:1221` MUST capture `include_incidents=True` in `helper_input` (NOT a copy-paste of the False default)
- helper_input shape for `:1889`: `{"site_ids": site_ids, "window_days": 30, "include_incidents": False}`
- helper_input shape for `:1221`: `{"site_ids": [site_id], "window_days": 30, "include_incidents": True}`
- endpoint_path strings: `"/api/client/sites/{site_id}/compliance-health"` and `"/api/client/reports/current"` (use the actual route shape; literal `{site_id}` is fine — it's a sample-bucket label, not a request-time path)
- classification: `"customer-facing"` both
- Gate B: run full pre-push sweep + curl one sample call against the 2 endpoints to verify no 500s; query `canonical_metric_samples` to confirm rows land with the expected helper_input shape

**Batch B (NEW task — Counsel Rule 1 inline-aggregation migration):**
- 9 callsites, NOT mechanical
- Each needs own Gate A: windowing semantics differ
- Some require canonical helper enhancement (fixed-window param for quarterly + compliance-packet endpoints)
- Tracked separately from Task #67

---

## Top P0/P1

**P0-1 (Steve + Coach):** **Drop 9 of the 11 brief items from Phase 2b scope.** They are pre-existing Rule-1 violations (inline aggregations on the canonical-migration allowlist). Sampling them now generates guaranteed Phase 2c false positives that poison the drift-detection signal.

**P0-2 (Steve):** `client_portal.py:1221` integration MUST capture `include_incidents=True` in `helper_input`. The substrate recompute will use this value at `assertions.py:6183` — a False here against a True caller call produces a guaranteed-different canonical recompute and false drift fire. Pin this in the sampler-integration regression test (mirror of `test_f1_pdf_score_extraction.py` for the 2 endpoints).

**P0-3 (Coach):** Gate B verification step MUST include a query against `canonical_metric_samples` after a synthetic request to each endpoint, confirming the `helper_input` JSON contains the right `include_incidents` value per endpoint. Otherwise a copy-paste error from the False default in `2118f04c` would land undetected and silently poison Phase 2c.

**P1-1 (Coach):** Open new task to track the 9 inline-aggregation migrations under Counsel Rule 1 drive-down (Task #50 child). Each callsite separate Gate A.

**P1-2 (Steve):** Verify `endpoint_path` strings are consistent across the 4 customer-facing samples (`/api/client/dashboard`, `/api/client/sites/{site_id}/compliance-health`, `/api/client/reports/current`, `f1:attestation_letter`). The substrate doesn't pivot on these but they appear in violation `details` for triage — bake a convention now (literal route template with `{site_id}` placeholder for parameterized routes; `f1:`/`f3:`/etc prefix for non-HTTP emit-paths).

**P2-1 (PM):** Update Task #67 description to reflect actual scope (2 real callsites + spin-out task).

---

## Final overall verdict

**BLOCK as written — APPROVE-WITH-FIXES on revised scope.**

The brief's "11 remaining emit-paths" premise does not survive source-grep. The actual remaining Phase 2b work is **2 customer-facing canonical-helper callsites** (`client_portal.py:1221` + `:1889`). The other 9 are pre-existing inline Rule-1 violations on the canonical-migration allowlist; they need their own migration work BEFORE sampler integration would be meaningful.

**Proceed with Batch A** (2 callsites, 1 commit, ~40min including Gate B). The pattern adapts mechanically from `2118f04c` + `9c64cd28` PROVIDED that `:1221` captures `include_incidents=True` in `helper_input` (NOT the False default from the proof-of-life snippet).

**Spin out a new task** for the 9 inline-aggregation migrations — those are Counsel Rule 1 drive-down work, distinct per-callsite Gate A, not mechanical.

Gate B for Batch A must (a) run full pre-push CI parity sweep, (b) curl-verify both endpoints don't 500, (c) `psql` query against `canonical_metric_samples` to confirm rows land with correct `helper_input.include_incidents` per endpoint, (d) cite the actual sweep pass/fail counts in the commit body.
