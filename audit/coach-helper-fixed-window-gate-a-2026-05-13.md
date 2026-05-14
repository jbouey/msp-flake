# Class-B 7-lens Gate A — Task #83 Phase A (compute_compliance_score fixed-window enhancement)

**Date:** 2026-05-13
**Author/scope:** Fork-based adversarial review (Steve / Maya / Carol / Coach / OCR / PM / Counsel)
**Subject:** Validate the signature + semantics of the `window_start` / `window_end` extension to `compute_compliance_score()` proposed as Phase A of the 11-callsite inline-Rule-1 drive-down plan (`audit/coach-9-inline-rule-1-violations-enumeration-gate-a-2026-05-13.md`).
**Pre-state:** Helper today exposes `(conn, site_ids, *, include_incidents, window_days, _skip_cache)` and implements `WHERE cb.checked_at > NOW() - ($2::int * INTERVAL '1 day')`. Cache key is `("compute_compliance_score", tuple(sorted(site_ids)), include_incidents, window_days)`. 4 of 11 Rule-1 callsites cannot migrate until fixed-window is supported.

---

## 250-word summary

**APPROVE-WITH-FIXES.** The proposed signature is right in shape but the brief's mutual-exclusion rule ("if both window_start AND window_end set, override window_days") is too permissive and the "mutual exclusion validation in helper body" is under-specified. Stricter spec: **if EITHER `window_start` OR `window_end` is set, the fixed-window path engages; passing `window_days` alongside (when not equal to the default) MUST raise `ValueError` — never silently ignore**. This matches Maya's prior Gate A recommendation and prevents the call-pattern-drift class.

**Critical implementation findings:**
1. **Cache-key extension is non-negotiable** — keying on `window_days` alone for a fixed-window call collapses different (start, end) pairs onto the same key. Must extend to `(window_days, window_start_iso, window_end_iso)`.
2. **Scoring-formula divergence at `client_quarterly_summary.py:345`** — inline uses `(passed + 0.5*warnings) / denom`; canonical uses `passed / total`. Phase B1 (F3 PDF) migration WILL change the customer-facing number. This is a Counsel-Rule-1 finding in its own right (F3 PDF historically shipped non-canonical math); flag to Counsel-queue Task #37 BEFORE Phase B1 ships.
3. **Partition pruning preserved** — `compliance_bundles` is month-partitioned on `checked_at` (mig 138). The fixed-window predicate `cb.checked_at >= $2 AND cb.checked_at < $3` is parameter-stable and prunes correctly. The relative-window path uses `NOW() - INTERVAL` which is also stable. No regression.
4. **TZ-aware datetimes mandatory** — `checked_at` is `timestamptz`. Helper MUST reject naive `datetime` inputs at the boundary; `_score_cache_key` must serialize via `.isoformat()` (UTC-anchored).

**Final verdict: APPROVE Phase A** with the 4 fix-its above incorporated before code lands.

---

## Per-lens verdicts

### Steve (Engineering) — APPROVE WITH FIXES

Read `compliance_score.py:141-285`. The current single-statement implementation has TWO query branches (`window_days is None` all-time vs. bounded). Adding fixed-window requires a **third** branch OR a unified query-build helper. Recommendation: collapse to a single query with a `WHERE` clause built dynamically:

```python
predicate, params = _build_window_predicate(window_days, window_start, window_end)
# predicate is one of:
#   "" (all-time, no constraint)
#   "AND cb.checked_at > NOW() - ($N::int * INTERVAL '1 day')"
#   "AND cb.checked_at >= $N AND cb.checked_at < $N+1"
```

This avoids a third copy of the CTE. PgBouncer statement-cache benefits — 3 stable query shapes instead of N.

**Steve P0 — argument-validation logic:**
```python
fixed_window_set = window_start is not None or window_end is not None
relative_window_explicit = (
    window_days is not None and window_days != DEFAULT_WINDOW_DAYS
)
if fixed_window_set and relative_window_explicit:
    raise ValueError(
        "compute_compliance_score: pass EITHER window_days OR "
        "(window_start, window_end), not both."
    )
if fixed_window_set:
    if window_start is None or window_end is None:
        raise ValueError(
            "compute_compliance_score: window_start AND window_end must "
            "both be set together; got start=%r end=%r" % (
                window_start, window_end
            )
        )
    if window_start >= window_end:
        raise ValueError(
            "compute_compliance_score: window_start must be < window_end"
        )
    if window_start.tzinfo is None or window_end.tzinfo is None:
        raise ValueError(
            "compute_compliance_score: window_start/end MUST be tz-aware "
            "(UTC). compliance_bundles.checked_at is timestamptz."
        )
```

This is stricter than the brief's "if both set, override" rule — but matches Maya's prior Gate A recommendation and closes the silent-ignore footgun.

**Steve P1 — only-start / only-end mode:** brief Maya wrote "if only `window_start`, end = NOW. If only `window_end`, start = window_end - {window_days} days." This is convenient BUT a footgun for the `routes.py:3216` split-window-24h-ending-7d-ago case which is exactly this shape. Two paths:
- (a) support it (matches a real callsite), but document precedence loudly.
- (b) require both (cleaner, forces routes.py:3216 to compute its start explicitly).

**Steve recommends (b)** — explicit beats implicit, and the call site is only 2 lines longer. Reject single-bound mode in Phase A; add later if real callsites materialize.

**Steve P1 — return-shape `window_description` extension:** today's helper sets `f"Latest result per (...), last {window_days} days"`. For fixed-window, suggest `f"Latest result per (...), {window_start.date()} to {window_end.date()}"`. Auditor-PDF feed consumers (Phase B targets) will surface this string in evidence packets.

---

### Maya (Database) — APPROVE WITH FIXES

**Maya P0 — cache-key extension:**

Current `_score_cache_key` (line 141-154):
```python
return (
    "compute_compliance_score",
    tuple(sorted(site_ids)),
    bool(include_incidents),
    window_days,
)
```

Required extension:
```python
def _score_cache_key(
    site_ids, include_incidents, window_days, window_start, window_end,
) -> tuple:
    return (
        "compute_compliance_score",
        tuple(sorted(site_ids)),
        bool(include_incidents),
        window_days,
        window_start.isoformat() if window_start else None,
        window_end.isoformat() if window_end else None,
    )
```

**Maya P0 — partition pruning verified:** `compliance_bundles` partitioned monthly on `checked_at` (mig 138). The query `WHERE cb.site_id = ANY($1) AND cb.checked_at >= $2 AND cb.checked_at < $3` with `$2/$3` as `timestamptz` parameters DOES prune at plan time per pg14+ partition-pruning rules (constant-folded). `EXPLAIN (ANALYZE)` should show only the relevant monthly partitions scanned. Will verify in Phase A Gate B with EXPLAIN output cited.

**Maya P0 — cache TTL ladder (deferred):** prior Gate A suggested fixed-window calls cache LONGER (1h+) because the result is deterministic on a bounded historical range. Recommend deferring this to a Phase A-2 micro-task (`_FIXED_WINDOW_CACHE_TTL = 3600`) — not blocking Phase A core. Initial cut: same 60s TTL across both paths. Followup task to TaskCreate.

**Maya P1 — `_should_cache_score` extension:** today gates on `window_days is not None` to bypass auditor-export. With fixed-window, the bypass rule should be "bypass only if window_days IS None AND no fixed-window set" — i.e. only the true all-time path bypasses. Fixed-window paths ARE bounded → SHOULD cache.

```python
def _should_cache_score(window_days, window_start, window_end):
    if window_start is not None and window_end is not None:
        return True
    return window_days is not None
```

**Maya P1 — DB time vs Python time precision:** `checked_at` is microsecond-precision `timestamptz`. Python `datetime` is microsecond-precision UTC. No precision-loss class. But: cache-key uses `.isoformat()` which truncates non-microsecond fields — confirm `datetime.isoformat()` round-trips bit-stable on `timestamptz` reads. (Spot-check: it does in CPython 3.11+.)

---

### Carol (Security) — APPROVE / N/A

Per brief Carol is N/A. **One Carol note:** the helper's RLS contract ("caller must have set app.current_org or app.current_tenant") is preserved — fixed-window adds NO new RLS surface, just changes the `WHERE` filter on the bundle scan. Cache key continues to include `tuple(sorted(site_ids))` which preserves tenant isolation at cache layer. No new attack surface.

---

### Coach (Process/Quality) — APPROVE WITH FIXES

**Coach P0 — backwards-compat verification protocol:**

Every existing caller of `compute_compliance_score()` passes ZERO new kwargs:
```bash
grep -rn "compute_compliance_score(" mcp-server/central-command/backend/ --include="*.py"
```

ALL callsites today pass `(conn, site_ids)` or `(conn, site_ids, include_incidents=True)` or `(conn, site_ids, window_days=N)`. None pass `window_start` or `window_end`. The new kwargs default `None` → fall through to the existing `window_days` branch → byte-identical behavior. ✅

Phase A Gate B MUST cite this grep + run the test suite that exercises every existing caller. Mandatory in the verdict per Session 220 lock-in.

**Coach P0 — substrate-invariant `_skip_cache` path interaction:**

Today `_skip_cache=True` is set by the canonical-metric-drift substrate invariant (Task #64). Fixed-window callers should NEVER pass `_skip_cache=True` — it's an internal-only kwarg. Verify the invariant's recompute path doesn't accidentally interact with fixed-window by sampling a fixed-window endpoint. **Phase A Gate B must include a test that asserts the substrate invariant skips fixed-window samples** OR explicitly handles them.

**Coach P0 — scoring-formula divergence at client_quarterly_summary.py:345 (DEFERRED finding):**

Read line 397: `sc = (passed + 0.5 * warnings) * 100.0 / float(denom)`. The canonical helper uses `total_passed / total * 100` — **warnings are NOT half-pass**. This is a SEMANTIC DIFFERENCE that will change the customer-facing F3 PDF compliance number on Phase B1 migration.

This is NOT a Phase A blocker (Phase A is helper-only) BUT it IS a Phase B1 P0. Two paths:
- (a) Phase B1 ships the canonical math and customer sees a different (lower) number in next quarterly. Flag explicitly to Counsel before Phase B1 ships.
- (b) Add a `warnings_as_half_pass: bool = False` flag to the canonical helper — but this re-introduces dual-shape footgun.

**Coach recommends (a)** with Counsel sign-off. The "half-credit for warnings" math has no canonical authority — it appears to be a one-off divergence. Carry as Task #83-B1 explicit P0 in the Phase B1 Gate A.

**Coach P1 — naming bikeshed:** `window_start` / `window_end` matches Python `datetime` conventions and is unambiguous. ✅ no change.

---

### OCR (Counsel Rule 1 enforcement) — APPROVE

Phase A is the prerequisite work. No new Rule-1 violations introduced; existing helper continues to be the single canonical source. The 4 P0 customer-facing/auditor-PDF callsites become migratable upon Phase A ship.

**OCR finding (escalates to Phase B1 Gate A):** the scoring-formula divergence at `client_quarterly_summary.py:345` means the F3 PDF today produces a DIFFERENT number than the canonical helper. Counsel Rule 1 is "no non-canonical metric leaves the building" — F3 PDF is THE outside-facing artifact. **Flag to Counsel-queue Task #37** as a sub-finding: "F3 PDF currently uses half-credit-for-warnings scoring which is not the canonical formula; Phase B1 migration will change the number customers see."

---

### PM — Effort estimate

| Item | Effort |
|---|---|
| Helper signature extension + validation logic | ~30min |
| Query-builder refactor (3 shape → 1 dynamic predicate) | ~45min |
| Cache-key extension + `_should_cache_score` update | ~20min |
| Unit tests: 5 new (validation, fixed-window basic, fixed-window vs relative parity, cache-key isolation, tz-naive rejection) | ~60min |
| EXPLAIN-output verification on prod-mirror | ~30min |
| Gate B sweep + curl-test 3 existing customer-facing endpoints | ~45min |
| **Total** | **~3.5h** |

PM-NOTE: brief estimated 45min for helper enhancement. Actual is ~3.5h with validation + tests + EXPLAIN verification. Adjust Phase A schedule accordingly.

---

### Counsel (Rule 1 forward-path) — APPROVE

Phase A unblocks Phase B which closes the 4 customer-facing/auditor-PDF P0s. Strongly endorse the 3-phase batched plan. The scoring-formula divergence finding (Coach P0) needs to surface in Phase B1's Gate A as an explicit Counsel-review item before the F3 PDF number changes.

---

## Validated final signature

```python
async def compute_compliance_score(
    conn,
    site_ids: List[str],
    *,
    include_incidents: bool = False,
    window_days: Optional[int] = DEFAULT_WINDOW_DAYS,
    window_start: Optional[datetime] = None,   # NEW — tz-aware UTC
    window_end: Optional[datetime] = None,     # NEW — tz-aware UTC
    _skip_cache: bool = False,
) -> ComplianceScore:
```

**Validation logic (in helper body, BEFORE cache lookup):**
1. If `window_start XOR window_end` → `ValueError` (must be paired).
2. If `window_start >= window_end` → `ValueError`.
3. If `window_start.tzinfo is None or window_end.tzinfo is None` → `ValueError`.
4. If both fixed-window set AND `window_days != DEFAULT_WINDOW_DAYS` (i.e. caller explicitly passed non-default `window_days`) → `ValueError`.
5. Otherwise: fixed-window path engages.

**Cache-key extension:**
```python
def _score_cache_key(site_ids, include_incidents, window_days, window_start, window_end):
    return (
        "compute_compliance_score",
        tuple(sorted(site_ids)),
        bool(include_incidents),
        window_days,
        window_start.isoformat() if window_start else None,
        window_end.isoformat() if window_end else None,
    )
```

**Cache-eligibility:**
```python
def _should_cache_score(window_days, window_start, window_end):
    if window_start is not None and window_end is not None:
        return True
    return window_days is not None
```

**WHERE-clause shape (single dynamic predicate):**
```sql
-- if fixed-window:
WHERE cb.site_id = ANY($1) AND cb.checked_at >= $2 AND cb.checked_at < $3
-- if relative-window:
WHERE cb.site_id = ANY($1) AND cb.checked_at > NOW() - ($2::int * INTERVAL '1 day')
-- if all-time (window_days=None, no fixed-window):
WHERE cb.site_id = ANY($1)
```

---

## Callsite-mapping (which kwargs each Phase B/C callsite passes)

| # | File:Line | New call shape |
|---|---|---|
| 1 | routes.py:3203 | `compute_compliance_score(conn, sids, window_days=1)` |
| 2 | routes.py:3216 (split-window 24h-ending-7d-ago) | `compute_compliance_score(conn, sids, window_start=now-7d-24h, window_end=now-7d)` |
| 3 | routes.py:3337 | `compute_compliance_score(conn, sids, window_days=1)` |
| 4 | routes.py:3729 | `compute_compliance_score(conn, sids, window_days=1)` |
| 5 | routes.py:4875 (via get_all_compliance_scores migration) | helper-level — closes 4 callers in one shot |
| 6 | routes.py:5732 (30d trend) | `compute_compliance_score(conn, sids, window_days=30)` |
| 7 | routes.py:5767 | `compute_compliance_score(conn, sids, window_days=30)` |
| 8 | routes.py:7867 (admin monthly-report) | `compute_compliance_score(conn, sids, window_start=month_first, window_end=next_month_first, include_incidents=False)` |
| 9 | routes.py:8678 (admin compliance-packet) | `compute_compliance_score(conn, sids, window_days=30)` |
| 10 | org_management.py:1118 (org packet, fixed-month) | `compute_compliance_score(conn, sids, window_start=month_first, window_end=next_month_first)` |
| 11 | client_quarterly_summary.py:345 (F3 PDF, fixed-quarter) | `compute_compliance_score(conn, [site_id], window_start=q_start, window_end=q_end)` |

**Coach P0 (re-emphasized):** Callsite 11 today has half-credit-for-warnings scoring. Migration WILL change the number on the F3 PDF. Phase B1 must address this with Counsel sign-off.

---

## Top P0/P1 findings

**P0-1 (Steve):** Argument-validation logic in brief is under-specified. Use the strict-mode rule above — XOR rejection + tz-naive rejection + start<end check + explicit-non-default-window_days rejection.

**P0-2 (Maya):** Cache key MUST extend to include `(window_start.isoformat(), window_end.isoformat())`. Without this, fixed-window calls with different ranges collapse onto the same cache entry → tenant-isolation-correct but RANGE-incorrect cache hits.

**P0-3 (Coach):** Scoring-formula divergence at `client_quarterly_summary.py:345` (`passed + 0.5 * warnings` vs canonical `passed / total`). Phase B1 migration will change the customer-facing F3 PDF number. NOT a Phase A blocker but MUST surface as explicit P0 in Phase B1 Gate A + Counsel-queue Task #37.

**P0-4 (Maya):** Verify partition-pruning preserved via `EXPLAIN (ANALYZE)` against prod-mirror with a fixed-window query. Cite in Phase A Gate B.

**P1-1 (Steve):** Reject only-one-bound mode (window_start set, window_end unset). Force callers to be explicit. Cleaner than the brief's "fill in NOW() or window_days" implicit-completion rule.

**P1-2 (Maya):** Defer cache-TTL ladder (1h+ for fixed-window) to a follow-up micro-task. Initial cut: same 60s TTL across paths.

**P1-3 (Coach):** Phase A Gate B MUST cite (a) grep showing no existing caller passes new kwargs (backwards-compat); (b) EXPLAIN output showing partition pruning; (c) full pre-push sweep pass count.

**P2-1 (Coach):** Add a `warnings_as_half_pass` flag to canonical helper IF future Counsel review insists F3 PDF keep historical math — but this re-introduces dual-shape and is Coach-DISCOURAGED. Default: migrate F3 to canonical math.

---

## Final overall verdict

**APPROVE-WITH-FIXES — proceed to Phase A implementation.**

Phase A is mechanically the right size (~3.5h, single commit, helper-only). The proposed signature is correct in shape. The 4 fix-its (strict validation, cache-key extension, partition-pruning verification, tz-aware rejection) MUST land in the same commit as the signature extension — they are not follow-ups, they are the spec.

The scoring-formula divergence at callsite 11 is the most important finding from this Gate A — it is a Phase B1 P0 NOT a Phase A blocker, but it MUST be surfaced now so Phase B1's Gate A and Counsel-queue Task #37 know to address it. Without that flagging, Phase B1 ships with a number change customers will notice without warning.

**Phase A Gate B requirements (Session 220 lock-in):**
1. Full source-level pre-push test sweep — pass count cited.
2. Grep showing no existing caller passes new kwargs (backwards-compat empirical).
3. EXPLAIN (ANALYZE) output of fixed-window query showing partition pruning.
4. New unit tests (5 minimum: validation matrix, fixed-window correctness, cache-key isolation, tz-naive rejection, no-cache-collision across different ranges).
5. Curl-test against existing customer-facing endpoints (no behavioral regression on `window_days` path).

**Sequencing:** Phase A → Phase B1 (with explicit half-credit-for-warnings Counsel review) + Phase B2 + Phase B3 (parallel) → Phase C. As planned in the prior Gate A.
