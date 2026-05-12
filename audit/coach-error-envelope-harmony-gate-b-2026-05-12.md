# Gate B — Middleware error-envelope harmony (task #123, 2026-05-12)

**Verdict:** APPROVE-WITH-FIXES (P2-only follow-ups; no P0 / no P1 blockers)

Source-verified every claim in the Gate A packet against the uncommitted
diff. New CI gate runs 5/5 green; prior task #121 gate runs 4/4 green;
full-CI-parity sweep 237/237 green. Adversarial sweep surfaces three small
gate-coverage gaps worth filing as P2 follow-ups but none block this commit.

---

## Source verification — every claimed change present

### csrf.py:205-217 — envelope harmonized

Read `csrf.py:200-217`. Confirmed:

- Line 209-214 emits `return JSONResponse(status_code=403,
  content={"detail": "CSRF validation failed. Refresh the page and try
  again."})`. **Only one key (`detail`).** No `error`, no `status_code` in
  body. PASS.
- Comment block at `csrf.py:200-208` cites Session 220 task #123 +
  2026-05-12 + frontend-grep evidence (`utils/api.ts`, `portalFetch.ts`,
  `integrationsApi.ts` all read `.detail`; ZERO read `.error`) + sibling
  pattern `rate_limiter.py`. PASS.
- Prior task #121 comment block at `csrf.py:190-199` preserved (the
  return-not-raise rationale). PASS.

### tests/test_no_middleware_dispatch_raises_httpexception.py:192 — synthetic updated

Read line 191-195. Confirmed synthetic now uses
`content={"detail": "nope"}`. PASS.

### tests/test_middleware_error_envelope_harmony.py — new gate

Read the file end-to-end. Confirmed:

- 5 tests: `test_middleware_jsonresponse_uses_detail_envelope` (main),
  `test_synthetic_violation_caught` (positive control),
  `test_synthetic_safe_envelope_passes` (negative control),
  `test_synthetic_success_response_not_flagged` (success-response
  control), `test_synthetic_allowlist_marker_passes` (allowlist
  control). PASS.
- AST walks every `ClassDef` whose bases include `BaseHTTPMiddleware`,
  finds the `dispatch` method, inspects every `return JSONResponse(...)`,
  filters to `status_code >= 400` literal-int kwarg + `content=` is a
  Dict literal, asserts a `"detail"` key string-literal is present.
- Allowlist marker `# noqa: envelope-shape-allowed` on the
  JSONResponse opening line exempts.
- Scope: `_BACKEND` (this dir) + `_REPO/mcp-server` (covers
  central-command-backend's siblings if any). Excludes
  `venv/.venv/node_modules/__pycache__/tests`.

---

## Test execution — all green

### New harmony gate

```
$ python3 -m pytest tests/test_middleware_error_envelope_harmony.py -v --tb=short
collected 5 items

tests/test_middleware_error_envelope_harmony.py::test_middleware_jsonresponse_uses_detail_envelope PASSED [ 20%]
tests/test_middleware_error_envelope_harmony.py::test_synthetic_violation_caught PASSED [ 40%]
tests/test_middleware_error_envelope_harmony.py::test_synthetic_safe_envelope_passes PASSED [ 60%]
tests/test_middleware_error_envelope_harmony.py::test_synthetic_success_response_not_flagged PASSED [ 80%]
tests/test_middleware_error_envelope_harmony.py::test_synthetic_allowlist_marker_passes PASSED [100%]
======================== 5 passed, 8 warnings in 7.23s =========================
```

**5/5 PASS.** Including the real-codebase walk
(`test_middleware_jsonresponse_uses_detail_envelope`) which proves the
gate fires zero violations on the current tree post-fix.

### Prior task #121 gate (must still pass post-fixture-edit)

```
$ python3 -m pytest tests/test_no_middleware_dispatch_raises_httpexception.py -v --tb=short
collected 4 items

::test_no_middleware_dispatch_raises_httpexception PASSED [ 25%]
::test_synthetic_violation_caught PASSED [ 50%]
::test_synthetic_safe_pattern_passes PASSED [ 75%]
::test_synthetic_allowlist_marker_passes PASSED [100%]
======================== 4 passed, 8 warnings in 8.69s =========================
```

**4/4 PASS.** Synthetic fixture switch to `{"detail": "nope"}` did not
regress the no-raise gate.

### Full-CI-parity sweep (Coach mandatory per Session 220 lock-in)

```
$ bash /Users/dad/Documents/Msp_Flakes/.githooks/full-test-sweep.sh \
       /Users/dad/Documents/Msp_Flakes/mcp-server/central-command/backend
[…]
✓ 237 passed, 0 skipped (need backend deps)
```

**237 passed, 0 failed, 0 skipped, exit 0.** Both my independent
invocation AND the parallel author sweep returned identical tallies.
Coach lock-in satisfied.

---

## Adversarial sweep on the AS-IMPLEMENTED gate

### A1 — Variable-ref `content=` is silently skipped

```python
# tests/test_middleware_error_envelope_harmony.py:93
if not isinstance(content, ast.Dict):
    continue
```

Today's backend: zero middlewares build `content` as a variable then pass
it. Verified via grep — every `return JSONResponse` in
`backend/*.py` middlewares passes a Dict literal inline. So no current
bypass.

But the gate documents the skip with `# Skip if content is not a direct
literal Dict (can't inspect variable refs).` — that's an explicit
known-gap, not an unintentional miss. The right behavior for the
foreseeable corpus (4 middlewares) is to keep the skip and add a
companion **P2 follow-up**: emit a SOFT warning (collected list, not
assertion failure) when a variable-ref `content=` is encountered, so
future drift is at least visible in CI logs.

**Recommendation:** P2#1 in this verdict. Not a P0/P1.

### A2 — `JSONResponse as JR` alias would slip the gate

```python
# tests/test_middleware_error_envelope_harmony.py:71-79
if isinstance(func, ast.Name):
    if func.id != "JSONResponse":
        continue
elif isinstance(func, ast.Attribute):
    if func.attr != "JSONResponse":
        continue
```

Match is by literal name `JSONResponse`. `from starlette.responses
import JSONResponse as JR` → `func.id == "JR"` → silently skipped.

Repo-wide grep: zero such aliases today (all imports are the canonical
name). The sibling task #121 gate has the IDENTICAL match shape, also
matches by canonical name only. Pattern parity argues we don't introduce
asymmetric strictness in this commit.

**Recommendation:** P2#2 — file a CI test that fails the build if any
backend `.py` file does `from … import JSONResponse as <anything>`.
Cheap to write (single regex), structurally closes the alias-evasion
class. Not a P0 because no live alias exists.

### A3 — GET dispatchers returning 4xx flagged

If a middleware adds a `request.method == "GET"` branch that returns
`JSONResponse(status_code=403, content={"x": "y"})` it'd be flagged.
That's CORRECT behavior — even GET 4xx responses are read by frontends
via the same `.detail` parser. No remediation needed. ACCEPTABLE.

### A4 — Multi-line JSONResponse `# noqa` marker placement

`call.lineno` points to the opening line of the `JSONResponse(` call. If
a developer wraps the call across multiple lines AND puts the `# noqa:
envelope-shape-allowed` marker on the `content=` continuation line
(not the opening), the gate WILL still fire (marker not seen).

This is the SAFER failure mode — the gate doesn't accidentally exempt;
the developer gets a clear flag and learns to put the marker on the
opening line. Synthetic test `test_synthetic_allowlist_marker_passes`
pins the canonical placement (line 249: `return JSONResponse(  # noqa:
envelope-shape-allowed`). Documented behavior.

**Recommendation:** P2#3 — extend the gate docstring to state explicitly
"marker MUST be on the `JSONResponse(` opening line; continuation-line
placement will not exempt." Not a P0 — fail-safe direction.

---

## Real-codebase impact — zero violations besides the fixed callsite

The main gate `test_middleware_jsonresponse_uses_detail_envelope` walked
`backend/` + `mcp-server/` against the current tree (post-fix) and
returned ZERO violations (5/5 green). Confirmed by:

- Manual file enumeration of `BaseHTTPMiddleware` subclasses:
  `csrf.py:67 CSRFMiddleware`, `security_headers.py:16
  SecurityHeadersMiddleware`, `rate_limiter.py:133 RateLimitMiddleware`,
  `etag_middleware.py:15 ETagMiddleware`. **4 classes total.**
- Manual review of each `dispatch`:
  - `csrf.py:209` — now `{"detail": ...}` — passes.
  - `security_headers.py` — no JSONResponse emission; only header
    mutation on `call_next` response.
  - `rate_limiter.py:253/265/277` — all three already emit
    `{"detail": ..., "retry_after": ...}` — pass.
  - `etag_middleware.py:38/41` — only emits 200 or 304 `Response`
    (NOT `JSONResponse`, and status < 400) — out of scope.

Gate scope and current codebase fit cleanly. 0 false positives, 0 false
negatives on the present tree.

---

## Backward compat — repo-wide grep for OLD shape consumers

### Production code

```
$ grep -rn '"error":.*"status_code"' backend/ --include="*.py" \
    | grep -v test_ | grep -v worktrees | grep -v audit/
csrf.py:205     (a comment that REFERENCES the old shape, not emits it)
```

**Zero production emitters of `{"error", "status_code"}` remain.** csrf.py
is the only file with the string at all, and it's a comment explaining
what the prior shape was. PASS.

### Test code

```
$ grep -rn '"error"\|"status_code"' backend/tests/ --include="*.py" \
    | grep -iE "csrf|envelope"
test_middleware_error_envelope_harmony.py:148  (gate docstring)
test_middleware_error_envelope_harmony.py:186  (negative-control synthetic)
```

Two hits, both in the new gate file:
- Line 148: docstring referencing the prior shape as historical
  context — appropriate.
- Line 186: synthetic NEGATIVE control inside
  `test_synthetic_violation_caught` proving the gate FLAGS the orphan
  shape — appropriate.

No production test asserts on the old `{"error", "status_code"}` shape
from CSRF 403. `test_production_security.py:175/239` were grepped — both
test CSRF FLOW (cookie name, compare_digest) not BODY SHAPE. Carol-grade
PASS.

### Docs / runbooks

```
$ grep -rn 'CSRF validation' docs/ .agent/ scripts/ 2>/dev/null
docs/runbooks/BREACH_NOTIFICATION_RUNBOOK.md:62 — describes CSRF failure
  origin tracing (log line, not body shape)
```

Doc references log-line content, not response-body content. No impact.

### Frontend

```
$ grep -rn '\.error\|\.status_code' mcp-server/central-command/frontend/src/utils/
(no matches — same as Gate A claim)
```

Frontend already reads `.detail` exclusively. No consumer change needed.
CONFIRMED.

---

## Per-lens summaries

- **Steve:** Source verification on every Gate A claim PASS. New gate
  enforces the rule structurally. 237/237 full-sweep green. No prod
  emitters of old shape remain. **APPROVE-WITH-FIXES** (3 P2s on gate
  hardening).

- **Maya:** UX delivery confirmed (frontend reads `.detail`, gets the
  actionable "Refresh the page" copy). SIEM/log line unchanged
  (`csrf.py:185` warning still fires the same string). No regression in
  observability. **APPROVE.**

- **Carol:** Zero non-frontend / non-test consumers of the old shape.
  Production grep clean. The new gate prevents drift from re-introducing
  the orphan envelope on a 5th middleware. No new information channels.
  **APPROVE.**

- **Coach:** Gate B verified runtime (not just code-true): both the new
  harmony gate AND the prior task #121 gate AND the full pre-push
  sweep run green. Independent invocation matches author's parallel
  sweep (237/237). Sibling-test corpus updated (synthetic fixture at
  `test_no_middleware_dispatch_raises_httpexception.py:192` no longer
  teaches the wrong shape to future readers). Comment block at
  `csrf.py:200-208` documents the rationale + cites Gate A audit doc +
  cites sibling pattern. **APPROVE-WITH-FIXES.**

---

## P0 / P1 / P2 findings

**P0 — none.** Commit is mergeable.

**P1 — none.**

**P2** (file as follow-up tasks; do NOT block this commit)

1. **(gate coverage) Variable-ref `content=` skip is silent.** When the
   AST walker encounters `JSONResponse(content=my_payload)` with a
   variable instead of a Dict literal, it `continue`s. Today no
   middleware does this — but future drift could bypass the gate by
   building the dict in a helper. Soft warning (printed to pytest
   `-s` output, not assertion fail) would surface drift without
   blocking. ~5 LOC addition to `_extract_jsonresponse_violations`.

2. **(alias-import bypass) `from starlette.responses import JSONResponse
   as JR` would bypass the name-match.** Zero live aliases today.
   Companion CI gate (regex over `backend/*.py`) would refuse the
   alias-import shape. ~10 LOC test file.

3. **(documentation) Document allowlist marker placement.** Extend the
   `test_middleware_error_envelope_harmony.py` module docstring to
   state: "Marker `# noqa: envelope-shape-allowed` MUST be on the
   `JSONResponse(` opening line. Placement on a continuation line
   (e.g. `content=...` line of a multi-line call) will NOT exempt."
   Fail-safe direction, but worth documenting before the first dev
   stumbles on it.

---

## Recommendation

**APPROVE-WITH-FIXES** — proceed to commit. None of the P2s block.

Commit body must:
- Cite this Gate B doc
  (`audit/coach-error-envelope-harmony-gate-b-2026-05-12.md`).
- Cite the Gate A doc
  (`audit/coach-error-envelope-harmony-gate-a-2026-05-12.md`).
- Cite the full-sweep tally: **237 passed, 0 failed, 0 skipped**.
- Cite the new gate result: **5/5 PASS**.
- Cite the prior task #121 gate result: **4/4 PASS** (regression check
  post-synthetic-fixture-edit).
- File the 3 P2s as named TaskCreate follow-up items in the same
  commit so they're tracked (per Session 220 TWO-GATE rule: P1 from
  EITHER gate MUST be closed OR carried as named follow-ups in same
  commit; P2s do not strictly require this but the rule's intent is to
  not lose them).

**Post-deploy verification still required for task close-out per Gate A
P2#3 (Coach):** live curl against deployed VPS after CI rolls — POST
without CSRF token to a protected route, assert response body has
top-level `"detail"` key, NO `"error"` or `"status_code"` keys. Verify
`runtime_sha == disk_sha == deployed commit` (Session 215 deploy-
verification rule).
