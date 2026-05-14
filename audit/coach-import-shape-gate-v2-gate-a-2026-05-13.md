# Gate A v2 (Re-validation) — Import-Shape Regression Test (Task #72 FU-3)

**Topic:** AST CI gate for bare function-scope `from <local_module> import …` (adb7671a class closure)
**Gate:** A (pre-execution) — RE-VALIDATION of v1 verdict
**Date:** 2026-05-13
**Predecessor:** `audit/coach-import-shape-gate-gate-a-2026-05-13.md` (v1 — APPROVE-WITH-FIXES)
**Lenses:** Steve / (Maya N/A) / Carol / Coach / (OCR N/A) / PM / Counsel

---

## 200-word summary

Re-validation of v1 (APPROVE-WITH-FIXES) against the current tree. Three
empirical claims re-checked at HEAD:

1. **audit_report.py:213** — confirmed still **truly bare** (single
   `from baa_status import is_baa_on_file_verified` inside
   `verify_audit_readiness()` after `admin_transaction(pool)` enter,
   no try/except wrapping). Comment at L209-212 documents the gate's
   intent but the import shape is unchanged.
2. **assertions.py:404-410** — confirmed nested-Try false-positive
   (3-level: `.bg_heartbeat` → `dashboard_api.bg_heartbeat` → bare).
   Classifier MUST walk nested ancestor chains.
3. **sites.py:4515** — confirmed `dashboard_api.appliance_relocation`
   as primary sibling with bare in `except ImportError`. Classifier
   MUST accept `dashboard_api.X` as a valid sibling for top-token `X`.

Backend file count: **154 .py files** (matches v1's LOCAL_TOPS manifest
derivation). No drift since v1 was written.

**Verdict: ship-as-planned per v1.** Hard-fail on NEW, empty allowlist,
audit_report.py:213 fix in the **same commit** as the gate, the 2 P1
classifier refinements (nested-Try walk + `dashboard_api.X` sibling
acceptance) implemented before the gate is added to pre-push
SOURCE_LEVEL_TESTS. ~1h effort. Counsel Rule 3 structural close.

---

## Per-lens re-validation (brief)

### 1. Steve — APPROVE (v1 stands)

**Empirical re-check at HEAD:**

- `audit_report.py:213`: literal `from baa_status import is_baa_on_file_verified`
  with no enclosing `try:` — confirmed BARE. Inside `verify_audit_readiness()`
  function scope. This is the single P0 source-fix the gate must close
  in the same commit (allowlist stays empty).

- `assertions.py:404-410`: three-level fallback `try: .bg_heartbeat / except: try: dashboard_api.bg_heartbeat / except: bg_heartbeat`.
  The leaf bare on L410 is correctly guarded — classifier must walk
  ancestor `Try → ExceptHandler → Try → ExceptHandler` chain and
  detect the relative or `dashboard_api`-prefixed sibling at ANY
  level. v1's `_is_guarded()` ancestor-walk handles this. Confirmed.

- `sites.py:4514-4519`: outer `try: from dashboard_api.appliance_relocation import (...) / except ImportError: from appliance_relocation import (...)`.
  The bare in the except is guarded by the `dashboard_api.X` sibling
  in the try body. Classifier's `_try_body_has_sibling_import()` must
  accept BOTH `level >= 1` AND `dashboard_api.<top>` as valid
  fallback primaries. v1 already specifies this. Confirmed.

**AST traversal soundness:** v1's parents-map + `_enclosing_func()` +
ancestor-walk `_is_guarded()` correctly handles all three known
shapes. Manifest derivation from `os.listdir` is mechanical (zero
maintenance). Stdlib + thirdparty curated frozensets are stable
enough (small drift cost, caught at PR review).

**No new edge cases discovered** during re-validation. v1 traversal
sketch is production-ready.

---

### 2. Maya — N/A

No SQL surface. Skipping.

---

### 3. Carol — APPROVE (v1 stands)

Re-confirming Counsel Rule 3 framing: adb7671a outage silently disabled
D1 heartbeat-signature verification (an attestation-chain step) for
~3 weeks. Bare-import class is a structural risk to ANY future
attestation step that imports its verifier lazily. Empty-allowlist
hard-fail gate is the correct structural close.

No new security concerns introduced by re-validation. Dynamic imports
(`importlib.import_module`) remain out-of-scope — different class,
different failure mode.

---

### 4. Coach — APPROVE; ship-as-planned (v1 stands)

**Recommendation re-affirmed:**

- **Hard-fail on NEW** (not ratchet) — today's true-bare count is 1,
  ratchet-baseline-1 invites silent drift up.
- **Empty allowlist** — fix audit_report.py:213 in the same commit.
- **2 P1 classifier refinements** in v1 are mandatory before pre-push
  adoption (otherwise assertions.py + sites.py become false-positive
  blockers).

**Implementation order recommendation (NEW for v2):**

**SAME COMMIT — combined approach is correct.** Three steps in one
patch:

1. Write `tests/test_no_bare_local_function_scope_imports.py` with the
   2 classifier refinements (nested-Try walk + `dashboard_api.X`
   sibling acceptance). Empty allowlist literal.
2. Fix `audit_report.py:213` with the standard 3-level fallback shape
   (matches assertions.py:404-410 pattern):
   ```python
   try:
       from .baa_status import is_baa_on_file_verified
   except ImportError:
       try:
           from dashboard_api.baa_status import is_baa_on_file_verified  # pragma: no cover
       except ImportError:
           from baa_status import is_baa_on_file_verified  # type: ignore
   ```
3. Add the test to `.githooks/pre-push` SOURCE_LEVEL_TESTS (Session
   220 lock-in — every Gate B sweep must run it).

**Why same commit, not gate-then-fix:** the gate WITH empty allowlist
will fail at pre-push if audit_report.py isn't fixed first. Two-PR
approach forces a temporary allowlist entry, which violates the
"empty allowlist contract" design. Atomic commit = no transitional
state where allowlist has a TODO.

**Gate B reminder:** fork MUST run the full pre-push sweep (NOT
diff-only) per Session 220 lock-in. The sweep must include the new
test AND must verify
`PYTHONPATH=mcp-server/central-command python -c "from dashboard_api.audit_report import verify_audit_readiness"` succeeds (production package shape).

---

### 5. OCR — N/A

No customer-facing artifact.

---

### 6. PM — APPROVE (v1 stands)

**Effort estimate unchanged: ~1 hour total.**

- 25 min: AST test with 2 classifier refinements
- 15 min: audit_report.py:213 source fix (3-level fallback)
- 10 min: smoke test against 79 function-scope local imports (expect 0
  failures after fix + refinements)
- 10 min: pre-push integration

**Followups (carry as named TaskCreate items in commit body):**

- P2 #3: subprocess literal-shape test
  (`importlib.import_module("dashboard_api.audit_report")` under
  prod-shape PYTHONPATH) — broader class than bare-imports.
- P3 #5: pre-push SOURCE_LEVEL_TESTS integration (done in this commit
  per Coach above — close out same-commit).

Cost-benefit: trivial. Empty-allowlist hard-fail closes a Counsel
Rule 3-adjacent structural class for ~1h cost.

---

### 7. Counsel — APPROVE (v1 stands)

Rule 3 (no privileged action without attested chain) — re-confirmed.
The adb7671a class is structurally a "silent disablement of an
attestation step" — verifier code exists, imports resolve at
test/dev time, fails ImportError in prod package context, exception
swallowed somewhere upstream → attestation step inert. Empty-allowlist
gate closes the class.

Commit body MUST cite "Counsel Rule 3 structural close" per v1
recommendation.

---

## P0 / P1 / P2 findings (re-validated)

**P0:** None NEW. Existing P0: fix audit_report.py:213 in same commit
(v1 finding still stands).

**P1 — must ship in same commit (Gate A requirement):**

1. Classifier walks nested Try chains via ancestor-walk
   (assertions.py:404-410 shape) — v1 `_is_guarded()` already
   correct.
2. Classifier accepts `dashboard_api.X` as valid sibling fallback
   for `X` (sites.py:4514-4519 shape) — v1
   `_try_body_has_sibling_import()` already correct.

**P2 — Gate B carry-forward (named TaskCreate followups):**

3. Subprocess literal-shape test
   (`importlib.import_module("dashboard_api.audit_report")` etc.)
   under production PYTHONPATH — broader than bare-imports.

**P3 — same-commit observability:**

4. Pre-push SOURCE_LEVEL_TESTS integration (Session 220 lock-in).

---

## Final overall verdict: **APPROVE — ship per v1 plan**

**v1 verdict stands.** All three empirical claims re-confirmed at HEAD:

- audit_report.py:213 still bare (1 P0 source fix)
- assertions.py nested-Try shape still requires ancestor-walk
- sites.py:4514 `dashboard_api.X` sibling shape still requires
  classifier acceptance

**Implementation-order recommendation:** **SAME COMMIT**. Gate + source
fix + 2 classifier refinements + pre-push integration in one atomic
patch. Empty allowlist contract from day 1. No transitional
allowlist-entry-with-TODO state.

**Counsel Rule 3 structural close.** ~1h effort. Worth it.

**Gate B requirement** (re-affirmed): fork MUST run full pre-push
sweep (NOT diff-only); MUST verify production package-shape import
succeeds for the fixed audit_report.py. Diff-only review = automatic
BLOCK per Session 220 lock-in.
