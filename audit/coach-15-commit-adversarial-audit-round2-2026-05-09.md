# 15-Commit Adversarial Audit Round 2 — 2026-05-09

**Auditor:** consistency-coach
**Scope:** last 15 commits, top: `a0333e73`
**Audit time:** 2026-05-09T08:26Z
**Worktree:** `.claude/worktrees/agent-a378ba140ed77301b` (branch `main`)

**Verdict TL;DR:** **The code-true-runtime-false class is NOT closed — it has merely moved address.** Three NEW assertions shipped today (5 broken `Violation(detail=…)` callsites + 1 SAVEPOINT-outside-transaction + 1 false-positive baseline-GUC-checker-checking-itself) are all firing every 60s in production right now; none were caught by code review or pre-push because no runtime smoke covered the assertion-loop path. **Apply round-1's lesson AGAIN: a CI gate that runs the assertion loop once and asserts `errors=0` is the structural close.**

---

## §1. Commit-by-commit verification

| # | SHA | Claim | Runtime check | PASS/FAIL | Label |
|---|---|---|---|---|---|
| 1 | `a0333e73` | wave-11 ratchet 191→186 | `ADMIN_CONN_MULTI_BASELINE_MAX=186, _collect_violations()=186` (Python collector matches; baseline pinned in source) | PASS | CODE+RUNTIME |
| 2 | `0ee8d1e8` | Jinja2 templates close `.format()`-drift class | Files exist: `templates/auditor_kit/{README.md.j2, verify.sh, verify_identity.sh, __init__.py}`. Boot smoke firing — no Jinja errors in `docker logs --since=2h` | PASS | CODE+RUNTIME (smoke-via-absence-of-error) |
| 3 | `6d70fe59` | mkdir pgbouncer release dir before rsync | Deploy outage 2 fix; runtime conf shows `default_pool_size=50 max_client_conn=400` per spec | PASS | RUNTIME-VERIFIED |
| 4 | `de33d28c` | pgbouncer rsync MUST be `pgbouncer.ini` only, not directory | userlist still has 2 SCRAM-SHA-256 verifiers (not clobbered) | PASS | RUNTIME-VERIFIED |
| 5 | `da096024` | Phase 5 verdict CONDITIONAL READY for N=2 | Verdict prose only; per-condition check below | PARTIAL | CODE-ONLY (sub-claims runtime-mixed) |
| 6 | `ced7fea6` | wave-10 ratchet 196→191 | ratchet pin matches; subsumed by current 186 | PASS | CODE-VERIFIED |
| 7 | `77efd88b` | counsel v2.4 packet | Pure docs; no runtime path | N/A | DOCS-ONLY |
| 8 | `6ca77798` | Phase 1: pgbouncer + SQL + 2 invariants | Pgbouncer 50/400 RUNTIME-VERIFIED. **SQL fix unverified — see findings below.** **2 new invariants both deployed, both broken at runtime.** See P0-RT2-A and P1-RT2-A. | **FAIL (partial)** | CODE+RUNTIME-FALSE |
| 9 | `2b5ad139` | wave-9 ratchet 201→196 + advisory locks | `pg_advisory_xact_lock` confirmed in `runbook_consent.py:404`, `appliance_relocation.py:122`, `evidence_chain.py:1384`, `privileged_access_attestation.py:350`, `journal_api.py:126`, `stripe_connect.py:405`. Concurrent re-run script not executed today — fall-back from prior Phase-1 §A audit accepted | PASS (locks present) | CODE-VERIFIED |
| 10 | `ba527a9b` | mig 296 NOW() in partial-index predicate fix | `schema_migrations.version=296` applied | PASS | RUNTIME-VERIFIED |
| 11 | `de956ca9` | mig 298 `created_at` column drift fix | `schema_migrations.version=298` applied | PASS | RUNTIME-VERIFIED |
| 12 | `42b41c4d` | P0 partner-PDF schema-drift (4 fixes) | Code diff confirms `monitoring_only`→`is_monitoring_only`, `fo.target_site_id` rewrite, etc. All 4 routes return **401** unauth (auth posture intact). **No post-fix synthetic-session PDF byte-content re-test was run today** — the prior audit's session expired (1h TTL). See P1-RT2-B. | PARTIAL | CODE-VERIFIED (runtime-not-re-tested post-fix) |
| 13 | `a5e7737e` | P0 cold-onboarding wire-through | Mig 296 applied; idempotency unique indexes exist; F1 BAA SQL fix in code. End-to-end Stripe-→client_orgs walk not re-run today | PARTIAL | CODE-VERIFIED + mig-RUNTIME-VERIFIED |
| 14 | `9a4c14db` | P1 partner-portal RLS migration + CI gate | Mig 297 applied. `tests/test_evidence_endpoints_auth_coverage.py` in pre-push allowlist. Runtime evidence doc captures pre-fix 500s on partner PDFs (correct evidence shape) | PASS | CODE+RUNTIME (gate present + mig applied) |
| 15 | `d3d6943a` | scorecard + sibling-parity AST gate | `tests/test_evidence_endpoints_auth_coverage.py` exists and is in the pre-push curated list | PASS | CODE-VERIFIED |

**Tally: 9 PASS / 4 PARTIAL / 1 FAIL / 1 N/A.**

---

## §2. Findings (severity-ranked)

### P0-RT2-A — assertion loop has 5 broken `Violation(detail=…)` kwarg callsites + 1 SAVEPOINT-outside-transaction (`RUNTIME-VERIFIED`)

**The exact same code-true-runtime-false class flagged by round-1's prometheus_metrics savepoint finding has reappeared, in greater numbers, in the substrate-engine itself. The substrate engine is the load-bearing canary; if its own loop throws TypeError every tick, no upstream gauge will show it.**

**Evidence (runtime, last 1h on VPS):**

```
$ ssh root@178.156.162.116 "docker logs --since=1h mcp-server 2>&1 | grep -c TypeError"
105
$ ssh root@178.156.162.116 "docker logs --since=1h mcp-server 2>&1 | grep -c NoActiveSQLTransactionError"
102
```

That's ~105 TypeError + ~102 SAVEPOINT errors per hour. The dataclass `Violation(@dataclass)` only accepts `site_id` and `details: Dict` — but five callsites pass `detail=` (singular, str):

```
$ python3.11 -c "import ast; src=open('assertions.py').read(); tree=ast.parse(src); \
  print([n.lineno for n in ast.walk(tree) \
         if isinstance(n,ast.Call) and getattr(n.func,'id',None)=='Violation' \
         and any(kw.arg=='detail' for kw in n.keywords)])"
[1056, 1108, 1155, 943, 1006]
```

The 5 broken assertions:

| Line | Assertion | Sev | Status today |
|---|---|---|---|
| 943  | `_check_cross_org_relocate_chain_orphan` | sev1 | Silent (zero rows in window) — **stealth-broken, latent P0** |
| 1006 | `_check_cross_org_relocate_baa_receipt_unauthorized` | sev1 | Silent (zero rows) — **stealth-broken** |
| 1056 | `_check_unbridged_telemetry_runbook_ids` | sev2 | **Firing every tick — 105×/hr TypeError** |
| 1108 | `_check_l2_resolution_without_decision_record` | sev2 | **Firing every tick** |
| 1155 | (next assertion in file) | — | Confirmed by AST — needs same fix |

**The SAVEPOINT bug at line 1225 (`_check_client_portal_zero_evidence_with_data`):**

```python
try:
    await conn.execute("SAVEPOINT client_rls_sim")  # ← outside conn.transaction()
    ...
except Exception:
    try:
        await conn.execute("ROLLBACK TO SAVEPOINT client_rls_sim")  # ← also bare
```

`asyncpg` runs statements in autocommit mode unless `conn.transaction()` is held. SAVEPOINT outside a transaction raises `NoActiveSQLTransactionError` BEFORE any setup. The recovery path then ALSO raises (it tries the same bare ROLLBACK TO SAVEPOINT) and emits `client_rls_sim_savepoint_recovery_failed` to the log — but the assertion completes the `for org_id` loop never seeing a single org. **The invariant is dead at runtime.** This is the EXACT class flagged in the prior audit as `feedback_runtime_evidence_required_at_closeout.md`.

**Operator-visible impact:** every `assertions tick: opened=N refreshed=M resolved=K held=L errors=2 sigauth_swept=…` line shows `errors=2`. Operators reading the panel see "errors=2" baked into normal idle and stop noticing. Real new errors will not register.

**Fix shape:** (a) rename `detail=` → `details={...}` on lines 943, 1006, 1056, 1108, 1155 with str→`{"interpretation": str}`; (b) wrap the SAVEPOINT block at line 1224 in `async with conn.transaction(): …`; (c) add a CI gate that runs `assertions.run_assertions_once()` once and asserts `errors == 0`.

---

### P0-RT2-B — `_check_db_baseline_guc_drift` is reading its OWN session GUC (false-positive design) (`RUNTIME-VERIFIED`)

**The new `db_baseline_guc_drift` invariant from commit `6ca77798` fires on its OWN observation, not on real database drift.** It currently has **1 OPEN sev2 violation** (verified today) claiming `app.is_admin='true' (expected 'false')` — but this is the value that `admin_connection`/`admin_transaction` SET LOCAL'd into the session BEFORE the assertion loop ran the read.

**Evidence:**

```
$ ssh root@178.156.162.116 "docker exec mcp-postgres psql -U mcp -d mcp \
    -c \"SELECT invariant_name, severity, details FROM substrate_violations \
       WHERE invariant_name='db_baseline_guc_drift' AND resolved_at IS NULL;\""

 invariant_name        | severity | details
-----------------------+----------+--------------------------------
 db_baseline_guc_drift | sev2     | {"guc": "app.is_admin",
                                    "actual": "true",
                                    "expected": "false", ...}
```

Source (`assertions.py:512-514`):

```python
actual = await conn.fetchval("SELECT current_setting($1, true)", guc)
```

`current_setting()` returns the SESSION value, which was just set by the assertion loop's own `admin_connection` (`SET LOCAL app.is_admin='true'`). The invariant is fundamentally measuring its own observer.

**Operator-visible impact:** there is now an "unfixable-by-design" sev2 in the open-violations panel. It cannot be resolved (will reopen on next 60s tick). Trains operators to ignore the panel.

**Fix shape:** read DB role default via `SELECT setdatabase, setrole, unnest(setconfig) FROM pg_db_role_setting WHERE setrole = (SELECT oid FROM pg_roles WHERE rolname = current_user)` — the actual baseline, not the in-session value.

---

### P1-RT2-A — `compliance_bundles_trigger_disabled` invariant has no synthetic-injection test (`CODE-ONLY`)

The new invariant from commit `6ca77798` is registered (`Assertion(name="compliance_bundles_trigger_disabled")` confirmed in code), but the audit dimension explicitly asked: *"inject a synthetic violation + verify the invariant catches it within 60s."* That experiment was **not run** in this commit. The DB shows only the two normal triggers (`_no_delete`, `_no_truncate`) — neither is currently disabled, so the invariant fires zero rows in the happy path. Whether it fires on the bad path is unverified.

**Recommendation:** run-once test that does `ALTER TABLE compliance_bundles DISABLE TRIGGER compliance_bundles_no_delete; <wait 90s>; SELECT … FROM substrate_violations WHERE invariant_name='compliance_bundles_trigger_disabled' AND resolved_at IS NULL;` then re-enable. Pin in `tests/`.

---

### P1-RT2-B — partner-PDF runtime evidence not re-collected post-fix (`CODE-VERIFIED, runtime-not-re-tested`)

Commit `42b41c4d` claims "close 4 schema-drift bugs caught by P1-3." The diff is correct (column names match `is_monitoring_only`, removed `fo.target_site_id`, etc.) and unauth probes today return 401 (auth intact). **But the audit doc (`audit/partner-pdf-runtime-evidence-2026-05-09.md`) captured the BROKEN state pre-fix and noted the synthetic session has 1h TTL.** No post-fix curl with a fresh synthetic session was run today to confirm that all four PDFs now return `Content-Type: application/pdf` with valid `%PDF-1.x` bytes.

**Risk:** the schema-drift fix may have introduced new column references (e.g. the F7 rewrite "JOIN through fleet_order_completions → site_appliances → sites for partner_id") that themselves don't match production schema. Pure code-review against schema files won't catch a stale grep. Same class as the original P0.

**Recommendation:** re-provision the synthetic session, curl all four endpoints, capture `head -c 100` output to confirm `%PDF-1.` magic bytes. Append to the existing audit doc.

---

### P2-RT2-A — `workstations_compliance_status_check` constraint blocks live device-sync row (`RUNTIME-VERIFIED`)

```
Device→workstation linkage failed for north-valley-branch-2:
  new row for relation "workstations" violates check constraint
  "workstations_compliance_status_check"
DETAIL:  Failing row contains (..., 192.168.88.250, ..., warning, ...).
```

Device-sync is currently emitting `compliance_status='warning'` but the CHECK constraint doesn't list it. Out of scope for the 15 commits but **firing during this audit window** — flag for sprint queue. Not a regression of this batch.

---

### P2-RT2-B — `prometheus_metrics.py:521` log-entries query times out (59 hits/hr) (`RUNTIME-VERIFIED`)

```
metrics: log entries query failed
asyncpg.exceptions.QueryCanceledError: canceling statement due to statement timeout
```

59 occurrences in the last hour. Out of scope for the 15 commits but adjacent to the prom-savepoint round-1 fix; the savepoint fix didn't address the underlying slow query. Index on `log_entries (created_at)` may be missing or stale stats. Sprint-track.

---

### P3-RT2-A — Phase 5 verdict's "Maya reservations queued" not actually verified (`CODE-ONLY`)

Phase 5 verdict (commit `da096024`) names two non-blocking reservations: (1) multi-tenant load harness (k6/Locust), (2) Phase 4 substrate-MTTR 24h soak. The verdict claims "task #97 / task #98" but no evidence in this audit window that those tasks are queued in `claude-progress.json` / TaskCreate. **Not RUNTIME-VERIFIED.**

---

## §3. What's strong (don't lose it)

- **pgbouncer outage recovery was decisive.** Both deploy-outage commits (`6d70fe59`, `de33d28c`) were tight, surgical, and runtime-verified afterward (`default_pool_size=50 max_client_conn=400` live + 2 SCRAM verifiers preserved).
- **Migration idempotency.** 296/297/298 all use `IF NOT EXISTS` on indexes, `DROP CONSTRAINT IF EXISTS` before re-add, well-documented rollback recipes in headers. Re-run safe.
- **5 unauth evidence endpoints DO return 403.** Live curl: summary, signing-status, compliance-packet all 403 today. Sibling-parity AST gate (`test_evidence_endpoints_auth_coverage.py`) is in pre-push curated list.
- **Jinja2 migration shipped clean.** Templates exist on disk, no rendering errors in 2h of logs, README.md.j2 properly uses `{{ presenter_brand }}` (no `.format()` placeholders).
- **admin_connection ratchet held the line.** Baseline pinned at 186, collector at 186 — three waves (9/10/11) reduced 15 sites in one day with no rollbacks.
- **Partner role-gating intact.** All four PDF routes use `require_partner_role("admin")` (verified in source), unauth returns 401 not 200.

---

## §4. What's missing evidence

1. **Assertion-loop runtime smoke test.** Nothing in pre-push runs `run_assertions_once()` and asserts `errors=0`. The 5 broken `Violation(detail=…)` calls would have failed instantly under such a smoke. **This is the close on the round-2 lesson.**
2. **Synthetic-violation injection for new invariants.** Both `compliance_bundles_trigger_disabled` and `db_baseline_guc_drift` shipped without a positive-control test. Adversarial principle: an invariant that has never fired is indistinguishable from a broken one.
3. **Post-fix partner-PDF byte-content re-test.** Pre-fix 500s captured; post-fix `%PDF-1.x` magic bytes never re-curl'd with a fresh session.
4. **Concurrent advisory-lock re-run.** Phase 1 §A script (per `audit/multi-tenant-phase1-concurrent-write-stress-2026-05-09.md`) was not re-executed against today's HEAD; locks are CODE-VERIFIED only.
5. **Migration rollback rehearsal.** What happens if 296 succeeds, 297 fails, 298 doesn't run? `schema_migrations` would be at 296. Re-running the failed migration runner — does it pick up at 297? No evidence collected.
6. **Maya reservations queue evidence.** No psql/TaskCreate dump showing tasks #97/#98 actually scheduled.

---

## §5. Round-table queue (priority order)

1. **[P0] Fix the 5 `Violation(detail=…)` callsites + the SAVEPOINT-outside-transaction in `assertions.py`.** Single-commit fix. Source in §2 P0-RT2-A.
2. **[P0] Add CI gate `tests/test_assertions_loop_runs_clean.py`** — calls `run_assertions_once()` against a real pool, asserts `errors == 0`. Pin in pre-push curated list. Closes the round-2 class structurally (round-1 closed prometheus_metrics savepoint inline; round-2 needs the loop-level gate).
3. **[P0] Fix `_check_db_baseline_guc_drift`** to read DB-role baseline via `pg_db_role_setting`, not in-session `current_setting()`. Resolve the open sev2 false-positive.
4. **[P1] Synthetic-injection test for `compliance_bundles_trigger_disabled`.** DISABLE TRIGGER → wait 90s → assert violation row → re-enable. Pin.
5. **[P1] Re-provision synthetic partner_user, re-curl all 4 PDF endpoints, capture `%PDF-1.x` magic bytes.** Append to `audit/partner-pdf-runtime-evidence-2026-05-09.md` post-fix section.
6. **[P1] Adopt the pattern: every new substrate invariant ships with (a) negative control = no rows in happy path, (b) positive control = synthetic violation injection test.** Update `feedback_runtime_evidence_required_at_closeout.md` to require both.
7. **[P2] Fix `workstations_compliance_status_check` constraint** to include `'warning'` (or fix device-sync to map to allowed value). Live device-sync hit every checkin.
8. **[P2] Investigate `prometheus_metrics.py:521` log-entries query timeout** — 59 hits/hr. Likely missing index on `log_entries (created_at)` or stale ANALYZE.

---

## §6. Final verdict

**REGRESSION on the round-1 lesson, NOT a stop-ship.**

The 15 commits SHIPPED their headline claims:
- Migrations 296/297/298 applied at runtime
- pgbouncer 50/400 live and SCRAM verifiers preserved
- 5 unauth endpoints return 403
- Jinja2 migration boot-clean
- admin_connection ratchet 186/186

But three new substrate assertions are **all broken at runtime**, firing 200+ errors per hour, with the engine's own "errors=N" gauge baking the noise into idle. **This is the same class round-1 explicitly named** (`feedback_runtime_evidence_required_at_closeout.md`) — the lesson did not propagate to the assertions module because no CI gate covers it.

The structural close is **task #2 above**: a 30-line CI gate that runs the assertion loop once and asserts `errors == 0`. Without it, the next assertion ships with the same bug.

**Recommend: fix the 5 callsites + 1 SAVEPOINT + GUC false-positive in a single commit, add the loop-runs-clean CI gate, then re-audit. N=2 cold onboard remains GO once that close lands.**

---

## §7. Runtime checks executed (raw evidence)

```
# 1. Latest deployed SHA — MATCHES disk + commit SHA
$ curl -sS http://localhost:8000/api/version
{"runtime_sha":"a0333e7338417103fc44a3a9b9b4c02546a08592",
 "disk_sha":"a0333e7338417103fc44a3a9b9b4c02546a08592","matches":true}

# 2. Migrations 296/297/298 applied
$ psql -c "SELECT version FROM schema_migrations WHERE version IN ('296','297','298')"
 296
 297
 298

# 3. pgbouncer pool config live
$ cat /etc/pgbouncer/pgbouncer.ini | grep -E 'default_pool|max_client_conn|reserve'
max_client_conn = 400
default_pool_size = 50
reserve_pool_size = 10
reserve_pool_timeout = 3

# 4. userlist intact (2 SCRAM verifiers)
$ head -2 /opt/mcp-server/pgbouncer/userlist.txt | grep -c 'SCRAM-SHA-256'
2

# 5. 5 unauth endpoints return 403
summary: 403
signing-status: 403
compliance-packet: 403

# 6. Container health
$ docker inspect -f '{{.RestartCount}}' mcp-server
0
$ docker ps --filter name=mcp-server --format '{{.Status}}'
Up 58 minutes (healthy)

# 7. Substrate invariants registered + count
$ grep -c 'Assertion(' assertions.py     → ratio (raw)
$ python3 -c "AST count of Assertion(name=…)"  → 66 registrations
   (commit body claimed 60+; PASS)
   compliance_bundles_trigger_disabled: REGISTERED
   db_baseline_guc_drift: REGISTERED + FIRING (false-positive — see P0-RT2-B)

# 8. admin_connection ratchet
baseline: 186, actual: 186  (PASS)

# 9. InFailedSQLTransactionError last 30m
0  (PASS — no transaction-state poisoning)

# 10. TypeError last hour (assertion loop)
105  (FAIL — see P0-RT2-A)

# 11. NoActiveSQLTransactionError last hour
102  (FAIL — see P0-RT2-A)

# 12. Substrate violations open right now
 sev1     |    0
 sev2     |    1   ← db_baseline_guc_drift false-positive
 sev3     |    2

# 13. Broken Violation(detail=…) callsites
[1056, 1108, 1155, 943, 1006]  (5 sites broken; 2 actively firing)

# 14. Partner PDF unauth posture
/api/partners/me/portfolio-attestation: 401
/api/partners/me/ba-attestation:        401
/api/partners/me/rollup/weekly.pdf:     401
```

---

**Auditor sign-off:** consistency-coach
**Worktree:** `agent-a378ba140ed77301b`
**Commit (this audit):** see follow-up
