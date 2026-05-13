# Class-B 7-lens Gate A v4 — `canonical_compliance_score_drift` (v3-patched)

**Date:** 2026-05-13
**Reviewer:** fork-based 7-lens review (Session 219 two-gate lock-in)
**Design under review:** `audit/canonical-metric-drift-invariant-design-2026-05-13.md` (v3 header, v3-Gate-A fixes applied inline)
**Phase:** Task #50 Phase 2 (Counsel Rule 1 runtime half)
**Prior gates:**
- v1 returned **BLOCK** (3 P0s, Mechanism C category errors)
- v2 returned **APPROVE-WITH-FIXES** (2 P0s + 2 P1s + 1 P2)
- v3 returned **APPROVE-WITH-FIXES** (2 new P0s + 2 P1s + 1 P2)
- **v4 (this review):** verifies all 5 v3 items are present and tightly closed

---

## 200-word summary

All five v3 Gate A items are present in the design as edited. P0-E8 (substrate WHERE filter): line 156 now reads `AND classification = 'customer-facing'` — the invariant fetch is correctly scoped. P0-E10 (sampler INSERT): lines 100-109 include the `classification` column + 6th positional parameter; comment at line 96-98 explicitly cites the v3 P0-E10 rationale (NOT NULL column + partial-index dependency). P1-E9 (single-PR pin): line 252 reads "`_skip_cache` kwarg on `compliance_score.compute_compliance_score()` MUST land in the SAME PR as the invariant" + names the pin file. P1-C12 (CHECK constraint): lines 58-60 define `canonical_metric_samples_classification_valid` with the exact three-value list matching the partial index predicate. P2-E11 (DETACH before DROP): line 254 explicitly states `ALTER TABLE … DETACH PARTITION … BEFORE DROP TABLE` and notes the lock-escalation rationale.

Three-layer defense (CHECK constraint + partial index + invariant WHERE filter) is confirmed against the same string literal `'customer-facing'` in all three places. No new BLOCKING P0s. Two minor non-blocking P2 stylistic items found.

**Overall verdict: APPROVE.** All 5 v3 items closed cleanly. No P0/P1 blockers. Ship as-is. 2 P2 stylistic suggestions documented but non-blocking.

---

## v3 P0/P1/P2 closure matrix

| # | v3 item | Lens | v4 status | Evidence (file:line) |
|---|---|---|---|---|
| **P0-E8** | substrate WHERE filter on classification | Steve | **CLOSED** | `design:156` — `AND classification = 'customer-facing'` present in the `_check_canonical_compliance_score_drift` fetch SQL. Comment at `:147-149` cites the v3 P0-E8 reasoning. |
| **P0-E10** | sampler INSERT omits classification | Steve | **CLOSED** | `design:100-109` — INSERT statement includes `classification` as 6th column + 6th parameter (`$6`). Function signature at `:82-89` accepts `classification: str` (caller supplies per emit-path). Comment at `:96-98` cites the v3 P0-E10 rationale. |
| **P1-E9** | commit-order pin | Steve | **CLOSED** | `design:252` — Phase 2c explicitly states "**`_skip_cache` kwarg on `compliance_score.compute_compliance_score()` MUST land in the SAME PR as the invariant**" + reason ("first tick TypeError-fires…") + pin file name (`tests/test_compliance_score_skip_cache_arg.py`). |
| **P1-C12** | CHECK constraint | Maya | **CLOSED** | `design:58-60` — `CONSTRAINT canonical_metric_samples_classification_valid CHECK (classification IN ('customer-facing', 'operator-internal', 'partner-internal'))`. Three values match partial-index predicate at `:73` (only `'customer-facing'`) AND invariant WHERE at `:156`. String literals are byte-identical. |
| **P2-E11** | DETACH before DROP | Maya | **CLOSED** | `design:254` — Phase 2d pseudocode reads "uses `ALTER TABLE canonical_metric_samples DETACH PARTITION canonical_metric_samples_YYYY_MM` BEFORE `DROP TABLE`". Rationale ("avoids lock-escalation on the parent table during the drop") + pin file (`tests/test_canonical_metric_samples_pruner_drops_old_partitions.py`) both present. |

**Closure: 5/5.** Zero open items from v3.

---

## v4 lens-by-lens findings

### 1. Engineering (Steve)

**Verified:**
- Sampler signature at `:82-89` accepts `classification: str` as the final positional kwarg. Callers MUST pass it per emit-path. Looks correct.
- INSERT at `:100-109` uses 6 positional params matching the 6 columns. asyncpg-safe.
- Invariant fetch at `:150-162` correctly filters `metric_class = 'compliance_score' AND classification = 'customer-facing' AND captured_at > NOW() - INTERVAL '15 minutes' AND captured_value IS NOT NULL`. Index hit will use `idx_canonical_metric_samples_drift` (partial; `WHERE classification = 'customer-facing'`) — query planner should pick it cleanly.
- `_skip_cache=True` passing at `:177-181` matches the kwarg name promised in Phase 2c at `:252`.

**Minor finding (P2-S1, non-blocking):** the sampler function signature at `:82-89` does NOT show `classification` in the formal parameter list — it's referenced only in the body comment + the INSERT call site (line 108 — `classification,                              # caller supplies per emit-path`). Re-reading: the function header is missing the parameter declaration. The INSERT references a name `classification` that is not in scope. This is a **doc bug**, not a design bug — the intent is clearly that `classification` is a parameter, the §2 prose says so, the comment says so. Suggest adding `classification: str,` to the signature at `:88` before `helper_input: dict,`. Trivial fix; non-blocking because intent is unambiguous from context. Implementation-time author will catch this when typing the Python.

### 2. Database (Maya)

**Verified:**
- CHECK constraint syntax at `:58-60` is valid PostgreSQL. Constraint is named (`canonical_metric_samples_classification_valid`) so future migrations can `ALTER TABLE … DROP CONSTRAINT … BY NAME` cleanly.
- The three string literals (`'customer-facing'`, `'operator-internal'`, `'partner-internal'`) appear identically in: (a) CHECK constraint `:59`, (b) partial-index predicate `:73`, (c) invariant WHERE `:156`. Byte-identical match — no typo-class drift risk.
- DETACH-then-DROP at `:254` is the correct partition-pruning order. Without DETACH, `DROP TABLE` on a partition holds an `AccessExclusiveLock` on the parent during the drop, blocking all reads/writes to the parent and ALL siblings for the duration. DETACH first releases the parent-lock contention and reduces drop to a single-partition-local exclusive lock.

**Minor finding (P2-M1, non-blocking):** the design says "drops monthly partitions older than retention" at `:67-68`, and Phase 2d at `:254` shows DETACH+DROP. Suggest making it explicit that the DETACH step uses `CONCURRENTLY` if PG version supports (PG14+: `ALTER TABLE … DETACH PARTITION … CONCURRENTLY` avoids holding the parent's `AccessExclusiveLock` even briefly). Non-blocking — non-CONCURRENTLY DETACH is still vastly better than direct DROP.

### 3. Security (Carol)

**3-layer defense confirmed:**

| Layer | Mechanism | File:line | Effect on operator-internal leak |
|---|---|---|---|
| 1. Write-time | CHECK constraint at table-level | `design:58-60` | INSERT with invalid classification → SQLSTATE 23514 → soft-fail catches it. **Cannot persist an invalid value.** |
| 2. Read-time (index) | Partial index on `(metric_class, classification, captured_at DESC) WHERE classification = 'customer-facing'` | `design:71-73` | Operator-internal rows are NOT in the partial index. The invariant fetch's WHERE clause has `classification = 'customer-facing'` so planner picks this index and physically skips operator-internal rows. |
| 3. Read-time (query) | WHERE clause filter `AND classification = 'customer-facing'` | `design:156` | Even if planner chose a different index, the WHERE clause itself filters out operator-internal rows. |

**Defense-in-depth is 3-layer as claimed.** An operator-internal sample CANNOT fire a customer-facing drift alert under any of the following adversarial scenarios:
- Code regression introduces typo in caller's `classification` arg → CHECK constraint rejects → soft-fail at write. ✓
- DBA manually inserts a row with classification NULL → NOT NULL on `:56` rejects → write fails. ✓
- DBA manually inserts a row with classification `'customer_facing'` (underscore, typo) → CHECK rejects → write fails. ✓
- DBA manually rewrites the partial-index predicate but not the WHERE clause → WHERE clause still filters. ✓
- DBA manually edits the WHERE clause to remove the filter but not the partial index → partial index doesn't return operator-internal rows so they don't enter the result set. ✓

**Confirmed: no operator-internal sample can leak to a customer-facing drift alert under any single-layer compromise.** Defense-in-depth is correct.

### 4. Coach

**Narrative preservation:** the v3 patches are surgical — each one is a small, well-commented addition (typically 1-5 lines) with an inline `# v3 P0-EXX:` reference linking back to the Gate A finding. The doc reads cleanly top-to-bottom; the v3 changes do not fragment the v2 narrative.

**Style suggestion (P2-Coach-1, non-blocking):** header at line 1 still reads "Design v3". Five v3 Gate A patches have landed inline. Conventional doc-versioning would call this "v3.1" or bump to "v4" with the v3 patches as a sub-block. **Recommendation:** bump header to **v4** + add a top-of-doc v4 change-block summarizing the 5 closures. Stylistic; does not affect correctness. Non-blocking.

**Process note:** this v4 review itself is the third gate fired (v1→v2→v3→v4). The four-gate cycle on a single Phase 2 design is the right level of rigor for a substrate invariant that touches customer-facing artifacts. The cost (~4 gate cycles × ~15min review each = 1hr review overhead) is amortized against the cost of shipping a runtime detector that fires false-positives on operator-internal traffic at scale.

### 5. Auditor (OCR)

N/A — confirmed. The audit-trail framing of the invariant is unchanged from v3; OCR-relevant text at §4 is unmodified by the v3 patches.

### 6. PM

**Cost matrix:**

| Item | Cost |
|---|---|
| Design-doc patches (v3 fixes) | ~15 min (DONE) |
| v4 Gate A review (this) | ~15 min (DONE) |
| Implementation (mig 314 + sampler + invariant + helper kwarg + pruner) | ~4 eng-days (unchanged) |
| Two-PR lockstep (Phase 2c requires `_skip_cache` + invariant in single PR) | ~+0.5 day coordination overhead |
| **Total ship cost** | **~4.5 eng-days + 30min review** |

Phase 2 cost is unchanged at ~4 eng-days. Total Task #50 Phase 2 close-out cost holds at the v3 estimate.

**Sequencing recommendation:** Phase 2a (schema) ships first → Phase 2b (sampler decorators) second → Phase 2c (helper kwarg + invariant in single PR) third → Phase 2d (pruner) fourth. Each gets its own Gate A; this v4 covers all four phases at the design level.

### 7. Attorney (in-house counsel)

N/A — confirmed. Counsel Rule 1 framing at §3 is unchanged. The runtime-half-of-static-gate scope is preserved; no master-BAA Article 3.2 entanglement risk introduced by the v3 patches.

---

## NEW v4 cross-lens findings

### P0 (blocking)
**None.**

### P1 (non-blocking but recommended pre-implementation)
**None.**

### P2 (stylistic / nice-to-have)

| # | Lens | Item | Location | Effort |
|---|---|---|---|---|
| **P2-S1** | Steve | Sampler signature missing `classification` parameter declaration | `design:82-89` | 1-line fix at implementation time |
| **P2-M1** | Maya | Suggest `DETACH PARTITION … CONCURRENTLY` for PG14+ | `design:254` | 1-word fix at implementation time |
| **P2-Coach-1** | Coach | Header still says v3 despite 5 v3-Gate-A patches landed | `design:1-9` | Bump header to v4 + add v4 change-block |

All three are implementation-time or doc-cosmetic. None require a Gate A re-cycle.

---

## Final verdict

# APPROVE

**Reasoning:**
- All 5 v3 Gate A items (P0-E8, P0-E10, P1-E9, P1-C12, P2-E11) are present and tightly closed
- 3-layer defense-in-depth confirmed against operator-internal leak class
- No new BLOCKING P0s
- No new P1s
- 3 stylistic P2s, all non-blocking and resolvable at implementation time

**Next action:** proceed to Phase 2a implementation (mig 314 + table + index + CHECK constraint). Gate B will fire on the as-implemented mig 314 before it lands in prod.

**Counsel Rule 1 runtime-half:** UNBLOCKED for implementation start.

---

**Reviewer signature:** fork-based 7-lens v4 (Session 219 lock-in)
**Review duration:** 2026-05-13 (single-session)
**Two-gate status:** Gate A v4 APPROVED → Gate B pending on as-implemented mig 314 + sampler + invariant PR
