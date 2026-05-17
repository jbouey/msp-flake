# Gate A — #130 / #117 Sub-commit C — Chain-Lock Metrics + Synthetic Endpoint + k6 Scenario

**Date:** 2026-05-16
**Reviewer:** fresh-context fork (Steve / Maya / Carol / Coach)
**Series:** #117 multi-device P1-1 — Sub-A (a0a6b08c) + Sub-B (7b7fafaf via #129) shipped
**Spec:** advisory-lock contention proof at 20-way concurrency

---

## OVERALL VERDICT: APPROVE-WITH-FIXES

Design is sound. Six P0s + four P1s MUST close before merge. No structural blockers; all P0s are scope-discipline + defense-in-depth bindings, not redesigns.

---

## RECOMMENDED DESIGN

### File layout
- `mcp-server/central-command/backend/chain_lock_metrics.py` (NEW)
  Exposes a `chain_lock_timer(site_id)` async context manager + in-process counters. NO new Prometheus library dep — extends the existing manual-text-format pattern (`prometheus_metrics.py`). Module owns three process-local dicts: `_wait_samples[site_id] -> deque(maxlen=10_000)`, `_contention_total[site_id]`, `_serialization_violations_total[site_id]`. Two new exporter functions `render_chain_lock_metrics()` returning text-format blocks, called by `prometheus_metrics.py` as a new section under its own `admin_transaction` (sectional-isolation pattern, see P0-2 fix comment at line 107).
- `mcp-server/central-command/backend/evidence_chain.py` (EDIT, ~12 lines)
  Wrap the `pg_advisory_xact_lock` execute call + the subsequent `prev_bundle` SELECT inside `async with chain_lock_timer(site_id) as t:`. The context manager records wall-clock `acquired_at - entered_at` as the wait sample, increments contention counter when wait > 50ms.
- `mcp-server/central-command/backend/chain_contention_load_api.py` (NEW)
  Single router `POST /api/admin/load-test/chain-contention/submit`. Auth: `Depends(require_appliance_bearer)` (the pre-seeded mig-325 bearers) + a hard runtime sentinel rejecting `auth_site_id != 'load-test-chain-contention-site'`. Synthesizes a minimal `EvidenceBundle` and passes through `evidence_chain.create_compliance_bundle(...)` UNMODIFIED — same codepath as prod so the lock measurement is true. Pre-call check: `SELECT 1 FROM load_test_runs WHERE status IN ('starting','running') AND 'chain-contention' = ANY(target_endpoints) LIMIT 1` — 409 if absent.
- `load_tests/chain_contention_20way.js` (NEW)
  k6 script, 20 VUs, deterministic bearer-per-VU mapping (`__VU` ↔ `load-test-bearer-NN` zero-padded), 60s ramp + 4min sustain, custom trends for p50/p95/p99 wait + counter for serialization-violations (read back from `/api/metrics`).
- `mcp-server/central-command/backend/migrations/RESERVED_MIGRATIONS.md` (EDIT) — NO new migration needed. Sub-C is pure code; the storage substrate (mig 325) already exists.

### Endpoint shape

```
POST /api/admin/load-test/chain-contention/submit
Authorization: Bearer load-test-bearer-NN
Body: { "iteration": <int>, "vu_id": <int> }     # logged in bundle.summary, no other inputs
Response: { "bundle_id": "...", "chain_position": <int>, "wait_ms": <float> }
```

The request validator + runtime sentinel BOTH check `auth_site_id == 'load-test-chain-contention-site'`. The check inside `create_compliance_bundle` is automatic (RLS via `tenant_connection(site_id=auth_site_id)` — `_enforce_site_id` is the existing 13-endpoint pattern from agent_api.py).

### Serialization-violation detector

Signal: when the per-site `chain_lock_timer` exits, sample `pg_try_advisory_xact_lock(hashtext(site_id))` inside a sibling read-only conn. If it returns TRUE (lock NOT held), AND another writer is currently in its critical section, increment `chain_lock_serialization_violations_total`. **Simpler + sufficient:** detect via chain-position duplicates post-hoc — the `bundle_chain_position_gap` invariant (Sub-A, sev1) already detects the OUTCOME. Sub-C adds an in-flight cheap detector: in-process `_critical_section_holders[site_id] -> set[task_id]` — context manager adds on enter, removes on exit; if `len > 1` at any sample point, increment violations. This is a process-local proxy (won't detect across mcp-server replicas) but adequate for the 20-VU single-replica soak.

### k6 bearer feeding

Env-var **file path** — `K6_BEARERS_FILE=/etc/load-test/bearers.txt`, one bearer per line, line N (1-indexed) maps to VU N. The file is rendered by the k6 wrapper (Sub-D) at run start by reading the 20 known plaintexts (`load-test-bearer-01..20`). NEVER hardcode bearers in the .js file (commits to git). NEVER pass 20 separate env vars (operator footgun).

---

## PER-LENS VERDICT

- **Steve (SWE):** APPROVE-WITH-FIXES — wrapper-vs-probe pick is right; context manager preserves the unmodified prod codepath. Sectional admin_transaction in metrics renderer correct.
- **Maya (Counsel):** APPROVE — Rule 1 OK (operator-facing only); Rule 2 OK (synthetic site, zero PHI surface); Rule 3 OK (NOT a privileged endpoint, mirrors load_test_api.py rationale); Rule 4 OK (Sub-A already covers orphan chain gaps for this site).
- **Carol (Security):** APPROVE-WITH-FIXES — bearer-per-VU file must be 0600 + tmpfs; sentinel needs the dual layer (P0-1); no body-supplied site_id ever.
- **Coach (Consistency):** APPROVE-WITH-FIXES — Sub-D handoff contract underspecified (P0-6); k6 scenario_sha must be pinned for run-ledger reproducibility.
- **DBA:** APPROVE — no schema change; reuses mig 325; `pg_try_advisory_xact_lock` sample is cheap (~30µs).
- **SRE:** APPROVE-WITH-FIXES — process-local violation detector caveat (P1-3) must be documented in metric HELP text; runbook needed (P1-4).
- **QA:** APPROVE-WITH-FIXES — needs test for sentinel rejection of `auth_site_id != 'load-test-chain-contention-site'` AT TWO layers (P0-1).

---

## P0 BINDINGS (must close before merge)

- **P0-1 Dual-layer site_id sentinel.** The runtime check `auth_site_id == 'load-test-chain-contention-site'` MUST exist in BOTH (a) the endpoint handler before calling `create_compliance_bundle`, and (b) inside a re-confirm `assert` immediately before the synthesized bundle is built. Pin via `tests/test_chain_contention_endpoint_dual_sentinel.py` (AST scan: two distinct site_id literal comparisons in the handler).
- **P0-2 Request body MUST NOT accept site_id.** Pydantic model contains only `iteration: int` and `vu_id: int`. Pin via test that POSTs `{"site_id": "foo", ...}` and asserts 422.
- **P0-3 load_test_runs precondition.** Endpoint MUST reject (HTTP 409) when no active run row covers `target_endpoints` containing `'/api/admin/load-test/chain-contention/submit'`. Otherwise this becomes an always-on production-poisoning surface. Add the literal endpoint path to `_WAVE1_ALLOWED_ENDPOINTS` in `load_test_api.py` (lockstep CI gate `test_load_harness_wave1_paths_exist.py` will fail otherwise — fix in same commit).
- **P0-4 Bearer file mode + cleanup.** k6 wrapper (Sub-D) creates `/etc/load-test/bearers.txt` mode 0600, owner = k6 runner UID; on `/complete` the file is unlinked. Sub-C design doc MUST specify this contract so Sub-D implements it correctly. NEVER write bearers to k6 stdout, logs, or run_ledger metadata.
- **P0-5 Invariant coverage verification.** Add `tests/test_chain_contention_endpoint_covered_by_invariants.py` that asserts: (a) `bundle_chain_position_gap` SQL would scan compliance_bundles where site_id='load-test-chain-contention-site' (it does — no exclusion), (b) `load_test_chain_contention_site_orphan` (sev2) scans this site (it does — the carve-out is the WHERE clause). Documents that this NEW writer callsite is covered.
- **P0-6 Sub-D handoff contract — written.** Sub-C ships with `docs/load-testing/chain-contention-soak-contract.md` declaring: metric names + label set + scrape interval + verdict thresholds (PASS: violations_total == 0, p95 wait < 250ms; FAIL: any violation OR p99 > 1s OR error rate > 0.5%) + run-ledger fields the soak writes (`metrics_summary.chain_lock = {p50, p95, p99, contention_count, violations}`). Sub-D's k6 wrapper consumes this contract; without it, Sub-D has no acceptance criteria.

## P1 BINDINGS (in-commit OR named TaskCreate followup)

- **P1-1** chain_lock_timer must be a no-op (zero allocations) for `site_id != 'load-test-chain-contention-site'` AND `not METRICS_ENABLED`. Production sites must not pay a deque-append cost per bundle. Gate via module-level allowlist set.
- **P1-2** Metric label cardinality cap. Hard-cap `_wait_samples` dict to 8 site_ids max (load-test site + headroom). Above cap → log WARN + drop new keys. Prevents accidental unbounded growth.
- **P1-3** Document in metric HELP text: "process-local; aggregate across replicas before alerting." Avoids on-call false confidence.
- **P1-4** Runbook `substrate_runbooks/chain_lock_serialization_violation.md` — what to do if violations_total > 0 on a soak (BLOCK on Sub-D pass; quarantine site; do NOT roll forward).

## P2 CONSIDERATIONS

- Histogram buckets for wait — defer; trend samples + percentile-on-read sufficient for first soak.
- Per-VU latency distribution split — defer; aggregate is what proves serialization.
- Grafana dashboard — defer to Sub-D or post-soak followup.

## ANTI-SCOPE (NOT in Sub-C)

- NO multi-replica metric aggregation (Prometheus federation handles this externally).
- NO new migration (storage exists in mig 325).
- NO non-load-test site support (single-site by design).
- NO alerting rules (Sub-D + ops own AlertManager wiring).
- NO Grafana JSON (post-soak deliverable).
- NO modification to advisory-lock semantics in evidence_chain.py — only ADD the timer wrapper.

## MIGRATION CLAIM

None. Sub-C is pure code. (Mig 326 stays free for next claimant.)

## SUB-D HANDOFF CONTRACT (binding)

Sub-D's 30-min soak consumes:
1. **Metric scrape** every 15s: `chain_lock_wait_duration_seconds_{p50,p95,p99}{site="load-test-chain-contention-site"}` + `chain_lock_serialization_violations_total{site="load-test-chain-contention-site"}` + `chain_lock_contention_total{...}`.
2. **Verdict** written to `load_test_runs.metadata.metrics_summary.chain_lock`:
   - PASS: `violations == 0 AND p95 < 250ms AND p99 < 1000ms AND http_error_rate < 0.5%`
   - FAIL: any violation OR p99 ≥ 1s OR error_rate ≥ 0.5%
3. **Invariant check** post-run: `bundle_chain_position_gap` reports zero violations for site_id='load-test-chain-contention-site' over the soak window. Sub-D's `/complete` handler queries the substrate engine + records the result.
4. **Crash path:** if k6 dies, `load_test_run_stuck_active` (sev3, mig 316) fires within 6h. The Sub-C endpoint does NOT need to detect this — Sub-A's existing chain invariant + Sub-B's site-orphan invariant cover the data side; mig 316 covers the run-ledger side.

---

**Word count:** ~990
