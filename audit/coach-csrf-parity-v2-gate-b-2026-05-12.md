# Gate B — CSRF Parity v2 (decorator-kwarg shape) — 2026-05-12

**Task:** #124 — post-implementation review of follow-up patch to commit `a612a7e6` (task #122).
**Artifact under review:** `mcp-server/central-command/backend/tests/test_csrf_exempt_paths_match_appliance_endpoints.py` (AS-IMPLEMENTED, post Gate A P2 fix).
**Gate A verdict cited:** `audit/coach-csrf-parity-v2-gate-a-2026-05-12.md` — APPROVE-WITH-FIXES (0 P0, 2 P1 deferred to task #125, 1 P2 dead-wrapper deletion recommended).
**P2 fix applied post-Gate-A:** Dead `_has_appliance_bearer_dep` wrapper deleted. Two leaf helpers + call composer retained.

---

## Verdict: **APPROVE** — 0 P0, 0 new P1 (Gate A P1-A + P1-B remain deferred to task #125 as previously named), 0 P2

**Patch is correct, sweep is green, adversarial cases pass, dead-wrapper deletion has no collateral references.**

---

## Verification ledger (mandatory citations)

### 1. Source-read of AS-IMPLEMENTED file

- `_APPLIANCE_BEARER_DEP_NAMES` set defined: line 60. Contains `require_appliance_bearer` + `require_appliance_bearer_full`. ✓
- `_is_appliance_bearer_depends_call`: line 228. Handles `Depends` as Name AND Attribute; dep arg as Name AND Attribute. ✓
- `_function_has_appliance_bearer_dep`: line 244. Scans `func.args.defaults + kw_defaults`. ✓
- `_decorator_has_appliance_bearer_dep`: line 254. Scans `kw.arg == "dependencies"` with `ast.List` value. ✓
- Dead `_has_appliance_bearer_dep` wrapper: **GONE.** grep within file returns only `_function_has_*` and `_decorator_has_*` (lines 244, 254, 289, 312). The bare wrapper name has zero hits. ✓
- `_extract_handlers` ordering (line 274–329): pre-computes `func_level_dep` once (line 289); per-decorator validates method/path **first** (296–308), then dep-check (312), then registry-resolution (315–325). Correct cheap-first ordering. ✓
- `_scan_file` (line 332–354): no pre-filter by function-level dep — function is unconditionally passed to `_extract_handlers`, which now applies the per-decorator OR check. ✓
- `test_synthetic_dependencies_kwarg_shape_detected`: line 527. Asserts 3 cases: (a) bearer detected, (b) `_full` variant detected, (c) `require_auth` MUST NOT fire. ✓

### 2. Local test result

```
tests/test_csrf_exempt_paths_match_appliance_endpoints.py::test_csrf_exempt_paths_match_appliance_endpoints PASSED
tests/test_csrf_exempt_paths_match_appliance_endpoints.py::test_extract_csrf_exemptions_parses_both_structures PASSED
tests/test_csrf_exempt_paths_match_appliance_endpoints.py::test_is_exempt_membership_logic PASSED
tests/test_csrf_exempt_paths_match_appliance_endpoints.py::test_synthetic_handler_path_resolution PASSED
tests/test_csrf_exempt_paths_match_appliance_endpoints.py::test_synthetic_require_appliance_bearer_full_variant_detected PASSED
tests/test_csrf_exempt_paths_match_appliance_endpoints.py::test_synthetic_dependencies_kwarg_shape_detected PASSED

6 passed, 4 warnings in 3.83s
```

**6/6 PASS — confirmed by Gate B (not author-claim).**

### 3. Full sweep (Coach mandatory — `bash .githooks/full-test-sweep.sh`)

```
236 passed, 0 skipped (need backend deps)
```

**Tally: 236 passed / 0 failed / 0 skipped.** No regressions vs the pre-patch baseline. The CSRF parity gate file is included in the sweep and passes alongside all sibling gates. **No diff-only scoping — full source-level sweep executed per the 2026-05-11 Gate-B lock-in.**

### 4. Adversarial probe vs AS-IMPLEMENTED (4 cases)

Executed `/tmp/csrf_gate_b_v2_probe.py` against the actual loaded module:

| # | Shape | Expected | Got | Verdict |
|---|-------|----------|-----|---------|
| 1 | `@router.get("/r", dependencies=[Depends(require_appliance_bearer)])` | not-emitted (CSRF safe method) | `[]` | PASS |
| 2 | Function-level dep + decorator-kwarg dep on same `@router.post` | single emit | `[("post", "/api/x/p")]` | PASS — no double-emit |
| 3 | Function-level dep + `@router.get + @router.post` stacked (no kwarg) | only POST | `[("post", "/api/x/r")]` | PASS |
| 4 | No function-level dep + `@router.post(kwarg) + @router.post(no-kwarg)` | only kwarg-bearing emits | `[("post", "/api/x/has-kwarg")]` | PASS |

**All 4 adversarial cases PASS.** The per-decorator `OR` check does NOT regress on mixed-shape functions. GET handlers with kwarg-deps are correctly skipped (line 297: `if method not in _STATE_CHANGING_METHODS: continue`).

### 5. Dead-wrapper deletion side-effect scan

`grep -rn "_has_appliance_bearer_dep" --include='*.py'` repo-wide (excluding the test file itself):

```
mcp-server/central-command/backend/tests/test_appliance_delegation_auth_pinned.py:62
mcp-server/central-command/backend/tests/test_appliance_delegation_auth_pinned.py:107
mcp-server/central-command/backend/tests/test_appliance_delegation_auth_pinned.py:131
```

All three matches are `_handler_has_appliance_bearer_dep` — a DIFFERENT function (prefixed with `_handler_`, scoped to `test_appliance_delegation_auth_pinned.py`). **No reference to the deleted `_has_appliance_bearer_dep` wrapper anywhere in the repo.** Side-effect free.

### 6. Real-codebase main-gate sanity check

Main test `test_csrf_exempt_paths_match_appliance_endpoints` runs the full repo scan against `_BACKEND.rglob("*.py") + _MAIN_PY` — emits zero violations. The 6 entries in `_KNOWN_BLOCKED_DEAD_ROUTES` remain correctly suppressed (no test assertion change, no failure). No new appliance-bearer callsite has been silently un-covered by the v2 patch.

---

## Lens-by-lens synthesis

### Steve — Principal SWE / correctness

The two leaf helpers `_function_has_appliance_bearer_dep` (line 244) and `_decorator_has_appliance_bearer_dep` (line 254) carry their own clear contracts; `_extract_handlers` composes them via `func_level_dep or _decorator_has_appliance_bearer_dep(deco)` at the correct point in the loop. Removing the wrapper was the right call once the only caller (`_scan_file`'s pre-filter) was deleted. Naming follows codebase convention (`_is_*`/`_function_has_*`/`_decorator_has_*` predicate prefixes match `test_no_middleware_dispatch_raises_httpexception.py`'s sibling shape). Failure-message ergonomics in the main gate are unchanged from task #122. **APPROVE.**

### Maya — Security / what still escapes?

Gate A's adversarial 9-case probe identified P1-A (Name-reference `dependencies=_DEPS`) and P1-B (`from fastapi import Depends as D`). Both remain open and are correctly deferred to task #125 — they pre-date the v2 patch and were not chartered to close here. Gate B confirms via the 4-case AS-IMPLEMENTED probe that the chartered class (decorator-kwarg with literal `[Depends(require_appliance_bearer[_full])]`) is closed without regression, double-emit, or false-positive on non-bearer deps (Case 4 + the `auth_only` synthetic at line 568 both verify the negative control). **APPROVE — no new P0/P1 introduced.**

### Carol — Compliance / customer impact

The patch is preventive — `grep -rn "dependencies=\[Depends(require_appliance_bearer"` returns only synthetic test cases. No production callsite migrated. No retroactive backfill, no §164.528 disclosure-accounting impact, no customer-facing artifact change. The witness modules (`install_reports.py`, `install_telemetry.py`) use `dependencies=[Depends(...install_token...)]` — they prove the shape is live but are correctly NOT flagged because the dep is not in `_APPLIANCE_BEARER_DEP_NAMES`. **APPROVE.**

### Coach — Process gate

This Gate B was executed via fork (`Agent(subagent_type="general-purpose")` per session policy), fresh context, NOT diff-scoped. Full source-level sweep cited above (236/0/0). Gate A's P1-A + P1-B remain named as task #125 to carry in the merge commit body. Gate A's P2 (dead wrapper deletion) is applied in the current local. Per the 2026-05-11 two-gate lock-in: BOTH gate verdicts are documented at `audit/coach-csrf-parity-v2-gate-{a,b}-2026-05-12.md` — commit body must cite BOTH. **APPROVE-WITH-named-followups (task #125).**

---

## Required commit-body lines

```
Gate A: audit/coach-csrf-parity-v2-gate-a-2026-05-12.md (APPROVE-WITH-FIXES; P1s -> #125, P2 applied)
Gate B: audit/coach-csrf-parity-v2-gate-b-2026-05-12.md (APPROVE; full sweep 236/0/0)
Followup: task #125 — close P1-A (Name-reference deps) + P1-B (aliased Depends import)
```

---

## Synthesis

| Finding | Severity | Status |
|---------|----------|--------|
| P0 | (none) | — |
| Patch correctness | — | All 4 helpers present + correctly composed |
| Dead-wrapper deletion | — | No remaining repo references; clean |
| Local test result | — | 6/6 PASS |
| Full sweep | — | 236 passed / 0 failed / 0 skipped |
| Adversarial probe | — | 4/4 PASS (mixed-shape, double-presence, GET-skip, negative-control) |
| Gate A P1-A (Name-ref deps) | P1 (pre-existing) | Deferred to task #125 — named in commit body |
| Gate A P1-B (aliased Depends) | P1 (pre-existing) | Deferred to task #125 — named in commit body |
| Gate A P2 (dead wrapper) | P2 | RESOLVED in-commit |

**Verdict: APPROVE.** Ships under TWO-GATE compliance with the documented followups.
