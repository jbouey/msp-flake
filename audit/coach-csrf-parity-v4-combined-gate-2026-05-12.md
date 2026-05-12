# Coach Verdict — CSRF-Parity v4 Combined Gate A+B (Task #126)

**Date:** 2026-05-12
**Author:** Coach (fork-based adversarial review)
**Scope:** Single-file ~20-LoC extension adding `APIRouter as <alias>` detection to `tests/test_csrf_exempt_paths_match_appliance_endpoints.py`.
**Gates combined:** Gate A (pre-execution design) + Gate B (pre-completion as-implemented).
**Verdict:** **APPROVE**

---

## Combined-gate justification (Coach #5)

Combining Gate A + Gate B into a single fork IS justified for this change.

- Diff size: 20 LoC + 2 control tests in a single file.
- Real-codebase impact: **zero callsites** today — `grep -rn "from fastapi import.*APIRouter as" mcp-server/` returns only the docstring note in the same file.
- Risk profile: pure defense-in-depth, sibling to task #125 (which also had zero real-codebase impact but was split A/B because Gate B's separate job was to verify Depends-alias-tracking landed as designed in a multi-function refactor; #126 is a single-function extension).
- Sibling pattern compliance: Gate B's role of "verify AS-IMPLEMENTED ≠ design" is preserved here by Coach running both the target file's 10 tests AND the full pre-push sweep, NOT just reading the diff. Both source-verification (Steve #1-3) AND runtime-verification (Coach #6-7) cited below.

Separate forks for a 20-LoC change with zero current callsites would be theater. Combined-gate APPROVE means the implementation has been verified both at the design layer (Steve/Carol/Coach lenses applied) AND at the runtime layer (10/10 file + 237/237 sweep).

---

## Steve — source verification

### #1 — Patch landed as documented

Read `tests/test_csrf_exempt_paths_match_appliance_endpoints.py` end-to-end. Confirmed:

| Element | Location | Status |
|---|---|---|
| `_extract_apirouter_aliases(tree)` helper | lines 196-208 | PRESENT |
| Returns `{"APIRouter"} ∪ aliased_forms` | line 201 (`out: set[str] = {"APIRouter"}`) + line 207 (`out.add(alias.asname or alias.name)`) | PRESENT |
| `_extract_router_prefixes` invokes alias helper | line 215 (`router_class_names = _extract_apirouter_aliases(tree)`) | PRESENT |
| Name-form constructor check | line 226 (`func.id in router_class_names`) | PRESENT |
| Attribute-form constructor check | line 228 (`func.attr in router_class_names`) | PRESENT |
| `test_synthetic_apirouter_alias_detected` | lines 684-700 | PRESENT |
| `test_extract_apirouter_aliases_canonical_only_when_no_import` | lines 703-708 | PRESENT |
| `_extract_alias_maps` docstring updated | lines 260-261 ("tracked by `_extract_apirouter_aliases` (task #126 2026-05-12)") | PRESENT |

PATCH LANDED EXACTLY AS DOCUMENTED.

### #2 — Edge case: module-import `fastapi.APIRouter(...)`

`router_class_names` is **seeded** with the canonical `"APIRouter"` at line 201 (`out: set[str] = {"APIRouter"}`) **before** any alias loop runs. The Attribute branch at line 228 (`func.attr in router_class_names`) therefore still matches `fastapi.APIRouter(...)` because `func.attr == "APIRouter"` and `"APIRouter" ∈ router_class_names` unconditionally.

**Confirmed: backward-compatible with `import fastapi; router = fastapi.APIRouter(prefix=...)` shape.**

Note: this shape was supported in v3 and remains supported in v4. The `_extract_apirouter_aliases` helper does NOT scan plain `ast.Import` nodes (only `ast.ImportFrom`), which is fine because the seed-set already covers the canonical-attr case. No regression.

### #3 — Multi-alias single ImportFrom

`from fastapi import APIRouter as R, Depends as D, FileResponse as FR` produces ONE `ast.ImportFrom` node whose `.names` is a list of three `ast.alias` objects. Line 205 `for alias in node.names` iterates **all** of them, not just `node.names[0]`. The conditional `if alias.name == "APIRouter":` at line 206 selectively adds only the APIRouter alias to the set, ignoring the Depends and FileResponse aliases (the Depends alias is handled separately by `_extract_alias_maps`).

**Confirmed: multi-alias single ImportFrom works correctly.**

Sibling note: `_extract_alias_maps` at line 268 uses the same `for alias in node.names` iteration shape and was already exercised by `test_synthetic_aliased_import_detected` (task #125). The implementation patterns are consistent.

---

## Carol — threat model

The alias-aware detection closes the obvious 1-line bypass:

```python
# Pre-task #126 — silently escapes CSRF parity gate
from fastapi import APIRouter as R
router = R(prefix="/api/sneaky")

@router.post("/{site_id}/exfil")
async def exfil(_=Depends(require_appliance_bearer_full)):
    ...
```

Pre-patch, `_extract_router_prefixes` would see `R(prefix=...)` and the literal-string check `func.id == "APIRouter"` would return False, dropping the assignment. The handler's full path resolution would fail back to `deco_path` alone (`/{site_id}/exfil` — without the `/api/sneaky` prefix), so the EXEMPT_PATHS check would compare `/{site_id}/exfil` against actual exempt entries and pass (or fail noisily on the wrong path), not on `/api/sneaky/{site_id}/exfil`. The real risk class: the gate's full-path scan becomes inaccurate for any router using the aliased shape, which means future EXEMPT_PATHS curation drifts from reality.

Supply-chain hardening argument from task #125 applies identically: defense-in-depth against a future contributor adopting the alias shape (common in larger FastAPI codebases that import multiple symbols). Zero current callsites is a feature, not a critique — closing the class before it appears.

**Carol verdict: APPROVE. Threat model unchanged from #125; this closes the symmetric router-side gap.**

---

## Coach — full process verification

### #6 — Target file test execution

Command run: `python3 -m pytest tests/test_csrf_exempt_paths_match_appliance_endpoints.py -v --tb=short`

Result: **10 passed, 4 warnings in 6.45s**

All ten tests including the two new task #126 controls:
- `test_synthetic_apirouter_alias_detected PASSED`
- `test_extract_apirouter_aliases_canonical_only_when_no_import PASSED`

(The 4 SyntaxWarnings are pre-existing regex escape-sequence noise from the gate parsing main.py, not from this patch.)

### #7 — Full pre-push sweep

Command run: `cd /Users/dad/Documents/Msp_Flakes && PRE_PUSH_SKIP_FULL=0 .githooks/full-test-sweep.sh`

Result: **237 passed, 0 skipped (need backend deps)**

Zero skipped tests, zero failures. The Session 220 lock-in rule "Gate B MUST run the full pre-push test sweep, not just review the diff" is satisfied: I executed `.githooks/full-test-sweep.sh` myself (not just inspected the diff) and cite the exact tally. This closes the diff-only-review antipattern that landed three deploy outages this session.

---

## Findings

| Severity | Count | Details |
|---|---|---|
| P0 | 0 | — |
| P1 | 0 | — |
| P2 | 0 | — |

No findings. The patch is minimal, narrowly scoped, AST-pure (no imports of fastapi), backward-compatible with prior router-construction shapes (canonical `APIRouter(...)` + module-import `fastapi.APIRouter(...)`), exercised by two new positive/negative control tests, and verified at runtime via both the target file (10/10) and the full sweep (237/237).

---

## Verdict

**APPROVE** — combined Gate A + Gate B. Commit may proceed.

Cite both gate verdicts in commit body per Session 219 lock-in rule. Suggested footer:

```
Gate A+B (combined fork-based adversarial review, 2026-05-12):
  audit/coach-csrf-parity-v4-combined-gate-2026-05-12.md
  Verdict: APPROVE. 10/10 target file pass. 237/237 full sweep pass.
  Steve/Carol/Coach lenses applied. Zero findings.
```

---

**Coach signature:** fork-based adversarial review, fresh context window, 2026-05-12.
