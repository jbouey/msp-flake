# Gate B — #130 Sub-C.1 chain_lock_metrics + evidence_chain timer wrap

**Commit:** `d3e7188a` (2026-05-16)
**Reviewer:** Fork-based 7-lens Gate B (fresh context)
**Verdict:** **APPROVE-WITH-FIXES** (1 P1, 2 P2 — Sub-C.1 IS truly passive on production; the issue is a partial-strength claim in the commit body about the serialization detector that should be tightened before Sub-C.2 lands.)

## Test sweep

`bash .githooks/full-test-sweep.sh` → **280 passed / 0 failed / 0 skipped** (clean).

## Per-lens verdict

1. **Steve (Principal SWE)** — APPROVE. Context-manager shape correct; `time.monotonic()` is the right clock; `id(asyncio.current_task())` handles `None` via Python (`id(None)` returns a stable int — defensible, though slightly muddy semantics). Function-scope import is cached by `sys.modules` after first call. Zero allocations on non-allowlisted sites verified (early `yield` before `_ensure_site_state`).
2. **Maya (Security/Privacy/HIPAA)** — APPROVE. No PHI. site_id label is operator-visible only via Prometheus scrape (already operator-only surface). Counsel Rule 4 satisfied — operator-only metric. Allowlist literal `load-test-chain-contention-site` is non-sensitive.
3. **Carol (CCIE Network/Ops)** — APPROVE. Prometheus text-format is valid (HELP + TYPE per family, summary quantile labels, counter shape correct, trailing `\n` present). Empty-state rendering produces valid output (no data rows, but scrape will succeed).
4. **Coach (DBA)** — APPROVE-WITH-FIXES (see P1-1). Wrap correctly preserves `pg_advisory_xact_lock` transactional semantics — lock is acquired inside the txn and released at commit/rollback. The timer doesn't interfere with txn state. **However**, the wrap window (only the conn.execute) is correct for wait-time measurement but creates a known weakness for the in-process holder-set detector (see P1-1).
5. **Auditor** — APPROVE. HELP text on serialization counter explicitly says "ANY non-zero value indicates the advisory lock is not serializing" — actionable. process-local caveat present on all three families.
6. **PM (scope discipline)** — APPROVE. Sub-C.1 is genuinely passive: production sites take the `_allowed()` early-yield branch (one frozenset membership check, no dict/deque touch). No new endpoints exposed. No DB writes. Zero customer-facing surface.
7. **Counsel (Rule 1, canonical metric)** — APPROVE. These metrics are operator-only diagnostic instrumentation for load-test traffic; they are NOT customer-facing. No canonical-source obligation triggered.

## Findings

### P1-1 — Holder-set detector is weaker in production than commit body implies
The `_critical_section_holders` set is populated on `__aenter__` and discarded on `__aexit__`. The wrap window contains ONLY the `conn.execute` (lock-acquire) — so the holder lives in the set for the duration of the acquire call only, NOT the full critical section. The test `test_serialization_violation_detected_when_two_tasks_overlap` only passes because the test inserts a 20ms `asyncio.sleep` INSIDE the with-block to extend the holder window. In real production usage on the load-test site, two simultaneous failed-to-serialize lock-acquires would both return fast → both __aexit__ fire fast → very narrow overlap window → likely no violation tripped. **Recommendation:** Either (a) extend the wrap to enclose the prev_bundle SELECT + INSERT (the actual critical section), accepting that wait-time-percentile semantics conflate with critical-section work; OR (b) document explicitly in the commit body + HELP text that the detector primarily catches the in-process re-entrancy case, with cross-process/cross-replica serialization-failure detection delegated to `bundle_chain_position_gap` invariant. Sub-C.2 should not ship without this disambiguation.

### P2-1 — `_critical_section_holders[site_id]` is created at first sample but never bounded; site_ids that hit the cardinality cap silently no-op without a WARN log emission
Comment at line 78 says "Operator-visible WARN once via module-level set tracking" but no logger call exists. Add `logger.warning("chain_lock_metrics_cap_reached", extra={"site_id": site_id})` once per dropped site_id.

### P2-2 — `_reset_for_test()` does not clear a `_warned_sites` set (because none exists). Couple with P2-1 fix.

## Sub-C.1 passivity confirmation

**Production sites: zero risk.** Verified by:
- `_allowed()` short-circuit yields immediately for any site_id not in the 1-element frozenset
- No imports of psycopg/asyncpg/db pools — pure stdlib (asyncio, contextlib, time, collections)
- evidence_chain.py wrap adds 1 function-scope import (sys.modules cached) + 1 frozenset `in` check + early yield → measurable cost ≪ 1µs/call on production sites
- No new DB writes, no new endpoints, no new scheduled work

Word count: 597.
