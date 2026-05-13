# Gate A Verdict — D1 Per-Control Granularity
Date: 2026-05-12
Reviewer: Gate A fork (Steve / Maya / Carol / Coach) — fresh context, no author counter-args allowed
Scope reviewed: design doc + mig 271 + evidence_chain.py:1745-1880 + compliance_score.py + scripts/backfill_efm_check_status.py + tests/test_per_control_lockstep.py + tests/test_admin_transaction_for_multistatement.py + tests/test_check_constraint_fits_column.py

## Verdict: **APPROVE-WITH-FIXES**

The design is structurally sound. Code is largely already on-disk (mig 271 + writer change + backfill script + lockstep tests). However, **the design doc's framing significantly overstates the customer impact** of this fix, and there are 4 P0s + 5 P1s that must close before backfill executes against prod.

The single most load-bearing finding: **`calculate_compliance_score` (the SQL function this fix changes) is NOT the customer-facing canonical score.** The customer-facing helper is `compute_compliance_score` in `compliance_score.py`, which unnests `compliance_bundles.checks` JSONB directly and never touches `evidence_framework_mappings.check_status`. Round-table-approved framing of "fixes under-reporting on the dashboard score" is wrong on the surfaces it claims. The fix is real, but it lands on `compliance_scores` (the per-appliance rollup table populated via `refresh_compliance_score → calculate_compliance_score`), which feeds `/api/frameworks` + per-appliance metrics, **not** the client portal headline.

---

## Findings by lens

### Steve (correctness) — P0:2 / P1:2 / P2:1

**P0 — Wrong-helper customer-impact mis-scope** (`compliance_score.py:157` vs `migrations/271_*.sql:59`). Design doc §"Customer impact" claims this fixes the customer-facing dashboard under-reporting. It does not. Three customer surfaces (`/api/client/dashboard`, `/api/client/reports/current`, `/api/client/sites/{id}/compliance-health`) all delegate to `compute_compliance_score` (compliance_score.py:157) which directly unnests `cb.checks` JSONB at line 234-279. They never read `evidence_framework_mappings.check_status`. The mig-271 fix lands on the `compliance_scores` rollup table consumed by `/api/frameworks` (frameworks.py:216, 425, 759) and per-appliance compliance views in routes.py — NOT the customer headline. Either: (a) close-out claim must be re-scoped to "per-appliance + framework-rollup score fix" with explicit list of fixed/unfixed surfaces, OR (b) plan a follow-up that ports per-control aggregation into `compute_compliance_score` as well. The current state is mathematically inconsistent across surfaces (a sin Stage-2 RT25 went to lengths to fix).

**P0 — Race-window during 24h backfill: re-ingest of a historical bundle writes correct check_status before the backfill reaches it, then backfill UPDATEs `WHERE check_status IS NULL` correctly skips it — but the inverse path is broken**. If backfill processes bundle B at T=10:00 (writes 'pass'), and then a stale daemon re-submits the same bundle at T=11:00 with subtly different per-host statuses (genuine re-ingest), the writer's `ON CONFLICT DO UPDATE` overwrites the backfilled value. This is documented but unconsidered: backfill row + re-ingest row should produce identical aggregates (YAML hasn't moved in that hour), so divergence implies the bundle's JSONB has the wrong shape. Defensible — but only if telemetry alerts on the case. No metric exists today.

**P1 — Default `unknown` consumption.** Reader function (mig 271:106) counts `unknown_controls` separately but excludes from numerator. Total denominator INCLUDES unknowns. A site with 8 pass / 0 fail / 2 unknown computes 8/10 = 80%, NOT 8/8 = 100%. The design doc §"Open design questions" #1 lists this as "separate bucket — excluded from score numerator" but the function as-written includes them in denominator. This is the strict-HIPAA-conservative reading and likely correct, but the design doc never closes the question explicitly and the customer-facing copy ("data_completeness" Steve delta) has not shipped, so customers will see scores drop without explanation when an `unknown` slot enters.

**P1 — `if not statuses: continue` guard is dead code.** evidence_chain.py:1830 — `statuses` is the value side of `control_to_statuses.setdefault(key, []).append(status)`. Empty list is impossible because the `.append` only runs after a successful key insert, AND the outer for-loop iterates over `control_to_statuses.items()` which only contains keys that had ≥1 append. Brian's delta was specced as defense-in-depth, but it can never fire. Risk: if a future refactor populates `control_to_statuses[key] = []` without appending, the guard masks the regression silently rather than raising. Recommend: replace with `assert statuses, f"empty statuses for {key}"` so it fails loud in tests.

**P2 — Function ignores `appliance_id` parameter.** mig 271:60-99 — `p_appliance_id` resolves to a single `v_site_id` via `LIMIT 1` then queries by `site_id`. For multi-appliance sites the function ignores which appliance asked. Pre-existing in mig 268 (not a D1 regression) but worth noting given the per-control granularity now makes it more visible: appliance A's score and appliance B's score on the same site will be IDENTICAL even when they cover different controls. Adjacent to flywheel-spine per-appliance metric requirement.

### Maya (HIPAA/§164.528) — P0:1 / P1:1 / P2:1

**P0 — Score-shift forensic disclosure framing is wrong if backfilled scores DROP for any site (not just rise).** Design doc §"Forensic disclosure" assumes scores will only "JUMP UP" because pre-fix under-reported. That holds IF every historical bundle's mapping resolves to ≥1 passing check per control. But: (1) `unknown_controls` is a new bucket. A bundle covering a check that didn't run on a given control will produce `unknown` per-control aggregation post-backfill, where pre-fix it would have been counted as bundle-level pass/fail. Sites with shrinking check coverage post-backfill will see scores DROP into the `unknown_controls` bucket. (2) Sites where the bundle-level result was 'pass' (no host failed any check) but the per-control aggregation finds a control with no matching check at all will now show unknown for that control where pre-fix it didn't appear. Disclosure tier may need to elevate to "minor advisory" if any site's score moves down >5% in either direction. Specifically: dual-direction telemetry + the "data_completeness" field MUST land in the same PR as backfill, not "separate follow-up" (Steve delta #3 should be tightened from "separate PR" to "same PR").

**P1 — §164.528 disclosure-accounting interaction.** Auditor kit and quarterly reports both bind to `compute_compliance_score` (the unaffected helper) — chain-of-custody integrity preserved. The auditor kit determinism contract (CLAUDE.md Session 218) is therefore unaffected. **But** the per-appliance compliance scores in `compliance_scores` table (read by routes.py:178 dashboard endpoints) feed admin/partner-facing views and are mirrored in the partner BAA roster + quarterly practice summaries. Verify no §164.528-tracked artifact reads from `compliance_scores` directly — if any does, retroactive correction of the cached rollup requires the same disclosure framing as the rest. Worth a 30-min grep before backfill.

**P2 — Auditor kit determinism cross-check.** Mig 271 doesn't touch the kit, but the kit's `chain_metadata` (per CLAUDE.md Session 218 contract) MUST NOT include any score derived from `calculate_compliance_score`. Confirmed by code-walk — the kit reads bundles + OTS proofs directly. NO-OP for D1, but worth flagging in the close-out so a future "let's add the score to the kit" change has the right defaults.

### Carol (DB) — P0:1 / P1:2 / P2:2

**P0 — Backfill performs `UPDATE … WHERE id = $X` on a partitioned table without partition pruning.** Backfill script line 172-176 issues one UPDATE per (bundle_id × mapping_row). Mig 138 partitioned `compliance_bundles` (per CLAUDE.md note) — does `evidence_framework_mappings` follow? mig 013 line 60 created it non-partitioned. Confirmed non-partitioned, so partition-prune concern doesn't apply HERE, BUT: the UPDATE issues one row at a time. At 117K rows × 200ms sleep / 1000-row chunks × 1 UPDATE per mapping row, expected runtime is dominated by network roundtrips not DB work. Empirical math: 117K / 1000 = 117 chunks × (1000 bundles × ~5 mappings each = 5000 UPDATEs/chunk) = 585K UPDATEs total. At even 2ms each that's ~20min, not 12min. AND: no batching means PgBouncer transaction-pool will churn — each UPDATE is auto-commit by asyncpg default which hits PgBouncer hard. Recommend: wrap each chunk in `async with conn.transaction()` to batch commits, OR convert the inner loop to a single `UPDATE … FROM (VALUES (...), (...)) AS v WHERE efm.id = v.id` per bundle.

**P1 — Backfill does not advisory-lock against concurrent runs.** Two operators (or a cron + a manual run) both starting the script will both query `WHERE check_status IS NULL` and process overlapping bundles. The `WHERE check_status IS NULL` filter makes double-UPDATE idempotent, but each redundant UPDATE bloats WAL + ~doubles cost. Recommend: `SELECT pg_try_advisory_lock(hashtext('backfill_efm_check_status'))` at script entry, abort if not acquired.

**P1 — Function still has no index on `(framework, control_id, check_status)`.** Diana's delta dropped the proposed `idx_efm_status_lookup` pending EXPLAIN. No follow-up exists in any TaskCreate or commit log to actually RUN the EXPLAIN. Without it, the function falls back to the UNIQUE `(bundle_id, framework, control_id)` index. For a hot site with 50K bundles, the planner has to scan + filter `check_status IS NOT NULL` + the framework filter. Acceptable for one-off, NOT acceptable inside `refresh_compliance_score` which fires every evidence submission. Carol asks: schedule the EXPLAIN within 48h post-backfill; commit threshold for adding the index is "any production EXPLAIN over 200ms per call".

**P2 — `make_interval(days => p_window_days)` is correct but the function has no `STABLE`/`IMMUTABLE` volatility marker.** Default is `VOLATILE`. Postgres re-runs it per row, which doesn't matter for a one-shot function call but bites when called inside a query. Low-impact today; flag for the next refactor.

**P2 — `ADD COLUMN IF NOT EXISTS check_status VARCHAR(20)` with no DEFAULT is non-blocking** ✓ verified in mig 271:30. CHECK constraint `DROP IF EXISTS … ADD` is OK on a small column. The 117K-row scan to verify the new CHECK won't lock writers (Postgres 11+ uses concurrent constraint validation in this pattern). However, mig 271 has no `NOT VALID` + `VALIDATE CONSTRAINT` split — if `evidence_framework_mappings` grows to 5M rows the next constraint addition WILL block. Pattern-precedent for future migrations.

### Coach (lockstep) — P0:0 / P1:3 / P2:2

**P1 — `test_admin_transaction_for_multistatement.py` does NOT pin `map_evidence_to_frameworks`.** Design coach delta #1 explicitly required extending this test to include the new admin_transaction site. Verified line 32-53 lists 4 pinned sites; `evidence_chain.py::map_evidence_to_frameworks` is NOT among them. A future revert of the `admin_connection → admin_transaction` swap at evidence_chain.py:1772 + 1792 will fail silently. Add a 5th entry: `("mcp-server/central-command/backend/evidence_chain.py", "async def map_evidence_to_frameworks", 150)`.

**P1 — No D6-gate coverage of `efm_check_status_valid` CHECK.** `tests/test_check_constraint_fits_column.py` exists but has zero references to `efm_check_status_valid` (grep `efm|evidence_framework` returns 0). Design doc claimed D6 covered it automatically. Verify the gate's discovery mechanism — if it's grep-based on migration files it MAY pick up mig 271 dynamically; if it's an allowlist it won't. Either way, the design's claim should be backed by a positive-control assertion ("CHECK efm_check_status_valid appears in D6 output") in the same PR.

**P1 — No DB-level integration test (`test_per_control_granularity_pg.py`) shipped.** Design doc §"Test pinning" specifies this as a coach #5 mandatory test. Not on disk. The lockstep test (`test_per_control_lockstep.py`) is source-level only — it pins the taxonomy but does NOT exercise the function. A function bug (e.g., reversed PASSING/FAILING in the rewrite, or DISTINCT ON ordering wrong) will not be caught. Carry as Gate B blocker.

**P2 — `compute_compliance_score` perf cache (`compliance_score.py:218-225`) caches by site_ids alone.** If D1's per-control logic gets ported here (per Steve P0 followup), the cache must invalidate on `evidence_framework_mappings` writes. Today writes to compliance_bundles don't invalidate either — 60s TTL absorbs it — but per-control granularity introduces a new variable. Note for whoever lands the cross-port.

**P2 — Backfill script has no `admin_audit_log` write.** A 117K-row UPDATE of a customer-facing-adjacent table with no audit trail is a Carol-on-call nightmare. Add an `INSERT INTO admin_audit_log (username, action, target, details)` at script entry + exit with bundles_processed / rows_updated / drift_warnings counts. Username = `'backfill-efm-mig-271'`. Same pattern as `auditor_kit_download` per CLAUDE.md Session 218.

---

## Required pre-execution closures (P0)

1. **Re-scope close-out language**: enumerate exactly which surfaces this fix touches (`compliance_scores` rollup → `/api/frameworks` + per-appliance metrics) and which it does NOT (`compute_compliance_score` → client portal headline). Update memory.
2. **Add bidirectional score-shift telemetry** to backfill script: emit per-site delta histograms (pre-mig score vs post-mig score) so Maya's disclosure-tier escalation can run on real data. Defer EXECUTION of backfill until telemetry shipped.
3. **Land `data_completeness` API field in the same PR as backfill** (tighten Steve delta #3): customers must see "X of Y controls have status" alongside the score, or score-drops into `unknown` are invisible to them.
4. **Batch backfill UPDATEs**: `async with conn.transaction()` per chunk OR convert to multi-row `UPDATE … FROM VALUES`. 585K individual auto-commits through PgBouncer is unacceptable load.

## Carry-as-followup (P1, blocks Gate B)

- Extend `test_admin_transaction_for_multistatement.py` to pin `map_evidence_to_frameworks` (one-line fix).
- Add positive-control assertion in `test_check_constraint_fits_column.py` for `efm_check_status_valid`.
- Ship `test_per_control_granularity_pg.py` (DB-gated integration test) before backfill runs.
- Add `pg_try_advisory_lock` guard at backfill entry.
- Add `admin_audit_log` writes at backfill entry/exit.
- Schedule the EXPLAIN ANALYZE for Diana's deferred index decision; commit threshold "200ms per refresh_compliance_score call".
- Replace dead `if not statuses: continue` guard with `assert statuses` (or remove + comment).

## Recommended implementation order

1. Land the 4 P0 closures (re-scope + telemetry + completeness field + batched UPDATEs) — DOC + CODE changes, no DB writes.
2. Land the 6 P1 follow-up tests + guards — TEST-ONLY changes, no DB writes.
3. Run backfill in DRY-RUN against prod, verify drift-warning count + estimated row delta.
4. Run backfill in APPLY mode with a 50K-row cap first (`--max-bundles 50000`); read telemetry; abort if any site sees >10% score swing without `data_completeness` correlation.
5. Run remaining backfill.
6. Schedule EXPLAIN ANALYZE 48h post-backfill; index decision based on output.
7. Cross-port question for Stage-3 round-table: should `compute_compliance_score` adopt the same per-control aggregation? If yes, this fix's footprint roughly doubles.

**Gate B will BLOCK** if any of: P0 #1-#4 unaddressed, P1 lockstep tests not extended, no DB-level integration test, or backfill telemetry doesn't include bidirectional score-delta histograms.
