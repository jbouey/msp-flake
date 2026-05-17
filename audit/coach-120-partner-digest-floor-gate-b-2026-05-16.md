# Gate B — #120 Partner Digest FLOOR (de696071) Adversarial 2nd-Eye Review

**Date:** 2026-05-16 · **Fork:** fresh-context Gate B
**Reviewing:** commit `de696071` (post-Gate-A implementation)
**Sweep:** `bash .githooks/full-test-sweep.sh` → **279 passed, 0 skipped**

## VERDICT: **BLOCK**

One P0 runtime-fatal bug (wrong column name on a partitioned table — query throws on every Friday partner digest), one P0 correctness bug (multiplicative over-count in `offline_24h`/`offline_7d` aggregates). Tests pass because the column-validation gate `tests/test_sql_columns_match_schema.py` only parses `INSERT`/`UPDATE`, not `SELECT FROM LATERAL`. Gate A copied the bad column name from the design spec verbatim; Gate B did not verify against the prod schema fixture.

---

## PER-LENS

- **Steve (SWE):** BLOCK — receiver column wrong (`received_at` ≠ `observed_at`).
- **Maya (HIPAA):** APPROVE — opaque-mode tile labels + no per-row keys + aggregate-only verified in `test_fleet_health_block_only_aggregate_counters`.
- **Carol (Ops):** APPROVE-WITH-FIX — once query is fixed, 4 tiles + tone-adaptive amber/green is sound. Stuck-orders tile has NO right-border (last tile) — visual inconsistency only.
- **Coach (DBA):** BLOCK — multiplicative JOIN-explosion poisoning offline counters; LATERAL prunes 30d partitions correctly IF column name is right.
- **Auditor:** APPROVE — no audit row required (advisory weekly digest, not privileged action). Gate A already cleared.
- **PM:** APPROVE — FLOOR scope respected; SPIKE deferred per Gate A.
- **Counsel:** APPROVE — Rule 2 + 7 enforced in test sentinels; Rule 6 (BAA) deferred to SPIKE track is acceptable for weekly aggregate.

## P0 (MUST FIX before re-deploy)

1. **`background_tasks.py:2103,2105` — column `received_at` does not exist on `appliance_heartbeats`.** Actual column per `mig 191_appliance_heartbeats.sql` + `prod_columns.json` is **`observed_at`** (partition key). Query throws `UndefinedColumnError` on EVERY Friday partner digest run for any partner with sites + appliances. Untraceable until Friday's cron fires. The Gate A SQL skeleton used `received_at` (line 35 of `audit/coach-120-partner-digest-gate-a-2026-05-16.md`); Gate B should have grepped the schema fixture. **Fix:** s/received_at/observed_at/g in the LATERAL — partition pruning unchanged.

2. **`background_tasks.py:2083–2090` — `offline_24h`/`offline_7d` over-count via fan-out.** `COUNT(*) FILTER (...)` without DISTINCT multiplies each (sa) row by matching (fo) rows (via `fo.parameters->>'target_appliance_id' = sa.appliance_id::text`) and by client_orgs matches. Partner with 250 appliances × avg 3 active fleet_orders per appliance × 1 client_org reports `offline_24h ≈ 750` when ground truth is 250. **Fix:** `COUNT(DISTINCT sa.appliance_id) FILTER (WHERE ...)` on both offline tiles. `chronic_unack_orders` + `baa_expiring_30d` already use DISTINCT — correct. Add a unit test that synthesizes 1 offline appliance + 3 active orders and asserts offline_24h=1.

## P1 (in-commit OR named followup)

1. **`BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL '30 days'` excludes past-expired BAAs.** Gate A spec said "expiring 30d" (look-ahead), so behavior matches spec — but ALREADY-EXPIRED BAAs are silently invisible in this digest. Counsel Rule 6 enforcement triad (Session 220 #52) catches new mutations against expired BAA at substrate level (sev1), so this is not a P0 — but the operator looking at this tile would NOT see "BAA expired 14d ago". Followup: rename tile to `BAA expiring ≤30d` OR widen to `co.baa_expiration_date <= CURRENT_DATE + INTERVAL '30 days'` (includes expired-already). Decision belongs to Maya, not me.

2. **`test_fleet_health_block_only_aggregate_counters` only checks `fleet_health.get("KEY"` literal.** A future hand-edit using `fleet_health["clinic_name"]` (subscript) bypasses the sentinel. Broaden to also check `fleet_health\[` patterns.

3. **`test_sql_columns_match_schema.py` does NOT scan SELECT-FROM-LATERAL.** This is the class root-cause for P0#1 — the gate would have caught `received_at` in an INSERT/UPDATE but not in a fetchrow SELECT. Extend the parser to validate SELECT column refs. Class task — bigger than #120.

## P2 (cosmetic, non-blocking)

- `email_alerts.py:1137-1142` — the 4th tile ("Stuck orders") has no `border-right` while the first 3 do. Minor visual inconsistency — last tile should be borderless by design but worth verifying intent.
- `email_alerts.py:1122` — hint-conditional string-interp uses nested `<div style="..."` quotes inside an f-string expression escape; works but fragile (RT33 deploy-outage class). Extract to a variable if extended.

## #119 EOFError fix (fleet_cli.py:770-780)

APPROVE. Fail-closed `sys.exit(...)` is correct; `KeyboardInterrupt` propagates as SIGINT (intentional exit, no special handling needed). Message tells the operator how to recover. No P0/P1.

## ANTI-SCOPE VERIFICATION

- No SPIKE-track code shipped — confirmed.
- No BAA-suppression on weekly digest — confirmed (Gate A explicitly deferred).
- No Jinja2 template migration — confirmed (existing f-string surface unchanged).
- No new substrate invariant — confirmed (deferred per Gate A).
- No `partner_fleet_spike_alert` audit row — confirmed (no spike code).
- No MV reads — confirmed (`test_gather_fleet_health_no_materialized_view`).

Scope discipline: **clean.**

## RECOMMENDATION

REVERT `de696071` OR ship a follow-up commit fixing P0#1 + P0#2 BEFORE Friday cron. The runtime bug is silent until cron fires; ratchets won't catch it. Add a `test_partner_digest_query_smoke_pg` that actually runs the SQL against the pg fixture (requires `tests/conftest_pg.py` wiring — sibling to `test_startup_invariants_pg.py`).

---
**Sweep:** 279/279 passed. None of the existing tests cover the actual SELECT query against a real DB; this is the same class as the Session 220 mig-303 + the RT33 deploys.
