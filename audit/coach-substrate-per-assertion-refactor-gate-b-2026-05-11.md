# Gate B verdict — substrate per-assertion admin_transaction refactor (2026-05-11)

**Verdict:** APPROVE

Reviewer: Coach (Steve + Maya + Carol + Coach lenses, fork-fresh context)
Predecessor: `audit/coach-substrate-per-assertion-refactor-gate-a-2026-05-11.md`
Artifacts reviewed:
- `mcp-server/central-command/backend/assertions.py` lines 1262-1296, 5392-5676
- `mcp-server/central-command/backend/tests/test_assertions_loop_uses_admin_transaction.py` (168 lines, 5 tests)
- `mcp-server/central-command/backend/tenant_middleware.py:181-218` (admin_transaction shape)

## Gate A directive compliance

- **P0-1 docstring updated:** ✓ — assertions.py:1262-1272 explicitly documents "every caller now wraps this function in `admin_transaction(pool)`, so `conn` is ALWAYS inside an outer transaction. `async with conn.transaction()` therefore opens a true SAVEPOINT here". Round-2 historical context (NoActiveSQLTransactionError ~102/hr) preserved. Matches Gate A prescription verbatim.
- **P0-2 full-body wrap:** ✓ — assertions.py:5426 opens `async with admin_transaction(pool) as conn:` and the block spans through line 5592 (resolve loop). `current = await a.check(conn)` (5428), `open_rows = await conn.fetch(...)` (5470), every UPSERT/INSERT/RESOLVE savepoint (5492, 5512, 5541, 5560) all share one conn within one outer txn. Read-then-write consistency preserved.
- **P0-3 public asyncpg import:** ✓ — assertions.py:5414 `import asyncpg`; catches at 5429 + 5593 use `except asyncpg.InterfaceError`. No `_base` private import remains.
- **P0-4 _ttl_sweep own block + short-circuit gone:** ✓ — assertions.py:5659-5663 wraps `_ttl_sweep` in its own `async with admin_transaction(pool) as sweep_conn:` inside `assertions_loop`. The `if counters['errors'] == 0` short-circuit is absent (verified via test #3 + source walk).
- **P0-5 conn_dead band-aid removed:** ✓ — only one residual reference (line 5409, inside `run_assertions_once` docstring) and it explicitly EXPLAINS the removal. No `conn_dead = True/False`, no `if conn_dead`, no `and/or conn_dead`. Test #5 enforces.
- **P1-4 CI gate:** ✓ — 5 tests, all PASS locally (pytest output: `5 passed in 0.26s`). Path discovery `parent.parent.parent.parent.parent` matches sibling `test_minio_worm_bucket_validation_pinned.py:29`.

## Adversarial findings

- **P2 — test #2 regression-detection has a narrow false-pass corridor.** `test_ttl_sweep_runs_in_its_own_admin_transaction` searches the unparsed source of `assertions_loop` only, and looks ±5 lines back from `_ttl_sweep(` for `admin_transaction(pool)`. If a future regression inlined `_ttl_sweep` directly inside the per-assertion loop body AND `assertions_loop` had its OWN `admin_transaction(pool)` for any unrelated reason within 5 lines, the test passes. Mitigation: `assertions_loop` today contains exactly one `admin_transaction(pool)` opener (the sweep block); the per-assertion wrap lives in `run_assertions_once`, which the test does not scan. Robust enough today; consider tightening to "_ttl_sweep call's enclosing async-with's context manager is `admin_transaction(pool)` and that with-statement does NOT also contain a `for a in ALL_ASSERTIONS` loop." Not blocking.
- **P2 — outer except positioning is correct but subtle.** assertions.py:5593 `except asyncpg.InterfaceError as e:` sits at the per-assertion `try:` level (5425), siblings the `async with admin_transaction(pool) as conn:` (5426). If `admin_transaction.__aenter__` itself raises `InterfaceError` (pool exhaustion, PgBouncer outage during SET LOCAL), the except fires correctly and the loop continues. Verified by Python's `async with` semantics: an exception in `__aenter__` propagates out of the `async with` statement, caught by the surrounding `try`. Correct.
- **P2 — pool-acquire cadence (60/min) is well within headroom.** asyncpg pool `max_size=25`, sequential loop → peak concurrency 1. PgBouncer sees 60 short transactions/min replacing 1 long one — strict improvement for transaction-pool routing fairness (matches Gate A P1-1 math). No new pressure.
- **No race between outer admin_transaction and inner savepoints.** `admin_transaction` opens a top-level txn + SET LOCAL app.is_admin='true'; `_check_client_portal_zero_evidence_with_data` opens an asyncpg `conn.transaction()` which auto-detects the outer txn and emits SAVEPOINT. SET LOCAL inside the savepoint scopes to the savepoint (verified against asyncpg savepoint contract). Confirmed by assertions.py:1296 comment "Savepoint committed on with-block exit; SET LOCAL released."

## Recommendation

**Ship now.** All 5 Gate A P0 directives executed correctly; the P1-4 CI gate test exists, has correct path discovery matching siblings, and all 5 tests PASS. Two P2 observations (test-#2 tightening, outer-except position documentation) are non-blocking — carry as TaskCreate items if desired but do not gate the commit. The cascade-fail bug class is now closed structurally rather than patched defensively, and the substrate engine becomes the canonical reference site for per-assertion `admin_transaction(pool)` patterns in any future background loop.
