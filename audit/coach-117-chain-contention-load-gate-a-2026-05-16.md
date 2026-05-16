# Gate A — #117 chain-contention load + bundle_chain_position_gap invariant

Date: 2026-05-16
Reviewer: fork-based 7-lens (general-purpose subagent, fresh context, opus-4.7[1m])
Source coach: `audit/coach-multi-device-p1-batch-gate-a-2026-05-16.md` (batch feasibility — APPROVE-WITH-FIXES)
Predecessor specs: `.agent/plans/40-load-testing-harness-design-v2.1-2026-05-16.md` (#62 v2.1)

**Verdict (Part 1 — `bundle_chain_position_gap` invariant): APPROVE-WITH-FIXES**
**Verdict (Part 2 — load extension): APPROVE-WITH-FIXES**
**Overall: APPROVE-WITH-FIXES. Sequence #117 in 4 sub-commits. P0s below MUST be closed before code lands.**

---

## Part 1 — `bundle_chain_position_gap` substrate invariant

### Existing-state audit (verified read-only)

- `assertions.py:915` `_check_cross_org_relocate_chain_orphan` — only chain-integrity invariant; scans `sites.prior_client_org_id` for org-move bypass. **Does NOT touch `compliance_bundles.chain_position` per se.**
- `chain_tamper_detector.py:46` walks per-site chains and validates `prev_hash` linkage by re-hashing — but it's an **on-demand verifier** (read by auditor kit, not a substrate tick).
- Migration 043 added `UNIQUE INDEX uq_compliance_bundles_site_chain_position ON (site_id, chain_position)` — **prevents duplicates** but does NOT detect gaps (a missing position 5 between 4 and 6 passes the UNIQUE check).
- `pg_advisory_xact_lock(hashtext(site_id), hashtext('attest'))` serializes the 6 chain-writer callsites: `appliance_relocation.py`, `evidence_chain.py`, `journal_api.py`, `privileged_access_attestation.py`, `runbook_consent.py`, `stripe_connect.py`.

**Conclusion:** no per-site chain-position-gap invariant exists today. The batch Gate A P0-3 is correct. Confirmed via grep — only 4 lines match `*chain_orphan*` and they are the existing relocate invariant.

### Query shape recommendation

Recommended (window-function with LAG, scoped to last 24h to bound table scan):

```sql
WITH gaps AS (
  SELECT site_id,
         chain_position,
         LAG(chain_position) OVER (PARTITION BY site_id ORDER BY chain_position) AS prev_position
    FROM compliance_bundles
   WHERE created_at > NOW() - INTERVAL '24 hours'
)
SELECT site_id, chain_position, prev_position,
       (chain_position - prev_position - 1) AS gap_size
  FROM gaps
 WHERE prev_position IS NOT NULL
   AND chain_position - prev_position > 1
 LIMIT 100
```

**Performance:** `compliance_bundles` is monthly-partitioned (mig 138). `WHERE created_at > NOW() - INTERVAL '24 hours'` triggers partition pruning to 1-2 partitions. Combined with the existing `uq_compliance_bundles_site_chain_position` btree on `(site_id, chain_position)`, the window scan is index-covered. Estimate: <50ms on 232K-row table per Session 219 prometheus_metrics profiling.

**MUST NOT** use `SELECT COUNT(*)` or unbounded partition scans (Session 219 timeout class — `prometheus_metrics.py:521` outage).

### Sev level — **sev1** (recommended)

Precedent: `load_test_marker_in_compliance_bundles` (sev1), `cross_org_relocate_chain_orphan` (sev1) both treat chain-corruption as sev1. A gap in `chain_position` is a **chain-integrity violation** — auditor-kit determinism contract relies on contiguous chain. Counsel Rule 9 (determinism + provenance) directly invoked. Sev1.

### Window — **24h** (recommended)

24h tolerates the slow OTS-anchoring window (Merkle batch worker runs hourly) and partition rollover, while bounding the scan cost. Live-contention detection during a 30-min load run is well within window. 1h would miss gaps that materialize during partition flip-over (00:00 UTC class). 24h is the right floor.

### False-positive carve-outs

- **Genesis bundle (chain_position=0 OR 1):** LAG returns NULL; the `WHERE prev_position IS NOT NULL` clause already excludes. OK.
- **Mig 043 re-sequencing artifact:** historical re-sequence moved positions; any site with bundles authored before mig 043 (March 2026 timeframe) has positions that started at 1, not 0. **24h window inherently excludes this** — historical re-sequence has not run since the lock landed.
- **Cross-org relocate `prior_client_org_id` rewrites:** the relocate flow does NOT renumber chain_position — it advances forward. No carve-out needed.
- **OTS retro-anchoring:** OTS updates `ots_status` column, NOT `chain_position`. No carve-out needed.
- **Planned backfills (e.g. mig 300 L2 backfill):** mig 300 inserts into `l2_decisions`, not `compliance_bundles`. No impact. If a future backfill writes to `compliance_bundles`, the backfill itself MUST advance chain_position contiguously (or be quarantined as a sev1 incident).

**P0-1a (this gate):** the design doc MUST enumerate these carve-outs explicitly and pin via comment in the assertion docstring. Future backfills are gated by the invariant — operators must coordinate.

### Per-site vs per-(site, check_type) — **per-site only**

Verified at `evidence_chain.py:1384` and `appliance_relocation.py:122` — both lock on `pg_advisory_xact_lock(hashtext(site_id))` WITHOUT check_type. The chain is per-site across all check_types. The query MUST `PARTITION BY site_id` only (NOT include check_type). Including check_type would split a real contiguous chain into apparent gaps. Pinned.

### Runbook sketch — `substrate_runbooks/bundle_chain_position_gap.md`

```markdown
# bundle_chain_position_gap

**Severity:** sev1
**Display name:** Compliance bundle chain has a gap (missing chain_position)

## What this means (plain English)

The per-site Ed25519+OTS evidence chain for the named site has a hole.
Position N exists, position N+2 exists, but N+1 is missing. Auditor-kit
determinism contract requires contiguous chains — missing positions
break the hash linkage and customer-facing tamper-evidence promise.

## Root cause categories
- Failed INSERT inside `_get_prev_bundle` flow rolled back mid-chain (FK
  violation, deadlock, statement timeout)
- Race between two writers on a site where `pg_advisory_xact_lock` was
  bypassed (caller did NOT enter `admin_transaction()` first)
- Manual DELETE on a chain row (DBA error)
- Bulk operation that allocated chain positions optimistically and then
  partial-failed mid-batch

## Immediate action
- Run `chain_tamper_detector.verify_chain(site_id)` — confirms exact
  positions missing
- If gap is at a position written within the active load test window:
  abort the load test (POST /api/admin/load-test/{run_id}/abort) and
  quarantine the run for investigation
- If gap predates the load: query `admin_audit_log` for DELETE actions
  on compliance_bundles + check application logs for failed INSERT
  callsites at the affected timestamps

## Verification
- Panel: invariant row should clear on next 60s tick once the gap is
  filled (re-insertion preserving prev_hash linkage) OR the affected
  site is quarantined

## Escalation
- §164.528 disclosure-accounting impact: a missing chain row may mean
  an evidence event was unrecorded — Maya counsel review required.
- Auditor-kit determinism: if the gap pre-dates the most recent
  auditor-kit download for this site, the customer hash is invalidated;
  notify customer per kit-tamper-detection runbook.
```

(File must reach ≥40 lines, matching the existing runbook floor.)

### `_DISPLAY_METADATA` entry

```python
"bundle_chain_position_gap": {
    "display_name": "Compliance bundle chain has a gap",
    "recommended_action": (
        "A site's per-site Ed25519+OTS chain has missing "
        "chain_position(s). Run chain_tamper_detector.verify_chain() "
        "to enumerate the gaps, then trace via admin_audit_log + "
        "application logs at the affected timestamps. If gap "
        "coincides with active load_test_runs row, abort the run "
        "and quarantine. Sev1 because auditor-kit determinism + "
        "§164.528 disclosure-accounting integrity are on the line."
    ),
},
```

### Binding requirements (Part 1)

- **P0-1a** Invariant MUST ship in a sub-commit BEFORE the load harness extension runs. Order: invariant + runbook + display_metadata + CI gate (`test_substrate_docs_present` auto-pass) + baseline 0 in `substrate_violations` BEFORE load.
- **P0-1b** Carve-outs enumerated explicitly in docstring (genesis, mig 043 historical, future-backfill gate). Future-backfill rule documented in `CLAUDE.md` ledger.
- **P0-1c** Query MUST use `WHERE created_at > NOW() - INTERVAL '24 hours'` for partition pruning. NEVER scan unbounded.
- **P0-1d** Invariant MUST NOT include check_type in PARTITION BY (per-site chain semantics verified above).
- **P1-1a** Baseline pre-load `substrate_violations` for this invariant MUST be 0 (citation in Gate B commit body). If non-zero, the load test is invalid — quarantine + investigate before running.

---

## Part 2 — Load extension design

### Synthetic site choice (RECOMMENDATION + rationale) — **NEW dedicated load-test site WITHOUT `synthetic-` prefix AND WITHOUT `sites.synthetic=TRUE` flag; carve-out via dedicated `sites.load_test_chain_contention=TRUE` column (new) OR a new well-known site_id literal**

**Tension explained:**
- `no_synthetic_bundles` CHECK rejects `site_id LIKE 'synthetic-%'` → cannot use prefix.
- `load_test_marker_in_compliance_bundles` (sev1) scans `sites WHERE synthetic=TRUE` → flipping `synthetic=TRUE` on a load-test site fires the invariant every 60s during the run.
- `synthetic_traffic_marker_orphan` (sev2) scans 4 aggregation tables (incidents/l2_decisions/evidence_bundles/aggregated_pattern_stats) JOIN sites WHERE synthetic=TRUE — doesn't touch compliance_bundles, BUT the chain-contention scenario only writes to compliance_bundles.

**Three options (recommend Option C):**

- **Option A — relax `no_synthetic_bundles` CHECK to allow chain-contention writes:** REJECTED. Weakens an enterprise-grade CHECK protecting the auditor-kit determinism contract for ALL sites. Counsel Rule 9 violation.
- **Option B — carve-out for `sites.synthetic=TRUE` in the `load_test_marker_in_compliance_bundles` invariant:** REJECTED. The invariant exists EXACTLY to detect this class. Weakening it for a load test means production load-test contamination could go undetected.
- **Option C — NEW dedicated column `sites.load_test_chain_contention BOOLEAN DEFAULT FALSE` PLUS a new well-known site_id literal `load-test-chain-contention-site` (no `synthetic-` prefix so it passes the CHECK).** ✅ RECOMMENDED.
  - The load-test site_id literal is whitelisted by NAME in `load_test_marker_in_compliance_bundles` invariant (single-line carve-out: `AND cb.site_id != 'load-test-chain-contention-site'`).
  - `sites.synthetic` STAYS FALSE on this site (so it doesn't fire the load_test_marker invariant via the `synthetic=TRUE` branch).
  - A NEW sev2 invariant `load_test_chain_contention_site_orphan` (companion) detects bundles on this site_id OUTSIDE of an active `load_test_runs` row — fires if a writer regressed and is hitting the test site in production traffic.
  - The site has `client_org_id IS NULL` so it cannot appear in customer-facing aggregations or auditor kits (those filter `client_org_id IS NOT NULL`).
  - Pre-seed via mig: site row + 20 site_appliances rows + 20 bearers, all marked `synthetic_marker='load_test'` and `bearer_revoked=FALSE` initially.

**Bonus:** the chain on this dedicated site is INTENTIONALLY non-determinism-bound (no auditor kit will ever pull it; `client_org_id IS NULL` → kit query returns empty). Chain integrity still tested by the new `bundle_chain_position_gap` invariant (the gate's primary purpose).

### Synthetic bearer provisioning

- **Mig 325 (pre-claim)** seeds: 1 site row (`site_id='load-test-chain-contention-site'`, `client_org_id=NULL`, `load_test_chain_contention=TRUE`), 20 site_appliances rows (`appliance_id=load-test-appliance-{00..19}`, distinct Ed25519 pubkeys), 20 long-lived bearer tokens (table `api_keys` or wherever appliance bearers live — verify via grep; suspect `site_appliances.bearer_token_hash` or sibling).
- **Bearer hashes** stored in mig as bcrypt-hashed (production parity). Plaintext bearers piped into 1Password vault under entry `OsirisCare → Load Test Synthetic Bearers`. k6 wrapper reads from 1Password CLI at run start, not from env (no plaintext in deploy artifacts).
- **`bearer_revoked` semantics:** STAY FALSE for these — they are long-lived test fixtures, NOT per-run mints. Per-run mint pattern (mig 324) is for #62 v2.1 wave-1 endpoints; chain-contention uses pre-seeded bearers.
- **CI gate:** `tests/test_load_test_bearers_have_expected_shape.py` asserts mig 325 seed exists + 20 bearers + `client_org_id IS NULL`.

### `chain_lock_wait_seconds` histogram — bucket choice + registration callsite

**Critical finding:** `prometheus_metrics.py` does NOT use `prometheus_client.Histogram` — it generates Prometheus text format **manually** per the file's docstring line 6 ("no prometheus_client dependency needed"). There is NO existing histogram in the file (grep `Histogram\|histogram\|HISTOGRAM` returns 0 hits). Adding the first histogram requires either:

- **Option A — emit pre-computed buckets manually** in the same text-format generator (Counter/Gauge sibling at `_format_metric` line 51). Pattern: caller maintains an in-memory dict of `{bucket_upper_bound: cumulative_count}` and the metrics endpoint emits Prometheus histogram lines (`_bucket{le="0.01"}`, `_bucket{le="0.1"}`, ..., `_count`, `_sum`).
- **Option B — introduce `prometheus_client` dependency** in `requirements.lock` for proper Histogram support.

**Recommend Option A** — the in-process counter is a singleton dict guarded by `asyncio.Lock`, no new dep, matches existing file pattern. Sample:

```python
# In a NEW module backend/chain_lock_metrics.py (do NOT add stateful
# globals to prometheus_metrics.py — keep that file query-only)
_CHAIN_LOCK_BUCKETS = [0.001, 0.01, 0.1, 0.5, 1.0, 5.0, 10.0]  # seconds
_chain_lock_observations: dict[float, int] = {b: 0 for b in _CHAIN_LOCK_BUCKETS}
_chain_lock_observations["+Inf"] = 0
_chain_lock_sum: float = 0.0
_chain_lock_count: int = 0
_chain_lock_lock = asyncio.Lock()

async def observe_chain_lock_wait(seconds: float) -> None:
    async with _chain_lock_lock:
        global _chain_lock_sum, _chain_lock_count
        _chain_lock_count += 1
        _chain_lock_sum += seconds
        for b in _CHAIN_LOCK_BUCKETS:
            if seconds <= b:
                _chain_lock_observations[b] += 1
        _chain_lock_observations["+Inf"] += 1
```

Bucket rationale: 1ms floor (sub-millisecond locks invisible), 10s ceiling (above = catastrophic, alert separately). Steve P1 budget: p99 < 500ms — buckets at 0.5s + 1.0s let us read p99 directly from line counts.

**Instrumentation callsite:** wrap each `pg_advisory_xact_lock` call (the 6 callsites listed in Part 1 existing-state audit). Use a context manager pattern to ensure timing even on exception:

```python
start = time.monotonic()
await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1), hashtext('attest'))", site_id)
await observe_chain_lock_wait(time.monotonic() - start)
```

**Caveat:** `pg_advisory_xact_lock` blocks indefinitely; the metric captures actual wait time which IS the contention signal. No timeout needed (statement_timeout backstops at 30s).

### Scenario script — **k6** (matches #62 v2.1 pattern)

#62 v2.1 ships k6 — extension to that. New k6 scenario file `loadtest/scenarios/chain_contention_20way.js`:

- 20 VUs constant (one per synthetic bearer)
- Each VU loops: POST `/api/journal/upload` (which calls chain-writer at `journal_api.py:126`) — uses the journal-upload path because it's already the chain-writer that has `pg_advisory_xact_lock($1)` on appliance_id. (Verify: it may be hashtext(appliance_id) not site_id — re-grep before ship.)
- Wait, journal_api locks per-appliance, NOT per-site. For TRUE chain-contention testing (one site, 20 appliances) we need a writer that locks per-SITE: `evidence_chain.py:1384` or `appliance_relocation.py:122` or the privileged_access_attestation path. Recommend `/evidence/upload` callsite — but #62 v2.1 P0-2 explicitly DROPPED `/evidence/upload` from wave 1.
- **Resolution:** the chain-contention test is INTRINSICALLY a wave-2-shape concern. It needs to hit a per-site chain-writer. Use a new dedicated admin endpoint `POST /api/admin/load-test/chain-contention/submit` (admin-bearer gated, requires active `load_test_runs` row, REJECTS any site_id other than `load-test-chain-contention-site`). This endpoint invokes the same `_get_prev_bundle` + chain-write logic but isolated from production callsites. **P0-2a binding.**

### Soak duration — **30 min** (recommended, matches Gate A spec)

20 VUs × 30 min ≈ 36,000 chain-write attempts (assuming ~1s loop). Long enough for tail-latency p99 capture (need ~30 samples in tail buckets), short enough to iterate within a single round-table cycle. 60min would help if p99 budget is tighter; 30 min is the right floor.

### Substrate invariant interaction

- `load_test_marker_in_compliance_bundles` (sev1): single-literal carve-out `AND cb.site_id != 'load-test-chain-contention-site'` in the SQL. Pinned by extending the existing test `tests/test_no_load_test_marker_in_compliance_bundles.py` to assert the carve-out literal exists.
- `synthetic_traffic_marker_orphan` (sev2): NO change needed (scans 4 aggregation tables, doesn't touch compliance_bundles).
- NEW companion invariant `load_test_chain_contention_site_orphan` (sev2): fires if `compliance_bundles` rows exist for `site_id='load-test-chain-contention-site'` WITHOUT an active `load_test_runs` row covering their `created_at` window. Detects writer regressions (production code accidentally targeting the test site).
- `bundle_chain_position_gap` (Part 1, sev1): IS the primary gate the load test exercises. Baseline 0 before load. If non-zero AFTER load, the chain-lock implementation has a bug — load test result is "DEFECT FOUND" not "PASS".

### Mig number(s) needed — pre-claim via ledger

- **Mig 325** — `load-test-chain-contention-site` seed (site row + 20 site_appliances + 20 bearer hashes) + `sites.load_test_chain_contention BOOLEAN DEFAULT FALSE` column.
- **Mig 326** — `bundle_chain_position_gap` runbook is doc-only, no mig. The substrate invariant is Python-only. NO mig needed for Part 1.
- **Mig 327** — companion invariant `load_test_chain_contention_site_orphan` (Python-only, no mig). Skip.

**Net mig claims: 325 only.** Pre-claim via RESERVED_MIGRATIONS.md with `<!-- mig-claim:325 task:#117 -->` marker in the design doc.

### Binding requirements (Part 2)

- **P0-2a** Use dedicated admin-gated chain-write endpoint (`POST /api/admin/load-test/chain-contention/submit`) — NOT real production writers. Endpoint REJECTS any site_id ≠ `load-test-chain-contention-site` and requires an active `load_test_runs` row. Prevents accidental production poisoning if a k6 script regresses.
- **P0-2b** Mig 325 pre-seed pattern: ONE site row + 20 site_appliances + 20 long-lived bearers. NEVER per-run mint (burns RLS-cache, leaks `bearer_revoked` rows). Bearers stay `bearer_revoked=FALSE` permanently.
- **P0-2c** `chain_lock_wait_seconds` histogram registered in NEW module `chain_lock_metrics.py`. Buckets `[0.001, 0.01, 0.1, 0.5, 1.0, 5.0, 10.0]` seconds. Instrumented at all 6 existing `pg_advisory_xact_lock` callsites (not only the load-test endpoint — production observability is the side-benefit).
- **P0-2d** Carve-out literal `'load-test-chain-contention-site'` added to `load_test_marker_in_compliance_bundles` invariant SQL. Companion invariant `load_test_chain_contention_site_orphan` ships in same sub-commit.
- **P0-2e** k6 scenario gated by `load_test_runs.status='running'` poll (already wired in #62 v2.1) — no carve-out from kill-switch.
- **P1-2a** Steve p99 budget: chain_lock_wait_seconds p99 < 500ms at 20-VU steady state. If actual p99 ≥ 500ms, the load result is "BLOCKING" (chain is the bottleneck), file follow-up to redesign per-site lock granularity before claiming any multi-site scale-out.
- **P1-2b** `hashtext()` is int4 — 32-bit hash collisions across 250 production sites possible. Acceptable for the lock (false-collision = unnecessary serialization, not safety violation). Document in design doc + add a TaskCreate for future graduation to `hashtextextended()` int8 if site count crosses 1M.
- **P1-2c** Audit log every load-test run start/abort/complete (already in #62 v2.1) — verify mig 316's `load_test_runs.started_by` is a named human, NOT `'k6-wrapper'` or `'system'`.

---

## Part 3 — Sub-commit sequencing

**Recommend 4 sub-commits, NOT one mega-commit:**

1. **Sub-commit A** — `bundle_chain_position_gap` invariant + runbook + `_DISPLAY_METADATA` entry + CI gate auto-pass via `test_substrate_docs_present`. **Ships standalone; production-deployable independent of load test.** Reduces blast radius if implementation has a bug — the invariant alone is a generic chain-integrity gate that should exist anyway. Gate B run before B starts.

2. **Sub-commit B** — Mig 325 (load-test site seed + bearer fixtures + `load_test_chain_contention` column) + companion invariant `load_test_chain_contention_site_orphan` + carve-out literal in `load_test_marker_in_compliance_bundles` SQL + tests. **Ships standalone; mig applies safely (additive only).** Gate B before C.

3. **Sub-commit C** — `chain_lock_metrics.py` module + 6 callsite instrumentation + `/api/metrics` endpoint exposure + new admin endpoint `POST /api/admin/load-test/chain-contention/submit` + k6 scenario file `loadtest/scenarios/chain_contention_20way.js`. **Code-only; no runtime activation.** Gate B before D.

4. **Sub-commit D** — actual 30-min soak run, post-run verdict file `audit/coach-117-chain-contention-soak-verdict-2026-05-16.md` citing baseline `bundle_chain_position_gap=0` pre-run + p99 chain_lock_wait_seconds + post-run baseline still 0 (chain integrity held under contention). If post-run baseline > 0 OR p99 ≥ 500ms: verdict is BLOCK + redesign cycle. Verdict file IS the Gate B for this sub-commit.

**Total estimated calendar:** 4-5 days (matches batch Gate A estimate). Sub-commit A is 1 day; B is 1-2 days; C is 1-2 days; D is 0.5 day (soak runs in 30 min + verdict drafting).

---

## Counsel's 7 Rules application

- **Rule 1 (no non-canonical metric leaves the building):** `chain_lock_wait_seconds` is a NEW metric. Its canonical source is the `chain_lock_metrics.py` module — declared at module top. Histogram emits with `# HELP chain_lock_wait_seconds [authoritative: chain_lock_metrics.py] ...`. Operator-only metric (admin /metrics endpoint), not customer-facing — Rule 1 lighter scrutiny applies but declaration is still mandatory.
- **Rule 4 (no segmentation that creates silent orphan coverage):** `bundle_chain_position_gap` (Part 1) IS the orphan-detector Counsel demands. Sev1 per the rule. ✅
- **Rule 3 (privileged-chain attribution):** load test does NOT touch privileged events. The new admin endpoint requires admin bearer but does NOT issue privileged orders (its writes go to the dedicated load-test site, not to privileged_access_attestation). ✅
- **Rule 9 (determinism + provenance):** auditor-kit determinism preserved because (a) load-test site has `client_org_id IS NULL` → never appears in any kit; (b) `bundle_chain_position_gap` invariant DETECTS determinism violations in production at 60s ticks; (c) the carve-out literal is single-line, version-controlled, and gated by `load_test_runs.status='running'` for the dedicated test site. ✅
- **Rule 6 (no legal/BAA state in human memory):** load-test site has no BAA, no client_org. Not applicable. ✅
- **Rule 2 (no raw PHI crosses appliance boundary):** load test uses synthetic data only. Verified by mig 325 seed shape (no PHI fields). ✅
- **Rule 7 (no unauthenticated channel gets meaningful context):** load test endpoints require admin bearer. /metrics requires scrape-token or admin auth (existing). ✅

---

## Findings

### P0 (BLOCK — must close before any sub-commit ships)

- **P0-1a** Ship `bundle_chain_position_gap` invariant + runbook + display_metadata FIRST as sub-commit A. Load extension CANNOT run before this is live with baseline 0 (or non-zero quarantined).
- **P0-1b** Invariant docstring MUST enumerate carve-outs (genesis, mig 043 historical, future-backfill gate rule). Carve-out rule added to CLAUDE.md ledger in same commit.
- **P0-1c** Query MUST use `WHERE created_at > NOW() - INTERVAL '24 hours'` (partition pruning). NEVER unbounded.
- **P0-1d** PARTITION BY site_id only (NEVER include check_type).
- **P0-2a** Use dedicated admin endpoint for chain-contention writes — never invoke production journal_api / evidence_chain / privileged_access_attestation paths from k6.
- **P0-2b** Pre-seeded 20 bearers via mig 325, NEVER per-run mint. `bearer_revoked` stays FALSE permanently for these.
- **P0-2c** `chain_lock_wait_seconds` histogram in NEW module `chain_lock_metrics.py` (NOT in prometheus_metrics.py — keep that file query-only). 7-bucket choice pinned. Instrument all 6 production callsites for the side-benefit of production observability.
- **P0-2d** Carve-out literal in `load_test_marker_in_compliance_bundles` invariant + companion invariant `load_test_chain_contention_site_orphan` ship in same sub-commit. Carve-out test gate updated.
- **P0-2e** k6 scenario gated by `load_test_runs.status='running'` (no carve-out from existing kill-switch).

### P1 (MUST-fix-or-task)

- **P1-1a** Pre-load `substrate_violations` for `bundle_chain_position_gap` MUST baseline 0. If non-zero, load test invalid until investigated.
- **P1-2a** Stated p99 budget: chain_lock_wait_seconds < 500ms at 20-VU. Above = BLOCKING redesign trigger.
- **P1-2b** Document `hashtext()` int4 collision risk in design doc (acceptable today, file follow-up task for hashtextextended() at >1M sites).
- **P1-2c** Verify `load_test_runs.started_by` is named human (mig 316 already requires NOT NULL TEXT; verify CI gate `test_load_test_runs_started_by_is_human_email.py` exists or add).
- **P1-2d** Sub-commit D verdict file path pre-registered in design doc; Gate B forks read it post-soak.

### P2 (consider)

- **P2-2a** Histogram emit at scrape time may need a snapshot-and-reset pattern if cumulative counter is not desired. For load-test debugging the cumulative-since-process-start counter is fine (k6 wrapper records pre/post deltas in the verdict file).
- **P2-2b** Future graduation path: when prometheus_client is added for some other reason, migrate `chain_lock_metrics.py` to use `prom.Histogram` properly (clean buckets, no manual reset semantics). Track as low-priority task.
- **P2-2c** Consider per-callsite label on `chain_lock_wait_seconds` (`source="evidence_chain"|"journal"|"relocation"|"privileged"|"runbook_consent"|"stripe_connect"`) — costs 6× cardinality but lets operators see which writer contends worst. Recommend YES; cost is trivial at this label count.

---

## Final

- **Part 1 (`bundle_chain_position_gap` invariant):** APPROVE-WITH-FIXES (4 P0 + 1 P1)
- **Part 2 (load extension):** APPROVE-WITH-FIXES (5 P0 + 4 P1)

**Overall #117: APPROVE-WITH-FIXES.** Proceed with 4-sub-commit sequence A → B → C → D. Each sub-commit requires its own Gate B fork (TWO-GATE lock-in). Sub-commit D's soak verdict file IS the Gate B for #117 as a whole. Total estimated calendar 4-5 days.

Path: `audit/coach-117-chain-contention-load-gate-a-2026-05-16.md`
