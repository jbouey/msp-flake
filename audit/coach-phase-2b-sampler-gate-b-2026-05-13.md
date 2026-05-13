# Gate B — Phase 2b Sampler Integration (AS-SHIPPED)

**Task:** #63 — `sample_metric_response` + 1-of-13 integration at `/api/client/dashboard`
**Date:** 2026-05-13
**Gate:** B (pre-completion, AS-IMPLEMENTED review)
**Gate A reference:** `audit/coach-canonical-compliance-score-drift-v3-patched-gate-a-2026-05-13.md` (v4 APPROVE)
**Verdict:** **APPROVE**

---

## 200-word summary

The AS-SHIPPED Phase 2b sampler integration matches the Gate A v4 design with no
material divergence. `canonical_metrics_sampler.py` ships a 108-line module with
`SAMPLE_RATE=0.1`, `_TRACKED_METRIC_CLASSES=frozenset({"compliance_score"})`, and
`_VALID_CLASSIFICATIONS` matching mig 314's CHECK constraint
(`customer-facing|operator-internal|partner-internal`) byte-for-byte. The 7-param
signature is correct, the stochastic gate `random.random() >= SAMPLE_RATE` short-circuits
correctly, the INSERT uses asyncpg parameter binding with the required `$5::jsonb` cast,
and the soft-fail try/except logs at `warning` with `exc_info=True`. The
`client_portal.py:765-784` integration uses a lazy import (avoids cycle risk), wraps the
await call in defensive try/except (over-cautious but harmless — module already soft-fails),
correctly stringifies `org_id` as UUID, guards `None` on `captured_value`, sets
`endpoint_path` matching the router decorator, supplies `helper_input` matching the
`compute_compliance_score(conn, site_ids)` defaults verbatim, and pins
`classification="customer-facing"`. 9/9 unit tests pass in 0.13s. Full pre-push sweep
246/246 passed, 0 skipped. New test in pre-push allowlist at line 126. No P0 or P1
findings — ship.

---

## Per-lens verdict

### 1. Engineering (Steve) — APPROVE

Read `canonical_metrics_sampler.py` line by line. Every checked invariant holds:

| Invariant | Line | Status |
|---|---|---|
| `SAMPLE_RATE = 0.1` | 36 | PASS (matches design §2 + test pin) |
| `_TRACKED_METRIC_CLASSES = frozenset({"compliance_score"})` | 45 | PASS |
| `_VALID_CLASSIFICATIONS` matches mig 314 CHECK enum | 38-40 | PASS (byte-for-byte) |
| `sample_metric_response` 7 params (conn, metric_class, tenant_id, captured_value, endpoint_path, helper_input, classification) | 48-56 | PASS |
| Soft-fail try/except wraps `conn.execute` | 87-107 | PASS (logger.warning + exc_info=True) |
| INSERT has 6-tuple VALUES + classification col | 88-98 | PASS |
| `random.random() >= SAMPLE_RATE` gate | 85 | PASS (correct direction) |
| `$5::jsonb` asyncpg cast | 93 | PASS |
| `json.dumps(helper_input)` | 96 | PASS |
| Early-return on unknown metric_class | 77-78 | PASS |
| Early-return on invalid classification w/ warn log | 79-84 | PASS |

Integration at `client_portal.py:762-784`:

| Invariant | Line | Status |
|---|---|---|
| Lazy import `from .canonical_metrics_sampler import sample_metric_response` | 766 | PASS (avoids module-import cycle risk + lifespan smoke clean) |
| Outer try/except defensive wrap | 765, 783-784 | PASS (defense-in-depth; over-cautious but acceptable) |
| `tenant_id=str(org_id)` UUID stringified | 770 | PASS |
| `captured_value=float(...)` with `None` guard | 771-774 | PASS |
| `endpoint_path="/api/client/dashboard"` matches decorator | 775 + decorator line 726 | PASS |
| `helper_input={site_ids, window_days:30, include_incidents:False}` matches `compute_compliance_score(conn, site_ids)` defaults | 776-780 | PASS (verbatim with default args) |
| `classification="customer-facing"` | 781 | PASS |

Outer try/except is defensible: protects against the lazy import itself failing
(ImportError if module ever moves) and is consistent with the design's
"never block the customer-facing response" promise. Mild over-engineering, not a
defect.

### 2. Database (Maya) — APPROVE

- INSERT uses `$5::jsonb` cast — closes the asyncpg `IndeterminateDatatype`
  class documented in CLAUDE.md (jsonb_build_object rule).
- `json.dumps(helper_input)` serializes cleanly for `{site_ids: [], window_days: 30, include_incidents: False}`
  (verified via test_none_captured_value_still_inserts which exercises empty list).
- Partition coverage: `canonical_metric_samples_2026_05` exists (mig 314 line 37-39
  `FROM '2026-05-01' TO '2026-06-01'`). Today is 2026-05-13 → INSERT lands in
  the 2026_05 partition via `captured_at DEFAULT NOW()`. PASS.
- CHECK constraint `classification IN ('customer-facing', 'operator-internal', 'partner-internal')`
  matches `_VALID_CLASSIFICATIONS` frozenset literal-for-literal.

### 3. Security (Carol) — APPROVE

- `org_id` derived from `user["org_id"]` → `Depends(require_client_user)` → session-validated.
  Tenant cannot be spoofed.
- `classification="customer-facing"` is a hardcoded literal, not user-supplied. No
  injection surface.
- INSERT uses asyncpg parameter binding throughout — no SQL string concat. Verified
  every `$N` placeholder maps to a positional arg.
- `helper_input` is server-constructed (`site_ids` derived from RLS-filtered query,
  literals for window/incidents) — no user-controlled data.
- `tenant_id` is sourced from session, not request body — no IDOR vector.

### 4. Coach — APPROVE

- 1-of-13 walking-skeleton scope is honestly named in the design + the
  module docstring. Future integrations land under their own Gate A/B.
- Lazy import pattern is the right choice for a non-hot-path emit decorator.
- The outer try/except in the endpoint IS over-cautious given the module already
  soft-fails, but it's defense-in-depth and costs nothing. Acceptable.
- One mild observation: the integration uses `except Exception: pass` (no log).
  The class-rule (CLAUDE.md `no_silent_write_warnings`) bans `logger.warning` on
  DB write failures — but THIS pass is on the SAMPLER, not a customer-state write.
  The sampler module itself logs at warning. The outer pass is fine. The
  `test_no_silent_db_write_swallow.py` ratchet PASSED (verified).

### 5. Auditor (OCR) — APPROVE

The audit narrative is sound: ~10% of `/api/client/dashboard` calls will write a
row to `canonical_metric_samples` carrying `(metric_class='compliance_score',
tenant_id=<org-uuid>, captured_value=<the-score-the-customer-saw>,
endpoint_path='/api/client/dashboard', helper_input={...}, classification='customer-facing')`.
Phase 2c invariant will recompute via `compute_compliance_score(conn, site_ids,
window_days=30, include_incidents=False)` and compare. Auditor can SELECT against
the partition at runtime to verify capture cadence.

### 6. PM — APPROVE

Quartet shipped together: module + integration + 9 tests + allowlist entry.
Walking-skeleton honesty enables iterative Phase 2c without revisiting design.

### 7. Attorney — N/A confirmed

No customer-facing artifact change, no legal-language surface, no PHI flow added
(captured_value is a numeric score, helper_input is server-internal metadata).

---

## AS-IMPLEMENTED vs DESIGN deviation matrix

| Design element | Implementation | Deviation |
|---|---|---|
| `SAMPLE_RATE = 0.1` | Line 36 | NONE |
| Frozenset metric classes (compliance_score) | Line 45 | NONE |
| Frozenset classifications (3 values) | Lines 38-40 | NONE |
| 7-param signature | Lines 48-56 | NONE |
| Soft-fail try/except + warning log + exc_info=True | Lines 87-107 | NONE |
| INSERT 6-col + $5::jsonb | Lines 88-98 | NONE |
| Stochastic gate `random.random() >= SAMPLE_RATE` | Line 85 | NONE |
| 1-of-13 integration at `/api/client/dashboard` | client_portal.py:765-784 | NONE |
| `helper_input` captures site_ids + window_days + include_incidents | Lines 776-780 | NONE |
| Lazy import to avoid cycle risk | Line 766 | NONE |
| 9 unit tests | tests/test_canonical_metrics_sampler.py | NONE |
| Pre-push allowlist entry | .githooks/pre-push:126 | NONE |

**Zero material divergence from Gate A v4 design.**

---

## Adversarial probe results

| Probe | Result |
|---|---|
| 9/9 unit tests pass | PASS — `9 passed in 0.13s` |
| Sampler test in `.githooks/pre-push` allowlist | PASS — line 126 `tests/test_canonical_metrics_sampler.py` |
| Integration block doesn't break existing response | PASS — sample call is awaited but does not return into `score_result`; soft-fail short-circuit returns None |
| SQL uses parameter binding (no string concat) | PASS — 6 `$N` placeholders, no f-string interpolation |
| `helper_input` json.dumps cleanly for empty site_ids | PASS — `test_none_captured_value_still_inserts` exercises `{"site_ids": [], ...}` |
| Full pre-push sweep | **246 passed, 0 skipped** (`.githooks/full-test-sweep.sh`) |
| Silent-DB-write-swallow ratchet | PASS — 5 passed (8 unrelated syntax warnings about other files' raw strings) |
| Partition coverage 2026-05 | PASS — mig 314 line 37-39 includes 2026_05 partition |

---

## Findings

**P0:** none.
**P1:** none.
**P2 (style observation, not blocking):**
- The outer `except Exception: pass` at client_portal.py:783-784 could log at
  debug level for observability, but is functionally correct. The inner module
  already logs warnings on the hot path (the DB write); the outer wrap protects
  the cold path (import resolution). Leave as-is.

---

## Final verdict: APPROVE

All 7 lenses APPROVE. Zero material divergence from Gate A v4 design. Full
pre-push sweep clean (246/246, 0 skipped). 9/9 unit tests pass. Allowlist
entry confirmed. CHECK constraint matches frozenset byte-for-byte. Partition
exists for 2026-05.

**Cleared to commit. Cleared to mark Task #63 in_progress → ready-for-Phase-2c.**
