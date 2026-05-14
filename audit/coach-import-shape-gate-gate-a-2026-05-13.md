# Gate A — Import-Shape Regression Test (Task #72 FU-3)

**Topic:** CI gate for bare-import-in-package-context (sites.py:4231 → adb7671a class)
**Gate:** A (pre-execution)
**Date:** 2026-05-13
**Lenses:** Steve / Maya / Carol / Coach / Auditor / PM / Attorney

---

## 150-word summary

Author proposes an AST-based CI gate that walks every backend `.py` file
looking for function-scope `from <local_module> import …` statements
that are NOT wrapped in a `try / except ImportError` fallback against a
sibling relative-or-prefixed sibling. The class to be closed is the
adb7671a outage: bare imports work in dev (cwd=backend/) but blow up in
prod (cwd=/app, package=dashboard_api). The design holds. Probing
ran against 154 backend `.py` files and the strict-shape classifier
identifies exactly **3 truly-bare callsites** (audit_report.py:213
literal `from baa_status import`, and 2 nested-Try false-positives in
assertions.py:410 + sites.py:4506 that the classifier needs to learn).
Manifest of "local modules" derives mechanically from the directory
listing (~150 modules). Recommendation: **hard-fail on NEW**, with a
small allowlist for the 1 known-true bare callsite to be fixed in the
same commit. ~1 hour to ship. **VERDICT: APPROVE-WITH-FIXES.**

---

## Pre-Gate probe results

Walked every `.py` in `mcp-server/central-command/backend/` (154 files,
exclude `test_*.py`). Found **79 function-scope local imports**.
Strict-shape classifier:

- **74 are guarded** — wrapped in `try/except ImportError` with a
  relative (`.module`) primary or a `dashboard_api.module` primary.
- **5 unguarded** by strict classifier, but only **1 truly bare**:
  - `audit_report.py:213` — `from baa_status import is_baa_on_file_verified`
    in `verify_audit_readiness()`. **TRUE BARE — must fix.**
  - `assertions.py:410, 1709, 1759` — `from bg_heartbeat import get_heartbeat`
    inside an OUTER `try / except ImportError` whose body is itself a
    `try / except ImportError`. Classifier false-positive (must learn
    nested-Try chains).
  - `sites.py:4506` — `from appliance_relocation import` inside
    `except ImportError:` whose sibling Try.body has
    `from dashboard_api.appliance_relocation import` (absolute-prefixed,
    not relative). Classifier false-positive (must accept
    `dashboard_api.X` as a valid sibling for `X`).

So the gate needs **2 classifier refinements** before fix-vs-allowlist
counts are accurate. After refinements: **1 true-bare callsite**
(audit_report.py:213). FU-5 (P3) of the original retro already named
audit_report.py:213 — confirmed here.

---

## Per-lens verdict

### 1. Engineering (Steve) — APPROVE-WITH-FIXES

Design is correct: AST `ImportFrom` where `level==0`, module top-token
∈ local-manifest, contained inside `FunctionDef`/`AsyncFunctionDef`,
NOT covered by an `ImportError`-catching sibling Try.

**Edge cases the gate MUST handle:**

1. **Nested Try chains** (assertions.py:410 shape):
   ```python
   try:
       from .bg_heartbeat import get_heartbeat
   except ImportError:
       try:
           from dashboard_api.bg_heartbeat import get_heartbeat
       except ImportError:
           from bg_heartbeat import get_heartbeat   # ← inside nested except
   ```
   Classifier must walk up: if ancestor chain is
   `ExceptHandler → Try → ExceptHandler → Try` and ANY Try.body in the
   chain has a relative or `dashboard_api.X` import of the same top
   module, the leaf bare is guarded.

2. **`dashboard_api.X` as sibling fallback** (sites.py:4506 shape):
   The known-good shape is BIDIRECTIONAL. Some files do
   `try: from .X / except: from dashboard_api.X / except: from X`
   (3-level fallback) — and some do the inverse. Both work in prod.
   The gate must accept BOTH `.X` (relative) AND `dashboard_api.X`
   (absolute-package-prefixed) as a valid "primary" sibling.

3. **Class-method imports** — `ast.ClassDef → FunctionDef` chain.
   Walk-up to the nearest function ancestor handles this naturally;
   the gate ignores class bodies (which run at import time).

4. **Lambdas / comprehensions** — Python forbids `from X import` in
   these contexts at the grammar level, no edge case.

5. **`__init__.py` package imports** — `from . import X` is
   `level=1, module=None` — already excluded by `node.module` check.

**Manifest source-of-truth:** `os.listdir('backend/')` filtered to
`.py` and stripped of `.py` — mechanical, no maintenance. ~150
entries. Plus `dashboard_api` prefix recognition.

**Stdlib + third-party detection:** maintain a STDLIB_TOPS frozenset
(small, well-known) and a THIRDPARTY_TOPS frozenset (from
`requirements.lock` parsing, OR a curated list of ~30 entries). Any
top-token not in either set AND in `LOCAL_TOPS` triggers the gate.

**Ratchet rejected; hard-fail preferred** — see Coach below.

---

### 2. Database (Maya) — N/A

No SQL surface. Skipping.

---

### 3. Security (Carol) — APPROVE

Static AST does not bypass `__import__()` / `importlib.import_module()`
dynamic imports, BUT those are a different class (different failure
mode, different shape, and almost always at module level). No
security concern with the gate's narrower scope.

**Counsel Rule 3 framing (cited in brief):** correct. The adb7671a
fix restored D1 heartbeat-signature verification — which IS a
privileged-chain attestation step. A bare-import outage silently
disabled it for ~3 weeks. The gate is a structural close on the
"silent disablement of attestation step" class. Recommend the
commit body cite "Counsel Rule 3 structural close."

---

### 4. Coach — APPROVE; recommend HARD-FAIL not ratchet

**Today's bare-callsite count is 1** (audit_report.py:213 — the
known-from-retro FU-5 item). A ratchet baseline at 1 invites
"comment out the assertion to make CI green" later. With effectively
zero callsites, hard-fail-on-NEW is the cheaper and stronger contract:

- **If gate finds a bare local import in any function scope:** fail
  with a clear message naming file:line, module, and pointing at
  the relative-then-absolute-fallback recipe.
- **One allowlist entry** for `audit_report.py:213` IF (and only if)
  the fix isn't shipped in the same commit. Default plan: ship the
  fix in the same commit as the gate, no allowlist needed.

**Sweep-execution rule (Session 220 lock-in):** Gate B fork MUST run
the full pre-push sweep including this new test, AND must verify
the audit_report.py:213 fix renders correctly under
`python -c "from dashboard_api.audit_report import …"` against the
production package shape. Diff-only review is auto-BLOCK.

**Gate B follow-up:** add a literal-shape test that simulates the
production import path — `importlib.import_module("dashboard_api.audit_report")`
in a subprocess with `PYTHONPATH=mcp-server/central-command`. Catches
ANY future regression where a new module breaks the package-context
resolution path (not just bare imports — also circular deps, typos,
etc.).

---

### 5. Auditor (OCR) — N/A

No customer-facing artifact. Skipping.

---

### 6. PM — APPROVE

**Effort:** ~1 hour:
- 25 min: write `tests/test_no_bare_local_function_scope_imports.py`
  with AST walk + 2 classifier refinements (nested Try chains +
  `dashboard_api.X` sibling acceptance).
- 15 min: fix audit_report.py:213 with the standard 3-level fallback.
- 10 min: smoke test against the 79 callsites — should be 0 failures
  after the audit_report.py fix.
- 10 min: add to `.githooks/pre-push` SOURCE_LEVEL_TESTS curated list
  (Session 220 lock-in — every Gate B sweep runs it).

**Followups (carry as named tasks, not in-scope here):**
- Gate B literal-shape subprocess test (per Coach above).
- Sweep `appliance/` Go imports for an analogous class — skip, Go
  resolution is fundamentally different (compile-time, vendored).
- Sweep `agent/` workstation agent Python — none, agent is Go.

---

### 7. Attorney (in-house counsel) — APPROVE

**Counsel Rule 3** (no privileged action w/o attested chain): the
adb7671a class is a *silent disablement of an attestation
verification step* — the chain was nominally implemented but
inert in prod. Structurally closing the bare-import class is a
Rule-3 hygiene improvement. Recommend the commit body explicitly
cites Rule 3.

**Counsel Rule 5** (no stale doc may outrank current posture): not
applicable.

No new privacy / disclosure exposure introduced by the gate itself.

---

## AST traversal sketch

```python
import ast, os
from pathlib import Path

BACKEND = Path(__file__).parent.parent / "mcp-server/central-command/backend"
LOCAL_TOPS = {f.stem for f in BACKEND.glob("*.py")}
STDLIB_TOPS = frozenset({"datetime","io","collections","os","sys","json",
    "typing","functools","itertools","re","time","uuid","hashlib","base64",
    "secrets","asyncio","contextlib","dataclasses","enum","pathlib", ...})
THIRDPARTY_TOPS = frozenset({"sqlalchemy","fastapi","starlette","pydantic",
    "asyncpg","redis","stripe","sendgrid","minio","weasyprint","nacl",
    "cryptography","jose","jwt","httpx","aiohttp","requests",
    "prometheus_client","jinja2","reportlab", ...})

# ALLOWLIST is empty by default. Add named callsites only with explicit
# justification + a TODO referencing a fix task.
ALLOWLIST: dict[str, set[int]] = {
    # "audit_report.py": {213},   # — fixed in same commit, allowlist empty
}

def _parents_map(tree):
    return {child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)}

def _enclosing_func(node, parents):
    p = parents.get(node)
    while p is not None:
        if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return p
        p = parents.get(p)
    return None

def _is_importerror_handler(h: ast.ExceptHandler) -> bool:
    if h.type is None: return True
    if isinstance(h.type, ast.Name) and h.type.id in (
        "ImportError", "ModuleNotFoundError", "Exception", "BaseException"):
        return True
    if isinstance(h.type, ast.Tuple):
        return any(isinstance(e, ast.Name) and e.id in (
            "ImportError","ModuleNotFoundError","Exception","BaseException")
            for e in h.type.elts)
    return False

def _try_body_has_sibling_import(try_body, top: str) -> bool:
    """A guarding sibling is either:
       (a) relative ImportFrom of same top (level >= 1), OR
       (b) absolute ImportFrom of dashboard_api.<top>."""
    for stmt in try_body:
        if isinstance(stmt, ast.ImportFrom) and stmt.module:
            if stmt.level >= 1 and stmt.module.split('.')[0] == top:
                return True
            if stmt.level == 0 and stmt.module.split('.')[0:2] == ['dashboard_api', top]:
                return True
    return False

def _is_guarded(node: ast.ImportFrom, parents) -> bool:
    """Walk up nested Try / ExceptHandler chains. If any ancestor Try
    has a sibling-Try.body containing a relative-or-prefixed import
    of node's top module, this leaf is guarded."""
    top = node.module.split('.')[0]
    cur = parents.get(node)
    while cur is not None:
        if isinstance(cur, ast.Try):
            if _try_body_has_sibling_import(cur.body, top):
                # confirm path from node descends through an ExceptHandler
                # of this Try (or this Try.body itself)
                return True
        cur = parents.get(cur)
    return False

def check_file(path):
    src = path.read_text()
    tree = ast.parse(src)
    parents = _parents_map(tree)
    bare = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ImportFrom)
                and node.module and node.level == 0):
            continue
        if _enclosing_func(node, parents) is None:
            continue
        top = node.module.split('.')[0]
        if top in STDLIB_TOPS or top in THIRDPARTY_TOPS:
            continue
        if top not in LOCAL_TOPS:
            continue
        if _is_guarded(node, parents):
            continue
        if node.lineno in ALLOWLIST.get(path.name, set()):
            continue
        bare.append((path.name, node.lineno, node.module))
    return bare

def test_no_bare_local_function_scope_imports():
    failures = []
    for f in sorted(BACKEND.glob("*.py")):
        if f.name.startswith("test_"): continue
        failures.extend(check_file(f))
    if failures:
        msg = "\n".join(f"  {fn}:{ln}  from {mod} import …" for fn, ln, mod in failures)
        raise AssertionError(
            "Bare function-scope imports of LOCAL modules detected. These "
            "fail in production (package context, cwd=/app). Wrap each in:\n"
            "    try:\n"
            "        from .MODULE import …\n"
            "    except ImportError:\n"
            "        from MODULE import …  # type: ignore\n\n"
            f"Offenders:\n{msg}")
```

---

## Manifest-list size estimate

- **LOCAL_TOPS:** 154 entries (one per `backend/*.py`), mechanically
  derived — no maintenance burden.
- **STDLIB_TOPS:** ~50 entries (curated, frozen list — Python stdlib
  doesn't change frequently).
- **THIRDPARTY_TOPS:** ~30 entries (curated from `requirements.lock` —
  CI gate could optionally validate against pip metadata, but probe
  showed manual list is reliable).
- **ALLOWLIST:** **0 entries** if audit_report.py:213 is fixed in
  same commit. Keep at 0 — empty allowlist is the contract.

---

## Recommendation: **HARD-FAIL on NEW**, not ratchet

- Today's true-bare count is 1.
- Fix it in the same commit (~30 LOC change, audit_report.py).
- Ship with empty allowlist.
- Future regressions fail loudly at pre-push and CI.

This is the cheapest enforcement contract and avoids the "ratchet
silently drifts up because everyone treats it as 'acceptable
baseline'" failure mode.

---

## P0 / P1 / P2 findings

**P0:** None.

**P1 — fix in same commit (Gate A requirement):**
1. Classifier MUST walk nested Try chains (assertions.py:410 shape).
   Naive direct-parent check yields false positives.
2. Classifier MUST accept `dashboard_api.X` as a valid sibling
   fallback for `X` (sites.py:4506 shape). Both shapes work in prod.

**P2 — Gate B carry-forward (named followup tasks):**
3. Add a subprocess test: `importlib.import_module("dashboard_api.audit_report")`
   under `PYTHONPATH=mcp-server/central-command` to catch any FUTURE
   module that breaks the production package resolution path
   (broader class than just bare-imports — also typos, circular deps,
   missing dependencies).
4. Audit `appliance/` Go imports for analogous "lazy import resolves
   differently in prod" class — skip, Go is compile-time.

**P3 — observability:**
5. Add the new test to `.githooks/pre-push` SOURCE_LEVEL_TESTS curated
   list per Session 220 lock-in (every Gate B sweep runs it).

---

## Final overall verdict: **APPROVE-WITH-FIXES**

Ship the gate. Fix the 2 classifier refinements (nested-Try chains
+ `dashboard_api.X` sibling acceptance) before declaring done.
Fix audit_report.py:213 in the same commit so allowlist is empty.
Carry P2 #3 + #5 as named TaskCreate follow-ups in the same commit
body. Gate B fork MUST run the full pre-push sweep, NOT just review
the diff.

**Effort:** ~1 hour total (gate + 1 source fix + 2 classifier
refinements). Worth the cost — closes a Counsel-Rule-3-adjacent
structural class with empty-allowlist contract.
