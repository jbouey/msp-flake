# Class-B 7-lens Gate A v2 — `canonical_compliance_score_drift` (Mechanism B)

**Date:** 2026-05-13
**Reviewer:** fork-based 7-lens review (author cannot self-grade per Session 219 two-gate lock-in)
**Design under review:** `audit/canonical-metric-drift-invariant-design-2026-05-13.md` (v2)
**Phase:** Task #50 Phase 2 (Counsel Rule 1 runtime half)
**Prior gate:** v1 returned **BLOCK** with 3 P0s; v2 is the Mechanism B pivot per fork recommendation

---

## v1 P0 closure matrix

| # | v1 P0 | v2 status | Evidence |
|---|---|---|---|
| **P0-E1 (Steve)** | `compute_compliance_score` does not accept absolute window bounds (only `window_days: int`); Mechanism C "re-run against chain's input" not implementable | **CLOSED** | v2 drops Mechanism C entirely. The runtime invariant recomputes the helper with the same relative kwargs the endpoint passed (`window_days` + `site_ids`) — no API extension. §2 `_check_canonical_compliance_score_drift` calls helper exactly as the helper's existing signature accepts (line 133-135). |
| **P0-E2 (Steve)** | `compliance_bundles` row attests raw per-scan `checks[]` from ONE scan; does NOT carry an aggregated score; "chain attests a score" is a category error | **CLOSED** | v2 §2 carries no chain-attested-score premise. The sample table stores `captured_value` from the customer-facing response itself (endpoint output), and the invariant compares to fresh-helper output. No reading from `signed_data`. The chain is untouched. §4 explicitly: "Neither gate is the master BAA Article 3.2 cryptographic-attestation-chain claim (that's the Ed25519+OTS chain itself)." |
| **P0-C1 (Coach)** | mig 314 `attested_compliance_score` column was double-build vs existing `signed_data` JSONB | **CLOSED** | v2's mig 314 creates a new operator-internal `canonical_metric_samples` table — does NOT touch `compliance_bundles`, does NOT add a sibling column to a signed row. The new table is explicitly operator-scope per §1 + §4. No cryptographic implications. |
| Bonus | Rename `canonical_metric_drift` → `canonical_compliance_score_drift` | **CLOSED** | v2 design header (line 1, §2 substrate-invariant function name at line 101) + design's preamble explicitly renames + scopes narrowly. Other 3 classes (baa_on_file, runbook_id_canonical, l2_resolution_tier) get separate invariants with cited precedents. |

**All 3 v1 P0s + the rename bonus: CLOSED.** v1→v2 pivot is structurally sound.

---

## Per-lens verdict (NEW on Mechanism B)

| # | Lens | Verdict | Headline P0/P1 |
|---|---|---|---|
| 1 | Engineering (Steve) | **APPROVE-WITH-FIXES** | P0-E4 helper_input missing `include_incidents`; P0-E5 endpoint list incomplete (≥10 surfaces, not 6); P1-E6 60s TTL cache + NOW() shift; P1-E7 partition_maintainer_loop doesn't cover new table |
| 2 | HIPAA auditor (OCR surrogate) | APPROVE | Mechanism B is auditor-grade; sample-and-recompute statistically defensible; no Article 3.2 risk |
| 3 | Coach (no double-build) | APPROVE-WITH-FIXES | P1-C2 `prometheus_metrics` already samples some metrics — verify no overlap with sampling decorator; P1-C3 retention story (§2 says "auto-pruned" but loop doesn't cover the table) |
| 4 | Attorney | APPROVE | Mechanism B is supplementary to static AST gate; together they materialize Counsel Rule 1 runtime; not Article 3.2 territory |
| 5 | Product manager | APPROVE-WITH-FIXES | P1-P3 0.1 tolerance + NOW()-window shift produces false-positives at scan boundary; recommend 0.5 OR freeze input set; P1-P4 alert routing must be ops-only |
| 6 | Medical-technical | APPROVE | Operator-internal substrate signal; never reaches clinic admins |
| 7 | Legal-internal (Maya + Carol) | APPROVE | Banned-word scan on design draft: **CLEAN** (no ensures/guarantees/prevents/protects/100%). Runbook not yet drafted — gate at Gate A on the runbook itself. |

**Overall: APPROVE-WITH-FIXES** — 2 P0s in Lens 1; design ships in 3 phases with P0s addressed in Phase 2a before mig 314 lands.

---

## Lens 1 — Engineering (Steve)

### P0-E4: `helper_input` JSONB capture is missing `include_incidents` kwarg

Verified at `compliance_score.py:157-163` — the actual canonical helper signature is:

```python
async def compute_compliance_score(
    conn,
    site_ids: List[str],
    *,
    include_incidents: bool = False,
    window_days: Optional[int] = DEFAULT_WINDOW_DAYS,
) -> ComplianceScore
```

Design v2 §2 captures `helper_input = {"site_ids": ..., "window_days": ...}` — **OMITS `include_incidents`**.

This matters because:
- `/api/client/sites/{id}/compliance-health` passes `include_incidents=True` (matches per-site endpoint's shape — verified at `client_portal.py:1196-1199` region per existing code patterns).
- All other 5 customer-facing surfaces pass `include_incidents=False` (default).
- `include_incidents=True` adds open incidents as fail-votes via the `incident_rows` query (line 287-298). This produces a **different score** for the same `site_ids` + `window_days`.

If the invariant recomputes with `include_incidents=False` against a sample captured from the per-site endpoint (`include_incidents=True`), it will fire false-positive drift on every per-site sample.

**Fix:** `helper_input` must capture `include_incidents` along with `site_ids` + `window_days`. The recompute at §2 line 133 must pass it through. Update `sample_metric_response` signature + the recompute logic.

### P0-E5: Endpoint enumeration in §2 is incomplete — at least 10 customer-facing emit-paths exist

Design §2 Phase 2b lists 6 endpoints (5 customer-facing + 1 admin). Source-grep across `mcp-server/central-command/backend/` for emissions of `compliance_score` / `overall_score` as response keys reveals at least 10 customer-facing emit-paths:

| # | File | Line | Endpoint |
|---|---|---|---|
| 1 | client_portal.py | 817 | /api/client/dashboard |
| 2 | client_portal.py | 1206 | /api/client/sites/{id}/compliance-health |
| 3 | client_portal.py | 1745 | per-site reports (existing path) |
| 4 | client_portal.py | 1842 | quarterly summary fallback |
| 5 | client_portal.py | 1934 | /api/client/reports/current |
| 6 | client_portal.py | 5796 | another portal surface (verify scope) |
| 7 | client_attestation_letter.py | 225, 372 | F1 attestation letter facts |
| 8 | org_management.py | 1203 | partner-facing org list |
| 9 | portal.py | 1305, 2432 | legacy portal endpoints |
| 10 | routes.py | 3398, 4875, 5774, 5786, 7627, 7851 | admin/auditor/utility surfaces |

Of these, **path 7 (F1 attestation letter)** is the highest-stakes customer artifact and is NOT in design §2's list. F1 letters are PDF-signed and have downstream legal weight per the F-series ship 2026-05-08.

**Fix:** Phase 2b decoration plan must enumerate the FULL set. Source-grep is fast; the design's "6 endpoints" undercount becomes a coverage gap if the gate is to be auditor-grade. Recommend:

1. Run `grep -rn '"compliance_score":\|"overall_score":' backend/ --include="*.py" | grep -v test_` and inventory every emit path.
2. Classify each as `customer_facing` (decorate + drift-fire) or `operator_only` (decorate + exclude from drift fire — already noted in §2 for `/api/admin/orgs/{id}/audit-report`).
3. F1 letter path needs special handling — it's not a JSON endpoint, it's a PDF generator; the decorator pattern needs to support non-HTTP-response surfaces.

### P1-E6: 60s TTL cache + NOW()-window shift = false-pass risk on recompute

`compute_compliance_score` has a 60s TTL cache (verified at `compliance_score.py:132 _SCORE_CACHE_TTL_SECONDS = 60.0`). The cache key includes `site_ids`, `include_incidents`, `window_days` — but NOT capture-time.

When the invariant recomputes at T+δ (where δ can be up to 15min per the §2 LIMIT 50 / 15min window), one of three things happens:

1. **Cache HIT** (δ < 60s): recompute returns the EXACT value the endpoint returned — invariant always passes, regardless of whether the endpoint actually went through the canonical path. **The invariant becomes a no-op for this class.**
2. **Cache MISS, NOW() shifted** (δ > 60s): the helper's SQL filter is `cb.checked_at > NOW() - ($2::int * INTERVAL '1 day')`. At T+δ, NOW() is δ later — a `checked_at` near the window edge that was IN at T may be OUT at T+δ (or new bundles within δ may flip the count). Score can drift legitimately by more than 0.1.
3. **Cache MISS, no new bundles** (δ > 60s, stable data): recompute matches — invariant passes correctly.

Net: false-positives at scan boundaries + false-negatives within cache TTL.

**Fix (one of):**
- (a) Bypass cache on invariant recompute (add `_skip_cache=True` kwarg to helper) and increase tolerance to ≥0.5 to absorb NOW()-shift drift; OR
- (b) Freeze input set: invariant samples include the bundle_id list the helper read at T, then recomputes against the same frozen set at T+δ. Requires helper extension to accept frozen-bundle-list — same shape v1 P0-E1 rejected as out-of-scope. NOT recommended for v2.
- (c) Keep 0.1 tolerance + 60s sample-recency window (smaller than cache TTL) — invariant only runs against samples ≤60s old, both calls hit cache, true-comparison only for non-cached paths. Still has the cache-collapsing-the-test problem.

**Recommended:** path (a) — bypass cache on invariant recompute + tolerance 0.5. Statistical defensibility intact at multi-tenant scale.

### P1-E7: `partition_maintainer_loop` does NOT cover the new table

Design v2 §2 mig 314 declares `PARTITION BY RANGE (captured_at)` with "Monthly partitions, 30-day retention" and claims "auto-pruned by `partition_maintainer_loop`". Verified at `background_tasks.py:1480-1533`: the loop only creates partitions for `promoted_rule_events`. No other tables are in its body. Adding `canonical_metric_samples` to the loop is a **net code change to background_tasks.py**, not a free side-effect.

Also: the loop CREATES forward partitions; it does NOT prune old ones. 30-day retention means partitions older than 30d need explicit DROP. That code does not exist today.

**Fix:**
1. Phase 2a mig 314 also adds the new partition class to `partition_maintainer_loop` (or sibling function) with both create-forward + drop-older-than-30d semantics.
2. Add substrate invariant `partition_maintainer_dry` (already exists at `assertions.py:1423`) to its watch-list for the new table.

### Tick-cost check (Steve math)

Design §6: 10% sample × 6 endpoints × 5 req/site/day = 3 samples/site/day → 150 samples/day at 50 customers → ~50 samples in any 15-min window worst case. Recompute cost is ≤2.6s per call (per `compute_compliance_score` docstring profiling).

50 × 2.6s = **130 seconds per tick**. Greater than 60s tick budget. The §6 claim "Tick cost: ~10 helper calls × ~5ms each = ~50ms" is wrong on both counts (5ms is cache-hit cost; 2.6s is cold cost; and the count is not 10 — sample 50 is the LIMIT in §2 line 121).

If the 60s TTL cache holds + the invariant runs immediately after the endpoint (i.e. samples are warm), recompute is ~50ms each × 50 = 2.5s. **Acceptable** — but only if the cache is hit. P1-E6's "bypass cache on recompute" path would push tick-cost to 130s.

**Resolution:** the recompute SHOULD hit cache (cache TTL 60s, sample window 15min — first 60s sees cache-hit, next 14min sees cache-miss). Cost depends on when the substrate tick fires relative to endpoint hits. Need explicit budget assertion.

**Fix:** Phase 2c documents tick-cost behavior + adds a per-assertion timeout (Session 220 admin_transaction precedent) of 5s; truncate sample list if recompute budget exceeded.

### Verdict: APPROVE-WITH-FIXES — P0-E4 + P0-E5 must be addressed in Phase 2a; P1s tracked into Phase 2b/2c.

---

## Lens 2 — HIPAA auditor surrogate (OCR posture)

Mechanism B avoids the Article 3.2 risk that Mechanism C had:

- **No chain touch.** The new sample table is operator-internal; the cryptographic chain (`compliance_bundles.signed_data` + Ed25519 + OTS) is untouched.
- **Auditor framing is honest.** §4 of v2: "Neither gate is the master BAA Article 3.2 cryptographic-attestation-chain claim (that's the Ed25519+OTS chain itself). This invariant is a Rule 1 helper-semantic-and-delegation-drift detector." This is exactly the framing the v1 P1-A1 finding asked for.
- **10% sample at 50-customer scale is statistically defensible.** OCR's interest is "do you have evidence that customer-facing values match the canonical computation?" A 10% sample with 15-min recency provides ~3-5 samples/customer/day per metric class — adequate for "regular monitoring, not point-in-time proof" auditor framing.
- **Pairs with static gate.** §3's compile-time-vs-runtime taxonomy is the kind of defense-in-depth narrative auditors find credible.

### Verdict: APPROVE.

---

## Lens 3 — Coach (no over-engineering, no double-build)

### P1-C2: `prometheus_metrics` already exposes some metrics — verify no overlap

`canonical_metrics.py:86` allowlists `prometheus_metrics.*` with `classification: operator_only`. The sampling decorator should NOT also decorate prometheus-emit paths — that would be double-sampling. Need explicit exclusion.

**Fix:** Phase 2b `sample_metric_response` decorator wraps customer-facing route handlers only. Operator-only paths (prometheus_metrics.*, /api/admin/*) bypass the sampler. Document this in §2.

### P1-C3: "Auto-pruned by partition_maintainer_loop" is a double-build claim that doesn't exist

Verified above (P1-E7) — the loop does not cover the new table. Saying "auto-pruned" implies free infrastructure. It is not. Either:
- Treat as a Phase 2a build item (extend the loop), OR
- Use a sibling background task explicitly (Coach's preference: don't extend a loop that has a different domain).

The coach lens prefers a sibling task because mixing `promoted_rule_events` partition management with operator-internal substrate-sample retention couples two unrelated concerns. Session 220 precedent: per-assertion isolation > centralized loops.

### Sampling-decorator pattern is sound

Soft-fail wrap (try/except + logger.warning skip) matches `sigauth-soft-verify` precedent (heartbeats with sig errors don't block heartbeat write). Pattern is correct.

The 10% sample rate is appropriate for a 50-customer scale where the goal is detection-over-time, not point-in-time-completeness. Lower rate (1-5%) would be appropriate at 1000+ customer scale; the cadence can be tuned downward later via `SAMPLE_RATE` constant.

### Verdict: APPROVE-WITH-FIXES — P1-C2 + P1-C3 are documentation/scope corrections, not redesign.

---

## Lens 4 — Attorney

Counsel Rule 1 runtime materialization: yes, Mechanism B does this.

- **Static AST gate (Phase 0+1 already shipped):** catches non-canonical-delegation at compile time.
- **Runtime invariant (this Phase 2):** catches non-canonical-value drift at sample time.

Together they cover Rule 1 ("no non-canonical metric leaves the building") — compile-time gate catches the "is this delegating?" question, runtime invariant catches the "did the delegated path produce the same value?" question.

**Not Article 3.2 territory.** Article 3.2 is the Ed25519+hash-chain+OTS substrate; this invariant is supplementary product-quality signal. v2 design §4 explicitly disclaims the legal load. No conflation.

**Runbook copy MUST scope the legal claim narrowly.** The runbook (`substrate_runbooks/canonical_compliance_score_drift.md`) is not yet drafted — gate at its own Gate A. Must NOT say "this proves chain integrity" or "this guarantees compliance correctness."

### Verdict: APPROVE — pending runbook Gate A on copy.

---

## Lens 5 — Product manager

### P1-P3: 0.1 tolerance + NOW()-shift = false-positive class

Per Lens 1 P1-E6: the helper's SQL uses `NOW() - INTERVAL '$N day'` so the window slides every second. A bundle within seconds of the window edge can be IN at T and OUT at T+δ. Score can drift by 0.1+ legitimately at the boundary.

Tolerance 0.1 (exactly 1 rounding step) is brittle. Tolerance 0.5 absorbs the NOW()-shift class without losing detection power (a real non-canonical computation will diverge by 5-20+ score points, not 0.5).

**Fix:** tolerance 0.5 in Phase 2c. Document the rationale in the runbook.

### P1-P4: Alert routing must be ops-only

Substrate invariants fire to `/admin/substrate-health` panel (operator-only). Confirmed in §1 + §6. Important to verify the Phase 2c implementation does NOT add this to any customer-facing alert pipeline.

**Fix:** Phase 2c reviews the sev2 routing to confirm operator-only.

### Retention + DB cost

§6 estimates ~4500 samples/30d. At ~200 bytes/row (JSONB overhead) = ~900KB total. Negligible. **Pass.**

### Verdict: APPROVE-WITH-FIXES.

---

## Lens 6 — Medical-technical

Operator-internal substrate signal. Never surfaces to clinic admins per `/admin/substrate-health` scoping. Correct posture per design §1 ("operator-internal"). No medical-side concerns.

### Verdict: APPROVE.

---

## Lens 7 — Legal-internal (Maya + Carol)

### Banned-word scan on `audit/canonical-metric-drift-invariant-design-2026-05-13.md` (v2):

Searched for: `ensures`, `guarantees`, `prevents`, `protects`, `100%`, `audit-ready`, `PHI never leaves`.

**Result: CLEAN.** Zero matches. Design language uses honest framing throughout ("detects", "surfaces", "indicates", "catches").

### Substrate runbook (`substrate_runbooks/canonical_compliance_score_drift.md`)

Not yet drafted. When written, must pass its OWN banned-word + Maya-Carol gate. Recommend the runbook ships in Phase 2c bundled with the invariant code commit so the gate runs together.

### `.format()` template risk

Design §2 line 152-157 has f-string interpolations inside `details["interpretation"]` and `details["remediation"]`. These are operator-internal (never reach customer-visible artifacts) — same scope as v1 P1-LM2 verdict. Safe today; would need `{{`/`}}` audit if ever exposed to customer artifact.

### Verdict: APPROVE.

---

## Specific cross-cutting verifications

### Sample-vs-canonical comparison semantics (the load-bearing question)

**The question:** when the sample is captured, the endpoint EITHER (a) called the canonical helper directly OR (b) went through one of the 7 `migrate`-allowlist non-canonical paths. The invariant fires drift only if (b) produced a value the canonical helper would NOT have produced for the same input.

**Path-a samples** (endpoint called helper directly): captured_value == helper_result.score by construction (modulo cache + NOW() shift). Invariant compares helper-output to helper-output — should always match.

**Path-b samples** (endpoint computed inline via `db_queries.get_compliance_scores_for_site` etc.): captured_value is the inline result, helper_result is the canonical recompute. If they differ > tolerance, fire drift. This is the load-bearing case.

The design's logic handles this correctly — but P1-E6 (cache) + P1-P3 (tolerance + NOW() shift) introduce noise. With the recommended fixes (bypass cache on recompute + tolerance 0.5), the comparison is semantically correct.

**Open:** can a path-b allowlist entry happen to produce the same value as the helper by coincidence? Yes — that's a false-negative the invariant can't catch. The static AST gate is the compile-time backstop. Defense-in-depth is the right framing.

### Time-window for comparison

When sample captured at T with input I and helper recomputed at T+δ with the same input I, data could change in δ (new bundles arrive, status flips). Per P1-E6, this manifests as NOW()-window-shift drift. Mitigated by tolerance 0.5 + small δ (≤15min per design §2 LIMIT 50).

**Acceptable** — the invariant is QA-grade, not security-grade. Some false-positives at the NOW() boundary are tolerable if alert routing is ops-only.

### `helper_input` field design — endpoints that derive input

Some endpoints compute `site_ids` from the authenticated principal (e.g. /api/client/dashboard derives from `current_org_id` → all sites for that org). The sample must capture the DERIVED `site_ids` list, not the principal. Design §2 captures `helper_input` AFTER derivation (i.e. the actual args passed to `compute_compliance_score`). Verified by reading §2's decorator-call shape.

**Pass** — the decorator is called WITH the derived input, so capture is correct by construction. Document this explicitly in Phase 2b helper docstring.

### Cross-task lockstep with Task #54 PHI-pre-merge gate

The sampling decorator captures `tenant_id` (UUID) + `endpoint_path` + `helper_input` (JSONB containing site_ids + window_days). NONE of these are PHI. `tenant_id` is a UUID, `site_ids` are opaque IDs, no PII/PHI fields.

Recommend the decorator declares its boundary explicitly:

```python
async def sample_metric_response(...):
    # phi_boundary: operator_internal — captures opaque IDs only,
    # never PHI fields, never customer-readable.
    ...
```

This pairs with Task #54's PHI-pre-merge gate ratchet. Not a blocker for Phase 2a (Task #54 not yet shipped); add the marker when Task #54 lands.

### Verdict on cross-cutting: design's logic is sound; surface the P0-E4 + P0-E5 fixes + P1-E6/E7 cache/partition issues in Phase 2a/2b.

---

## Recommended implementation order (3-phase)

### Phase 2a — mig 314 + partition wiring (gate: this verdict's P0s closed before code)

1. mig 314: `canonical_metric_samples` table + monthly partition + indexes.
2. Extend `partition_maintainer_loop` (or sibling task) to cover the new table — both create-forward + drop-older-than-30d semantics.
3. Source-grep complete enumeration of customer-facing `compliance_score` / `overall_score` emit-paths (≥10 not 6).
4. Class-B Gate A on the mig + partition wiring (small scope).

**P0s to address before Phase 2a code lands:**
- **P0-E4:** `helper_input` JSONB schema must include `include_incidents` field.
- **P0-E5:** Phase 2b decoration plan must enumerate ≥10 customer-facing surfaces (incl. F1 attestation letter facts) + classify each.

### Phase 2b — sampler module + endpoint decoration (gate: P0s closed)

1. `canonical_metrics_sampler.py` with `sample_metric_response(metric_class, tenant_id, captured_value, endpoint_path, helper_input)` — soft-fail.
2. Decorate the full enumerated set (≥10 paths) — customer-facing surfaces fire drift; operator-only surfaces sample but exclude from drift.
3. Phi-boundary marker per Task #54 carry-forward.
4. Class-B Gate A on sampler module + Gate B on AS-IMPLEMENTED decoration coverage.
5. **Wait 7 days for sample population** before Phase 2c.

### Phase 2c — substrate invariant + runbook (gate: design + runbook)

1. `_check_canonical_compliance_score_drift` in `assertions.py` with:
   - Cache-bypass kwarg on recompute (P1-E6).
   - Tolerance 0.5 instead of 0.1 (P1-P3).
   - Per-assertion timeout 5s (Session 220 admin_transaction).
   - LIMIT 50 + 15min window (design v2 §2 unchanged).
2. `_DISPLAY_METADATA` entry — sev2, operator-only routing.
3. `substrate_runbooks/canonical_compliance_score_drift.md` — own Class-B Gate A on banned-word + framing.
4. Class-B Gate A + Gate B on query shape + threshold + runbook copy.

### Phase 3 (unblocked once Phase 2 lands)

Drive-down allowlist's 7 `migrate` entries one PR at a time per `audit/canonical-source-registry-design-2026-05-13.md` v3.

---

## Open questions for user-gate

1. **`include_incidents` capture (P0-E4):** confirm extending `helper_input` JSONB shape adds `include_incidents: bool`. Approve before Phase 2a mig 314 lands.
2. **Full endpoint enumeration (P0-E5):** approve source-grep-driven inventory; expand Phase 2b scope from 6 → ≥10. Special handling for F1 letter (PDF, not JSON).
3. **Cache bypass on recompute (P1-E6):** approve adding `_skip_cache=True` kwarg to `compute_compliance_score`. Additive — preserves existing callers.
4. **Tolerance 0.5 vs 0.1 (P1-P3):** approve 0.5 to absorb NOW()-window-shift drift.
5. **Partition pruning (P1-E7):** confirm extending `partition_maintainer_loop` (or sibling) covers `canonical_metric_samples` retention.
6. **Phi-boundary marker (cross-task lockstep):** add `# phi_boundary: operator_internal` on `sample_metric_response` when Task #54 lands? (Not blocker; defer to Task #54 gate.)

---

## Final recommendation

**APPROVE-WITH-FIXES.** All 3 v1 P0s + rename bonus are closed. Mechanism B pivot is structurally sound and statistically defensible for Counsel Rule 1 runtime. Auditor framing is honest; legal language is clean; coach-anti-double-build posture is correct.

Two new P0s in Lens 1 (P0-E4 `include_incidents` missing from helper_input; P0-E5 endpoint enumeration ≥10 not 6) must be addressed in Phase 2a before mig 314 + sampler decoration land. P1s (cache bypass, tolerance 0.5, partition retention) tracked into Phase 2c.

### Top 3 NEW P0s

1. **P0-E4 (Steve):** `helper_input` JSONB capture missing `include_incidents` kwarg. `compute_compliance_score` actually accepts `(site_ids, *, include_incidents, window_days)` — `/api/client/sites/{id}/compliance-health` passes `include_incidents=True`. Recomputing without it produces a different score → false-positive drift on every per-site sample. Fix in Phase 2a schema + Phase 2b decorator + Phase 2c recompute.
2. **P0-E5 (Steve):** Design enumerates 6 endpoints; source-grep finds ≥10 customer-facing emit-paths across client_portal.py, client_attestation_letter.py, org_management.py, portal.py, routes.py. F1 attestation letter (PDF generator) is high-stakes and not in the list. Coverage gap = auditor-grade signal is incomplete. Fix in Phase 2b by full inventory + classification.
3. **P1-E6/P1-P3 (Steve/PM, combined):** 60s TTL cache + NOW()-window shift + tolerance 0.1 = false-positive class at scan boundary + false-negative class within cache window. Fix in Phase 2c by adding cache-bypass kwarg to helper + tolerance 0.5. Not Phase-2a-blocking but must land with the substrate invariant.

Resubmit redesign-deltas for Phase 2a Gate A round 2 once user-gate questions 1-5 are answered. Phase 2b + 2c each require their own Gate A + Gate B per two-gate lock-in (Session 219 extension).

— Class-B Gate A v2 fork, 2026-05-13
