# Gate B — Task #128 fleet_order_fanout_partial_completion (commit 608cef3c)

**Date:** 2026-05-16  **Reviewer:** fork (fresh ctx, 7-lens Class-B)  **Commit:** 608cef3c

## Overall verdict: APPROVE-WITH-FIXES

All 3 Gate A P0s landed correctly. SQL shape verified against real schema (mig 049). PREREQ_SCHEMA DROP+CREATE present + lockstep. Test sweep clean. Two P1s + one P2 surfaced below — none blocking, all can land as immediate follow-ups in next commit.

## Test sweep
**`bash .githooks/full-test-sweep.sh`: 276 passed / 0 failed / 0 skipped (need backend deps).** New `test_fleet_order_fanout_invariant.py` (10 sentinels) wired into pre-push SOURCE_LEVEL_TESTS — verified.

## Per-lens verdict
1. **Steve:** APPROVE-WITH-FIXES — see P1-A (fabricated sibling citation), P2-A (18h-not-24h detection band).
2. **Maya:** APPROVE — no PHI on the read path; audit-log scan is `details->>` jsonb extraction of order UUIDs only; LIMIT 100 bounds spam; no evidence-chain mutation. Counsel Rule 2 clean.
3. **Carol:** APPROVE — runbook is operator-actionable (4 root-cause categories, immediate-action SQL, escalation matrix). Counsel Rule 4 satisfied — invariant IS the orphan-coverage detector for the K-of-N fan-out case.
4. **Coach (DBA):** APPROVE — admin_transaction per-assertion boundary inherited from `run_assertions_once` refactor (Session 220 cascade-fail closure); query is single `await conn.fetch`, no savepoint needed. Action LIKE narrows admin_audit_log scan (no Session 219 COUNT(*) class). CTE + LEFT JOIN shape is correct against real composite PK.
5. **Auditor:** APPROVE — runbook has change log, severity stated, escalation matrix present, related runbooks cross-referenced. Verification section names exact clear-conditions.
6. **PM:** APPROVE — P1-3 (fleet_cli.py:543 cross-link UPDATE best-effort) correctly carried as separate followup with explicit caveat in docstring. Sibling cluster (#117/#129/#130/#131) untouched — correct scope (chain-contention is independent).
7. **Counsel:** APPROVE — Rule 3 privileged chain unchanged (read-only invariant). Rule 4 orphan detection actively strengthened. No leaks to unauth surfaces.

## Findings

### P1-A — Fabricated sibling citation
`assertions.py:998`, `:2959` (description), runbook line 24, Gate A doc, test_function_exists_and_registered_at_sev2 all cite `enable_emergency_access_failed_unack` as the sev2 parity reference. **This assertion does not exist** (`grep 'name="enable_emergency'` returns nothing). The real `*_unack` sibling is `appliance_moved_unack` (assertions.py:2779). Sev2 choice is defensible on its own merits (chain-of-trust-affected fan-out > sev3 threshold), but the cited rationale is invented.

**Fix:** s/enable_emergency_access_failed_unack/appliance_moved_unack/ in 5 callsites (description, function docstring, runbook §"Why sev2", test assertion message, Gate A doc) OR drop the sibling-parity language entirely and justify sev2 from first principles ("chain-of-trust-affected privileged fan-out merits operator-attention tier").

### P1-B — 18h detection band, not 24h
SQL filters `created_at > NOW() - INTERVAL '24 hours' AND created_at < NOW() - INTERVAL '6 hours'` — effective detection window is **6h–24h after issuance (18h band)**. A fan-out unacked at hour 25 silently stops alerting; runbook's "24h passes (rolling window slides past the issuance)" frames this as feature, but operationally a fan-out that's still K-of-N orphaned at day 2 is MORE alarming, not less. Auto-clear at 24h means a Friday-evening fan-out can disappear from the operator panel before Monday triage.

**Fix:** widen upper bound to `INTERVAL '30 days'` (matches audit-log retention) OR drop the upper bound entirely. Keep the 6h lower bound. Update runbook §Verification to remove the "24h slide" exit condition.

### P2-A — `jsonb_array_length` is called twice
CTE references `jsonb_array_length(al.details->'fleet_order_ids')` in both SELECT list (`fan_out_size`) and WHERE filter. Minor — Postgres should fold via constant-expression CSE but not guaranteed for jsonb extract. Convert to subquery materialization or accept the (likely zero) cost. Skip if Coach signs off.

## Sibling-issue scan (#117, #118, #129/130/131 cluster)
- **#118 P1-3 carry (fleet_cli.py:543):** correctly filed as separate followup; cross-link UPDATE failure leaves invariant blind to that fan-out's existence. Not addressable from THIS invariant — needs upstream guarantee (transaction-wrap the audit-log insert + cross-link UPDATE atomically, OR a separate `cross_link_update_failed` invariant that scans for privileged-access bundles without `fleet_order_ids` key). File as new task.
- **#129/130/131 (chain-contention):** out of scope, correctly untouched.
- **#117 sub-A Gate B P2-1 lesson** ("runbook references invalid sites.status='paused'"): this runbook does NOT reference sites.status values — clean.

## Recommended next steps
1. Fix P1-A (sibling citation, 5 callsites) in next commit.
2. Decide P1-B (widen window vs. accept 24h auto-clear); update runbook either way.
3. File new task: "cross-link UPDATE-failure detector" (mirror of P1-3 carry, structural fix for the invariant-blind-spot class).

**Gate B verdict: APPROVE-WITH-FIXES** — ship #128 as complete after P1-A + P1-B addressed in same session (single-commit hotfix, no second Gate A needed — pure source-only doc/SQL parameter change, no schema/logic shift).
