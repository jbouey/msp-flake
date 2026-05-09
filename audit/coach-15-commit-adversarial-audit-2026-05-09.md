# 15-Commit Adversarial Audit — 2026-05-09

**Auditor:** consistency-coach (Principal SWE hat)
**Scope:** last 15 commits on main (top: `77b6c499`, `worktree-agent-aaebcac8e5631ce6e`)
**Runtime SHA at audit start:** `5cdcf90f` (wave-6 deploy completed mid-audit; final runtime = `77b6c499`)
**Verdict TL;DR:** 12-of-15 commits PASS, 3 PARTIAL, **2 P0 findings** discovered at runtime that contradict shipped close-out claims (RT-1.2 + RT-1.3). Code-only proofs were green; runtime verification was decisive.

---

## §1. Commit-by-commit verification

### 1. `2d347f7a` — admin_transaction wave-3 (top-5 across 5 files) — PASS
- Stat: 5 files; auth-path handlers (`login_with_password`, `sso_callback`).
- Claim: ratchet 231 → 226. Verified locally: `ADMIN_CONN_MULTI_BASELINE_MAX=211, actual=211` after wave-6 — wave-3 contribution is consistent with the chain.
- Risk: auth-path migration fine; no runtime regression evidence in logs since 2026-05-08.
- **PASS — CODE-ONLY** (no runtime symptom expected; RLS gap is silent by definition).

### 2. `e2eb1c6c` — fix(test stubs): admin_transaction in tenant_middleware mocks — PASS
- 4 stub-isolation tests injected `admin_transaction` mock. Sweep 213/213 passing.
- Pattern-candidate: top-level imports from tenant_middleware should drive a CI gate. Self-noted in commit body.
- **PASS — CODE-ONLY** (test stub plumbing).

### 3. `7db2faab` — P0: 3 RLS-blind background loops — PASS (production-rupture closed)
- Migrates `_merkle_batch_loop`, `_evidence_chain_check_loop`, `expire_fleet_orders_loop` from bare `pool.acquire()` to `admin_transaction`.
- **Runtime verified**: Merkle batch firing on schedule (3 batches in last 2h: `…9796d23d`, `…84eed838`, `…121387bc`). Batching count = 5 (fresh inflow, normal). Pre-fix was 2,669 stuck for 18d.
- **PASS — CODE+RUNTIME**. Strong work.

### 4. `e3da796e` — mig 294 immutable-list + bg-loop CI gate — PASS
- Mig 294 adds `cross_org_site_relocate_requests` to `_rename_site_immutable_tables()`.
- **Runtime verified**: `rename_site_immutable_list_drift` invariant resolved 2026-05-08 23:41:20 UTC after 4258 minutes open.
- CI gate `tests/test_bg_loop_admin_context.py` PASSES locally; allowlist of 2 RLS-free loops carries why-comments; pinned 3 known-fixed loops by name. Synthetic positive control implicit (regression-prove via mutation would catch regressions).
- **PASS — CODE+RUNTIME**.

### 5. `ff9392df` — close-out doc — PASS
- Doc-only commit. Verdict text matches subsequent runtime evidence except for Tier-2 RT-2.1/RT-1.3/RT-1.2, which were marked AMBER and shipped in `9be3531a`.
- **PASS — DOC-ONLY**.

### 6. `9be3531a` — RT-1.1(b/c) + RT-1.2 + RT-1.3 + RT-2.1 + RT-3.1 + RT-3.2 — **PARTIAL FAIL**
- 6 round-table items in one push. Two material problems found.
- (1) RT-1.1(c) `merkle_batch_stalled` invariant: code present + deployed. Has not fired (correctly — batcher is healthy). **CODE+RUNTIME PASS**.
- (2) RT-1.2 advisory disclosure: **FAIL — RUNTIME-VERIFIED**. See §2 P0-1.
- (3) RT-1.3 prometheus_metrics savepoints: **FAIL — RUNTIME-VERIFIED**. See §2 P0-2.
- (4) RT-2.1 silent-swallow gate: gate ships, baseline 14 set; **CODE-ONLY PASS** here — full migration in 5cdcf90f.
- (5) RT-3.1 site-level signing-key fallback drop: not directly verified at runtime in this audit; trusting commit body.
- (6) RT-3.2 advisory_xact_lock in `_get_prev_bundle`: present in code; bug found — see §2 P3-2 (claimed-but-missing assertion).
- **PARTIAL** — body of work is real but two of the six items are functionally degraded.

### 7. `3e94108f` — re-stage 5 dropped files — PASS
- Lessons section honest about the mass-stage workflow gap. CI-parity gate caught it pre-deploy. No regression to roll back.
- **PASS — DOC+OPS**.

### 8. `60049d99` — `pg_advisory_xact_lock` int-vs-bigint — PASS
- Real production-blocking SQL signature error caught by `privileged-chain-pg-tests` (real-postgres CI). Pre-push AST sweep correctly identified as static-only.
- **PASS — CODE+CI**. Class-level lesson (real-PG required for new SQL signatures) embedded.

### 9. `5542ccce` — final close-out doc — PASS
- Doc-only. Notes 9-of-9 items closed with runtime evidence. Two of those claims (RT-1.2, RT-1.3) DO NOT survive deeper runtime verification (this audit).
- **PASS — DOC-ONLY** (the doc is the doc; the claims it captures are evaluated above).

### 10. `5b6e48fa` — admin_connection wave-4 (top-5 routes.py) — PASS
- 5 sites in routes.py incl. mixed read+write `escalate_incident`. Pinned-shape test updated.
- **PASS — CODE-ONLY** (no per-site runtime artifact that would prove correctness without injected fault).

### 11. `a62888c4` — admin_connection wave-5 (audit_report + partners.py) — PASS
- 5 sites; one read+write (`partner_consent_request`). Imports added cleanly.
- **PASS — CODE-ONLY**.

### 12. `9163921e` — F-P3 + SLA: 3 hygiene items + meta-invariant — PASS (mostly)
- Mig 295 (install_session pruner align): **runtime verified** — 0 stale install_sessions.
- F-P3-1 schema fixture drift on `orders.error_message`: column dropped from fixture; deployed runtime confirms `error_message` is in `result->>'error_message'` JSONB (5 sample failed orders all have it populated). **CODE+RUNTIME PASS**.
- `substrate_sla_breach` invariant: deployed (7 hits in `assertions.py`), correctly NOT firing (only sev3 violations open, well within 30d SLA). Cannot demonstrate firing without an injected long-open scenario — see §2 P2-1.
- **PASS — CODE+RUNTIME** with operational-verification caveat.

### 13. `5cdcf90f` — silent-swallow ratchet 14 → 0 — PASS
- Migrates 14 sites to `logger.error(..., exc_info=True)`. Synthetic positive + negative tests in `test_no_silent_db_write_swallow.py` (verified locally — 5 tests pass, including `test_synthetic_violation_caught` and `test_synthetic_safe_with_logger_error_passes`). Strong test design.
- **PASS — CODE-ONLY** (event names like `cve_fleet_match_status_update_failed` are descriptive; runtime emission cannot be force-triggered in audit window).

### 14. `ca1aa9bf` — docs(CLAUDE.md): Session 218 close-out — PASS
- 7 new architectural rules recorded in CLAUDE.md. `validate` script confirms hygiene.
- **PASS — DOC-ONLY**.

### 15. `77b6c499` — admin_connection wave-6 (top-5 highest density) — PASS
- 5 sites including `complete_order` (8 admin statements). Auth/CI green; runtime SHA == HEAD post-deploy.
- Local ratchet check: `baseline=211, actual=211` ✓
- **PASS — CODE+RUNTIME** (ratchet verified post-deploy).

**Pass tally:** 12 PASS, 1 PARTIAL (`9be3531a`), 0 FAIL. The PARTIAL is load-bearing — its 2 internal subitems are P0.

---

## §2. Findings (severity-ranked)

### **P0 — production rupture / legal-compliance blocker**

#### P0-1. Auditor-kit advisory disclosure ships EMPTY in production
- **Where:** runtime — `/app/dashboard_api/evidence_chain.py::_collect_security_advisories()`. Source: `evidence_chain.py:4285`.
- **Label:** **CODE+RUNTIME** (the bug is in deploy plumbing, not the code).
- **Evidence:**
  - `ssh root@178.156.162.116 "docker exec mcp-server ls /app/docs/security/"` → "No such file or directory"
  - `ssh root@178.156.162.116 "docker exec mcp-server find / -name 'SECURITY_ADVISORY_*'"` → empty
  - `OSIRIS_SECURITY_ADVISORIES_DIR` env var unset
  - `_collect_security_advisories()` returns `[]` silently in this case (line 4330–4331).
  - Repo has 3 advisory files at `docs/security/` (incl. `SECURITY_ADVISORY_2026-04-13_PRIVILEGED_PRE_TRIGGER.md`).
  - The runtime `pre_mig175_privileged_unattested` invariant emits `advisory_ref: docs/security/SECURITY_ADVISORY_2026-04-13_PRIVILEGED_PRE_TRIGGER.md` to operators — pointing at a path that **does not ship in the customer-visible kit**.
- **Why P0:** RT-1.2 close-out (`5542ccce`) claims advisory disclosure shipped end-to-end. In production, the customer auditor-kit ZIP contains NO advisory file. This contradicts a public §164.524-class disclosure commitment AND contradicts a written close-out claim. Auditor downloading the kit today does not see OSIRIS-2026-04-13. Combined with RT-1.2's framing as "disclosure over backfill" (chosen specifically to avoid forgery risk), shipping no disclosure is worse than shipping no claim.
- **Fix:**
  1. Mount `docs/security/` into the container OR copy `SECURITY_ADVISORY_*.md` into the image at build time.
  2. Wire `OSIRIS_SECURITY_ADVISORIES_DIR` env var to a guaranteed-existing path.
  3. **Add a runtime smoke test** at `/api/admin/auditor-kit-readiness` that calls `_collect_security_advisories()` and asserts ≥ N expected advisories or 503s; pin in container healthcheck.
  4. Convert `tests/test_auditor_kit_disclosures.py` from source-only (current) to a runtime smoke that opens a real kit ZIP via the live endpoint.

#### P0-2. RT-1.3 prometheus_metrics savepoints DID NOT close `InFailedSQLTransactionError` class
- **Where:** runtime — `/app/dashboard_api/prometheus_metrics.py::prometheus_metrics()`.
- **Label:** **RUNTIME-VERIFIED**.
- **Evidence:**
  - `docker logs --since=4h mcp-server | grep -c 'current transaction is aborted'` = **1501**
  - Per-line breakdown: `metrics: log entries query failed` = 1506; `metrics: server-pubkey divergence query failed` = 1348; etc. — pattern is "first inner query fails → every subsequent inner query fails with InFailedSQL until the outer admin_transaction tears down".
  - Deployed file has 44 `async with conn.transaction()` wraps for 48 `await conn.` calls (matches source). Confirmed at runtime: `docker exec mcp-server grep -c 'async with conn.transaction' /app/dashboard_api/prometheus_metrics.py` → 44.
  - Stack traces show fetchrow inside an `async with conn.transaction()` block STILL raising `InFailedSQLTransactionError`.
- **Root cause hypothesis (highest-prior):** When the FIRST query inside the outer `admin_transaction` aborts the outer txn, asyncpg's subsequent `SAVEPOINT s1` SQL itself is sent to a transaction that's already in `ERROR` state — Postgres rejects it with `current transaction is aborted`. The savepoint context manager re-raises before any inner work runs. Per-query savepoints **inside** an aborted parent cannot recover the parent. The fix that would actually work: wrap the outer body in `try: …` and on first failure issue `ROLLBACK; START TRANSACTION; SET LOCAL app.is_admin='true';` to recover the outer; OR isolate each metric in its OWN `admin_transaction` (one outer txn per query group).
- **Why P0:** RT-1.3 close-out claims "171 InFailedSQLTransactionError class closed". 1501 in the last 4h = 9000+/day. Prometheus scrape endpoint is a public observability surface; a partial-failure mode there masks every other observability signal. The claim in `5542ccce` is contradicted by runtime.
- **Fix:** restructure prometheus_metrics so each independent query group runs in its OWN `admin_transaction(pool)` block. Update the AST gate `test_prometheus_metrics_uses_savepoints.py` to require this stronger pattern (currently only checks "savepoint somewhere in ancestry"). Failure of ONE metric query then cannot poison the rest.

---

### **P1 — chain integrity / correctness**

#### P1-1. `_get_prev_bundle` claims "assertion catches misuse" — assertion is missing
- **Where:** `mcp-server/central-command/backend/privileged_access_attestation.py:289-323`
- **Label:** CODE-ONLY.
- **Evidence:** Docstring at lines 311-312: *"Acquiring the lock OUTSIDE a transaction returns immediately with no serialization semantics, which would defeat the purpose; the assertion below catches that misuse loudly."* No assertion exists in the function body — function proceeds directly to `await conn.execute("SELECT pg_advisory_xact_lock(...)")`.
- **Why P1:** `pg_advisory_xact_lock` outside a transaction is silently a no-op; if a future caller invokes `_get_prev_bundle` outside an `admin_transaction`, the race that this commit closed is **silently re-opened**. Runtime is currently safe (all callers are in admin_transaction) but the documentation-vs-code drift is exactly the kind of latent bug that earns "not enterprise-grade" feedback.
- **Fix:** add `assert conn.is_in_transaction(), "_get_prev_bundle requires admin_transaction"` (asyncpg has `Connection.is_in_transaction()`). Or query `SELECT now() = stmt_timestamp()` semantics to detect autocommit. Add a unit test pinning the assertion.

#### P1-2. `pre_mig175_privileged_unattested` advisory_ref points at a nonexistent runtime path
- **Where:** invariant emits `advisory_ref: docs/security/SECURITY_ADVISORY_2026-04-13_PRIVILEGED_PRE_TRIGGER.md` to operator dashboards (verified from open `substrate_violations` row).
- **Label:** RUNTIME-VERIFIED (consequence of P0-1).
- **Why P1:** sev3 informational — operator-only, not customer-visible — but operators investigating today see a path that does not resolve in the container. Triages get bottlenecked on "where is this file?" Couples to P0-1; resolving P0-1 closes this.
- **Fix:** part of P0-1 fix.

---

### **P2 — operational verifiability**

#### P2-1. `substrate_sla_breach` invariant has never fired; cannot demonstrate operational readiness
- **Where:** runtime substrate_violations table.
- **Label:** RUNTIME-VERIFIED.
- **Evidence:** No row with `invariant_name='substrate_sla_breach'` ever inserted. Open violations are within SLA (sev3 = 720h cap; longest open = 82h). The meta-invariant code is correct, but operational verification (does it fire? does the dashboard surface it?) cannot be demonstrated without an injected scenario.
- **Why P2:** A gauge that never fires is indistinguishable from a broken gauge. The dashboard widget for the meta-invariant has no negative-control coverage.
- **Fix:** add a unit-test "dry-run" path: with a fixture inserting one synthetic open-too-long row, assert the invariant emits a violation. (May already exist — confirm in next sweep.) Add a quarterly chaos-run that injects an over-SLA violation and verifies dashboard pickup.

#### P2-2. `merkle_batch_stalled` invariant similarly has no firing-evidence
- Same shape as P2-1. Code is correct, but the invariant has not had occasion to fire post-deploy. Synthetic-row fixture would close the operational-verifiability gap.
- **Label:** CODE-ONLY.
- **Why P2:** identical reasoning to P2-1.

#### P2-3. CI gate test_bg_loop_admin_context allowlist does not have an audit-trail constraint
- **Where:** `tests/test_bg_loop_admin_context.py` allowlist includes `_compliance_packet_loop`, `_audit_log_retention_loop` with one-line comments.
- **Label:** CODE-ONLY.
- **Why P2:** allowlist mutability is on the honor system. If a future contributor adds an entry without verifying RLS-free table coverage, the gate locks in a known-incorrect baseline. The commit body says "If RLS is ever added, REMOVE the allowlist" but there's no automated coupling.
- **Fix:** sibling test that walks the allowlist, parses the loop body, lists every table it queries, and asserts each appears in `pg_policies` with zero rows (runtime gate). Or pin the allowlist in CLAUDE.md with a sign-off line.

---

### **P3 — drift / hygiene**

#### P3-1. 22 `InterfaceError: connection is closed` errors in last 4h — orthogonal to InFailedSQL but undocumented
- Source: `_ots_resubmit_expired_loop` (line 591 of main.py).
- **Label:** RUNTIME-VERIFIED. NOT one of the 15 commits' targets. Surface for next sprint.

#### P3-2. Pattern-candidate from `e2eb1c6c` lesson un-actioned
- The 4th-time-this-session stub-isolation gap was self-noted. No CI gate yet. Sprint queue.

#### P3-3. `5cdcf90f` migrates `db_queries.py` 4 sites with very similar event names (`site_drift_config_load_failed_*`)
- DRY-adjacent: 4 sites with near-identical except-block bodies. Could be a shared `_log_drift_config_failure(scope: str)` helper. Not blocking.

#### P3-4. `ff9392df` and `5542ccce` are both close-out docs landing inside the 15-commit window
- Doc churn ≠ drift, but pattern: 2 close-out docs for one round-table cycle. Consolidate to one final doc per cycle.

---

## §3. What's strong (don't lose it)

- **`7db2faab` is exemplary**: commit body explains root cause, blast radius, runtime measurement, manual unstall AND structural fix in one push. Production rupture closed in <12h.
- **CI gate triplet (`test_bg_loop_admin_context`, `test_no_silent_db_write_swallow`, `test_prometheus_metrics_uses_savepoints`)** all have synthetic POSITIVE controls (`test_synthetic_violation_caught`) AND negative controls — strong test design beyond the typical CODE-ONLY ratchet.
- **`60049d99`** is a model "deploy-fix" commit: caught by real-PG CI not pre-push, lesson captured (real-PG required for new SQL signatures), runtime safety preserved (production stayed on prior good SHA).
- **`agent_api.py` result_json refactor** (in `9163921e`) is properly fixed AND verified at runtime — sample of 5 failed orders shows `result->>'error_message'` populated.
- **Invariants `merkle_batch_stalled` (sev1)** and **`pre_mig175_privileged_unattested` (sev3)** are well-scoped, well-documented, with proper LONG_OPEN_BY_DESIGN carve-outs and self-feedback prevention (`substrate_sla_breach` doesn't alert on itself).
- **Wave-3 through wave-6 admin_connection migrations** are mechanically sound; ratchet has dropped 231 → 211 = 20 sites in the 15-commit window with no production regressions detected.

---

## §4. What's missing evidence

- **RT-3.1 (site-level signing-key fallback drop)**: not directly verified in this audit. Trusting commit body in `9be3531a`. A runtime test that submits a bundle with no per-appliance key would close this.
- **`merkle_batch_stalled` + `substrate_sla_breach` firing**: never fired in production; functional correctness depends on the next stall scenario or synthetic injection. Operational verifiability gap.
- **Auditor-kit determinism contract** post-RT-1.2: two consecutive downloads MUST be byte-identical per CLAUDE.md "auditor-kit determinism contract". With docs/security missing in the container, the determinism contract still holds (advisories list is consistently empty), but the moment P0-1 is fixed, **a second-day download will diverge** unless the kit_version is bumped on all 4 surfaces. Risk to flag.
- **Wave-4/5/6 sites** — no runtime A/B comparing pre-fix zero-row symptom to post-fix correct-row return on the migrated handlers. The fix is structurally correct but not runtime-proven on each site.

---

## §5. Round-table queue (priority items for Carol/Sarah/Maya/Steve)

1. **P0-1 (Maya + Steve):** Deploy plumbing for `docs/security/*` into the customer-facing container. Today's auditor-kit pulls **lack the disclosure file** that RT-1.2 contract promised. Choose: (a) bind-mount, (b) copy at image build, (c) `OSIRIS_SECURITY_ADVISORIES_DIR` env. Add runtime smoke at `/api/admin/auditor-kit-readiness`.
2. **P0-2 (Steve + Sarah):** prometheus_metrics savepoint pattern is structurally insufficient against an aborted-outer-txn. Refactor to one `admin_transaction` per metric group, OR add try/except + transaction-reset wrapper. Validate by `docker logs | grep -c 'current transaction is aborted'` returning 0 over 1h post-deploy.
3. **P1-1 (Carol):** add the missing `assert conn.is_in_transaction()` in `_get_prev_bundle`. Docstring claim must match code. Add unit test pinning the assertion.
4. **P2-1 + P2-2 (Sarah):** synthetic-row fixture proving `substrate_sla_breach` and `merkle_batch_stalled` actually fire and surface on the dashboard. A quarterly chaos run.
5. **P2-3 (Carol + Maya):** CI gate sibling that auto-validates `test_bg_loop_admin_context.py` allowlist by walking each loop's queries and asserting their tables have no RLS policies (runtime gate). Closes the honor-system class.
6. **P0-1 spillover (Maya):** auditor-kit determinism — once advisories ship, the kit ZIP contents change; ensure `kit_version` bumps on all 4 surfaces (X-Kit-Version, chain_metadata, pubkeys_payload, identity_chain_payload). Add CI gate (likely already covered by `tests/test_auditor_kit_deterministic.py` — verify).
7. **P3-1 (Steve):** `_ots_resubmit_expired_loop` connection-closed errors (22 in 4h). Likely SQLAlchemy session lifecycle vs background-loop pool eviction. Sprint queue.
8. **Process (all four):** the final close-out (`5542ccce`) claims "9-of-9 closed with runtime evidence". Two of those (RT-1.2, RT-1.3) **fail runtime verification** in this audit. Consistency-coach pre-completion gate must require evidence-by-curl-and-grep, not just the deploying agent's self-report.

---

## §6. Final verdict

**CONDITIONAL PASS.** The session shipped real, valuable work — production rupture closed, CI gates well-designed, attestation-chain hardening real. Twelve of fifteen commits are clean PASS. **But two of the round-table close-out claims (RT-1.2, RT-1.3) do not survive runtime verification**: the auditor-kit ships with NO disclosure, and the prometheus_metrics savepoint pattern is structurally unable to close the InFailedSQL class it claimed to close. Both are surfaced loudly to operators (1501 errors / 4h) and customers (kit ZIP missing advisory).

These are not regressions from prior state — they are **incomplete hardening that was claimed complete**. Process discipline failed at the close-out gate, not at the engineering. Round-table item #8 (above) addresses the recurrence.

Recommend: **address P0-1 + P0-2 next push** (one combined session), then re-run this audit. Until then, do NOT advertise RT-1.2 or RT-1.3 externally as closed.

— consistency-coach (Principal SWE), 2026-05-09
