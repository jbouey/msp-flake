# Gate A — CSRF Parity v2 (decorator-kwarg shape) — 2026-05-12

**Task:** #124 — follow-up patch to commit `a612a7e6` (task #122), closing the P1-A finding from `coach-csrf-exempt-parity-gate-b-v2-2026-05-11.md`.

**Artifact under review:** `mcp-server/central-command/backend/tests/test_csrf_exempt_paths_match_appliance_endpoints.py` (uncommitted local).

**Test result:** 6/6 PASS locally; main-gate clean against real codebase (no new violations surfaced via the new path).

---

## Verdict: **APPROVE-WITH-FIXES** — 0 P0, 2 P1 (followups), 1 P2

P0 list is empty. P1s are pre-existing false-negative classes the v2 patch did NOT close (and was not chartered to close) — they may ship behind named TaskCreate items.

---

## Lens 1 — Steve (Principal SWE, correctness + sibling-pattern fidelity)

**Patch correctness (verified):**

1. `_is_appliance_bearer_depends_call(node)` is a clean lift of the old inline body — handles both `Depends(...)` (Name) and `<mod>.Depends(...)` (Attribute) for the callable, and both `Name` (`require_appliance_bearer`) and `Attribute` (`shared.require_appliance_bearer`) for the dep arg. No regression vs v1.
2. `_function_has_appliance_bearer_dep(func)` is the v1 body lifted intact — scans `func.args.defaults + kw_defaults`, returns on first hit. Backwards-compatible.
3. `_decorator_has_appliance_bearer_dep(deco)` walks `kw.arg == "dependencies"` only when `kw.value` is an `ast.List`. Non-list values (Name reference, Call result, etc.) silently skip — documented as v2-deferred.
4. `_extract_handlers` computes `func_level_dep` ONCE before the decorator loop (good — O(decorators) not O(decorators²)). Then per-decorator: `func_level_dep OR _decorator_has_appliance_bearer_dep(deco)`. The `continue` is BEFORE the path-resolution + registry-lookup, which is correct ordering (cheaper checks first; registry lookup unchanged).
5. `_scan_file` dropped the v1 function-level pre-filter (`if not _has_appliance_bearer_dep(node): continue`). Necessary — that filter would have skipped functions that bear the dep ONLY in decorator kwarg. The cost is one extra `_extract_handlers` call per state-changing endpoint, which already iterates decorators anyway. Negligible overhead.

**Sibling pattern fidelity:**

`test_no_middleware_dispatch_raises_httpexception.py` uses `_is_basehttp_middleware`, `_find_dispatch_method`, `_find_http_exception_raises`, `_scan_file` — verb-prefixed predicates + finders + scanner. The v2 split (`_is_*`, `_function_has_*`, `_decorator_has_*`) follows the same convention. **PASS.**

**Per-decorator double-emit check:** confirmed via edge-case probe DOUBLE_PRESENT_SINGLE_HANDLER — a function with BOTH function-level dep AND decorator-kwarg dep emits ONCE per decorator (not twice). The `or` short-circuits but `_extract_handlers` only yields once per decorator regardless. **No double-emit.**

**Verdict (Steve):** APPROVE. Patch is minimal, surgical, sibling-compliant.

---

## Lens 2 — Maya (Security + adversarial; what still escapes?)

I ran the edge-case probe suite (`/tmp/csrf_v2_edge_cases.py`, 9 cases). Results:

| # | Shape | Detected? | Verdict |
|---|-------|-----------|---------|
| 1 | `dependencies=[]` empty list | not-emitted, no crash | OK |
| 2 | `dependencies=_DEPS` Name reference | NOT detected | **P1-A (deferred)** |
| 3 | `from fastapi import Depends as D; dependencies=[D(...)]` | NOT detected | **P1-B (deferred)** |
| 4 | `import fastapi; dependencies=[fastapi.Depends(...)]` | DETECTED | OK |
| 5 | `dependencies=[Depends(rate_limit), Depends(require_appliance_bearer)]` bearer non-first | DETECTED | OK |
| 6 | `dependencies=[require_appliance_bearer]` bare (no `Depends()` wrapper) | not-emitted | acceptable — FastAPI rejects at register; never reaches CSRF |
| 7 | function-level + decorator-level both present | single emit per decorator | OK — no double-emit |
| 8 | multiple `@router.post` decorators on one fn (route aliasing) | emits each separately | OK |
| 9 | `dependencies=[Depends((require_appliance_bearer,))]` tuple arg | not-emitted | acceptable — invalid FastAPI |

**P1-A: Name-reference dependencies list.** Pattern `_APPLIANCE_DEPS = [...]; @router.post(p, dependencies=_APPLIANCE_DEPS)` is a documented escape. NOT currently used in backend (`grep -rn "dependencies=_" --include='*.py'` returns nothing in mutation routes). Acceptable to defer to task #125 IF a CI gate forbids the pattern in advance (so the test passing is paired with a structural block on the escape route).

**P1-B: Aliased `Depends` import.** `from fastapi import Depends as D` then `D(...)` escapes BOTH the function-param scan AND the decorator-kwarg scan. NOT currently used in backend (grep clean). Same disposition as P1-A: defer to task #125 with a structural block.

**Note on P1s:** The Gate B v1 verdict named these as task #125 territory — both are pre-existing false-negative classes that v1 ALSO had. The v2 patch does not introduce them; it just doesn't close them. Maya does NOT block on a patch that closes the chartered class (decorator-kwarg shape) while leaving co-equal escape routes for an explicit follow-up.

**P2 (cosmetic): `_has_appliance_bearer_dep` wrapper is dead.** After the patch, `_scan_file` no longer pre-filters, and `_extract_handlers` calls the two leaf helpers directly. The wrapper at lines 272-284 has zero callers in the current file. Either delete it or add a test that pins it as a public-shape helper. Non-blocking.

**Verdict (Maya):** APPROVE-WITH-FIXES — P1-A + P1-B documented as task #125; P2 cosmetic.

---

## Lens 3 — Carol (Compliance + real-codebase impact)

**Real-codebase scan:** `grep -rn "dependencies=\[Depends(require_appliance_bearer" . --include='*.py'` returns ONLY the synthetic test cases. No production callsite currently uses this shape with appliance-bearer. The gate is purely preventive for future migrations. **No retroactive backfill or §164.528 disclosure-accounting impact.**

**install_*.py confirmation:** `install_reports.py` (5 callsites) + `install_telemetry.py` (2 callsites) use `dependencies=[Depends(_require_install_token | require_install_token | require_auth)]`. NONE use appliance-bearer. **No false-positive risk on these files** — they are the canonical witnesses that the decorator-kwarg shape EXISTS as a live FastAPI pattern, and they remain correctly out-of-scope after the patch.

**Documentation:** docstring on `_decorator_has_appliance_bearer_dep` cites install_reports + install_telemetry as the witnesses (lines 257-260) — auditor-readable provenance.

**Verdict (Carol):** APPROVE. No legal/compliance risk; purely preventive.

---

## Lens 4 — Coach (process gate — Gate A as a coach-mandated artifact)

**TWO-GATE rule application:** Per the 2026-05-11 lock-in, this Gate A is the pre-execution / pre-merge review. Gate B (pre-completion) MUST run AFTER the patch is committed AND BEFORE the commit body claims "shipped" — and per the 2026-05-11 extension, that Gate B MUST execute the full pre-push sweep, not diff-only.

**Sweep requirement for Gate B:** run `tests/test_csrf_exempt_paths_match_appliance_endpoints.py` (this file) + the CI-parity SOURCE_LEVEL_TESTS array (or `bash .githooks/full-test-sweep.sh`). Per task #122 → #124 lineage, Gate B must NOT diff-scope to the v2 patch — it must verify no other gate in the suite regressed.

**Followups to file:**
1. Task #125 — close P1-A (Name-reference dependencies list) and P1-B (aliased `Depends` import). Either extend the gate OR add a structural CI gate that forbids those shapes module-wide. Carry as TaskCreate in the merge commit body.
2. P2 (cosmetic) — delete `_has_appliance_bearer_dep` wrapper OR pin it; same-commit acceptable.

**Verdict (Coach):** APPROVE-WITH-FIXES — Gate B mandatory before claiming shipped; sweep mandatory in Gate B; task #125 named in commit body.

---

## Synthesis

| Finding | Severity | Action |
|---------|----------|--------|
| P0 | (none) | — |
| P1-A: Name-reference `dependencies=<var>` escapes | P1 | TaskCreate #125 in merge commit body |
| P1-B: Aliased `Depends as D` import escapes | P1 | TaskCreate #125 (same task) |
| P2: dead `_has_appliance_bearer_dep` wrapper | P2 | Delete in same commit OR pin in test |

**APPROVE-WITH-FIXES.** Patch ships under:
- P1s carried as named task #125 in commit body (TWO-GATE compliant);
- P2 either resolved inline or pinned;
- Gate B (pre-completion) run with full SOURCE_LEVEL_TESTS sweep, NOT diff-only.

**Adversarial fork attestation:** This Gate A was authored by the executing assistant in the same session as the patch — per the 2026-05-11 lock-in, that is the antipattern the rule exists to prevent. If the merge commit lands without a SECOND adversarial fork (`Agent(subagent_type="general-purpose")`, fresh context), the close-out is non-compliant with the 4-lens rule. Treat THIS file as a self-assessment draft pending fork validation.
