# Class-B 7-lens Gate A v3 — `canonical_compliance_score_drift` (Mechanism B)

**Date:** 2026-05-13
**Reviewer:** fork-based 7-lens review (author cannot self-grade per Session 219 two-gate lock-in)
**Design under review:** `audit/canonical-metric-drift-invariant-design-2026-05-13.md` (v3)
**Phase:** Task #50 Phase 2 (Counsel Rule 1 runtime half)
**Prior gates:**
- v1 returned **BLOCK** (3 P0s, Mechanism C category errors)
- v2 returned **APPROVE-WITH-FIXES** (2 P0s + 2 P1s + 1 P2)
- v3 applies all 5 items + ships

---

## 250-word summary

v3 closes all five v2 items cleanly and shifts the design from "approve with conditions" to "approve as drafted." Empirical verification confirms each closure: §2 `helper_input` JSONB schema is now a 3-field shape `{site_ids, window_days, include_incidents}`, the §2.5 recompute reads all three (line 158) + passes them to the helper (line 166-170); §2 emit-table enumerates 13 paths with per-row classification including the previously-missed F1 attestation-letter PDF (`client_attestation_letter.py:225, :372`); §2.5 invariant SQL bumps tolerance to `> 0.5`; §5 Phase 2c adds the `_skip_cache=True` kwarg requirement with a paired pin (`test_compliance_score_skip_cache_arg.py`); §5 Phase 2d lands `canonical_metric_samples_pruner` as an independent daily task.

Empirical cache-bypass check: today's `compute_compliance_score` (line 157-163) does NOT accept `_skip_cache` and does NOT accept `**kwargs`, so passing it without first adding the parameter would TypeError at runtime. v3 §5 Phase 2c explicitly says "add `_skip_cache` kwarg" — closure is correct but the helper change is a precondition gate for the invariant code itself. The partial index `WHERE classification = 'customer-facing'` is sound; the invariant SQL at §2.5 does NOT yet filter classification in the WHERE clause — a new minor P0 (P0-E8) to add `AND classification = 'customer-facing'` for substrate-fire scope correctness, otherwise operator-internal samples will fire customer-facing drift alerts.

**Overall verdict: APPROVE-WITH-FIXES** — 1 new minor P0 (classification filter), 2 P1s. v2's 5 items: all closed.

---

## v2 P0/P1/P2 closure matrix

| # | v2 item | Lens | v3 status | Evidence (file:line) |
|---|---|---|---|---|
| **P0-E4** | `include_incidents` capture | Steve | **CLOSED** | `audit/canonical-metric-drift-invariant-design-2026-05-13.md:54` (schema comment "include_incidents MUST be captured"), `:158` (`include_incidents = helper_input.get("include_incidents", False)`), `:168` (passed to helper). |
| **P0-E5** | endpoint enumeration ≥10, not 6 | Steve | **CLOSED** | `audit/canonical-metric-drift-invariant-design-2026-05-13.md:111-123` (13-row table with file:line + classification; F1 attestation-letter PDF at row 1+2). Verified against grep — counts match. |
| **P1-E6** | cache-bypass kwarg | Steve | **CLOSED (forward-coupled)** | `audit/canonical-metric-drift-invariant-design-2026-05-13.md:169` (`_skip_cache=True`), `:241` (Phase 2c "add `_skip_cache` kwarg + pin via `test_compliance_score_skip_cache_arg.py`"). Helper change is a precondition — see Empirical check below. |
| **P1-P3** | tolerance 0.1 → 0.5 | Steve+PM | **CLOSED** | `audit/canonical-metric-drift-invariant-design-2026-05-13.md:179` (`abs(helper_score - r["captured_value"]) > 0.5`), `:176-178` (rationale comment). |
| **P2** | partition pruner | Steve | **CLOSED** | `audit/canonical-metric-drift-invariant-design-2026-05-13.md:243` (Phase 2d `canonical_metric_samples_pruner` daily task + pin `test_canonical_metric_samples_pruner_drops_old_partitions.py`). Sibling task per Coach preference, not extension of `partition_maintainer_loop`. |

**All 5 v2 items: CLOSED.** v3 ships as designed pending the new P0 below.

---

## Empirical `compliance_score.py` cache-bypass check

**Current state (`compliance_score.py:157-163`):**

```python
async def compute_compliance_score(
    conn,
    site_ids: List[str],
    *,
    include_incidents: bool = False,
    window_days: Optional[int] = DEFAULT_WINDOW_DAYS,
) -> ComplianceScore:
```

- **No `_skip_cache` kwarg today.** Calling `compute_compliance_score(..., _skip_cache=True)` raises `TypeError: unexpected keyword argument '_skip_cache'`.
- **No `**kwargs` catch-all.** Silent-swallow is NOT a risk — Python will reject the call hard.
- **Cache check location:** `compliance_score.py:218-225` — the bypass MUST be added BEFORE `_cache_key = _score_cache_key(...)` at line 222 (or as a guard on the `if _cached_result is not None: return _cached_result` block at line 224).
- **Cache write location:** `compliance_score.py:421-422` — when `_skip_cache=True`, the write should also be suppressed to avoid poisoning warm dashboard reads with a fresh-recompute value that the cached path would have hit differently. v3 §5 does NOT explicitly say "suppress cache write on `_skip_cache=True`" — recommend the pin asserts both bypass-read AND skip-write.

**Verdict:** v3 says explicitly that the helper needs the kwarg added in Phase 2c. The implementation order is correct — helper change ships in the SAME Phase 2c commit as the invariant. If they ship separately the invariant will hard-fail at first tick. **P0-E8** below tightens this.

---

## Per-lens verdict (v3)

### Lens 1 — Engineering (Steve)

**APPROVE-WITH-FIXES** — 1 new minor P0 + 2 P1s.

`helper_input` 3-field shape: confirmed at design line 54 + 158. Recompute passes all three to helper: line 166-170. Tolerance 0.5: confirmed at line 179.

Source-grep validation of the 13-emit table: spot-checked `client_portal.py:817` (`overall_score: canonical.overall_score`), `client_portal.py:1206` region (per-site compliance-health), `client_attestation_letter.py:225, :372` — all present. Table is accurate.

`_skip_cache` precondition: helper does not yet have the kwarg (verified at `compliance_score.py:157-163`). v3 §5 Phase 2c calls for adding it. If Phase 2c lands invariant code BEFORE helper change → runtime TypeError. Recommend the commit order pins helper-first or single-commit. **P1-E9** below.

Classification filter on invariant query: SQL at design line 140-150 does NOT filter `classification = 'customer-facing'`. All 13 sampled paths get checked. Operator-internal routes.py callsites (6 rows) would fire customer-facing-shape violations. **P0-E8** below.

### Lens 2 — Database (Maya)

**APPROVE.**

`classification TEXT NOT NULL` with no DEFAULT is safe — mig 314 creates the table fresh (no existing rows). Sampler at design line 79-105 currently OMITS classification from the INSERT (verified by reading the SQL at line 95-99 — only 5 fields: `metric_class, tenant_id, captured_value, endpoint_path, helper_input`). The 6th column (`classification`) is in the schema but NOT in the INSERT. **P0-E10** below — this is a hard NOT NULL violation at first sample insert.

Partial index `WHERE classification = 'customer-facing'` is sound — gives small-rowset index for the substrate-fire query without burning index space on operator-internal rows. Good Postgres pattern.

`canonical_metric_samples_pruner` pseudocode at §5 Phase 2d is realistic. `DROP TABLE canonical_metric_samples_YYYY_MM` is the standard monthly-partition retention pattern. Recommend the pruner runs `ALTER TABLE ... DETACH PARTITION` first then `DROP` — detach is fast + lockless; drop is immediate. Document as Phase 2d implementation note. **P2-E11.**

### Lens 3 — Security (Carol)

**APPROVE-WITH-FIXES.**

Classification enum is text-typed (`TEXT NOT NULL`), not constrained — a `CHECK (classification IN ('customer-facing', 'operator-internal', 'partner-internal'))` constraint would prevent typo-driven misclassification (e.g. `'customer_facing'` underscore-vs-hyphen). The partial index `WHERE classification = 'customer-facing'` would silently exclude typos. **P1-C12** below — add CHECK constraint.

Operator-internal-to-customer-fire leak: the substrate invariant SQL at design line 140-150 lacks the classification filter (P0-E8 above). Without it, an operator-internal sample whose endpoint produces 85.5 vs canonical 84.7 would fire a customer-facing-shape violation. Customer-internal alert routing is operator-only (per design §1), so this is not a customer-data leak — but it pollutes the substrate-fire signal with operator-only paths that aren't part of Rule 1's customer-facing scope.

Partner-internal classification (`org_management.py:1203`): the v3 design says these are "informational; operator-internal classification is informational + excluded from substrate fire." But the SQL doesn't enforce that. Add classification filter (P0-E8) to materialize.

### Lens 4 — Coach (no over-engineering)

**APPROVE.**

The 13-row classification table inline is correct — it's load-bearing for §2 + §2.5 (sampler decorator needs classification per path, invariant SQL needs to filter on it). Splitting into a companion doc would force two-file synchronization for downstream readers. Inline is right.

Phase ordering 2a → 2b → 2c → 2d: correct. 2c (invariant) before 2d (pruner) is fine — invariant runs against ≤15min window so retention is irrelevant at first-light; pruner can lag a few days without functional impact.

Cost estimate: v2 was ~3 eng-days; v3 adds classification column wiring (1 hour) + pruner daily task (3-4 hours) + `_skip_cache` kwarg + pin (2 hours) = ~1 eng-day delta. v3 total ~4 eng-days. Reasonable for the closure quality.

### Lens 5 — Auditor (OCR posture)

**APPROVE.**

§4 auditor-grade evidence story is unambiguous with v3's 13-path enumeration. An OCR investigator asking "which customer-facing surfaces are covered?" gets a row-level answer from §2's table — 5 customer-facing client_portal paths + 2 F1 attestation-letter paths + 1 legacy portal path = 8 customer-facing paths in coverage. Operator-internal + partner-internal classifications are explicitly out-of-scope for Rule 1 customer-facing drift fire, which is correct per Counsel Rule 1 narrow scope.

Statistical defensibility intact: 10% sample × 8 customer-facing surfaces × ~5 req/site/day = ~4 samples/customer/day per metric class. Adequate for "regular monitoring, not point-in-time proof" auditor framing.

The F1 PDF path: §2 row 1+2 captures both the `_compute_facts()` site (line 225) AND the Jinja2 render kwarg (line 372). This is correct — they're re-emits of the same value, and capturing both gives display-time-rendering-drift coverage (§3 row 3). Sampling at `_compute_facts()` catches helper-vs-non-canonical drift; sampling at the Jinja kwarg catches the rare case where the renderer transforms the value (rounding, NULL coercion). Two samples per F1 PDF generation = 2× weight in the drift signal for the highest-stakes artifact — appropriate.

Cryptographic-binding note: F1 PDFs are signed elsewhere in the pipeline (the value the PDF carries IS the value emitted at line 225 → line 372 → Jinja2 → PDF bytes → Ed25519). Sampling at line 225 measures the same value the cryptographic chain attests — no chain-integrity risk, no Article 3.2 conflation.

### Lens 6 — PM

**APPROVE.**

Phase 2a/2b/2c/2d ordering correct (see Coach lens). Phase 2d retention is async — invariant works without pruner from day 1. Cost ~4 eng-days is acceptable for a Rule 1 runtime-half closure.

Alert routing: ops-only confirmed at §1 + §6. v3 inherits v2's correct routing.

Sample-rate 10%: appropriate for 50-customer scale. Document `SAMPLE_RATE` as a tunable constant for downstream scale-up to 1000+ customers.

### Lens 7 — Attorney (in-house counsel)

**APPROVE.**

Counsel Rule 1 runtime parity: v3 establishes runtime parity with the static AST gate. Static catches non-canonical-delegation; runtime catches non-canonical-value drift. Together they materialize "no non-canonical metric leaves the building" for `compliance_score`.

Tolerance 0.5 is defensible as "rounding noise + NOW()-window boundary variability" — the rationale comment at design line 176-178 surfaces this explicitly. Auditor attack: "you accept up to 0.5 drift" — counter: "the 0.5 absorbs legitimate boundary-NOW-shift, not real non-canonical-path drift which is typically >1.0 score points; below 0.5 is statistical noise, not Rule 1 violation." This framing is defensible. A 0.0 or 0.1 tolerance would generate noise-driven false-positives that the operator team would learn to ignore — a worse auditor narrative than honest noise-floor framing.

Banned-word scan on v3 design draft: searched `ensures`, `guarantees`, `prevents`, `protects`, `100%`, `audit-ready`, `PHI never leaves`. **CLEAN** — zero matches. Language uses honest verbs ("detects", "catches", "fires", "samples", "verifies").

Article 3.2 disclaim still present at §4. No conflation. Runbook (`substrate_runbooks/canonical_compliance_score_drift.md`) not yet drafted — must pass its own Gate A on copy.

---

## NEW v3 cross-lens findings

### P0-E8 (Steve + Carol + Maya) — Substrate invariant SQL missing classification filter

Design line 140-150: the recompute query selects from `canonical_metric_samples` WHERE `metric_class = 'compliance_score'` AND `captured_at > NOW() - INTERVAL '15 minutes'` AND `captured_value IS NOT NULL`. There is NO filter on `classification = 'customer-facing'`.

Consequence: operator-internal samples (6 routes.py paths) and partner-internal samples (`org_management.py:1203`) get recomputed and fire customer-facing drift alerts. Per design's own §2 narrative, "only `customer-facing` ... fire drift."

**Fix:** add `AND classification = 'customer-facing'` to the WHERE clause at design line 146 (between the `metric_class` filter and the `captured_at` filter so it benefits from `idx_canonical_metric_samples_drift` partial index). One-line change.

### P0-E10 (Maya) — Sampler INSERT omits `classification` column

Design line 95-99: the sampler's INSERT specifies 5 columns (`metric_class, tenant_id, captured_value, endpoint_path, helper_input`) but the schema declares 6 (the 6th is `classification TEXT NOT NULL`). First insert → `NotNullViolationError`.

**Fix:** add `classification` to the INSERT column list + the sampler signature accepts a `classification: str` arg + each decoration site passes the correct value. The 13-emit table in §2 already provides the per-path classification — this is just plumbing it through the sampler API.

### P1-E9 (Steve) — Commit-order risk on `_skip_cache` precondition

v3 §5 Phase 2c says invariant code + `_skip_cache` kwarg ship "in the same commit." Without explicit commit-order pin, a future split (e.g. invariant in PR-A, helper kwarg in PR-B) would land the invariant against a helper that TypeErrors at first tick.

**Fix:** Phase 2c implementation note: "single PR atomically modifies `compliance_score.py` (adds `_skip_cache: bool = False` kwarg + bypass-read + skip-write branches) AND `assertions.py` (new `_check_canonical_compliance_score_drift` assertion). Both files in the same commit. Pin: `test_compliance_score_skip_cache_arg.py` checks for parameter existence AND tests both bypass-read AND skip-write paths."

### P1-C12 (Carol) — Add CHECK constraint on classification

Schema declares `classification TEXT NOT NULL` with no enum-style enforcement. A typo (`customer_facing` underscore vs hyphen) would silently miss the partial-index AND the substrate-invariant WHERE filter, becoming invisible operator-internal drift.

**Fix:** add `CHECK (classification IN ('customer-facing', 'operator-internal', 'partner-internal'))` to mig 314 column declaration. Five-character change.

### P2-E11 (Maya) — Pruner detach-before-drop pattern

Pseudocode at §5 Phase 2d says "DROP TABLE canonical_metric_samples_YYYY_MM." Standard Postgres-partition-retention pattern is `ALTER TABLE ... DETACH PARTITION CONCURRENTLY ... ; DROP TABLE ...` — detach is fast + lockless, drop is then immediate.

**Fix:** Phase 2d implementation note documents detach-before-drop. Not a blocker; quality-of-implementation.

---

## Banned-word + copy gates

- Design v3 banned-word scan: **CLEAN** (re-verified).
- Substrate runbook copy not yet drafted — must pass its own Gate A.
- F-string interpolations inside `details["interpretation"]` + `details["remediation"]` at design line 189-203 are operator-internal — safe per Session 218 `.format()` rule (operator-internal artifacts are out of scope of the customer-facing `.format()` ban).

---

## Final overall verdict

**APPROVE-WITH-FIXES.**

All 5 v2 items (2 P0s + 2 P1s + 1 P2) closed cleanly. v3's classification + 13-emit-table + pruner-as-sibling-task + `_skip_cache` + tolerance 0.5 + `include_incidents` capture all verify against the actual codebase.

3 new minor P0s + 2 P1s + 1 P2 surfaced — all are small fixes to v3 itself (none structural, none requiring re-design):
- **P0-E8** — add `AND classification = 'customer-facing'` to substrate query (one-line)
- **P0-E10** — sampler INSERT must include `classification` column (signature change + 13 decoration sites)
- **P1-E9** — pin commit-order: helper kwarg + invariant in single PR
- **P1-C12** — add CHECK constraint on classification (mig 314)
- **P2-E11** — pruner detach-before-drop pattern

**Phase 2a (mig 314) can proceed** once P0-E8 + P0-E10 + P1-C12 are applied to the design draft. Phase 2b/2c/2d gate on their own Gate A reviews per §5.

Phase 2c MUST ship the helper `_skip_cache` change + the invariant in the same PR (P1-E9).

Substrate runbook copy: separate Gate A when drafted.

---

## Citations

- Design v3: `/Users/dad/Documents/Msp_Flakes/audit/canonical-metric-drift-invariant-design-2026-05-13.md`
- v2 verdict: `/Users/dad/Documents/Msp_Flakes/audit/coach-canonical-compliance-score-drift-v2-gate-a-2026-05-13.md`
- Cache implementation: `mcp-server/central-command/backend/compliance_score.py:135-225, :421-422`
- F1 attestation-letter helper call: `mcp-server/central-command/backend/client_attestation_letter.py:209-214, :225, :372`
- Partition maintenance loop scope: `mcp-server/central-command/backend/background_tasks.py:1480-1533` (covers `promoted_rule_events` only)
- Substrate per-assertion isolation precedent: Session 220 commit `57960d4b` (`admin_transaction` per-assertion)
