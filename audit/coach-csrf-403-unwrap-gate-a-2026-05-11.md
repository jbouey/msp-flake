# Gate A — CSRF 403 unwrap fix (2026-05-11)

**Verdict:** APPROVE-WITH-FIXES

Fix is correct, minimal, and matches an established sibling pattern in the same module set. Three small fixes required before commit; none redesign-level.

## Source verification

- `csrf.py:190` confirmed: inside `CSRFMiddleware.dispatch` (line 159, `class CSRFMiddleware(BaseHTTPMiddleware)` per import line 19), the validation failure branch calls `raise HTTPException(status_code=403, detail="CSRF validation failed. Refresh the page and try again.")`. The `logger.warning(...)` at 184-189 fires BEFORE the raise — preserved by Option A.
- Sibling pattern already exists: `rate_limiter.py:13` imports `from starlette.responses import JSONResponse` and `rate_limiter.py:253/265/277` all `return JSONResponse(status_code=..., content={...})` from inside a `BaseHTTPMiddleware.dispatch`. **This is the canonical project-local pattern; Option A is not a novel design — it brings csrf.py into parity with the working sibling.**
- Other middleware-internal raises that need the same fix: **none found**. `security_headers.py`, `etag_middleware.py`, `rate_limiter.py` all return responses (no `raise HTTPException` inside dispatch). Single-file fix.
- `starlette.responses.Response` is already imported at csrf.py:20 — add `JSONResponse` to the SAME line for consistency.

## P0/P1/P2 findings

**P0 — none.**

**P1**
1. Import path: use `from starlette.responses import Response, JSONResponse` (extend line 20), NOT `from fastapi.responses import JSONResponse`. Matches rate_limiter.py exactly and avoids the FastAPI re-export indirection.
2. Response body shape MUST match what frontends expect across the codebase. Audit shows the global error envelope is `{"error": "...", "status_code": N}` (per the 500 body cited in the bug report). Use `content={"error": "CSRF validation failed. Refresh the page and try again.", "status_code": 403}` — NOT the FastAPI default `{"detail": "..."}` shape, which would silently break any frontend code that reads `.error`. Verify utils/api.ts error parser before final shape.
3. Keep `logger.warning(...)` exactly as-is at 184-189. SIEM rules + audit-log persistence depend on this line; Option A preserves it (the `return` is below the log, not above).

**P2**
1. Add a unit test using `fastapi.testclient.TestClient`: POST without CSRF cookie+header → assert `response.status_code == 403`, assert body parses as JSON with `error` key. Test belongs in `tests/test_production_security.py` (already imports `BaseHTTPMiddleware`).
2. Add a CI gate: `tests/test_no_middleware_dispatch_raises_httpexception.py` — AST-walk every `BaseHTTPMiddleware` subclass's `dispatch` method, fail on `raise HTTPException`. Prevents regression class.

## Per-lens

- **Steve:** Correct root cause. `BaseHTTPMiddleware` wraps dispatch in anyio TaskGroup; raised exceptions surface as `ExceptionGroup` to FastAPI's exception_handler chain, which doesn't match `HTTPException` and falls through to the generic 500. Documented Starlette gotcha. Option A bypasses entirely. Approve.
- **Maya:** Audit log line preserved. 500→403 transition is a strict improvement for SIEM (CSRF-rejection class becomes detectable). Response-body-shape parity with project envelope is the only frontend risk — pinned as P1#2. Approve with that fix.
- **Carol:** Boundary unchanged — still rejects, still logs. Information disclosure delta (500 opaque → 403 "CSRF validation failed") is acceptable; CSRF is a public defense per OWASP, not security-through-obscurity. No CVE class introduced. Approve.
- **Coach:** Gate A applies because middleware = security boundary; size of fix is irrelevant. Sibling pattern in rate_limiter.py is load-bearing — cite it in the commit body. Gate B required before close-out: TestClient evidence + curl-against-deployed-VPS evidence + new CI gate landed.

## Recommendation

PROCEED with Option A. Apply P1 fixes #1-3 in the same commit. Land the P2 CI gate in the same PR (1-file AST test, ~30 lines). Commit body must cite rate_limiter.py:253 as the precedent and reference this Gate A doc. Gate B verdict before marking task #121 done.
