# Gate A — Broaden NULLABLE_RATES Anchor (Task #71)

**Date:** 2026-05-13
**Subject:** `tests/test_healing_metrics_none_guard.py` — replace hardcoded `NULLABLE_RATES = {"healing_rate", "order_rate"}` set with AST-inferred set based on `NAME = (... if ... else None)` assignment shape.

---

## 150-word Summary

The 1-minute prototype proves the inferred-set approach is **technically sound and meaningfully broader**: a tight AST scan across 6 backend modules found 49 nullable-conditional assignments and 7 `round(NAME, ...)` callsites where NAME was inferred-nullable — 6 already guarded, 1 raw hit at `routes.py:4875` which on manual inspection is a false positive (locally reassigned `else 0`, not `else None`, within the same function). The class is real but small: today 2 production-confirmed nullable rates, ~5 plausible future candidates. Recommend **implement now** with a scope-aware (per-function) inference walk to suppress the false positive, anchored on `db_queries.py` only initially (matches today's actual production outage class), with a path to broaden module coverage when the next nullable-rate prod incident lands. Effort: ~45min including the per-function scope fix. Steve APPROVE-WITH-FIXES, Coach APPROVE, PM APPROVE. **Overall: APPROVE-WITH-FIXES.**

---

## Per-Lens Verdict

### 1. Engineering (Steve) — APPROVE-WITH-FIXES

**AST traversal is reliable** for the target shape. The node graph is:

```
ast.Assign(
  targets=[ast.Name(id="healing_rate")],
  value=ast.IfExp(
    test=ast.Compare(...),                    # total_incidents > 0
    body=ast.BinOp(...),                      # resolved / total * 100
    orelse=ast.Constant(value=None),          # else None
  )
)
```

**Detection sketch:**

```python
nullable_names_per_func: dict[ast.FunctionDef, set[str]] = {}
for func in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
    names = set()
    for sub in ast.walk(func):
        if (isinstance(sub, ast.Assign)
            and len(sub.targets) == 1
            and isinstance(sub.targets[0], ast.Name)
            and isinstance(sub.value, ast.IfExp)
            and isinstance(sub.value.orelse, ast.Constant)
            and sub.value.orelse.value is None):
            names.add(sub.targets[0].id)
    nullable_names_per_func[func] = names
```

Then for each `round(NAME, ...)` call, locate its enclosing FunctionDef and check membership in that function's `nullable_names`.

**Edge cases (rank-ordered):**
1. **Cross-function false positive** — the prototype found `routes.py:4875 compliance_score` unguarded, but in that function it was assigned `... if sc.get("has_data") else 0` (line 4853). Module-wide inference produces noise; **must be function-scoped**. This is the load-bearing fix.
2. **Tuple unpacking** (`a, b = (... if ... else None), 0`) — skip. Rare and adds AST complexity. Document as known gap.
3. **Augmented assignments** (`NAME += ...`) — irrelevant; doesn't produce conditional-None shape.
4. **Walrus / nested IfExp** (`NAME = ... if X else (... if Y else None)`) — orelse is itself an IfExp, not Constant(None). Skip; rare in this codebase.
5. **Multi-line conditional expressions** — Python parses these into the same IfExp node regardless of source-line layout. AST is layout-agnostic. No special handling.
6. **Reassignment in same function** (`NAME = ... else None; NAME = 0`) — the inference over-reports. Cost is a false-positive test failure. Acceptable; suppress via inline `# noqa` if it ever fires.

**Verdict:** APPROVE-WITH-FIXES — implement with per-function scoping, NOT module-wide.

### 2. Database (Maya) — N/A.

### 3. Security (Carol) — N/A.

### 4. Coach — APPROVE (narrow scope)

**The hardcoded-set vs inferred-set tradeoff:**

| Dimension | Hardcoded `{"healing_rate", "order_rate"}` | AST-inferred |
|---|---|---|
| Today's production class | covered | covered |
| Future regression in NEW nullable rate | **MISSED** | caught |
| False positives | none possible | possible without scope-aware impl |
| Maintenance | add to set on each new rate | none |
| Lines of test code | 1 set literal | ~20 AST walk |

**Quantitative basis for "is this worth it":**
- Today: 2 production-confirmed nullable rates (`healing_rate`, `order_rate`)
- Existing codebase: **29 `... if ... > 0 else None` patterns** in backend across 8 modules
- 7 already feed into `round()` calls (6 properly guarded, 1 in-function-safe false positive)
- The pattern is idiomatic for the team — this is a recurring class, not a one-off

**The 5-rate threshold question:** today there are already 7 confirmed instances of the exact shape (round-of-nullable). Future scaling: each new compliance-metric endpoint adds 1-2. Crosses the "worth automating" line.

**Coach concern (and mitigation):** broader inference adds a test that can fail for unrelated refactors (false positives become blast radius). Mitigation: scope to `db_queries.py` initially — that's where today's prod outage hit. Expand module coverage when the next prod incident lands in a different file, with confidence from the first deployment.

**Verdict:** APPROVE — scope-narrowed (db_queries.py only, per-function inference). Defer module expansion to demand-driven rollout.

### 5. Auditor (OCR) — N/A.

### 6. PM — APPROVE

- Effort with scope-aware fix: ~45 min (15 prototype + 20 per-function refactor + 10 test the test)
- Recurrence rate: 2 confirmed production hits, ~5 plausible future candidates
- Risk: false-positive blast radius mitigated by db_queries.py scoping
- Opportunity cost: low; this is followup-task hygiene, not blocking feature work

**Verdict:** APPROVE — fits in the natural close-out window for the today's prod fix.

### 7. Attorney — N/A.

---

## Recommendation: IMPLEMENT NOW (with scope-aware fix)

**Concrete deliverable:**

Replace the `NULLABLE_RATES = {"healing_rate", "order_rate"}` set in `test_no_bare_round_on_potentially_none_metric_in_db_queries` with a per-function AST-inferred set. Keep the test scoped to `db_queries.py`. Sketch:

```python
def test_no_bare_round_on_potentially_none_metric_in_db_queries():
    src = (_BACKEND / "db_queries.py").read_text()
    tree = ast.parse(src)
    bare_rounds: list[str] = []

    for func in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
        # Infer nullable names in THIS function's scope
        nullable_names = set()
        for sub in ast.walk(func):
            if (isinstance(sub, ast.Assign)
                and len(sub.targets) == 1
                and isinstance(sub.targets[0], ast.Name)
                and isinstance(sub.value, ast.IfExp)
                and isinstance(sub.value.orelse, ast.Constant)
                and sub.value.orelse.value is None):
                nullable_names.add(sub.targets[0].id)

        # Check round(NAME, ...) callsites in same function
        for sub in ast.walk(func):
            if not (isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Name)
                    and sub.func.id == "round"):
                continue
            if not sub.args or not isinstance(sub.args[0], ast.Name):
                continue
            rate_name = sub.args[0].id
            if rate_name not in nullable_names:
                continue
            line = src.splitlines()[sub.lineno - 1]
            if "is not None" not in line:
                bare_rounds.append(
                    f"db_queries.py:{sub.lineno} in {func.name}() — "
                    f"round({rate_name}, ...) without `is not None` "
                    f"guard: {line.strip()}"
                )

    assert not bare_rounds, (
        "Unguarded round() on inferred-nullable variable. The variable "
        "was assigned via `... else None` upstream in the same function. "
        "Add `if NAME is not None else 0.0` guard.\n"
        + "\n".join(bare_rounds)
    )
```

**Keep test #1 (literal-string pin) unchanged** — it remains a load-bearing regression pin against today's specific outage, independent of the broader inference walk. Defense-in-depth.

**Do NOT expand to other backend modules yet** — the false-positive blast radius across `routes.py` (16k+ lines) is real, and we don't have a production incident driving rollout there. Add when needed.

---

## Final Overall Verdict: APPROVE-WITH-FIXES

Implement with two non-negotiable fixes from Steve's lens:
1. **Per-function scope** (not module-wide) — eliminates the routes.py:4875 false-positive class.
2. **Keep test scoped to db_queries.py** — Coach narrowing; expand later by production demand.

Effort fits the close-out window for today's healing-metrics None-guard fix. The inferred-set approach is meaningfully broader than the hardcoded set (catches FUTURE nullable rates the team adds without remembering to update the test), and the recurrence rate (~7 confirmed callsites today, idiomatic team pattern) justifies the ~45min spend.
