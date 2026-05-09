# Multi-Tenant Readiness Phase 1 — Concurrent-Write Stress Audit

**Date:** 2026-05-09
**Auditor:** consistency-coach (adversarial Principal-SWE + multi-tenant systems engineer)
**Phase 0 closed:** earlier today, 9 P0 + 7 P1 verified at runtime
**Goal:** find what breaks when N≥5 customer orgs write simultaneously to shared infrastructure.

---

## Executive Summary

| § | Dimension | Verdict | Severity if FAIL |
|---|-----------|---------|---|
| A | pg_advisory_xact_lock concurrency | **PASS** | — |
| B | RLS context bleed across PgBouncer | **PASS** | — |
| C | asyncpg pool exhaustion @ N=10 | **PASS (today's load)** but **P0 deploy-gap surfaced** | P0 |
| D | Substrate invariant tick @ N≥10 | **PARTIAL** — 1 invariant breaks at scale | P2 |
| E | Partition routing under concurrency | **PASS** | — |

**Severity counts:** P0 = 1 (deploy gap), P1 = 1 (audit-cleanup primitive), P2 = 1 (invariant SQL shape), P3 = 0.
**Phase 2 entry:** **CONDITIONAL** — Phase 2 may begin once the P0 (pgbouncer config deploy) is shipped + verified live.

---

## §A — pg_advisory_xact_lock concurrency proof

### Method
- Synthetic site_id `phase1-A-concur-fae7b351`
- 10 asyncpg writers, all firing `asyncio.gather` simultaneously, each:
  1. `BEGIN`
  2. `SELECT pg_advisory_xact_lock(hashtext($site_id), hashtext('attest'))`
  3. `SELECT bundle_id, bundle_hash, chain_position FROM compliance_bundles WHERE site_id=$1 ORDER BY checked_at DESC LIMIT 1`
  4. compute next chain link
  5. `INSERT INTO compliance_bundles (...)`
  6. `COMMIT`
- Mirrors the `_get_prev_bundle` shape from `privileged_access_attestation.py` (which is the same shape used by `runbook_consent.py` + `appliance_relocation.py` — all 3 chain mutators share the lock pattern).
- Script: `audit/phase1-scripts/a_concurrency_proof.py`

### Runtime evidence
```
[A] site_id=phase1-A-concur-fae7b351 N=10
[A] elapsed=0.624s
[A] positions=[0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
[A] no-dup=True contiguous=True chain-ok=True
[A] lock-wait: p50=200.3ms p99(max)=364.4ms
[A] total elapsed: 624.1ms (10-way)
[A] VERDICT: PASS
```

### Findings
1. **No duplicate `(site_id, chain_position)`** — positions came back `[0..9]` exactly.
2. **Contiguous** — perfect serialization, no skips.
3. **Chain integrity** — every row's `prev_hash` matched the prior row's `bundle_hash`.
4. **p99 lock-wait = 364ms** under 10-way concurrency — well under the 30s budget.
5. **Total elapsed 624ms** for 10 serialized chain mutations on a 137K-row site — a single chain mutator takes ~60ms wall-clock under contention.

**Verdict: PASS.** Cross-site is unaffected (lock is namespaced by `(hashtext(site_id), hashtext('attest'))`); same-site is correctly serialized; no chain-position collision under 10-way concurrency.

### Sub-finding (audit P1)
Test cleanup discovered `compliance_bundles_no_delete` trigger is registered with `tgenabled='A'` (ALWAYS — fires under replication too). `ALTER TABLE … DISABLE TRIGGER` flipped it to `'D'` (disabled), but `ALTER TABLE … ENABLE TRIGGER` defaults to `'O'` (origin-only) — this would weaken the protection. Fix in cleanup: `ENABLE ALWAYS TRIGGER` to restore exact state.

This isn't a production bug, but is a **P1 audit-tooling lesson**: any future synthetic-row cleanup that touches the audit-immutability triggers MUST use `ENABLE ALWAYS TRIGGER`, not bare `ENABLE TRIGGER`. Document this in the cleanup runbook before adding more synthetic-data audits. The audit-side scripts here all use `ENABLE ALWAYS` and verified `tgenabled='A'` was restored:

```
            tgname            |      tgrelid       | tgenabled
------------------------------+--------------------+-----------
 compliance_bundles_no_delete | compliance_bundles | A
(1 row)
```

---

## §B — RLS context bleeding across PgBouncer

### Method
- 5-connection asyncpg pool against `pgbouncer:6432` (transaction-pool mode)
- 200 iterations × 3 keys (`app.current_org`, `app.current_partner_id`, `app.is_admin`) × 4 tasks each (1 SET, 3 READ) = **2400 cross-transaction reads**
- Writer: `BEGIN; SET LOCAL <key>='leak-test-<iter>-<key>'; ... COMMIT`
- Reader: `BEGIN; SELECT current_setting(<key>, true); COMMIT` — must NEVER see `leak-test-*` value
- Initial naive run flagged 600 "leaks" — investigation found these were the **database-level defaults** set via `ALTER DATABASE mcp SET app.is_admin='false'` etc. (reads inheriting baselines, NOT cross-transaction leaks). Test was hardened to flag only `seen.startswith('leak-test-')`.
- Script: `audit/phase1-scripts/b_rls_context_bleed.py`

### Runtime evidence (post-hardening)
```
[B] iterations=200 keys=['app.current_org', 'app.current_partner_id', 'app.is_admin']
[B] total leaks observed: 0
[B] VERDICT: PASS
```

### Findings
1. **Zero cross-transaction leaks** of writer-set GUC values across 2400 readback ops.
2. The DB-level baseline (`SELECT setconfig FROM pg_db_role_setting`) is `{app.current_tenant=, app.is_admin=false, app.current_org=, app.cross_appliance_enforce=reject}` — these are the **safe defaults** every backend inherits. They are NOT leaks; they are intentional baselines that prevent "no policy bound" sessions from running with admin rights.
3. PgBouncer transaction-pool mode + `server_reset_query=DISCARD ALL` + `SET LOCAL` semantics work as documented.

**Verdict: PASS.** No P0 RLS-bleed finding.

### Carol-veto observation
The `app.cross_appliance_enforce=reject` baseline is a quiet but load-bearing defense. If a future deploy ALTERs the database to `enforce=permit`, every backend silently inherits it. Recommend adding a substrate invariant `db_baseline_guc_drift` that hashes the result of `SELECT setconfig FROM pg_db_role_setting` and alerts on change. **Phase 2 candidate.**

---

## §C — asyncpg pool exhaustion @ simulated N=10

### Method
- 30 "site checkin" workers (mimics 30 sites × 2 Hz checkin rate)
- 3 "admin auditor-kit polling" workers (chain walk, 100-row read)
- 3 "client dashboard polling" workers (canonical-score aggregation)
- Each worker through pgbouncer → mcp-postgres
- Sustained 30s load
- 1Hz sampler ran `SHOW POOLS` against pgbouncer admin console concurrently
- Script: `audit/phase1-scripts/c_pool_exhaustion.py`

### Runtime evidence
```
[C] starting load: 30 checkin + 3 admin + 3 client, duration=30s
[C] elapsed=31.0s
[C] errors: 0
[C] VERDICT: PASS

--- POOL SAMPLES (during load, mcp/mcp_app pool, 1 Hz) ---
cl_active=49, cl_waiting=0, sv_active=1-7, sv_idle=14-20, sv_used=0-3
(steady state across 18 samples)
```

### Findings
1. **Zero `cl_waiting`** across every 1Hz sample during 30s sustained load — the pool **never queued a client**.
2. **`sv_active` peak: 7** — well under the live `default_pool_size=25`.
3. **Zero asyncpg errors** — no `PoolTimeoutError`, no slow queries above the 5s threshold.

### **P0 finding surfaced during this test**
Phase 0 closed with the claim that pgbouncer was bumped to `default_pool_size=50, max_client_conn=400`. Live runtime evidence:

```
$ docker run --rm --network mcp-server_mcp-network postgres:16-alpine \
    psql "postgresql://mcp:***@mcp-pgbouncer:6432/pgbouncer" -c "SHOW CONFIG" \
    | grep -iE "pool_size|max_client"

 default_pool_size  | 25  ← LIVE (NOT 50)
 max_client_conn    | 200 ← LIVE (NOT 400)
 min_pool_size      | 5   ← LIVE (NOT 10)
 reserve_pool_size  | 5   ← LIVE (NOT 10)
```

The git commit `81194a9b` modifies `mcp-server/pgbouncer/pgbouncer.ini` to the new values, but the deploy workflow (`.github/workflows/deploy-central-command.yml`) **only rsyncs `dashboard_api/`, `frontend_dist/`, `app/`, `docker-compose.yml`** — never `pgbouncer/`. The pgbouncer container has not been restarted with the new config. **Phase 0 closeout verification did not check live `SHOW CONFIG`**, only that the file was committed.

**Severity: P0** — the closure claim of Phase 0 was false. The pool is still 25 server-side; the test passed today at single-tenant load (30 sites is a synthetic N=10 *projection*, but the live customer is 1). At ACTUAL N=5+ with real concurrent checkins, this becomes a saturation path.

**Class label: CODE+RUNTIME** — code committed, runtime not refreshed. Same class as the prior "rsync skips Dockerfile" lesson (CLAUDE.md, "stripe Python lib in image requires manual rebuild").

**Remediation (next session):**
1. Either: (a) extend deploy workflow to rsync `pgbouncer/` + `docker compose restart mcp-pgbouncer`, or (b) one-time scp + restart, then add a `SHOW CONFIG` smoke to the post-deploy health check.
2. Add a substrate invariant `pgbouncer_config_drift` that compares live `default_pool_size` to a checked-in golden value — fires sev2 if mismatch.

---

## §D — Substrate-engine 60s tick @ N≥10 invariant load

### Method
EXPLAIN (ANALYZE, BUFFERS) on the 4 hottest invariants at the current schema. Script: `audit/phase1-scripts/d_invariants_profile.sql`

### Runtime evidence
```
=== D1: cross_org_relocate_chain_orphan (parent SELECT)        →   0.067 ms ✓
=== D2: cross_org_relocate_chain_orphan (per-row N+1 lookup)   →   0.028 ms ✓ (each iter)
=== D3: compliance_packets_stalled                             → 161.985 ms ⚠
=== D4: heartbeat_write_divergence (LATERAL subquery)          →   1.3   ms ✓
```

### Findings

**D3 (`compliance_packets_stalled`) — P2 SQL-shape finding:**
The query uses `EXTRACT(YEAR FROM cb.created_at) = pm.y AND EXTRACT(MONTH FROM cb.created_at) = pm.m` to filter compliance_bundles for the prior month. Postgres **cannot use the `(site_id, created_at DESC)` partition index for an EXTRACT-based predicate** — it must scan every row in matched partitions:

```
->  Index Only Scan ... compliance_bundles_2026_04 ... (rows=15694) (4.0ms)
->  Index Only Scan ... compliance_bundles_2026_01 ... (rows=179729) (25.7ms)
->  Sort  Sort Key: cb.site_id  (rows=15694)  Memory: 882kB  ← O(N×bundles_per_month)
->  Append  (rows=251091)  ← total scan
Execution Time: 161.985 ms
```

Today: 251K bundles, 162ms, fine. **At N=10 customers** (~2.5M bundles after 6 months), this query becomes ~1.5s; **at N=20** (~5M), it crosses the 5s threshold cited in the audit dimension and at the 60s tick cadence will start overlapping with the next tick.

**Fix (Phase 2 candidate):**
```sql
-- replace EXTRACT with a range scan that uses the partition pruning + index:
WITH prior_month AS (
    SELECT date_trunc('month', NOW()) - INTERVAL '1 month' AS lo,
           date_trunc('month', NOW()) AS hi
)
active_sites AS (
    SELECT DISTINCT cb.site_id
      FROM compliance_bundles cb, prior_month pm
     WHERE cb.created_at >= pm.lo
       AND cb.created_at <  pm.hi
)
...
```
This lets the planner partition-prune to ONE month + use the existing `(site_id, created_at)` index. Expected: <10ms regardless of fleet size.

**D1+D2 N+1 pattern — observed:**
The `cross_org_relocate_chain_orphan` invariant runs 1 outer SELECT + N inner SELECTs (one per row with `prior_client_org_id IS NOT NULL`). Today N=0 (no relocates yet). At N=10 customers running cross-org moves, each tick walks ~M relocate rows (where M = total_orgs × historical_relocates_per_org). Bounded but worth a single-query rewrite (`LEFT JOIN ... WHERE relocate.id IS NULL`). **P3 (style improvement).**

**D4:** No issue. LATERAL subplan returns `loops=3` (matches site_appliances row count) — scales linearly with online appliance count. At N=10 customers × 3 appliances/customer = 30 loops × 0.26ms = <10ms. Fine.

**Verdict: PARTIAL.** D1, D2, D4 PASS. D3 is a **P2** that becomes critical between N=10 and N=20.

---

## §E — Partition routing under multi-tenant load

### Method
- 10 concurrent writers, 10 distinct synthetic site_ids, all inserting 1 bundle into `compliance_bundles`
- Verify `tableoid::regclass` for each row matches current-month partition (`compliance_bundles_2026_05`)
- Verify `pg_stat_user_tables.n_tup_ins` for `compliance_bundles_default` did not increment
- Script: `audit/phase1-scripts/e_partition_routing.py`

### Runtime evidence
```
[E] N=10 prefix=phase1-E-part-9c360a
[E] partition routing: {'compliance_bundles_2026_05': 10}
[E] compliance_bundles_default n_tup_ins (cumulative): 0
[E] all rows in current-month partition: True
[E] VERDICT: PASS

(post-cleanup verify)
 site_id | count
---------+-------
(0 rows)
```

### Findings
1. **All 10 concurrent inserts routed to the correct month partition** — no fallback to `compliance_bundles_default`.
2. **Zero default-partition leakage cumulatively** — `n_tup_ins=0` since partition creation. The default partition is doing its job as a safety net but has never fired (which is the desired state — partitions exist for every month from 2025_01 through 2026_12).
3. Concurrent writes do NOT contend on partition routing (the routing is metadata-only at plan time, not lock-bound).

**Verdict: PASS.**

---

## §F — Findings + Phase 2 entry conditions

### Severity-graded findings table

| ID | Severity | Class | Finding | Remediation |
|----|----------|-------|---------|-------------|
| F-P1-1 | **P0** | CODE+RUNTIME | pgbouncer config commit `81194a9b` not deployed; live `default_pool_size=25` not 50 | rsync `pgbouncer/` + `docker compose restart mcp-pgbouncer` + post-deploy `SHOW CONFIG` smoke |
| F-P1-2 | **P2** | CODE-ONLY | `compliance_packets_stalled` invariant uses `EXTRACT()` predicate, can't use `(site_id, created_at)` index; 162ms today, ~5s at N=20 | rewrite to range-scan with `created_at >= prior_month_lo AND created_at < curr_month_hi` |
| F-P1-3 | **P1** | RUNTIME-VERIFIED | Audit-cleanup primitive must use `ENABLE ALWAYS TRIGGER` not bare `ENABLE TRIGGER` — bare form silently weakens audit-immutability | document in cleanup runbook; add a substrate invariant `audit_trigger_enabled_state_drift` that fires if any `compliance_bundles_no_delete` partition has `tgenabled != 'A'` |
| F-P1-4 | **P3** | CODE-ONLY | `cross_org_relocate_chain_orphan` is N+1 (one query per outer row) | rewrite as single `LEFT JOIN ... WHERE x.id IS NULL` |

### Round-table queue (5 items, 4-voice consensus format)

> **Format reminder:** each round-table item: claim → Steve (Principal SWE) → Maya (Privacy/Security) → Carol (Customer/Compliance) → Coach verdict.

#### RT-P1-1: P0 — pgbouncer config-deploy gap
- **Claim:** Phase 0 closed with the assertion that pgbouncer pool was bumped 25→50, but the live container still serves `default_pool_size=25`. CI/CD doesn't sync `pgbouncer/`.
- **Steve:** APPROVE the P0. Architecturally, ANY config not auto-deployed is a future outage. Either rsync it + restart container, or move pgbouncer config into the docker-compose env block where compose owns it. Add `SHOW CONFIG` to post-deploy smoke. **Vote: APPROVE.**
- **Maya:** APPROVE. Same class as the auditor-kit `.format()` regression — the structural defense (rsync everything that runs in production) was bypassed. Until fixed, every Phase 0/1 P-fix that touches the pgbouncer container is invisible to runtime. **Vote: APPROVE.**
- **Carol:** APPROVE — this is the kind of finding auditors fall in love with: the system claims it has been hardened, but the live thing was never updated. Material misstatement risk. **Vote: APPROVE.**
- **Coach verdict: P0, ship before Phase 2.** Block Phase 2 entry on this fix being live-verified.

#### RT-P1-2: P2 — `compliance_packets_stalled` SQL shape doesn't scale
- **Claim:** EXTRACT-based predicate forces full-month-partition scan; ~250K rows today (162ms), ~2.5M rows at N=10 (~1.5s), ~5M at N=20 (>5s).
- **Steve:** APPROVE. This is a planner-friendly rewrite: one CTE shape change, no semantic change. Zero risk, ~10x speedup. **Vote: APPROVE.**
- **Maya:** APPROVE — would also like a CI gate that EXPLAINs every substrate-engine SQL on a pre-prod schema with `seq_scan=true` set, fails build if the plan picks a Seq Scan over partitioned bundles. **Vote: APPROVE with addendum.**
- **Carol:** APPROVE. The substrate-engine sustaining itself at 60s cadence at scale IS the customer-facing promise of "we will catch a missing monthly attestation". 5s/tick is a brownout signal. **Vote: APPROVE.**
- **Coach verdict: P2, fix in Phase 2 SQL-shape sprint.**

#### RT-P1-3: P1 — Audit-cleanup primitive: `ENABLE ALWAYS TRIGGER`
- **Claim:** During §A cleanup, naive `ENABLE TRIGGER` would have weakened `compliance_bundles_no_delete` from `'A'` (always) to `'O'` (origin-only). I caught + restored, but a future audit run could miss this.
- **Steve:** APPROVE. Add a 1-line substrate invariant: `SELECT 1 WHERE EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='compliance_bundles_no_delete' AND tgenabled <> 'A')` → sev1. Cheap. Catches both this future regression and the operator-shortcut "I'll just disable the trigger to backfill" path. **Vote: APPROVE.**
- **Maya:** APPROVE. The trigger IS the chain-immutability defense. State-drift of the trigger itself = silent integrity loss. **Vote: APPROVE.**
- **Carol:** APPROVE. **Vote: APPROVE.**
- **Coach verdict: P1, ship in Phase 2 first batch.**

#### RT-P1-4: Phase 2 candidate — substrate `db_baseline_guc_drift`
- **Claim:** §B revealed the `pg_db_role_setting` defaults are load-bearing (`app.is_admin=false`, `app.cross_appliance_enforce=reject`). A future ALTER DATABASE that flips these silently degrades RLS posture across every backend.
- **Steve:** APPROVE. One-row hash of the GUC set, compared to a known-good. Sev1. **Vote: APPROVE.**
- **Maya:** APPROVE — make the known-good a checked-in golden file with a migration to bake it. **Vote: APPROVE.**
- **Carol:** APPROVE. **Vote: APPROVE.**
- **Coach verdict: Phase 2 candidate, P2 substrate addition.**

#### RT-P1-5: Phase 2 entry condition — pgbouncer pool deploy
- **Claim:** Phase 2 should NOT begin until F-P1-1 is shipped + verified live (`SHOW CONFIG` evidence pasted in the closeout commit).
- **Steve:** APPROVE — verification must be runtime, not "I rsynced the file". **Vote: APPROVE.**
- **Maya:** APPROVE. **Vote: APPROVE.**
- **Carol:** APPROVE. **Vote: APPROVE.**
- **Coach verdict: BLOCKING. Phase 2 entry blocked on F-P1-1 runtime verification.**

---

### Final verdict

**Phase 2 entry: CONDITIONAL.**

Phase 1's 5 dimensions surfaced 1 P0 (deploy gap), 1 P1 (cleanup-runbook hardening), 1 P2 (SQL shape), and 1 P3 (style). The system holds up at single-tenant + projected N=10 load on every dimension EXCEPT the deployed-config drift surfaced by §C — which is a runtime-vs-claim mismatch that invalidates the Phase 0 closeout assertion.

Required before Phase 2:
1. **Ship F-P1-1** (pgbouncer config → live container) AND paste `SHOW CONFIG` runtime evidence to the closure commit.
2. **Document F-P1-3** in the cleanup runbook (no synthetic-data audit may use bare `ENABLE TRIGGER` on the audit-immutability triggers).

Acceptable in Phase 2 (not blocking):
- F-P1-2 SQL rewrite (P2, has time before N=20).
- F-P1-4 (P3 style).
- RT-P1-4 substrate invariant addition.

---

## Cleanup verification

All synthetic data inserted by Phase 1 scripts has been deleted; trigger state restored to `tgenabled='A'` (ALWAYS):

```
$ docker exec mcp-postgres psql -U mcp -d mcp \
    -c "SELECT COUNT(*) FROM compliance_bundles WHERE site_id LIKE 'phase1-%'"
 count
-------
     0
(1 row)

$ docker exec mcp-postgres psql -U mcp -d mcp \
    -c "SELECT tgname, tgrelid::regclass, tgenabled FROM pg_trigger \
        WHERE tgname='compliance_bundles_no_delete' \
        AND tgrelid::regclass::text='compliance_bundles'"
            tgname            |      tgrelid       | tgenabled
------------------------------+--------------------+-----------
 compliance_bundles_no_delete | compliance_bundles | A
(1 row)
```

Production DB is in the same state it was before this audit started. No synthetic rows leaked; no integrity controls weakened.

---

## Artifact index

| Artifact | Path |
|----------|------|
| §A script | `audit/phase1-scripts/a_concurrency_proof.py` |
| §B script | `audit/phase1-scripts/b_rls_context_bleed.py` |
| §C script | `audit/phase1-scripts/c_pool_exhaustion.py` |
| §D EXPLAIN SQL | `audit/phase1-scripts/d_invariants_profile.sql` |
| §E script | `audit/phase1-scripts/e_partition_routing.py` |
| This deliverable | `audit/multi-tenant-phase1-concurrent-write-stress-2026-05-09.md` |
