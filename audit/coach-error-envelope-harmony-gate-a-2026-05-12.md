# Gate A — Middleware error-envelope harmony (task #123, 2026-05-12)

**Verdict:** APPROVE-WITH-FIXES

Two-line `csrf.py` edit is correct, low-risk, and rolls back a wrong-directive from
the 2026-05-11 Gate A on task #121. Frontend-grep claim verified independently.
Three P2 follow-ups; no P0, no P1 blockers.

---

## Source verification — every packet claim re-run

### Claim 1 (Steve #1): frontend reads `.detail`, never `.error`

Ran the explicit greps the packet cites, in `mcp-server/central-command/frontend/src/utils/`:

```
$ grep -rnE "\.detail\b" mcp-server/central-command/frontend/src/utils/
portalFetch.ts:23     comment "carry .status + .detail"
portalFetch.ts:43     parsed?.detail || `${status} ${text || 'request failed'}`
portalFetch.ts:46     err.detail = parsed?.detail;
portalFetch.ts:102    comment ".status + .detail so callers branch"
integrationsApi.ts:30 const detail = error.detail;
api.ts:139            parseApiErrorMessage(response.status, error.detail)
api.ts:1643           parseApiErrorMessage(response.status, error.detail)
```
**7 hits across 3 files. Every error-parsing utility reads `.detail`. CONFIRMED.**

```
$ grep -rnE "error\.error\b|response\.error\b|body\.error\b|data\.error\b|\.error\b" \
    mcp-server/central-command/frontend/src/utils/
(no matches)
```
**0 hits. No utility reads `.error`. CONFIRMED.**

Broader sweep of the WHOLE frontend (`mcp-server/central-command/frontend/src/`)
for any `(parsed|body|data|response|err|res|json).error` access:

- `contexts/AuthContext.tsx:128` — reads `data.error` from the JSON body of
  `/auth/login` (its own 200/non-401 application response, NOT a middleware
  403 envelope). **Not a CSRF-403 consumer; out of scope.**
- `pages/SiteWorkstations.tsx:166`, `pages/SystemHealth.tsx:171-174` — read
  `data.error`/`data.errors.critical_1h` from compliance-status payloads,
  not from middleware error envelopes. **Out of scope.**

No frontend code reads `.error` from a CSRF 403. The packet's "ZERO frontend
parsers read `.error`" claim is CORRECT.

### Claim 2 (Steve #2): non-frontend consumers of CSRF 403 body

Repo-wide grep for the literal `"CSRF validation failed"`:

```
$ grep -rn "CSRF validation failed" /Users/dad/Documents/Msp_Flakes
```

Outside `.claude/worktrees/` (which is local scratch, not committed):

- `csrf.py:185` — the `logger.warning` line (string, not response body).
- `csrf.py:208` — the response body itself.

**Zero ops scripts (`.agent/scripts/`, `bin/`, `scripts/`), zero SIEM rules,
zero monitoring probes parse this string. Single producer, single consumer
class (browser), and the browser doesn't read it today.**

Tests pinning the current `{"error", "status_code"}` shape:

- `tests/test_production_security.py` — has `test_csrf_*` cases but `grep`
  finds no `"error"` / `"detail"` / `status_code` body-shape assertions.
  CSRF tests assert flow, cookie names, compare_digest — not body shape.
- `tests/test_no_middleware_dispatch_raises_httpexception.py:192` — synthetic
  fixture inline-quotes `content={"error": "nope", "status_code": 403}` as
  a NEGATIVE control (proves the "no raise" gate doesn't false-positive on
  return-not-raise). Not a body-shape assertion; the gate itself only checks
  for `raise HTTPException`. **Safe to update the synthetic to the new shape
  in the same commit (cosmetic, optional).**

Conclusion: zero non-frontend / non-test consumers. Backwards-compatibility
section of the packet is correct.

### Claim 3 (Steve #3): FastAPI default for `HTTPException(403, detail)`

Confirmed via source-of-truth: `starlette.exceptions.HTTPException` →
`fastapi.HTTPException` → the project's default exception_handler emits
`JSONResponse({"detail": detail}, status_code=status_code)`. The project
has NO custom `add_exception_handler` registered:

```
$ grep -rn "exception_handler" mcp-server/central-command/backend/ --include="*.py" \
    | grep -v test_ | grep -v worktrees
csrf.py:193    (only a comment, not a registration)
```

No HTTP/2 / header-key concerns from dropping `status_code` from the body —
that field is body-only data, not a framework signal. `Response.headers` are
unaffected.

### Claim 4 (Maya #4): user-visible message before vs after

Today (buggy): `parseApiErrorMessage(403, undefined)` → falls through to a
generic fallback string. User sees `"Request failed (403)"` or
`"403 request failed"` depending on the call path. Actionable
"Refresh the page" copy is invisible.

After fix: `parseApiErrorMessage(403, "CSRF validation failed. Refresh the
page and try again.")` → toast shows the actionable copy. **Strict UX
improvement. CONFIRMED.**

### Claim 5 (Maya #5): frontend reads of `body.status_code`

```
$ grep -rn "status_code" mcp-server/central-command/frontend/src
(no matches)
```
**Zero callsites. Dropping `status_code` from the body affects zero consumers.
CONFIRMED.**

### Claim 6 (Carol #6): information-disclosure delta

`.detail` is already the exposed surface on every 429 (rate_limiter.py — 3
sites), every 422 (FastAPI ValidationError), every uncaught
`HTTPException(detail=...)` across the entire backend. The 403 path becoming
`.detail`-shaped doesn't introduce a NEW information channel — it harmonizes
on the existing one. **No surface widening. CONFIRMED.**

### Claim 7 (Carol #7): is "Refresh the page" copy too informative?

Comparison:
- Plain FastAPI default: `"CSRF validation failed"` — sufficient.
- Current copy: `"CSRF validation failed. Refresh the page and try again."`
- Both reveal that CSRF is in use, which is intentional (OWASP CSRF defense
  is public, not security-through-obscurity).

The "Refresh the page" addendum gives the legitimate user a remedy. It
reveals NOTHING new to an attacker — anyone probing CSRF defenses already
knows token-rotation requires a refresh. **Acceptable. CONFIRMED.**

### Claim 8 (Coach #8): origin of the 2026-05-11 directive

Read `audit/coach-csrf-403-unwrap-gate-a-2026-05-11.md` §P1#2 verbatim:

> "Response body shape MUST match what frontends expect across the codebase.
> Audit shows the global error envelope is `{"error": "...", "status_code":
> N}` (per the 500 body cited in the bug report)."

**The "global error envelope" was sourced from a SINGLE data point — the
500-response body in the bug report. That body was Starlette's UNCUSTOMIZED
fallback for unhandled-exception 500s, NOT a project convention.**

Grep proves this:

```
$ grep -rn '"error":.*"status_code"' backend/ --include="*.py" \
    | grep -v test_ | grep -v worktrees
csrf.py:201     (a comment in csrf.py itself)
```

**csrf.py is now the ONLY production emitter of `{"error", "status_code"}`
shape in the entire backend.** No other endpoint, no other middleware, no
shared error_handler ever emits this envelope. The directive author
generalized from one Starlette-default sample to "global envelope" — that
generalization was wrong. The bug report's 500 body wasn't a project
convention; it was the absence of one.

Pydantic ValidationError 422 also uses `{"detail": [...]}` (FastAPI's
RequestValidationError handler) — also `.detail`, just an array. So both
candidate "global envelopes" (FastAPI HTTPException + Pydantic 422) agree:
the answer is `.detail`. The directive was non-evidence-based.

### Claim 9 (Coach #9): CI gate — same commit or separate?

**Same commit. Reasoning:**

1. Scope is tiny: 4 `BaseHTTPMiddleware` subclasses total (`csrf.py`,
   `security_headers.py`, `rate_limiter.py`, `etag_middleware.py`). Only 2
   emit `JSONResponse` with `status_code >= 400` (csrf — after this fix —
   and rate_limiter). The AST walk has 4-class search space and ~4
   `JSONResponse(...)` callsites to check.
2. The sibling CI gate `tests/test_no_middleware_dispatch_raises_httpexception.py`
   from task #121 lives in the same conceptual family. Pairing them keeps
   the "middleware error-emission rules" surface coherent — one place to
   reason about, one place to update when adding a 5th middleware.
3. Filing as a separate task risks Coach-pattern dropouts: the rule exists
   in human memory but no enforcement until the next regression. We're
   here NOW, the diff is open, land the gate now.
4. The 30-line AST walk is well-precedented (see
   `test_no_middleware_dispatch_raises_httpexception.py`,
   `test_l2_resolution_requires_decision_record.py`,
   `test_assertions_loop_uses_admin_transaction.py`). Low review burden.

---

## P0 / P1 / P2 findings

**P0 — none.**

**P1 — none.**

**P2**

1. **(scope) Land the CI gate `tests/test_middleware_error_envelope_harmony.py`
   in the SAME commit as the csrf.py edit.** AST-walks every
   `BaseHTTPMiddleware` subclass's `dispatch`, for each `return
   JSONResponse(status_code=N, content=...)` where `N >= 400` assert the
   `content` dict literal has a `"detail"` key (string key, top-level). Allow
   `# noqa: middleware-envelope-non-detail` for documented exceptions.

2. **(consistency) Update the negative-control synthetic in
   `tests/test_no_middleware_dispatch_raises_httpexception.py:192`** to use
   `content={"detail": "nope"}` instead of `{"error": "nope", "status_code":
   403}`. Cosmetic only — the gate doesn't assert body shape — but keeps the
   sibling-test corpus from teaching the wrong shape to the next reader.

3. **(documentation) Update the in-source comment block at
   `csrf.py:201-204`.** The comment currently asserts the old (wrong)
   "global envelope" rationale. Replace with: "Body envelope `{\"detail\":
   ...}` matches FastAPI's `HTTPException` default and is what every
   frontend error parser reads (`utils/api.ts`, `utils/portalFetch.ts`,
   `utils/integrationsApi.ts` all read `.detail`). Sibling pattern at
   `rate_limiter.py:253/265/277`. See `audit/coach-error-envelope-harmony-
   gate-a-2026-05-12.md` for the rationale that overturned the 2026-05-11
   directive."

---

## Per-lens summaries

- **Steve:** Root cause is correctly identified (wrong directive on task #121
  led to an orphan envelope shape). Grep evidence is airtight: 7 `.detail`
  callsites, 0 `.error` callsites in `frontend/src/utils/`. csrf.py is the
  sole `{"error", "status_code"}` emitter in production. Zero ops/SIEM
  consumers. Two-line edit, low blast radius. **APPROVE-WITH-FIXES** (the
  3 P2s).

- **Maya:** Pre-fix UX is broken (actionable copy invisible, users see
  `"Request failed (403)"`). Post-fix UX shows the intended "Refresh the
  page and try again." Strict improvement. SIEM impact: zero — log line at
  `csrf.py:184-189` is unchanged. **APPROVE.**

- **Carol:** No surface widening — `.detail` already exposed on every other
  4xx/5xx in the codebase. "Refresh the page" copy does not reveal anything
  an attacker doesn't already know about CSRF defenses (OWASP-public).
  **APPROVE.**

- **Coach:** The 2026-05-11 directive was evidence-thin (generalized from
  one Starlette-default 500 body to "global envelope"). This packet
  corrects it with primary-source grep evidence. **Lesson worth pinning to
  feedback memory: an audit directive that asserts a "global pattern" MUST
  cite >=2 producers + >=1 consumer of the pattern.** A single sample is
  not a pattern. Carry as a feedback-memory entry in the same session.
  CI gate addition pairs naturally with the edit — same commit. Gate B
  required before close-out: TestClient evidence (POST without CSRF →
  body has `"detail"` key, no `"error"`/`"status_code"`) + curl evidence
  against deployed VPS post-deploy + CI sweep green. **APPROVE-WITH-FIXES.**

---

## Recommendation

**PROCEED** with the 2-line `csrf.py:205-211` edit (drop `error` + `status_code`,
emit `{"detail": "..."}`). Land in the same commit:

- The CI gate `tests/test_middleware_error_envelope_harmony.py` (P2#1).
- The synthetic-fixture cosmetic update at
  `test_no_middleware_dispatch_raises_httpexception.py:192` (P2#2).
- The in-source comment correction at `csrf.py:201-204` (P2#3).
- A feedback-memory entry on the "audit directive must cite >=2 producers
  + >=1 consumer before claiming a global pattern" rule (Coach).

Commit body must:
- Cite this Gate A doc (`audit/coach-error-envelope-harmony-gate-a-2026-05-12.md`).
- Cite the prior wrong directive (`audit/coach-csrf-403-unwrap-gate-a-2026-05-11.md`
  §P1#2) as the artifact being corrected.
- Cite the 7 `.detail` callsites + 0 `.error` callsites grep evidence.
- Cite `rate_limiter.py:253/265/277` as the in-project sibling pattern that
  the harmonization aligns with.

**Gate B required before task #123 close-out.** Must include:
- `pytest tests/test_middleware_error_envelope_harmony.py -v` green output.
- TestClient evidence: POST without CSRF token → 403, body JSON has top-level
  `"detail"` key, NO `"error"` or `"status_code"` keys.
- Live curl against the deployed VPS after CI deploy completes (proves
  runtime — not just code-true).
- Full pre-push test sweep (`bash .githooks/full-test-sweep.sh`) pass/fail
  count cited in Gate B verdict (per Session 220 lock-in: Gate B MUST run
  the full sweep, not diff-only review).
