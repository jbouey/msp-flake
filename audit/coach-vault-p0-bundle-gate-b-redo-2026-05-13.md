# Gate B Verdict — Vault P0 Bundle As-Implemented (REDO)
Date: 2026-05-13
Reviewer: Gate B fork (4-lens adversarial, fresh context)

## Verdict: BLOCK

## Mandatory-9 evidence

1. **Sweep**: 244 passed, 0 failed (`bash .githooks/full-test-sweep.sh` from repo root). Matches baseline 241 + 3 new CI gates.
2. **Fixture parity**: 6 files matched —
   - tests/test_fleet_intelligence_api_pg.py
   - tests/test_flywheel_spine_pg.py
   - tests/test_privileged_chain_adversarial_pg.py
   - tests/test_privileged_chain_triggers_pg.py
   - tests/test_promotion_rollout_pg.py
   - tests/test_startup_invariants_pg.py
3. **Function-body imports**: 0 matches under `mcp-server/central-command/backend/`. The only match in the grep output (`tests/test_no_dual_import_for_signing_method.py:16`) is the CI-gate test's own description string, NOT a real import.
4. **asyncio.wait_for in INV**: `startup_invariants.py:316` inside the `INV-SIGNING-BACKEND-VAULT` block (`signing_backend_env in ("vault", "shadow")` branch). Confirmed wraps the ENTIRE `_check_vault_key_version(conn)` call which itself calls `get_signing_backend()` (singleton build) + key-version read + bootstrap INSERT. Iter-3 class closure verified.
5. **Eager-warm in main.py**: `main.py:1647` — `asyncio.wait_for(asyncio.to_thread(get_signing_backend), timeout=5.0)`. Worst-case startup 5s warm + 5s INV = 10s; matches design doc.
6. **ON CONFLICT DO NOTHING**: `startup_invariants.py:155` (bootstrap path) + `:178` (drift forensic-insert path). Both ON CONFLICT (key_name, key_version) DO NOTHING. P0 #3 closure verified.
7. **Explicit type casts**: `startup_invariants.py:138` (`$1::text` in known_good read), `:154` (`$1::text, $2::int, $3::text` in bootstrap INSERT), `:177` (same casts in forensic-insert). P0 #8 closure verified.
8. **Both commit bodies cite all 4 audit paths**: YES — commit 1 (`59e6d4c2`) and commit 2 (`22561d1f`) both cite Gate A initial BLOCK, Gate A redo-2 APPROVE, design doc, revert case study, and forward-reference this Gate B file path. Verified.
9. **3 new CI gates pass standalone**: YES.
   - `test_pg_fixture_fleet_orders_column_parity.py`: 1 passed in 0.39s
   - `test_no_dual_import_for_signing_method.py`: 1 passed in 2.34s
   - `test_inv_signing_backend_vault_admin_only.py`: 1 passed in 0.32s

## Per-failure-class proof obligations

- Iter-1 (ImportError → silent INSERT skip): **VERIFIED**.
  - Module-level imports on all 6 callsites (fleet_updates.py:20, sites.py:24, cve_watch.py:24, flywheel_promote.py:38 module-level try/except). No function-body imports.
  - CI gate test_no_dual_import_for_signing_method.py pins regression class via AST walk.

- Iter-2 (fixture column drift): **VERIFIED**.
  - All 6 *_pg.py fixtures contain `signing_method` column. CI gate test_pg_fixture_fleet_orders_column_parity.py pins forward.

- Iter-3 (startup hang on Vault probe): **VERIFIED**.
  - `asyncio.wait_for(timeout=5.0)` wraps `_check_vault_key_version` which itself contains `get_signing_backend()` singleton-build call (line 122) — the iter-3 hang surface. Caller wrap at startup_invariants.py:316-319. Eager-warm at main.py:1646-1649 also bounded.

## Findings by lens

### Steve (correctness) — 1 P0, 0 P1
**P0-STEVE-1 — Substrate invariant `signing_backend_drifted_from_vault` is BROKEN at runtime.** assertions.py:2282 references `_check_signing_backend_drifted_from_vault(c)` inside a `check=lambda c:` body, but the function is **NEVER DEFINED** anywhere in the codebase. Verified via:
- `grep -rn "_check_signing_backend_drifted_from_vault" mcp-server/central-command/backend/` returns ONLY the single lambda reference.
- AST walk of assertions.py confirms 0 FunctionDef/AsyncFunctionDef nodes named `_check_signing_backend_drifted_from_vault`.
- Full git diff of assertions.py (39 lines) adds ONLY the Assertion entry + _DISPLAY_METADATA entry; no function body is introduced.

Runtime impact: every 60s substrate tick, when the invariant runs, the lambda body executes and raises `NameError: name '_check_signing_backend_drifted_from_vault' is not defined`. The Session 220 `57960d4b` per-assertion `admin_transaction` cascade-fail closure means only this one assertion fails per tick (other invariants continue), so this WILL NOT crash production — but **the entire P0 #4 detection mechanism is dead**. The exact post-cutover regression Phase C was meant to prevent (silent fallback to disk-key signing) will be UNDETECTED. The substrate-integrity surface will throw an exception 1×/min in container logs.

This is a direct violation of the design doc's binding "8 P0s closed" claim — P0 #4 (substrate invariant) is implemented as a stub, not a working check.

### Maya (HIPAA / disclosure) — 0 P0, 0 P1
- Banned-word scan clean on substrate runbook + mig 311 + assertions.py descriptions.
- Commit body contains "Prevents fast-track of unauthorized rotation" — this is a technical-mechanism description of the CHECK constraint, not a customer-facing legal/compliance claim. ACCEPTABLE.
- mig 311 audit log placed AFTER COMMIT, matches mig 310 pattern. JSONB content honest (no overclaims).
- Phase C cutover (env flip) is NOT implied by this commit — verified via runbook §"Change log" + commit body §"Why this commit is safe to stage but not yet push". Shadow-mode posture preserved.

### Carol (DBA) — 0 P0, 0 P1
- mig 311: `CREATE TABLE IF NOT EXISTS` ✓; `CREATE INDEX IF NOT EXISTS` ✓; `DROP TRIGGER IF EXISTS` + `CREATE OR REPLACE FUNCTION` (idempotent) ✓; CHECK constraint `(NOT known_good OR (approved_by IS NOT NULL AND approved_at IS NOT NULL))` syntactically valid + correctly enforces the approval-pair invariant (P0 #5 + retro P0).
- Audit-log INSERT outside BEGIN/COMMIT — matches mig 310 pattern. Idempotent rerun ⇒ extra log row, acceptable.
- Bootstrap INSERT has explicit `$N::text` / `$N::int` casts per asyncpg+PgBouncer rule.
- Substrate-invariant query (`WHERE created_at > NOW() - INTERVAL '1 hour' GROUP BY signing_method`) — at ~10 orders/day no index pressure. Note: query body is in the missing function; cannot review the actual SQL since the function doesn't exist (Steve P0-1).
- 6 INSERT callsites spot-checked at fleet_updates.py:1086+1096, fleet_updates.py:1556+1567, sites.py:2206+2218, sites.py:3152+3161, cve_watch.py:370+381, flywheel_promote.py:866+876. All have `signing_method` in column list with positional `current_signing_method()` matching values. Correct.

### Coach (lockstep / banned shapes / protocol) — 0 P0, 1 P1
- `_LOOP_LOCATIONS` dicts untouched (this commit doesn't alter loop calibrations). ✓
- 2 new gates added to `.githooks/pre-push` SOURCE_LEVEL_TESTS (lines 328, 332). Third gate `test_inv_signing_backend_vault_admin_only.py` is NOT in the SOURCE_LEVEL_TESTS curated list, but IS picked up by glob-based `.githooks/full-test-sweep.sh` (`find tests -maxdepth 1 -name "test_*.py"`). Acceptable since pre-push runs the full sweep.
- `test_pre_push_ci_parity.py` passes (4/4) — no parity regression.
- ASSERTION_METADATA has entry for `signing_backend_drifted_from_vault` (assertions.py:3061). ✓
- `substrate_runbooks/signing_backend_drifted_from_vault.md` exists with 85 lines covering: what-this-means, root-cause-categories (4), immediate-action (4 steps), verification, escalation, related-runbooks, change-log. ✓
- Banned shapes scan: no `||-INTERVAL`, no unannotated `jsonb_build_object`, no `except Exception: pass` after `conn.execute`, no bare `pool.acquire` in loops, no f-string subjects, no function-body signing_backend imports.

**P1-COACH-1**: Recommend adding `test_inv_signing_backend_vault_admin_only.py` to SOURCE_LEVEL_TESTS so it runs in the fast-lane curated path too (defense in depth — if PRE_PUSH_SKIP_FULL=1 is set, this gate would silently not run). Non-blocking.

## Summary

**Automatic BLOCK per Session 220 TWO-GATE lock-in.** Per-failure-class verification: 3 of 3 verified. However, Steve found a NEW P0 introduced by this implementation that was not on the original 3-iter failure-class list: P0 #4 substrate invariant is referenced but never defined.

The design doc binds 8 P0 closures. As-implemented this delivers 7 of 8: P0 #4 (substrate invariant) is a stub that will NameError at every 60s substrate tick. The Session 220 cascade-fail closure (per-assertion admin_transaction) prevents this from killing the substrate engine, but the detection mechanism for "silent fallback to disk-key signing" — the exact regression class P0 #4 was created to surface — is non-functional.

This is the SAME class as the original revert iter-1 (ImportError → silent failure → no detection). Author wrote the alarm but the wire isn't connected to the bell. Treating Gate B as "code presence" rather than "runtime presence" would have shipped this exactly as Session 220 lock-in §"Gate B MUST run the full pre-push test sweep, not just review the diff" was written to prevent.

Fix required before re-submission: define `_check_signing_backend_drifted_from_vault(conn)` in assertions.py with the actual query body (env-derived expected vs. observed `fleet_orders.signing_method` distribution over last 1 hour). Add a positive-control test that imports the function symbol + a negative-control test that constructs the env+row scenario and asserts the function returns the expected (ok, detail) tuple.

## Allowed to push

**NO**. Block until P0-STEVE-1 is closed and the resulting commit receives a re-run Gate B fork with the function defined + the new control tests passing.
