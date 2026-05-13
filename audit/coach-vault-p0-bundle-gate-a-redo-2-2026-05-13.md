# Gate A Verdict (REDO-2) — Vault P0 Bundle Design Binding
Date: 2026-05-13
Reviewer: Gate A re-fork after design doc (Opus 4.7, fresh context, 4-lens adversarial)
Scope: verify `.agent/plans/vault-p0-bundle-redesign-2026-05-13.md` sufficiently binds the 8 P0s + cross-cutting fixture parity raised by the prior Gate A redo verdict.

## Verdict: **APPROVE-FOR-EXECUTION**

The design doc binds every P0 with (a) named file path, (b) concrete code shape (SQL, Python, AST regex), and (c) a citation to the prior-failure class it mitigates. Commit boundary is explicit (2 commits / 1 push). Gate B brief is pinned to 9 grep verifications. Two soft gotchas (thread-zombie risk on eager-warm; fleet_cli.py:348 7th-callsite class) are P1, not P0 — carry as TaskCreate followups, do not block execution.

## Per-P0 binding verification

- **P0 #1 (asyncio.wait_for wraps full vault block):** ✓ bound. Design §P0 #1 says verbatim "the entire Vault-probe section inside `INV-SIGNING-BACKEND-VAULT` is one coroutine wrapped in a single outer `await asyncio.wait_for(_vault_probe(conn), timeout=5.0)`" — and explicitly answers Steve's earlier P0 with "the timeout MUST cover the singleton-build AND the key-version read AND the public-key read, all in one coroutine." File: `startup_invariants.py`. Failure class: iter-3 `/health` 120s timeout. Closure satisfied.

- **P0 #2 (lifespan eager-warm BEFORE invariants):** ✓ bound. Design §P0 #2 shows exact Python code shape with `await asyncio.wait_for(asyncio.to_thread(get_signing_backend), timeout=5.0)` placed "before `check_all_invariants()` is called" in `mcp-server/main.py` lifespan startup. Non-fatal failure path documented (matches CREDIBILITY-not-availability promise). Closure satisfied.

- **P0 #3 (ON CONFLICT DO NOTHING):** ✓ bound. Design §P0 #3 shows SQL `ON CONFLICT (key_name, key_version) DO NOTHING` and explicitly excludes the prior `DO UPDATE SET last_observed_at = NOW()` side-effect. Rationale cites Steve P0 #3 (forensic-analysis preservation). File: `startup_invariants.py` inside `_check_signing_backend_vault`. Closure satisfied.

- **P0 #4 (admin-only INV detail test):** ✓ bound. Design §P0 #4 names new file `tests/test_inv_signing_backend_vault_admin_only.py` and shape: AST scan over `main.py` `/health` handler asserting no `check_all_invariants()` call or `InvariantResult.detail` exposure. Mitigates Maya P0 (operational-detail leak via careless health endpoint). Closure satisfied.

- **P0 #5 (known_good NOT NULL DEFAULT FALSE explicit):** ✓ bound. Design §P0 #5 declares verbatim `known_good BOOLEAN NOT NULL DEFAULT FALSE` in mig 311. Correctly explains the 3-valued-logic gap that NOT NULL closes (NULL in `NOT known_good` evaluates to UNKNOWN; Postgres CHECK treats UNKNOWN as PASS). Closure satisfied.

- **P0 #6 (module-level imports + CI gate):** ✓ bound. Design §P0 #6 names callsite files (`fleet_updates.py:19` already module-level, `flywheel_promote.py`, `cve_watch.py`, `sites.py`) and provides full AST gate source for `tests/test_no_dual_import_for_signing_method.py`. Gate correctly skips `tests` and `migrations` directories so it does not fire on itself. The 4 files × varying callsites = 6 INSERT sites — math checks against prior verdict's enumeration. Closure satisfied.

- **P0 #7 (commit body cites all 4 audit paths):** ✓ bound. Design §P0 #7 names all 4 paths verbatim: this Gate A verdict (redo-2), the Gate B verdict (to-be-written at `audit/coach-vault-p0-bundle-gate-b-redo-2026-05-13.md`), the revert case study, the design doc itself. Closure satisfied.

- **P0 #8 (explicit `$N::type` casts):** ✓ bound. Design §P0 #8 shows verbatim cast SQL `$1::text, $2::int, $3::text` in the bootstrap INSERT. Cites Session 219 `jsonb_build_object($N, ...)` class rule + asyncpg-under-PgBouncer prepare-phase type-inference flake. Closure satisfied.

- **Cross-cutting fixture parity:** ✓ bound. Design names all 6 fixture file paths with line numbers (`test_startup_invariants_pg.py:48`, `test_privileged_chain_adversarial_pg.py:55`, `test_privileged_chain_triggers_pg.py:56`, `test_fleet_intelligence_api_pg.py:91`, `test_promotion_rollout_pg.py:78`, `test_flywheel_spine_pg.py:104`) and provides full CI gate source for `tests/test_pg_fixture_fleet_orders_column_parity.py` using `re.finditer(r"CREATE TABLE fleet_orders\b[^;]+;")`. Closure satisfied.

## Gotcha checks

1. **Eager-warm thread zombie risk:** `asyncio.wait_for(asyncio.to_thread(get_signing_backend), timeout=5.0)` cancels the awaiting future but cannot kill the underlying OS thread if `get_signing_backend()` blocks indefinitely (e.g., TCP SYN hang on partitioned Vault). The container starts (non-blocking promise honored), but a thread leaks in the default ThreadPoolExecutor. **Severity: P1, not P0.** Mitigations already in design: (a) runs ONCE per container lifecycle, so worst case is 1 leaked thread per container start; (b) ThreadPoolExecutor has bounded `max_workers` so blast radius is finite; (c) container restart kills the thread. **Required P1 followup (carry as TaskCreate, NOT a P0 closure):** verify `hvac` client in `signing_backend.py` has a socket-level timeout set (`VAULT_CLIENT_TIMEOUT` env or `Client(timeout=N)` kwarg) so the thread itself is bounded. Recommend ≤10s.

2. **Bootstrap race (two containers):** `ON CONFLICT (key_name, key_version) DO NOTHING` is safe under concurrent INSERT. Postgres serializes via the `(key_name, key_version)` unique index — one INSERT wins, the other no-ops. `first_observed_at` defaults to NOW() on whichever transaction commits first. No corruption class. Acceptable.

3. **NULL known_good semantics:** Design correctly characterizes the Postgres CHECK semantics: with NULL `known_good`, `NOT known_good` evaluates to NULL, the `(NOT known_good OR ...)` disjunction evaluates to NULL (when right side is also NULL or FALSE), and Postgres CHECK constraints treat NULL as PASS. Explicit `NOT NULL DEFAULT FALSE` closes the gap unconditionally. Correct.

4. **CI gate scope (AST module-level import test):** Design test walks `_BACKEND.rglob("*.py")` where `_BACKEND = pathlib.Path(__file__).resolve().parent.parent`. With test sitting at `backend/tests/test_no_dual_import_for_signing_method.py`, `parent.parent` resolves to `backend/`. The `if "tests" in py_path.parts or "migrations" in py_path.parts: continue` filter excludes the test directory + migrations. Gate does not self-trigger. ✓

5. **Iter-1 characterization in design:** Design §P0 #6 mitigation line says "iter-1 root cause (try/except hides ImportError → silent INSERT skip)". This matches the revert case study's CI log: `flywheel_promote.py:872 sync_promoted_rule INSERT failed ... attempted relative import with no known parent package`. Design correctly treats iter-1 as the ImportError (not as the fixture-column class, which was iter-2). The first-fork brief's misattribution noted in the prior Gate A verdict (lines 9, 13) does NOT carry into this design doc. Correct.

6. **fleet_cli.py:348 treatment:** Design does NOT explicitly mention fleet_cli.py:348 as a 7th INSERT site. fleet_cli is a VPS-side CLI tool, not container code. **Risk:** if fleet_cli.py lives under `_BACKEND = mcp-server/central-command/backend/`, the AST gate WILL scan it. If fleet_cli uses function-body `from .signing_backend import current_signing_method`, the gate fires. **Severity: P1, not P0.** Functionally a try/except function-body import is safe in a one-shot CLI (no container-startup hang class), but the CI gate may surface a violation. **Required P1 followup:** before Commit 1, run `grep -rn "signing_backend.*current_signing_method" mcp-server/central-command/backend/fleet_cli.py` and either (i) refactor fleet_cli to module-level import (preferred, ~1 line change) OR (ii) add fleet_cli.py to the AST gate's exclusion list with an inline comment explaining why. Carry as TaskCreate item attached to Commit 1.

## Required pre-execution closures (P0)

None. All 8 P0s + cross-cutting are bound. Execution may proceed under the design doc's 2-commit single-push plan.

## P1 followups (carry as TaskCreate items in Commit 1 body)

1. **hvac socket-timeout** — verify `signing_backend.py` sets `Client(timeout=10)` or equivalent so the eager-warm OS thread cannot block indefinitely. If not set, add it in Commit 2.
2. **fleet_cli.py:348 callsite** — grep + either refactor to module-level import OR add explicit AST-gate exclusion with comment. Resolve before Commit 1 pushes.
3. **P1 carries from prior Gate A redo** — verify mig 311 audit-log-after-COMMIT placement matches mig 310; document `VAULT_PROBE_REQUIRED` env trade-off in runbook; byte-for-byte port of `substrate_runbooks/signing_backend_drifted_from_vault.md` from commit `9fa26a54`.

## Gate B brief (final)

After Commit 1 + Commit 2 are staged locally but BEFORE `git push`:

1. **Fork fresh-context Gate B** with 4 lenses (Steve / Maya / Carol / Coach). Save verdict at `audit/coach-vault-p0-bundle-gate-b-redo-2026-05-13.md`.

2. **Mandatory verification artifacts** (all 9 must pass — cite output in verdict):
   - `bash .githooks/full-test-sweep.sh` runs to completion; cite exact pass/fail count and compare against baseline (~241 + 2 new gates). Session 220 lock-in: full sweep required, not diff-only review.
   - `grep -c "signing_method" mcp-server/central-command/backend/tests/*_pg.py` returns ≥6 (one per fixture).
   - `grep -rn "try:\s*from \.signing_backend" mcp-server/central-command/backend/` returns 0.
   - `grep -n "asyncio.wait_for" mcp-server/central-command/backend/startup_invariants.py` returns ≥1 line inside the INV-SIGNING-BACKEND-VAULT block.
   - `grep -n "asyncio.wait_for\|asyncio.to_thread.*get_signing_backend" mcp-server/main.py` returns the eager-warm step.
   - `grep -n "ON CONFLICT (key_name, key_version) DO NOTHING" mcp-server/central-command/backend/startup_invariants.py` matches; verify NO `DO UPDATE SET last_observed_at` in the same block.
   - `grep -nE "VALUES \(\\\$1[^:]" mcp-server/central-command/backend/startup_invariants.py` returns 0 in the vault block (all params cast).
   - Both commit bodies cite the 4 audit paths (this verdict + Gate B verdict file + revert case study + design doc).
   - Run the 2 new CI gates standalone (`pytest tests/test_no_dual_import_for_signing_method.py tests/test_pg_fixture_fleet_orders_column_parity.py -v`) — expect 0 violations.

3. **Three failure-class proof obligations** — verdict must state for each:
   - Iter-1 (ImportError): "VERIFIED — all 6 callsites use module-level import; CI gate enforces."
   - Iter-2 (fixture column drift): "VERIFIED — all 6 fixtures contain `signing_method`; CI gate enforces."
   - Iter-3 (startup hang): "VERIFIED — `asyncio.wait_for(5.0)` wraps full Vault block at INV probe AND lifespan eager-warm; non-blocking on timeout."

4. **P1 followup status** must be cited in Gate B verdict:
   - hvac socket-timeout: confirm set OR carry as task.
   - fleet_cli.py:348: confirm refactored OR explicitly excluded.

5. **"Partial" / "best-effort" / "TODO" in any proof → automatic BLOCK.** No fix-forward without re-Gate-A.

6. **Post-merge runtime verification** (Session 215 #77 rule):
   - `curl https://msp.osiriscare.io/api/version` → assert `runtime_sha == disk_sha == merged commit SHA` BEFORE claiming shipped.
   - `curl /health` → assert `status=ok` within deploy-timeout window.
   - `docker compose exec mcp-server python -c "from startup_invariants import check_all_invariants; import asyncio; print(asyncio.run(check_all_invariants(...)))"` to print live INV-SIGNING-BACKEND-VAULT state.

**End of verdict.** APPROVE-FOR-EXECUTION on the design doc as written. Proceed to implementation under the 2-commit single-push plan; Gate B fork is mandatory before `git push`.
