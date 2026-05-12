# Gate A — CSRF parity v3 (task #125, alias-aware extractor)

**Date:** 2026-05-12
**Scope:** P1-B closure from Gate B v2 on #124 — alias-aware Depends + require_appliance_bearer[_full] detection.
**Reviewer:** Coach (fork) — 4 lenses (Steve / Maya / Carol / Coach).
**File:** `mcp-server/central-command/backend/tests/test_csrf_exempt_paths_match_appliance_endpoints.py`

---

## Verdict: **APPROVE-WITH-FIXES (P2 only, non-blocking)**

No P0. No P1. Two P2 documentation / edge-case items; merge is unblocked.

---

## Runtime evidence (per close-out rule)

### Target-test sweep (8/8)

```
$ python3 -m pytest tests/test_csrf_exempt_paths_match_appliance_endpoints.py -v --tb=short
...
tests/...::test_csrf_exempt_paths_match_appliance_endpoints                       PASSED [ 12%]
tests/...::test_extract_csrf_exemptions_parses_both_structures                    PASSED [ 25%]
tests/...::test_is_exempt_membership_logic                                        PASSED [ 37%]
tests/...::test_synthetic_handler_path_resolution                                 PASSED [ 50%]
tests/...::test_synthetic_require_appliance_bearer_full_variant_detected          PASSED [ 62%]
tests/...::test_synthetic_dependencies_kwarg_shape_detected                       PASSED [ 75%]
tests/...::test_synthetic_aliased_import_detected                                 PASSED [ 87%]
tests/...::test_extract_alias_maps_canonical_only_when_no_import                  PASSED [100%]
======================== 8 passed, 4 warnings in 4.50s =========================
```

Author claim 8/8 PASS confirmed. Warnings are pre-existing regex syntax warnings unrelated to this patch (main.py:4719/4735/4753 + auth.py:273).

### Full pre-push sweep (CI parity, NOT diff-only — per Session 220 lock-in)

```
$ bash .githooks/full-test-sweep.sh mcp-server/central-command/backend
... (parallel -P 6) ...
✓ 236 passed, 0 skipped (need backend deps)
```

**236/236 source-level tests pass, 0 dep-skips, 0 failures.** Diff-only review explicitly avoided per Session 220 lock-in lesson (three deploy outages this session shipped under diff-scoped Gate B).

---

## Lens-by-lens

### Steve — AST extractor correctness

**Q1: Does `_extract_alias_maps` handle `import X` (non-from) imports?**
File search: `grep -rn "^import fastapi$\|^import fastapi as"` across `backend/` + `main.py` → **zero matches**. FastAPI is always imported as `from fastapi import Depends, ...` in this codebase. The Attribute branch at line 265 (`isinstance(f, ast.Attribute) and f.attr in depends_names`) handles the hypothetical `fastapi.Depends(...)` shape correctly via attribute-name match. **Not a gap.**

**Q2: Backward compatibility when None is passed.**
Line-walk:
- `_is_appliance_bearer_depends_call` (lines 257–260): `if depends_names is None: depends_names = {"Depends"}` / `if bearer_names is None: bearer_names = set(_APPLIANCE_BEARER_DEP_NAMES)`.
- `_function_has_appliance_bearer_dep` (line 284): forwards both as-is.
- `_decorator_has_appliance_bearer_dep` (line 306): forwards both as-is.
- `_extract_handlers` (lines 330, 353): threads both, but the leaf checks default correctly when None.
- Control test `test_extract_alias_maps_canonical_only_when_no_import` proves: empty tree → `{"Depends"}` + `_APPLIANCE_BEARER_DEP_NAMES`. **Backward-compat preserved.**

**Q3: Nested-scope ImportFrom (function-local imports).**
`_extract_alias_maps` uses `ast.walk(tree)` at line 239 → traverses ALL nodes including function bodies. **main.py:2597** is a function-local `from dashboard_api.shared import require_appliance_bearer as _shared_auth`. The patch DOES add `_shared_auth` to main.py's `bearer_names` set.

Risk: would this cause a false positive? Verified: no `Depends(_shared_auth)` callsite exists in the codebase (`grep -rn "Depends(_shared_auth"` returns only the test file itself, in the docstring). The only usage is `await _shared_auth(request)` at line 2598 — a bare call, NOT inside `Depends(...)`. The predicate requires the outer node to be `Depends(<bearer>)`, which this is not. **No false positive risk.**

But this surfaces a P2: **the patch behavior on function-local imports is silently permissive** — it adds them to the file-wide alias set. If a future contributor writes `Depends(_shared_auth)` AT MODULE TOP-LEVEL (outside the function), the alias would already be in the set and detection works correctly. But the inverse — a function-LOCAL `Depends(_shared_auth)` — is also detected (FastAPI routers don't actually accept inside-function `Depends` though, so this is academic). **No correctness bug, document the choice.** → **P2-A**.

### Maya — Cost / value tradeoff (YAGNI vs defense-in-depth)

Patch adds ~80 LoC + 2 control tests. Zero current real-codebase callsites would change behavior with this patch in or out.

**Arguments for shipping:**
1. The Gate B v2 P1-B finding ON #124 already documented this as in-scope for #125 — deferring AGAIN would compound the carry.
2. CSRF-bypass via aliased import is a real supply-chain hardening (Carol — see below). The exact pattern an attacker would use.
3. The cost is bounded: zero runtime cost (only test-time AST walk), small maintenance surface (one new helper, parameter threads cleanly).
4. The patch composes with the existing extractor — no architectural disruption.

**Arguments for "react if observed":**
1. Zero current callsites. Strictly speculative.
2. ~80 LoC of code that exercises only synthetic ASTs.

**Maya verdict:** Ship. The defense-in-depth framing is real here because **the patch closes a documented Gate B finding** that was explicitly scoped to this task. Calling YAGNI now would be a process violation, not just a code-quality opinion.

### Carol — Threat model

**Adversary scenario:** A malicious contributor (or compromised dependency upstream) wants to introduce an appliance-bearer endpoint that BYPASSES the CSRF parity gate. Today they could:
1. Write `@router.post("/sneaky")` + `def x(_=Depends(require_appliance_bearer))` → caught by v1.
2. Use `dependencies=[Depends(require_appliance_bearer)]` decorator kwarg → caught by v2 (#124).
3. Use aliased import (`Depends as D` / `require_appliance_bearer as raf`) → **caught by v3 (this patch).**

The pattern is a 1-line change for an attacker, trivial to introduce. The patch closes the obvious supply-chain hardening gap. **Confirmed.**

**Edge case raised in brief:** `from fastapi import Depends as D, APIRouter as R; @R("...").post(...)`.

This is **NOT covered** by the patch. `R("...")` would create an inline APIRouter, which is unusual. But more importantly, `_extract_router_prefixes` at line 207 only recognizes `func.id == "APIRouter"` (line 207) or attribute `func.attr == "APIRouter"` (line 209) — it does NOT track aliased APIRouter imports. **A `R = APIRouter` alias + `R(prefix=...)` assignment would silently escape.**

Severity assessment: lower than #125's Depends alias because:
- Real attackers don't construct routers inline.
- The APIRouter alias would have to be at module top-level (less common than function-param aliasing).
- Zero current callsites use `from fastapi import APIRouter as <alias>` (verified: `grep -rn "APIRouter as"` returns the test file's docstring only).

But it IS a real residual gap. **P2-B: extend `_extract_router_prefixes` to track aliased APIRouter names (or file a separate followup task and reference it in the docstring).**

### Coach — Sibling parity

**Q1: Optional-parameter thread-through pattern.**
The codebase convention for AST-extractor-style gates (`test_no_middleware_dispatch_raises_httpexception.py`, sibling) uses module-level constants + flat function signatures — no per-file context object. The optional `set[str] | None = None` parameter shape matches Python typing convention and keeps the helpers usable standalone (control test `test_is_exempt_membership_logic` calls them directly with canonical sets). A `functools.partial` would add indirection without value. A dataclass would be overkill for two sets. **Pattern is correct; matches sibling.**

**Q2: Test naming.**
- `test_synthetic_aliased_import_detected` ✓ (matches `test_synthetic_*` shape).
- `test_extract_alias_maps_canonical_only_when_no_import` — uses `test_extract_*` shape, similar to existing `test_extract_csrf_exemptions_parses_both_structures`. ✓.

**Q3: Documentation parity.**
The new helper has a docstring explicitly citing the Gate B v2 P1-B finding. The two new tests have docstrings explicitly citing task #125. Matches the sibling-gate pattern from #124. ✓.

---

## Findings

### P2-A (advisory): document function-local-import behavior

`_extract_alias_maps` uses `ast.walk` which includes function-nested ImportFroms (e.g., main.py:2597). Today this is harmless — no `Depends(<local-alias>)` callsite exists — but the behavior is silently permissive. **Recommend:** add a one-sentence comment to `_extract_alias_maps` stating "walks ALL ImportFrom nodes including function-nested; aliases bound only inside a function still affect file-wide detection. This is intentional: any `Depends(<alias>)` at file scope using such an alias is unreachable Python, so detection-side over-inclusion has no false-positive surface."

### P2-B (advisory): APIRouter-alias residual gap

`_extract_router_prefixes` does not handle `from fastapi import APIRouter as <alias>`. **Zero current callsites** use this shape, but it is a parallel gap to the Depends/bearer alias hardening. **Recommend either:** (a) extend `_extract_router_prefixes` symmetrically in a small follow-up commit, OR (b) file a TaskCreate `#125-followup` and reference it in the test docstring so it doesn't get lost.

### Not findings (considered, dismissed):

- **YAGNI on defense-in-depth helper:** dismissed — Gate B v2 explicitly scoped this work to #125; deferring would be a process violation.
- **`import fastapi; fastapi.Depends(...)` not handled:** dismissed — zero callsites + the Attribute branch already handles the pattern.
- **Performance:** dismissed — extractor runs once per file at test-time, negligible cost.

---

## Gate B preview (advisory for the close)

When Gate B v1 runs on this patch, it MUST:
1. Re-run the 8/8 target sweep + full 236-test sweep (Session 220 lock-in: NO diff-only).
2. Verify the two P2s above are EITHER closed in the same commit OR carried as named TaskCreate followups in the same commit body.
3. Confirm no regression in the #124-tracked KNOWN_BLOCKED_DEAD_ROUTES disposition.

---

## Verdict (restated)

**APPROVE-WITH-FIXES (P2 only) — merge unblocked.**

The two P2s are documentation / follow-on items, not correctness issues. The patch correctly closes the Gate B v2 P1-B finding without introducing new false-positive surface. Runtime evidence (8/8 + 236/236) confirms.

— Coach, 2026-05-12
