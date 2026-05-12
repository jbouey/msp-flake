# Gate B — CSRF EXEMPT_PATHS sibling-parity gate (2026-05-11)

**Verdict:** BLOCK (P0-A: gate is blind to `require_appliance_bearer_full` — the
exact regression class it was built to prevent escapes detection)

**Artifact under review:** `mcp-server/central-command/backend/tests/test_csrf_exempt_paths_match_appliance_endpoints.py`

**Test run:** 4/4 PASS locally (3.98s, Python 3.14).
**Full-sweep run:** 236 passed, 0 skipped (need backend deps), 0 failed
(`PRE_PUSH_SKIP_FULL=0 .githooks/full-test-sweep.sh`).

---

## Gate A directive compliance — verified

| Gate A directive | Implementation | Verdict |
|---|---|---|
| P0-1 parse BOTH EXEMPT_PATHS + EXEMPT_PREFIXES; membership = `in PATHS or any(startswith p)` | `_extract_csrf_exemptions` walks both Set + Tuple literal. `_is_exempt` does both checks. `test_is_exempt_membership_logic` pins the negative case (`/api/exact/sub` does NOT match `/api/exact`) | PASS |
| P0-2 resolve prefix from BOTH `APIRouter(prefix=)` AND `app.include_router(router, prefix=)` | `_extract_registry` walks main.py for include_router calls + parses keyword prefix arg. `_extract_handlers` composes `include_prefix + router_prefix + deco_path`. Two-step alias resolution (import-alias → include_router var) correctly handles `from … import router as discovery_router` shape | PASS |
| P1-1 skip unregistered routers (agent_api.py false-positive class) | `_extract_handlers` skips when `(module_basename, owner_name)` not in registry. Verified: main.py:5767 explicitly states agent_api router "is NOT registered" → no false-positive | PASS |
| P1-2 drop inverse direction | Not implemented — confirmed in docstring (line 35) + deferred to task #123 | PASS |
| P2-1 skip GET handlers (SAFE_METHODS) | `_STATE_CHANGING_METHODS = {post,put,patch,delete}`. `test_synthetic_handler_path_resolution` includes positive GET-skip assertion | PASS |
| P2-2 failure message: file:line + handler + paste-able exemption | violation builder emits `rel:lineno  METHOD path  (handler: X, decorator path: 'Y')` + `Add to csrf.py EXEMPT_PATHS: "<path>",` (or PREFIXES line if `{` template). Sibling-shape parity with task #121 | PASS |

---

## P0 findings (BLOCK)

### P0-A — `require_appliance_bearer_full` is entirely invisible to the gate

**File:line:** `tests/test_csrf_exempt_paths_match_appliance_endpoints.py:228,230`

```python
if isinstance(a, ast.Name) and a.id == "require_appliance_bearer":
    return True
if isinstance(a, ast.Attribute) and a.attr == "require_appliance_bearer":
    return True
```

`shared.py` exports **two** appliance-bearer FastAPI dependencies, defined at
`shared.py:571` (`require_appliance_bearer_full` → `tuple[str, Optional[str]]`)
and `shared.py:589` (`require_appliance_bearer` → `str`). The "full" variant is
a strict superset of the bare one (it calls `require_appliance_bearer(request)`
internally and adds the `_bearer_aid` tuple member). Functionally identical for
CSRF purposes.

The gate's name-matcher hard-codes the bare name. Today's prod callsites of the
"full" variant:

| File | Line | Path resolved | Currently CSRF-exempt? |
|---|---|---|---|
| `journal_api.py:74` | `@journal_api_router.post("/upload")` | `/api/journal/upload` | yes (exact `EXEMPT_PATHS` entry, Session 210-B fix) |
| `watchdog_api.py:244` | `@watchdog_api_router.post("/checkin")` | `/api/watchdog/checkin` | yes (`/api/watchdog/` prefix) |
| `watchdog_api.py:279` | `@watchdog_api_router.post("/diagnostics")` | `/api/watchdog/diagnostics` | yes (`/api/watchdog/` prefix) |
| `watchdog_api.py:313` | `@watchdog_api_router.post("/orders/{order_id}/complete")` | `/api/watchdog/orders/{...}/complete` | yes (`/api/watchdog/` prefix) |
| `watchdog_api.py:390` | `@watchdog_api_router.post("/bootstrap")` | `/api/watchdog/bootstrap` | yes (`/api/watchdog/` prefix) |
| `breakglass_api.py:69` | `@breakglass_provision_router.post("/breakglass-submit")` | `/api/provision/breakglass-submit` | yes (`/api/provision/` prefix) |

So today's shipping state is INTACT — every `*_full` callsite is, by luck,
already covered by an existing EXEMPT_PREFIXES entry. But the gate's PURPOSE is
to prevent the next regression. Two of these — `journal_api.py` and the watchdog
endpoints — are LITERALLY THE FILES the Session 210-B + Session 207 Phase W
regressions touched. The Session 210-B journal-upload bug class is exactly the
class this gate exists to close, and the gate cannot see it.

**Concrete failure scenario:** developer adds
`@watchdog_api_router.post("/new-endpoint", …)` with `bearer = Depends(require_appliance_bearer_full)`
on a new route under `/api/watchdog2/` (or any path that doesn't fall under the
current 3 prefixes). The gate is silent. Prod returns 403 to every appliance
request. `substrate.health_invariants` fires hours/days later, same exact failure
mode as the Session 210-B journal_upload_never_received fire.

**Severity:** P0-BLOCK. The gate's tagline ("Closes the Session 210-B
/api/journal/upload regression class") is mechanically false until this is
fixed. journal_api.py would not appear in the gate's output today even if
csrf.py had no `/api/journal/upload` line — the function-defaults walk would
see `require_appliance_bearer_full` and skip.

**Fix (mechanical, ≤10 lines):**

```python
_APPLIANCE_BEARER_DEP_NAMES = {
    "require_appliance_bearer",
    "require_appliance_bearer_full",
}

def _has_appliance_bearer_dep(func):
    for default in list(func.args.defaults) + list(func.args.kw_defaults or []):
        if default is None: continue
        if not isinstance(default, ast.Call): continue
        f = default.func
        if not ((isinstance(f, ast.Name) and f.id == "Depends") or
                (isinstance(f, ast.Attribute) and f.attr == "Depends")):
            continue
        for a in default.args:
            if isinstance(a, ast.Name) and a.id in _APPLIANCE_BEARER_DEP_NAMES:
                return True
            if isinstance(a, ast.Attribute) and a.attr in _APPLIANCE_BEARER_DEP_NAMES:
                return True
    return False
```

Plus a new sanity test asserting both names are matched — e.g.
`test_extractor_catches_bearer_full_variant` with the inline AST shape used by
`test_synthetic_handler_path_resolution`.

After fix, re-run gate; should still 4/4 PASS because all 6 callsites are
currently exempt.

---

## P1 findings (ship with named follow-up)

### P1-A — `dependencies=[Depends(...)]` decorator shape unhandled

**File:line:** `_extract_handlers` (line 250-283) inspects `func.decorator_list`
for the URL but only checks the function's parameter defaults via
`_has_appliance_bearer_dep`. FastAPI's documented alternative is

```python
@router.post("/foo", dependencies=[Depends(require_appliance_bearer)])
async def foo(...): ...
```

Today's grep shows ZERO production usages of this shape with appliance bearer
(`grep dependencies=\[.*require_appliance_bearer`: empty). It IS used with
`require_install_token` and `require_auth`, so the pattern exists in the
codebase and could spread to appliance bearer.

**Severity:** P1 — class-level structural risk, no current callsites bypassed.
**Follow-up:** TaskCreate item "extend `_has_appliance_bearer_dep` to also
walk the decorator's `dependencies=[Depends(...)]` kwarg" — mechanical, ≤15
lines. Ship gate today, file follow-up in the same commit body.

---

## P2 findings (advisory)

### P2-A — `_KNOWN_BLOCKED_DEAD_ROUTES` is a set, not a list ratchet

The 6 known blocked dead routes are stored in a `set` literal. The Session
220 zero-auth audit cites 7 endpoints; the registry has 6. The 7th (likely
`/api/sites/{site_id}/something` form) is presumably covered by
`/api/appliances/` prefix or has been closed already. Either way the set
mechanism prevents growth (correct shape — adding a new entry is a deliberate
edit) but does NOT cause CI to fail if the set SHRINKS without an explicit
ratchet-down. That's task #120's concern, not this gate's, and the docstring
correctly says "gate's role is to PREVENT growth; reducing it is the triage
task's job."

**Verdict:** correct design. Advisory note only — when task #120 removes an
entry, the test will still pass (set is checked for membership, not equality).
Suggest a brief comment on each entry pointing to its source-of-record file
line (already partially done: `audit/csrf-blocks-zero-auth-endpoints-finding-2026-05-11.md`
named, but specific entry rationale could be tighter).

### P2-B — Path-normalization is single-pass on `//` but doesn't strip trailing `/`

`while "//" in full: full = full.replace("//", "/")` handles the
`include_prefix="" + router_prefix="/api/foo" + deco_path="/bar"` →
`/api/foo/bar` case correctly (no `//` appears). It does NOT normalize
trailing slash mismatches like `EXEMPT_PREFIXES` having `/api/appliances/`
vs. a handler path `/api/appliances` (without trailing slash). Today's exact
prefixes all end in `/` and decorator paths all begin with `/`, so the
concatenation always produces a leading `/...` that `startswith("/api/appliances/")`
matches. No current callsite trips this. Advisory only.

---

## Per-lens summary

- **Steve (principal engineer):** P0-A is a hard block. The gate's stated
  purpose is to close the Session 210-B journal-upload class, and the file
  it was designed to defend is invisible to it. Two-name allowlist fix is
  trivial. The full-sweep coming back 236-pass-clean confirms there's no
  collateral damage from the artifact today, but the artifact is incomplete.
- **Maya (product/UX risk):** Failure message is clear and paste-able (line
  348-353). Failure-mode is the silent class user-cited — gate prevents next
  occurrence after P0 fix. No UX/copy concerns.
- **Carol (security/legal):** No legal-language issues. CSRF defense-in-depth
  posture is preserved (gate does not WEAKEN exemption surface; it only
  guards against under-exempting). P0-A means the gate is partially silent
  on the high-value path class — security-relevant because silent 403s are
  a denial-of-service against legitimate appliance traffic, not a leak.
- **Coach (sibling parity):** Shape matches
  `test_no_middleware_dispatch_raises_httpexception.py` (task #121): AST
  walker + named scope + violation list + suggested-fix copy in failure
  message + positive/negative synthetic controls. P0-A is the
  "diff-only review missed what was MISSING" antipattern (cf. Session 220
  lock-in rule). The diff added `require_appliance_bearer` but the matching
  artifact required ALL bearer-name variants. Gate B sweep caught this.

---

## Recommendation

**BLOCK** until P0-A is closed. Fix is ≤10 LoC + 1 new control test. Then
re-run Gate B on the patched file — expect 5/5 PASS and 236+1 sweep
(or unchanged 236 — the new test is a control, doesn't traverse production
sources).

P1-A ships in the same commit as a named TaskCreate follow-up. P2 advisory
items do not block.

Once P0 closed, commit body must cite:
- Gate A verdict: APPROVE-WITH-FIXES (this file's parent)
- Gate B verdict: APPROVE-WITH-FIXES (post-P0-A fix, downgraded from BLOCK)
- Full-sweep: 236+ passed, 0 failed
- Followup: TaskCreate for P1-A `dependencies=[Depends(...)]` decorator shape.
