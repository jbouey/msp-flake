# Gate A retroactive verdict — jsonb_build_object($N) cast pin gate (2026-05-11)

**Verdict:** APPROVE-WITH-FIXES

## Findings

### P1 — Coach — gate is NOT in pre-push allowlist
`.githooks/pre-push SOURCE_LEVEL_TESTS` does not reference `test_jsonb_build_object_param_casts.py`. Sibling pin gates (`test_email_opacity_harmonized.py`, etc.) earn the fast lane; this one will only fire in the full sweep (`PRE_PUSH_SKIP_FULL=1` skips it entirely). Follow-up: add to curated list.

### P1 — Steve — sibling-function blind spot
Regex `_JSONB_BUILD = r"jsonb_build_object\s*\("` does NOT catch `jsonb_set($N, ...)`, `to_jsonb($N)`, `jsonb_insert`, `jsonb_build_array($N)`, `json_build_object`. Same `IndeterminateDatatypeError` class. Maya raised the same concern. Follow-up: extend regex to alternation `(jsonb_build_object|jsonb_build_array|json_build_object|jsonb_set|jsonb_insert|to_jsonb)\s*\(` and re-scan.

### P1 — Steve — `_ALLOWED_CASTS` missing common types
No `inet`, `cidr`, `varchar`, `oid`, `xml`, `tsvector`, `citext`, custom enums. A future legitimate callsite using `$1::inet` (network audit metadata is plausible — see Layer-2 leak vetos) would fail the gate, pushing devs to remove the cast. Follow-up: add `inet`, `cidr`, `varchar`, `oid`, `citext`; document the "add-with-citation" rule in the test docstring (already half there).

### P2 — Steve — whitespace between `$N` and `::cast`
`re.match(r"::(\w+)", after)` rejects `$1 ::text` and `$1/*c*/::text`. SQL accepts both; pgFormatter occasionally normalizes to space-before-cast. Low likelihood in this codebase (no current callsite uses it) — accept the strictness as forcing a single canonical form. No action.

### P2 — Carol — TOCTOU on `/tmp/test_jsonb_synthetic_*.py`
Lines 130, 150, 172 write to fixed `/tmp/` paths. Parallel pytest runs (`PRE_PUSH_PARALLEL=6` is a documented tunable) will collide; one test's `tmp.unlink()` deletes another's input. Follow-up: switch to `tempfile.NamedTemporaryFile(suffix=".py", delete=False)` or `tmp_path` fixture.

### P2 — Carol — runtime-templated SQL escapes static gate
`f"jsonb_build_object('k', ${n})"` evades the regex. No such pattern in current backend (verified). Document as known limitation, not a P0; runtime SQL templating is independently banned by the SQL-injection class.

### P2 — Coach — error message cites placeholder commit SHA
Line 119: `"commit fbf… in 2026-05-11"` — ellipsis, not a real SHA. Fix to `c2c28b69` (or whichever commit ships the gate) so future devs can `git show` the rationale.

## Per-lens analysis

### Steve
Balanced-paren walker (`_extract_body`, line 50-64) correctly handles nested calls — verified against `jsonb_build_array(jsonb_build_object(...))` at `appliance_relocation_api.py:186` which the gate currently passes. Multi-line spans (`journal_api.py:186-193`) work because `re.MULTILINE` + body extraction is char-by-char. Aliased calls (`AS meta`) are irrelevant — gate scans the function name itself. `_BACKEND.rglob("*.py")` correctly walks the backend root and the exclusion list (`/tests/`, `test_`, `/migrations/`) is right.

### Maya
False-negative risk is real but bounded — the sibling-function gap (P1 above) is the load-bearing concern. `journal_api.py:186` (the original prod-leak) and `fleet_cli.py:363` (fleet-order cross-link) are both covered by the current regex. `appliance_relocation_api.py:201` is a literal-only call correctly NOT flagged (verified by `test_no_static_only_jsonb_build_object_is_flagged`).

### Carol
TOCTOU + runtime-template caveats above. The scan exclusion `if "/tests/" in str(py_file) or py_file.name.startswith("test_")` correctly skips test files (which legitimately synthesize bad shapes for negative controls).

### Coach
Idiom matches sibling pin-gate shape (positive control + negative control + literal-edge-case). Sibling-comparison cite in module docstring is good. Pre-push omission (P1) is the only sibling-parity violation.

## Carve-out justified?

**Partially.** The "static CI pin gate matching sibling shape" carve-out would have caught the regex correctness + balanced-paren handling without Gate A, but it MISSED two structural items only fork-eyes catch: pre-push integration parity (P1 Coach) and sibling-function regex coverage (P1 Steve/Maya). Net: the carve-out is too permissive — even static pin gates deserve Gate A for sibling-coverage + ecosystem integration. Recommend retiring the carve-out.

## Recommendation

APPROVE-WITH-FIXES. Ship a follow-up commit closing P1×3 (pre-push list, regex alternation for sibling jsonb functions, `_ALLOWED_CASTS` expansion) and P2×2 (tempfile, error-message SHA) in the same sprint. The prod-leak class is already closed; these are hardening + scope-broadening. Retire the "static pin gate skip-Gate-A" carve-out — it failed two checks here.
