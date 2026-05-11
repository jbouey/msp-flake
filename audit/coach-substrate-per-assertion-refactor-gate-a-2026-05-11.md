# Gate A — Substrate Per-Assertion admin_transaction Refactor

Reviewer: Coach (running Steve + Maya + Carol + Coach lenses)
Date: 2026-05-11
Target commit baseline: `b55846cb` (cascade-fail band-aid in place)
Files reviewed:
- `mcp-server/central-command/backend/assertions.py` (5654 lines, 60+ assertions, `run_assertions_once` @ 5391, `assertions_loop` @ 5610, `_ttl_sweep` @ 5596)
- `mcp-server/central-command/backend/tenant_middleware.py` (327 lines, `admin_transaction` @ 182)
- `mcp-server/central-command/backend/tests/test_admin_connection_no_multi_query.py` (303 lines — class-general AST gate)
- `mcp-server/central-command/backend/tests/test_admin_transaction_for_multistatement.py` (sibling per-line ratchet)
- `mcp-server/central-command/backend/fleet.py` (pool config: min=2, max=25, statement_cache_size=0)

## Verdict

**APPROVE-WITH-FIXES** — the core architectural intent (one bad check no longer poisons every subsequent check) is correct, reuses the canonical `admin_transaction(pool)` shape exactly as Session 212 round-table prescribed, and is consistent with the 226-site ratchet baseline. Five P0 items must be addressed before implementation lands; none require redesign — all are mechanical.

## P0 findings (must fix before any implementation)

### P0-1 — `_check_client_portal_zero_evidence_with_data` (assertions.py:1262-1295) hard-depends on outer transaction shape

The check's documented contract (line 1262-1271) explicitly says:

> Use asyncpg's `async with conn.transaction()` for the savepoint, NOT raw `SAVEPOINT` SQL — raw SAVEPOINT requires an outer transaction, and `admin_connection` (caller) only begins one when wrapped in `admin_transaction`. The context-manager form auto-detects savepoint-vs-toplevel.

Today the outer caller is `admin_connection` (no outer txn) → `conn.transaction()` opens a top-level txn → the `SET LOCAL app.current_org = '<id>'` is released at txn commit. **Under the proposed refactor, the outer caller is `admin_transaction` (outer txn DOES exist) → `conn.transaction()` becomes a SAVEPOINT, and the SET LOCAL settings persist for the duration of the OUTER txn until the per-assertion `admin_transaction` block exits.**

The semantic that matters: each per-assertion outer txn is one check's worth of work, then COMMIT and conn release. So the SET LOCAL leaks across exactly ONE assertion (the client-portal-zero check itself) and dies at outer COMMIT. That is fine for this specific check because nothing after the savepoint reads `current_setting('app.current_org')` (verified — the savepoint contains the only read), and the savepoint's exit-time release happens BEFORE outer COMMIT in the asyncpg context-manager.

BUT: the docstring is now WRONG (it'll mislead the next reader who tries to add a 2nd query after the savepoint). Fix the docstring in the same commit:

```
# After refactor: caller is admin_transaction(pool), so conn.transaction()
# is a SAVEPOINT. SET LOCAL is released at savepoint exit (same as before).
```

Verify before merge that no NEW query is added between the savepoint exit and the per-assertion block end — i.e. nothing within `_check_client_portal_zero_evidence_with_data` runs an admin-context query AFTER the savepoint that would be silently org-scoped. Today: clean.

### P0-2 — Per-assertion conn means the savepoint-inside-`run_assertions_once` UPSERT/RESOLVE block runs across DIFFERENT conns from the assertion's `check(conn)` call

`run_assertions_once` (lines 5418-5591) currently:
1. `current = await a.check(conn)` — fetches violations on `conn`
2. `open_rows = await conn.fetch(...)` — fetches existing open rows on SAME `conn`
3. UPSERT/INSERT/resolve in `async with conn.transaction():` savepoints on SAME `conn`

The naive refactor as drafted in the brief wraps ONLY `a.check(conn)` in `admin_transaction`. Steps 2-3 happen OUTSIDE the per-assertion conn block, so they need their own conn. The brief is silent on this. Implementation MUST:

- Either widen the `async with admin_transaction(pool) as conn:` block to cover the WHOLE per-assertion processing (check + open_rows fetch + upsert + resolve loop), so the open-rows read and the UPSERT/resolve writes are co-transactional with the check that produced `current`. **This is the correct shape** — it preserves read-then-write consistency within one tick of one assertion, which is what the current code achieves via the single outer conn.

- OR explicitly partition into check-conn vs write-conn and document why the split is safe (it isn't — open_rows read after the check creates a TOCTOU window vs concurrent UPSERTs from a previous still-running tick). **Reject this shape.**

Correct shape (must match this):

```python
for a in ALL_ASSERTIONS:
    try:
        async with admin_transaction(pool) as conn:
            current = await a.check(conn)
            # ... open_rows fetch, UPSERT/INSERT/resolve, all inside ...
    except asyncpg.InterfaceError:
        counters["errors"] += 1
        continue
    except Exception:
        logger.error("assertion %s raised", a.name, exc_info=True)
        counters["errors"] += 1
        continue
```

Pin this in the implementation PR description so the reviewer can verify the block boundary.

### P0-3 — `except asyncpg.InterfaceError` is the WRONG import path; existing code imports from `asyncpg.exceptions._base`

Today's catch (line 5413) uses:

```python
from asyncpg.exceptions._base import InterfaceError as _AsyncpgInterfaceError
```

The brief writes `except asyncpg.InterfaceError`. asyncpg's PUBLIC `asyncpg.InterfaceError` IS the same class (re-exported from `_base`), so this happens to work — but the existing import is a private `_base` path that should be preserved for grep-parity with the band-aid commit, OR explicitly updated to the public import in BOTH locations (the run-once function AND any new sibling). Pick one; do not leave the codebase with two import paths for the same class.

Recommendation: switch to `import asyncpg` + `except asyncpg.InterfaceError:` everywhere (public API, lint-friendly). Drop the `_base` import. Update the docstring comment likewise.

### P0-4 — `_ttl_sweep` must move OUT of the per-assertion loop and into its own `admin_transaction` block

Current code (line 5638-5641) runs `_ttl_sweep` on the SAME outer conn as the assertions tick, AND short-circuits the sweep if any assertion errored. Under the per-assertion refactor:

- The outer `admin_connection(pool) as conn` block disappears — `conn` no longer exists at the `_ttl_sweep` callsite.
- The "if errors == 0" short-circuit was a band-aid because the outer conn was poisoned. Per-assertion isolation makes the band-aid unnecessary — the sweep can ALWAYS run because its conn is fresh.

Correct shape for the loop body:

```python
counters = await run_assertions_once(pool)         # takes pool now
async with admin_transaction(pool) as conn:
    deleted = await _ttl_sweep(conn)               # own conn, always runs
```

This is also a SEPARATE finding from P0-2: keep `_ttl_sweep` in its own per-call `admin_transaction` rather than folding it into `run_assertions_once`. Single responsibility — the sweep is unrelated to violation UPSERTs.

### P0-5 — Cascade-fail band-aid (`conn_dead` flag, b55846cb) MUST be removed in the same commit

The whole point of the refactor is that one check's `InterfaceError` no longer poisons the rest. Leaving the `conn_dead = True; continue` short-circuit in place is dead code that will confuse the next reader and ALSO breaks the design: under per-assertion conns, an `InterfaceError` on assertion N has zero effect on assertion N+1 (different conn). The "short-circuit" logic now SKIPS valid assertions for no reason.

Remove:
- `conn_dead = False` (line 5416)
- `if conn_dead: ... counters["errors"] += 1; continue` (lines 5419-5422)
- `conn_dead = True` (line 5438)
- The docstring paragraph at lines 5398-5411 explaining the band-aid; replace with a paragraph documenting the per-assertion isolation invariant + a pointer to this Gate A audit doc.

## P1 findings (fix before completion or carry as TaskCreate followups)

### P1-1 — Pool capacity math: ~60 acquires per tick × 1 tick/min = 1 acquire/sec average. Headroom is fine, but burst is the concern.

Pool is `min_size=2, max_size=25` (fleet.py:60). Today the substrate engine holds 1 conn for ~30-60s per tick. After refactor: 60+ sequential acquires per tick, each held for ~50-500ms (median check is one fetch).

- Sequential: peak concurrency is 1 (the substrate loop is single-task). 1 of 25 conns. Fine.
- Customer traffic: backend has separate SQLAlchemy pool (shared.py, pool_size=20) for admin CRUD. asyncpg pool (max=25) is shared between fleet endpoints + tenant_connection + assertions. 1 extra concurrent acquire is negligible.
- PgBouncer side: each `admin_transaction` is one PgBouncer transaction. ~60 short transactions/min replacing 1 long one. Lower bound on long-held server-side conns. **Strictly an improvement for PgBouncer transaction-pool routing fairness.**

No action — but add the math to the implementation PR description so future operators don't re-derive it.

### P1-2 — `_check_substrate_sla_breach` (line 562) reads `substrate_violations` and is sensitive to UPSERT visibility

Each per-assertion `admin_transaction` COMMITs at block exit. If `_check_substrate_sla_breach` runs LATER in the same tick than `_check_offline_appliance_long`, it can already see the committed UPSERT from the earlier assertion. Under today's single-outer-conn design, all UPSERTs were in nested savepoints that committed at outer block exit (same as the SLA check's read), so reads were consistent with the in-progress tick.

Under the new design, MID-TICK substrate_violations rows from earlier assertions become visible to LATER assertions. This is a behavior change but NOT a correctness problem: the SLA check fires on rows whose `created_at < NOW() - sla_minutes`; a row just inserted 50ms ago will never trip the SLA threshold (smallest SLA is multi-minute). Verify by reading `_check_substrate_sla_breach` and confirming all threshold expressions are `> N minutes` where N >= 5.

Action: in the implementation, add an inline comment on `_check_substrate_sla_breach` noting "this check reads substrate_violations from earlier assertions in the SAME tick — safe because all SLA thresholds are multi-minute."

### P1-3 — Heartbeat write moves inside the loop body but outside per-assertion conn — already correct

The `record_heartbeat("substrate_assertions")` call (line 5626) is process-local (in-memory dict per `bg_heartbeat`), not DB-bound. It does NOT need a conn. After refactor, leave it where it is — first thing in the `while True` body. No action.

### P1-4 — Add a new test gate `test_assertions_loop_uses_admin_transaction`

The class-general ratchet (`test_admin_connection_no_multi_query.py`) catches `admin_connection` regressions but does NOT catch a regression where the substrate engine is rewritten to use raw `pool.acquire()` without `admin_transaction`. Add a small AST/regex test pinning that `assertions.py::run_assertions_once` contains the substring `admin_transaction(pool)` and does NOT contain `admin_connection(pool)`. ~20 lines. Carry as TaskCreate item if not done in the same commit.

### P1-5 — `admin_transaction` does only `SET LOCAL app.is_admin TO 'true'`, not the GUCs that `_check_client_portal_zero_evidence_with_data` overrides

`admin_transaction(pool)` sets `app.is_admin='true'`. The client-portal-zero check then opens a SAVEPOINT and sets `app.is_admin='false' + app.current_org=<id> + app.current_tenant=''`. On savepoint exit, those THREE settings revert to the OUTER txn's values: `is_admin='true'`, `current_tenant=` (whatever the DB-role baseline is — empty by mig 234), `current_org=` (empty). This is correct. No action — but verify in implementation by reading the savepoint exit behavior in asyncpg docs.

## P2 findings (nice to have)

### P2-1 — Consider per-assertion timing instrumentation

With 60+ checks each taking their own conn, a single slow check is easier to identify. Add `time.perf_counter()` around `a.check(conn)` and log slow checks (>1s) at INFO. Drops cognitive load when the next prod regression appears.

### P2-2 — `ALL_ASSERTIONS` ordering review

Today the order is arbitrary (insertion order in the list). Now that each check is independently transactional, consider grouping by sev1 → sev2 → sev3 so the dashboard sees the most-urgent UPSERTs first within a tick. Not load-bearing — defer.

### P2-3 — Document the "60 SET LOCAL per tick" line in CLAUDE.md

The `admin_transaction()` per-endpoint pattern is per-endpoint and lower volume (CLAUDE.md Session 212 line). After this refactor the substrate engine becomes the FIRST high-volume caller (60+/min ≈ 86K/day). Update the CLAUDE.md note to reflect the new volume profile so future "pgbouncer SET LOCAL load" questions have a citable answer.

## Per-lens analysis

### Steve (Principal SWE)

- Pool capacity: 60 acquires/tick × 1 tick/min = 1/sec avg, sequential = max 1 concurrent. Pool is min=2/max=25. Headroom is 24 conns. Customer-traffic crowd-out is negligible. (P1-1)
- Nested `conn.transaction()` savepoints: 5 existing callsites in `run_assertions_once` (lines 5491, 5511, 5540, 5559) PLUS the `_check_client_portal_zero_evidence_with_data` savepoint (1273) and `_check_db_baseline_guc_drift` (no longer nested — uses pg_db_role_setting). Under per-assertion `admin_transaction`, all of these convert from "savepoint inside admin_connection's no-outer-txn" (where the asyncpg context manager opens a top-level txn) to "savepoint inside admin_transaction's outer txn" (true savepoint). The asyncpg context manager auto-detects this — behavior is preserved. (P0-1, P0-2)
- `_ttl_sweep` belongs in its own `admin_transaction`. (P0-4)
- Cross-check state via SET LOCAL: only `_check_client_portal_zero_evidence_with_data` uses SET LOCAL. The settings are released on savepoint exit (verified by reading the asyncpg savepoint contract — `SET LOCAL` is txn-scoped which means the innermost SUBTRANSACTION, i.e. the savepoint). No cross-check leak. (P1-5)
- `asyncpg.QueryCanceledError` is NOT a subclass of `InterfaceError` (verified via Python MRO inspection: `QueryCanceledError → OperatorInterventionError → PostgresError → PostgresMessage → Exception`). The `except asyncpg.InterfaceError` branch does NOT silently re-classify query-cancel errors. (P0-3 separate concern)
- `_check_substrate_assertions_meta_silent` (1599) reads in-process heartbeat dict, no DB dependency on tick conn. Safe.

### Maya (Legal / Compliance)

- `substrate_violations` UPSERT semantics: read open_rows → compute diff vs `current` → UPSERT or RESOLVE. All within ONE `admin_transaction` block per assertion (P0-2 mandate). Read-then-write consistency preserved. **Correct.**
- `compliance_bundles` (sev2 `l2_resolution_without_decision_record` line 1100), `evidence_bundles`, `fleet_orders`, `cross_org_site_relocate_requests` — all are READ-ONLY by check functions. No chain-attestation writes happen in the substrate loop. RLS posture: `admin_transaction` sets `app.is_admin='true'` (mig 234 admin bypass) IDENTICAL to current `admin_connection` posture. No RLS change.
- Within-tick race on `substrate_violations`: today's design serializes 60+ assertions on one conn so an UPSERT from assertion 5 is visible to assertion 6 ONLY at outer-txn commit (after all 60+ are done). Under per-assertion refactor, an UPSERT from assertion 5 is COMMITTED and visible to assertion 6 immediately. The only assertion that reads `substrate_violations` is `_check_substrate_sla_breach` (line 562). Its SLA thresholds are all multi-minute — a row UPSERTed 50ms ago in the same tick will not trip the SLA. Behavior change is safe. (P1-2)

### Carol (Security / HIPAA)

- 60+ `SET LOCAL app.is_admin TO 'true'` per tick: each is one INSERT-like statement, no cumulative state (LOCAL is txn-scoped). PgBouncer's `DISCARD ALL` reset is irrelevant here because each statement is inside its own transaction — but harmless. No state leak.
- PHI-adjacent reads: substrate checks read `incidents`, `log_entries`, `compliance_bundles`, etc. Per-assertion `admin_transaction` enforces the same `app.is_admin='true'` posture as today's `admin_connection`. Same RLS bypass, same PHI-handling boundary (i.e. substrate engine runs server-side, never leaves the VPS — phiscrub is appliance-egress). No change.
- `admin_audit_log` writes: substrate checks do NOT write to admin_audit_log. The cross-org chain-orphan check (914) and BAA-receipt-unauthorized check (980) READ from chain tables. No write boundary perturbed.

### Coach (Consistency)

- Sibling pattern parity: `admin_transaction(pool) as conn` is the canonical Session 212 / b62c91d2 shape, identical to prometheus_metrics.py:108 + device_sync.py:145 + 226 ratchet sites. Refactor uses the EXACT same shape — no new variant introduced. **Approved on consistency grounds.**
- `test_admin_connection_no_multi_query.py` ratchet: scans for `admin_connection(...)` blocks with 2+ DB calls outside a `conn.transaction()`. After refactor, `assertions_loop` will contain ONE `admin_connection`-free body (the loop directly calls `admin_transaction` per assertion). The class-general test will NOT flag this — it doesn't apply. (Verified by reading lines 58-72 of the test.)
- Cascade-fail band-aid removal: P0-5 mandates same-commit removal. Defense-in-depth argument fails here because the band-aid actively SKIPS valid work in the new design. Delete it.
- `_ttl_sweep` timing invariant: nothing in the codebase asserts "TTL sweep must run AFTER assertions of the same tick." Reading the function (line 5596), it deletes `sigauth_observations WHERE observed_at < NOW() - INTERVAL '24 hours'` — pure time-based, no dependency on substrate_violations state. Order doesn't matter. Run it once per tick after the assertion loop completes. (P0-4)
- PgBouncer SET LOCAL volume: 60+/min on the substrate loop alone, ~86K/day. Tiny per-statement cost. Within PgBouncer's typical 10K+/sec capacity. (P2-3 — just document.)

## What the proposed design got right

- Correctly identifies the architectural root cause (single conn = cascade-fail blast radius = all 60 assertions) rather than papering over the symptom (catch-and-continue).
- Reuses the canonical `admin_transaction(pool)` helper — zero new abstractions, zero new shapes for future readers to learn.
- Preserves check function signatures (`async def _check_X(conn)`) so 60+ check functions stay untouched. Minimal blast radius for the refactor.
- Identifies that `_ttl_sweep` needs to be considered separately (briefed it as Steve's bullet 3 — but the refactor brief itself stopped short of specifying the answer; P0-4 fills the gap).
- Makes the partial-tick data-loss problem go from "lose every check after the first failure" (~50% of checks on a bad tick) to "lose 1 check on a bad tick" (~1.6%). 30x improvement in worst-case fidelity per tick.

## Recommended next step

**APPROVE-WITH-FIXES.** Implementation proceeds once the 5 P0 items are addressed:

1. (P0-1) Update `_check_client_portal_zero_evidence_with_data` docstring to reflect the new outer-txn shape (savepoint behavior unchanged, but the explanation must be).
2. (P0-2) Block boundary: the per-assertion `admin_transaction(pool)` wraps the ENTIRE per-assertion body (check + open_rows fetch + UPSERT/INSERT/RESOLVE), NOT just `a.check(conn)`.
3. (P0-3) Use `import asyncpg` + `except asyncpg.InterfaceError` everywhere. Drop the `_base` private import.
4. (P0-4) `_ttl_sweep` moves to its own `admin_transaction(pool)` block in `assertions_loop`, removing the "if errors == 0" short-circuit.
5. (P0-5) Delete the `conn_dead` band-aid (lines 5416, 5419-5422, 5438) and the explanatory docstring paragraph in the same commit.

The P1-4 test gate (pin `admin_transaction` usage in `run_assertions_once`) is strongly recommended in the same commit to prevent regression to single-outer-conn shape — carry as a same-PR followup if not done.

After P0s land, the cascade-fail bug class is closed structurally rather than patched defensively, and the substrate engine becomes the canonical reference site for per-assertion admin transaction patterns in any future background loop.
