# Gate B verdict — CSRF 403 unwrap fix (2026-05-11)

**Verdict:** APPROVE-WITH-FIXES (1 P1 advisory, non-blocking — see Maya finding)

## Gate A v1 directive compliance

- **P1-1 import path:** ✓ — `csrf.py:20` reads `from starlette.responses import Response, JSONResponse`. Parity with rate_limiter sibling (which also imports `JSONResponse` from `starlette.responses`).
- **P1-2 envelope shape:** ✓ as-directed — `csrf.py:207-210` returns `content={"error": "CSRF validation failed. Refresh the page and try again.", "status_code": 403}`. Matches Gate A directive verbatim. **Tension noted under Maya findings.**
- **P1-3 logger preserved:** ✓ — `logger.warning(...)` at lines 184-189 unchanged. Fires BEFORE the `return JSONResponse(...)` at 205, so audit-trail ordering preserved.
- **P2 CI gate:** ✓ — `test_no_middleware_dispatch_raises_httpexception.py` exists, 4 tests as advertised (main detector + positive control + negative control + allowlist marker control). AST walker correctly identifies `BaseHTTPMiddleware` direct + qualified bases via `_is_basehttp_middleware`. Allowlist marker check is same-line via `src_lines[lineno - 1]` indexing — correct.

## Full sweep result (MANDATORY)

`grep ... pre-push | xargs python3 -m pytest`:
**582 passed, 0 failed, 18 skipped** (86.89s, 55 warnings). Matches author claim exactly.

## Adversarial findings (NEW)

**Steve — passes:** No remaining `raise HTTPException` in `csrf.py:dispatch` (only a doc-comment reference at line 191). AST walker base-class detection is sound for current repo state. Allowlist marker lineno indexing is correct (1-based → 0-based via `lineno - 1`).

**Steve — accepted limitation:** Walker checks DIRECT base names only — intermediate-mixin chains (`class A(BaseHTTPMiddleware): pass; class B(A): ...`) would be missed. Acceptable per Gate A Carol analysis; repo has no current intermediate-mixin pattern (grep confirmed). If introduced later, add resolver pass.

**Maya — P1 advisory (NEW, non-blocking):** Envelope shape inconsistency across sibling middlewares. `rate_limiter.py:253/265/277` uses `{"detail": "...", "retry_after": N}` (FastAPI-default `detail` key). `csrf.py:207` now uses `{"error": "...", "status_code": 403}`. Gate A v1 directed the `{"error"}` shape on grounds it matches a "project-wide global error envelope" — but `grep` finds ZERO other callsites of that shape in the backend. Two readings:
  (a) Gate A v1 is correct that frontend parsers read `.error` — implementation is right, rate_limiter is the outlier and should be migrated.
  (b) Gate A v1 was wrong about the global shape — csrf.py should use `{"detail"}` for sibling parity.
  Recommend: open followup task to audit frontend error-parser code (`utils/api.ts`?) and harmonize one direction. **Does NOT block this commit** — current shape matches Gate A directive verbatim, and worst case the frontend gracefully shows generic "CSRF rejected" copy. Filing as P1 followup.

**Carol — passes:** Information disclosure delta acceptable (still rejects, still logs, no PHI/PII in error body). The CI gate closes the regression class structurally.

**Coach — passes:**
- Full SOURCE_LEVEL_TESTS sweep cited: 582/0/18 (verified by direct run, not author claim).
- Sibling pattern (`rate_limiter.py:253`) cited inline at `csrf.py:198-199` ("rate_limiter.py:253/265/277 — same canonical return-not-raise shape").
- 3-file commit footprint verified: `csrf.py` + new test file + pre-push entry at line 304.
- Pre-push allowlist entry confirmed: `tests/test_no_middleware_dispatch_raises_httpexception.py`.

## Recommendation

**APPROVE-WITH-FIXES** — ship as-is. The single P1 advisory (envelope-shape sibling parity, Maya) does not block: implementation follows Gate A v1 directive verbatim, AND the alternate envelope (rate_limiter's `{"detail"}`) would itself diverge from Gate A. Resolution is a separate followup that touches frontend parsers + harmonizes across middlewares — out of scope for the prod-bug-fix this commit closes.

Commit body MUST cite both Gate A v1 + Gate B verdicts per the TWO-GATE rule. P1 followup task: "audit middleware error-envelope harmony across csrf.py + rate_limiter.py + frontend parsers".
