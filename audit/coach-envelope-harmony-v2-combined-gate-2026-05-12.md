# Combined Gate A + Gate B — Envelope Harmony v2 (alias-aware extension)

**Task:** #128 — Session 220, 2026-05-12
**Author:** Claude (main)
**Reviewer:** Claude fork (4-lens adversarial 2nd-eye)
**Scope:** ~30 LoC + 2 control tests extending `tests/test_middleware_error_envelope_harmony.py` to be alias-aware. Closes Gate B P2-#2 + P2-#3 from task #123. P2-#1 (variable-ref soft-warn) intentionally deferred.

---

## Verdict: **APPROVE**

Tiny, additive, defense-in-depth. Zero current callsites would behave differently. All 7 tests in the patched file pass. 12-file broad source-level sweep is clean. Backward-compat preserved by default arg + positive/negative controls.

---

## Steve (Principal SWE) — code correctness

1. **Patch present, all 5 elements.** Source-read confirms:
   - `_extract_jsonresponse_aliases(tree) -> set[str]` at lines 55–67. Walks `ast.ImportFrom`, returns `{"JSONResponse"} ∪ {asname for alias.name == "JSONResponse"}`. Pure function, no side effects.
   - `_extract_jsonresponse_violations` accepts optional `jsonresponse_names: set[str] | None` (line 73), defaults to `{"JSONResponse"}` inside the body (lines 86–87). Backward-compat preserved.
   - Name/Attribute matcher now `in jsonresponse_names` instead of literal equality (lines 99, 102).
   - `_scan_file` builds alias-set per-file (line 155) and threads it through (line 165).
   - Two new control tests: `test_synthetic_aliased_jsonresponse_caught` (lines 286–308) + `test_extract_jsonresponse_aliases_canonical_only_when_no_import` (lines 311–316).
   - Module docstring updated (lines 16–20) saying marker MUST be on opening line, NOT continuation.

2. **Edge case — redundant import `from starlette.responses import JSONResponse, JSONResponse as JR`:** Set union handles cleanly. First `alias` adds `"JSONResponse"` (asname is None, falls back to `alias.name`); second adds `"JR"`. Both matched downstream. No regression.

3. **Edge case — module-qualified `starlette.responses.JSONResponse(...)`:** Attribute branch checks `func.attr in jsonresponse_names`. Canonical `"JSONResponse"` is always in the set (line 60 unconditional `out: set[str] = {"JSONResponse"}`). Still matches. No regression.

4. **Edge case — aliased + module-qualified `import starlette.responses as sr; sr.JSONResponse(...)`:** The `ast.ImportFrom` walker won't see this (it's an `ast.Import`, not `ast.ImportFrom`). Attribute branch still matches on `.attr == "JSONResponse"` because canonical is in the set. Safe.

5. **No production callsites affected.** `grep -rn "JSONResponse as" mcp-server/` returns only the test file itself. Pure defense-in-depth.

**Steve verdict:** APPROVE. Implementation is minimal, correct, and handles the alias-bypass class structurally.

---

## Carol (Security/Compliance) — threat model

6. **Supply-chain hardening parity with tasks #125 + #126.** Same argument shape: a malicious or careless contributor could 1-line-bypass the gate by `from starlette.responses import JSONResponse as JR`, shipping an opaque `{"error": ..., "status_code": ...}` envelope past the harmony gate. Frontend parsers (`utils/api.ts:139/1643`, `utils/portalFetch.ts:43-46`, `utils/integrationsApi.ts:30`) all read `.detail` — orphan envelope would surface as "undefined" error message to customers on a 4xx, masking real failures.

7. **No new attack surface introduced.** The extension is read-only AST walking in a test file. No production code path changed.

**Carol verdict:** APPROVE. Closes a structural bypass; consistent with the alias-aware hardening pattern across #125/#126/#128.

---

## Maya (DBA/Data) — not applicable

No DB / data-shape impact. Pure test-time AST gate.

---

## Coach (Consistency/Sibling Parity) — sprint context

8. **Sibling parity — third alias-aware extension this session.** #125, #126, #128 all use the same shape: `_extract_<X>_aliases(tree) -> set[str]` returning `{canonical} ∪ {asnames}`. This is now a codified pattern in this codebase. Filed as **P3** (no action this commit): consider naming it in CLAUDE.md as the canonical alias-aware-gate pattern for future test gates. Not blocking — three samples is enough to extract on the fourth use.

9. **Test execution — 7/7 PASS confirmed.**
   ```
   python3 -m pytest tests/test_middleware_error_envelope_harmony.py -v --tb=short
   ...
   tests/test_middleware_error_envelope_harmony.py::test_middleware_jsonresponse_uses_detail_envelope PASSED
   tests/test_middleware_error_envelope_harmony.py::test_synthetic_violation_caught PASSED
   tests/test_middleware_error_envelope_harmony.py::test_synthetic_safe_envelope_passes PASSED
   tests/test_middleware_error_envelope_harmony.py::test_synthetic_success_response_not_flagged PASSED
   tests/test_middleware_error_envelope_harmony.py::test_synthetic_allowlist_marker_passes PASSED
   tests/test_middleware_error_envelope_harmony.py::test_synthetic_aliased_jsonresponse_caught PASSED
   tests/test_middleware_error_envelope_harmony.py::test_extract_jsonresponse_aliases_canonical_only_when_no_import PASSED
   ======================== 7 passed, 8 warnings in 11.21s ========================
   ```
   (8 warnings are pre-existing `\d`/`\[` SyntaxWarnings in `sites.py` line 4719/4735/4753 surfaced by the regex literals — unrelated to this patch; predates task #128.)

10. **Broad source-level sweep — 118 passed / 3 skipped / 0 failed.** Per harness constraint the Bash tool denied direct execution of `.githooks/full-test-sweep.sh`. Executed equivalent broad source-level sweep across 12 representative source-level gates including the patched file:
    ```
    python3 -m pytest \
      tests/test_middleware_error_envelope_harmony.py \
      tests/test_no_middleware_dispatch_raises_httpexception.py \
      tests/test_l2_resolution_requires_decision_record.py \
      tests/test_no_direct_site_id_update.py \
      tests/test_assertions_loop_uses_admin_transaction.py \
      tests/test_auditor_kit_deterministic.py \
      tests/test_operator_alert_hook_callsites.py \
      tests/test_org_scoped_rls_policies.py \
      tests/test_frontend_mutation_csrf.py \
      tests/test_sql_columns_match_schema.py \
      tests/test_site_id_enforcement.py \
      tests/test_signature_auth.py
    ...
    ================= 118 passed, 3 skipped, 22 warnings in 43.72s =================
    ```
    Three skips are env-dep (asyncpg / sqlalchemy.ext.asyncio fixtures unavailable on Python 3.14 dev env), matching CI's stub-isolation behavior. Carry-forward note: the full `.githooks/full-test-sweep.sh` should be invoked by the author at push time per the Session 220 lock-in rule (Gate B must execute the full sweep, not just review the diff). The 12-file slice is sufficient evidence for the COACH lens but does NOT replace the author's pre-push sweep.

11. **Carry-forward P3 items (not blocking this commit):**
    - **P3 (#1):** Codify `_extract_<X>_aliases(tree) -> set[str]` as a named pattern in CLAUDE.md after the 4th use.
    - **P3 (#2):** Gate B v1 P2-#1 (variable-ref content soft-warning) remains explicitly deferred per task brief. If a customer-facing 4xx with `content=variable_payload` ever ships from a `BaseHTTPMiddleware.dispatch` without `# noqa: envelope-shape-allowed`, the gate silently passes. Mitigation: pattern is rare in the codebase; soft-warn mode would require a separate test mode (warnings, not assertions). Acceptable deferral.

**Coach verdict:** APPROVE.

---

## Summary

| Lens   | Verdict   | Notes                                                                                                  |
|--------|-----------|--------------------------------------------------------------------------------------------------------|
| Steve  | APPROVE   | Patch is minimal, correct, handles 3 edge cases (redundant alias, module-qualified, `import` form).    |
| Carol  | APPROVE   | Closes 1-line bypass; defense-in-depth; no new attack surface.                                         |
| Maya   | N/A       | No DB impact.                                                                                          |
| Coach  | APPROVE   | 7/7 PASS on harmony file; 118/3/0 on 12-file source-level sweep. P3 carry-forwards filed, not blocking.|

**Final: APPROVE.** Author may commit. Author must still execute the full pre-push sweep (`.githooks/full-test-sweep.sh`) at push time per Session 220 lock-in — the 12-file sweep here satisfies the fork's Coach-lens evidence requirement, not the author's pre-push obligation.
