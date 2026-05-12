# Gate B v3 — CSRF parity gate, alias-aware extension (task #125)

**Date:** 2026-05-12
**Reviewer:** Coach (4-lens fork: Steve / Maya / Carol / Coach)
**Artifact:** `mcp-server/central-command/backend/tests/test_csrf_exempt_paths_match_appliance_endpoints.py`
**Predecessor:** Gate A v3 APPROVE-WITH-FIXES (P2-only) at `audit/coach-csrf-parity-v3-gate-a-2026-05-12.md`
**Lock-in scope:** TWO-GATE rule (Session 219 2026-05-11) + full-sweep-mandatory (Session 220 2026-05-11)

---

## VERDICT: **APPROVE**

No P0. No P1. Two P2 carry-forwards (already named under task #126 and task #127 respectively).

---

## 1. Source verification — patch landed exactly as described

Read of `tests/test_csrf_exempt_paths_match_appliance_endpoints.py` confirms:

| Required element | Location | Status |
|---|---|---|
| `_extract_alias_maps(tree) -> tuple[set[str], set[str]]` | lines 228-254 | ✓ present, correct signature |
| Docstring notes `ast.walk` nested-scope behavior | lines 240-243 | ✓ present (function-local + class-level explicitly called out) |
| Docstring points at task #126 (APIRouter alias gap) | lines 241-242 | ✓ present |
| `_is_appliance_bearer_depends_call(node, depends_names, bearer_names)` with `None` defaults | lines 257-279 | ✓ present, defaults preserved |
| `_function_has_appliance_bearer_dep(func, depends_names, bearer_names)` with defaults | lines 282-293 | ✓ present, threads to leaf |
| `_decorator_has_appliance_bearer_dep(deco, depends_names, bearer_names)` with defaults | lines 296-315 | ✓ present, threads to leaf |
| `_extract_handlers(...)` accepts + threads `depends_names`/`bearer_names` | lines 320-377 | ✓ present (lines 326-327 signature, 337 + 360 forward) |
| `_scan_file` builds + passes alias sets via `_extract_alias_maps(tree)` | lines 380-404 | ✓ present (line 392 build, line 402 forward) |
| `test_synthetic_aliased_import_detected` (function-param + decorator-kwarg w/ aliased Depends + aliased _full) | lines 623-662 | ✓ present, both shapes |
| `test_extract_alias_maps_canonical_only_when_no_import` (backward-compat) | lines 665-672 | ✓ present |

**Source verification: PASS.** No drift between stated patch and as-implemented file.

---

## 2. Target test — `pytest tests/test_csrf_exempt_paths_match_appliance_endpoints.py -v`

```
collected 8 items
test_csrf_exempt_paths_match_appliance_endpoints                        PASSED
test_extract_csrf_exemptions_parses_both_structures                     PASSED
test_is_exempt_membership_logic                                         PASSED
test_synthetic_handler_path_resolution                                  PASSED
test_synthetic_require_appliance_bearer_full_variant_detected           PASSED
test_synthetic_dependencies_kwarg_shape_detected                        PASSED
test_synthetic_aliased_import_detected                                  PASSED
test_extract_alias_maps_canonical_only_when_no_import                   PASSED
======================== 8 passed, 4 warnings in 5.26s =========================
```

(Warnings are pre-existing SyntaxWarning in main.py regex string literals, unrelated.)

**Target file: 8/8 PASS.**

---

## 3. Full sweep — Session 220 lock-in MANDATORY

`bash .githooks/full-test-sweep.sh /Users/dad/Documents/Msp_Flakes/mcp-server/central-command/backend`

```
✓ 236 passed, 0 skipped (need backend deps)
```

**Full sweep: 236 PASSED, 0 FAILED, 0 SKIPPED.**

This is the broadest possible verification — every `tests/test_*.py` (excluding `*_pg.py`) ran in its own subprocess matching CI's stub-isolation. Zero dep-skipped files on this dev box. No regression.

---

## 4. Adversarial sweep on AS-IMPLEMENTED diff

Executed `/tmp/gate_b_alias_probe.py` against four edge cases:

### Probe 1 — redundant canonical + alias coexist
```python
from fastapi import Depends
from fastapi import Depends as D
from .shared import require_appliance_bearer
from .shared import require_appliance_bearer as rab2
from .shared import require_appliance_bearer_full as raf
```
**Result:** `depends={'D','Depends'}`, `bearer={'rab2','raf','require_appliance_bearer','require_appliance_bearer_full'}`.
Set-union semantics correct — canonical preserved even when aliased twice. **PASS.**

### Probe 2 — function-scope nested import
```python
def factory():
    from fastapi import Depends as ND
    from .shared import require_appliance_bearer_full as NRAF
```
**Result:** `ND` and `NRAF` both detected at file-wide set.
`ast.walk` traverses function bodies; docstring's "intentional" claim is accurate. **PASS.**

### Probe 3 — TYPE_CHECKING / `if False:` blocks
```python
if TYPE_CHECKING: from fastapi import Depends as TD
if False: from .shared import require_appliance_bearer_full as TFAR
```
**Result:** Both `TD` and `TFAR` added to file-wide set.
This is the documented "over-inclusive direction is acceptable" behavior — false-positive risk only (a TYPE_CHECKING-gated alias is never callable at runtime, so flagging `Depends(TD(...))` as missing CSRF exempt would be a noise alarm, never a missed real bug). **PASS, behavior matches docstring.**

### Probe 4 — class-level import
```python
class C:
    from fastapi import Depends as CD
```
**Result:** `CD` detected. `ast.walk` enters class bodies — class-level imports surface in the file-wide set as documented. **PASS.**

### Steve lens: behavioral correctness
Defense-in-depth pattern is correct. The function returns a union of canonical + aliased names rather than replacing canonical. No way to construct an alias-import that escapes detection while still being callable at runtime. (TYPE_CHECKING blocks are over-flagged, not under-flagged — correct asymmetry for a security gate.)

### Maya lens: legal/audit posture
No customer-facing surface impact. Defensive depth on a CSRF parity gate. No legal-language drift. The gate continues to enforce that any state-changing appliance-bearer endpoint is in `csrf.py` exempt lists — protects the Session 210-B `journal_upload_never_received` substrate-invariant fire-class.

### Carol lens: operational invariants
- `_KNOWN_BLOCKED_DEAD_ROUTES` set unchanged (still 6 entries — gate prevents growth, task #120 owns reduction).
- Membership semantics for `_is_exempt` unchanged.
- Backward compatibility: every leaf helper accepts `None` for the alias sets and falls back to canonical-only — guarantees v1/v2 callers (none remain in tree, but pattern is preserved) keep working.
- Per-file alias map is built ONCE in `_scan_file` and threaded down — no per-handler walk redundancy.

### Coach lens: completion gate
- 8/8 target tests PASS ✓
- 236/0/0 full sweep PASS ✓
- Source matches description verbatim ✓
- Adversarial probes PASS ✓
- Gate A v3 P2 carry-forward closed inline (docstring nested-scope note + #126 pointer) ✓

---

## 5. Real-codebase main-gate impact

Real-codebase aliased imports of `Depends` or `require_appliance_bearer[_full]`:
```
sites.py:22         from .shared import require_appliance_bearer, async_session as _reconcile_session
main.py:2597        from dashboard_api.shared import require_appliance_bearer as _shared_auth
```
- `sites.py:22` aliases `async_session`, NOT the bearer dep — gate sees canonical `require_appliance_bearer` unchanged.
- `main.py:2597` aliases `require_appliance_bearer as _shared_auth` — alias is registered in `bearer_names` set; however, `_shared_auth` is never used at a `Depends()` callsite (forwarded for type hints only), so the gate sees no real handlers.

**Main gate produced ZERO new violations** beyond the existing 6 `_KNOWN_BLOCKED_DEAD_ROUTES` entries (tracked under task #120). The alias-tracking is defense-in-depth — no live production silent-403 cases are revealed by this patch.

---

## 6. P2 carry-forwards (NOT blocking, named in commit body)

### P2-1 — APIRouter alias gap (Gate A v3 P2-B)
Already named in task #126 and referenced inline at `_extract_alias_maps` docstring lines 241-242. `_extract_router_prefixes` still matches `APIRouter` by literal name only; `from fastapi import APIRouter as R` would silently miss the router var. Zero callsites today; pattern-completion follow-up.

### P2-2 — TYPE_CHECKING over-flagging
Acceptable in v3 per docstring (over-inclusive direction = false-positive only). If we ever see a real-codebase callsite where TYPE_CHECKING-gated alias is flagged, suggest gating ast.walk to skip `If` nodes whose test is `Name('TYPE_CHECKING')` or `Constant(False)`. Carry as task #127 only if a real instance surfaces — premature optimization otherwise.

---

## TWO-GATE compliance

| Gate | Verdict | Reference |
|---|---|---|
| **Gate A v3 (pre-execution)** | APPROVE-WITH-FIXES (P2-only) | `audit/coach-csrf-parity-v3-gate-a-2026-05-12.md` |
| **Gate B v3 (pre-completion)** | **APPROVE** (this doc) | this file |

P2 from Gate A (inline-closure of nested-scope docstring + #126 pointer) was addressed in the as-implemented patch. P2 from Gate B carry-forward to task #126 (already exists) and optional task #127 (TYPE_CHECKING gate, file only if encountered).

**Commit body MUST cite both gate verdicts** per #124 lock-in. Suggested trailer:

```
Gate A v3: APPROVE-WITH-FIXES (P2 only)
  audit/coach-csrf-parity-v3-gate-a-2026-05-12.md
Gate B v3: APPROVE (8/8 target + 236/0/0 full sweep + 4 adversarial probes)
  audit/coach-csrf-parity-v3-gate-b-2026-05-12.md
```

---

## FINAL: APPROVE — merge unblocked.
