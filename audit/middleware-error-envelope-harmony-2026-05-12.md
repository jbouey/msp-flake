# Task #123 — Middleware error-envelope harmony audit

**Status:** RESEARCH DELIVERABLE — Gate A required before implementation.
**Date:** 2026-05-12.

## TL;DR

Three middleware modules emit three different error-envelope shapes. Frontend parsers read **only `.detail`**. csrf.py uses **`.error`** — making its actionable copy invisible to every frontend caller. Recommend harmonizing all middleware on `{"detail": ..., ...}` (FastAPI default + frontend reality).

## What each middleware emits

| Module:line | Status | Envelope shape | Frontend visibility |
|---|---|---|---|
| `csrf.py:205-211` | 403 | `{"error": "CSRF validation failed...", "status_code": 403}` | **NONE** — frontend reads `.detail` |
| `rate_limiter.py:253-260` | 429 | `{"detail": "Too many authentication attempts...", "retry_after": N}` | Visible — `.detail` read |
| `rate_limiter.py:265-272` | 429 | `{"detail": "Agent rate limit exceeded.", "retry_after": N}` | Visible |
| `rate_limiter.py:277-284` | 429 | `{"detail": "Rate limit exceeded...", "retry_after": N}` | Visible |
| FastAPI default `HTTPException(status, detail)` | any | `{"detail": "..."}` | Visible |

## How the frontend actually reads errors

3 separate parsers, all converged on `.detail`:

```ts
// utils/api.ts:139, :1643 (canonical fetch wrapper)
throw new ApiError(response.status, parseApiErrorMessage(response.status, error.detail));

// utils/portalFetch.ts:43-46 (portal/companion fetch wrapper)
parsed?.detail || `${status} ${text || 'request failed'}`,
err.detail = parsed?.detail;

// utils/integrationsApi.ts:30
const detail = error.detail;
```

`grep -rn "error\.error\b" frontend/src/` → **0 callsites**. No frontend code reads csrf.py's `.error` key.

## Customer-visible consequence

Today, a CSRF-rejected POST returns 403 with `{"error": "CSRF validation failed. Refresh the page and try again.", "status_code": 403}`. The frontend's `error.detail` is `undefined`, so `parseApiErrorMessage(403, undefined)` falls through to a generic fallback string. Users see something like `"Request failed (403)"` — the actionable "Refresh the page" copy is lost.

A rate-limited POST returns 429 with `{"detail": "...", "retry_after": N}`. Frontend correctly surfaces the message and the retry-after seconds.

## Recommended harmonization

**Change csrf.py:205-211 from:**

```python
return JSONResponse(
    status_code=403,
    content={
        "error": "CSRF validation failed. Refresh the page and try again.",
        "status_code": 403,
    },
)
```

**To:**

```python
return JSONResponse(
    status_code=403,
    content={
        "detail": "CSRF validation failed. Refresh the page and try again.",
    },
)
```

Drop `status_code` field — HTTP status code is already in the response status; duplicating it in the body is redundant. Matches FastAPI default `HTTPException` shape, rate_limiter.py shape, and what 100% of frontend parsers expect.

## CI gate (proposed)

Add `tests/test_middleware_error_envelope_harmony.py`:
- AST-walk every `BaseHTTPMiddleware` subclass's `dispatch` method.
- For every `return JSONResponse(status_code=N, content=...)` (where `N >= 400`), assert the `content` dict has a `"detail"` key.
- Allowlist marker `# noqa: middleware-envelope-non-detail` for documented exceptions.

Sibling pattern: `tests/test_no_middleware_dispatch_raises_httpexception.py` (task #121).

## Backwards-compatibility

Removing `.error` key affects **zero** external consumers. The CSRF-rejection path is browser-side only; appliances use API-key + are in `EXEMPT_PATHS`. No public API contract to worry about.

Removing `.status_code` from the body affects zero consumers (frontend uses `response.status`, never `body.status_code`).

## Per-Gate-A questions

1. **Steve:** Is there any non-frontend consumer (admin CLI script, monitoring probe, SIEM rule) that depends on `{"error", "status_code"}` envelope from csrf.py 403 responses? Grep ops scripts.
2. **Maya:** Confirm user-visible copy after harmonization is at least as good as today's (since today's actionable copy was invisible, this is a strict UX improvement).
3. **Carol:** Information disclosure delta — `.detail` is exposed by every other middleware; harmonization doesn't widen surface.
4. **Coach:** Why did Gate A on task #121 direct `{"error", "status_code"}`? Look at `audit/coach-csrf-403-unwrap-gate-a-2026-05-11.md` — the directive said "global error envelope" but my (this Session 220) check shows that's not what the frontend reads. Was the Gate A directive author looking at backend Pydantic ValidationError shape (which IS `{"error", ...}`) rather than HTTPException shape?
