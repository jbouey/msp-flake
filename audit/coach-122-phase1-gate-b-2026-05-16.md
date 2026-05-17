# Gate B Adversarial 2nd-eye — #122 Phase 1 (commit 6b83b43a)

**Verdict:** APPROVE-WITH-FIXES (1 P2; no P0/P1)
**Reviewer:** fresh-context Gate B fork per TWO-GATE protocol
**Scope:** as-implemented commit `6b83b43a` — mig 326 + mig 327 + assertions sev2 + frameworks.py SELECT-list + AST CI gate + runbook + .githooks/pre-push allowlist add

## Test sweep
`.githooks/full-test-sweep.sh` → **281 passed, 0 skipped (back-end-dep tests deferred to CI as designed). Exit 0.**

## Per-lens verdict

1. **Steve (Principal SWE) — APPROVE.** Mig 326 view body is clean: WITH-CTE + ROW_NUMBER() partition is the right shape, `cb.created_at >= NOW() - INTERVAL '30 days'` aligns with partition-prune window. SA JOIN line is on the JOIN clause (Session 218 RT33 P1 rule). One-bundle→N-row fan-out is intentional and documented. CREATE OR REPLACE makes mig idempotent.
2. **Maya (Security/HIPAA) — APPROVE.** No PHI in the view (control_id + framework + status only). View inherits RLS from underlying tables (compliance_bundles, site_appliances both have site-RLS policies). Column rename outcome→status is internal — no email/auditor-kit/portal surfaces re-render. Sev2 invariant lives inside admin_connection context (`_check_*(conn)`) — does not leak rows.
3. **Carol (CCIE/Ops) — APPROVE.** View swap via CREATE OR REPLACE is online (no rewrite). Mig 327 uses CONCURRENTLY + IF EXISTS — no blocking lock; safe on prod with active writers. Index drop matches Gate A's "0 readers" claim — grep confirms no `ORDER BY appliance_id, reported_at` against compliance_bundles in any backend or appliance code path.
4. **Coach (DBA) — APPROVE.** Query plan should be:
   - Partition-prune on `cb.created_at` (compliance_bundles is monthly-partitioned, mig 138) — only ~1-2 partitions scanned.
   - HASH JOIN site_appliances (small table) — efficient.
   - HASH JOIN evidence_framework_mappings on bundle_id (indexed via mig 271).
   - ROW_NUMBER() window over (appliance_id, framework, control_id).
   - No table scan, no compliance_bundles index needed on appliance_id. Confirms mig 327's "index unused" assumption is defensible from query shape alone.
5. **Auditor — APPROVE.** Counsel Rule 4 (no silent orphan coverage) — strengthened: view used to silently return 0 rows for the auditor endpoint, now returns real per-appliance per-framework per-control status. Counsel Rule 5 (no stale-doc authority) — mig 326 COMMENT ON VIEW explicitly references mig 268 root cause. Deprecation is now machine-enforced via 3 layers: build-time AST + runtime sev2 + view dependency removal.
6. **PM — APPROVE.** Scope discipline is clean: Phase 1 ONLY (lockdown). Phase 2 (14d quiet-soak monitoring) is task #135. Phase 3 (DROP COLUMN) is task #136. Mig 326 + 327 + AST gate + sev2 invariant + runbook + pre-push allowlist = exactly the Phase 1 deliverable bundle from Gate A. No scope creep into Phase 2.
7. **Counsel (7-rule filter) — APPROVE.** Rule 1 (canonical metric) — strengthened: v_control_status used to be a non-canonical dead read (silent zeros to operator dashboards via frameworks endpoint); now returns the canonical compliance_bundles content via the per-mig-268 column. Rule 2 (no raw PHI) — no boundary crossed. Rule 3 (privileged chain) — not touched. Rule 4-7 — not impacted.

## Findings

**P2-1 (cosmetic — does NOT block):** The AST CI gate's `_UPDATE_PATTERN` regex `r"UPDATE\s+compliance_bundles\s+SET\s+([^;]+?)(?:WHERE|RETURNING|$)"` uses a non-greedy match with `$` as a terminator that is anchored to end-of-string, not end-of-line. For multi-statement Python triple-quoted strings without explicit `;`, this could over-capture. Verified harmless in current callsites (the existing UPDATE at `privileged_access_notifier.py:357` is single-column `notified_at = NOW()` and would not contain `appliance_id =`). Recommend in a followup: add `re.MULTILINE` flag and use `\Z` or anchor on the next-statement boundary. **Not a P0/P1 — gate currently produces no false positives or false negatives against the existing tree.**

## Adversarial concerns addressed

- **Multi-appliance fan-out (Steve adv check):** Only consumer = `frameworks.py::get_control_status` which filters `WHERE appliance_id = :appliance_id` — correctly grabs the right slice. No grep hits for v_control_status elsewhere.
- **outcome→status rename (Steve adv):** Only one Python reader (`frameworks.py:319-333` — already updated this commit). Migrations 013 + 138 reference `cb.outcome` in OLDER versions of the view body, but mig 326 supersedes via CREATE OR REPLACE. Mig 013's `calculate_compliance_score` function still references `cb.outcome` but is itself superseded by mig 268 — no live code path uses the old function body.
- **Mig 327 index unused (Carol adv):** Verified — zero backend/appliance code does `ORDER BY appliance_id, reported_at` against compliance_bundles. The 3 `ORDER BY appliance_id` matches in assertions.py are against different tables (heartbeat_signatures, drift_event partitions). Mig 327 file is single-statement (grep `[^-].*;` → 1).
- **1h invariant window (Coach adv):** No known intentional backfills currently in scope. Mig 306 (L1-orphan backfill) writes via DELETE+INSERT pattern and inserts NULL appliance_id by design. Safe.
- **AST gate appliance_id collision (Maya adv):** Regex captures the INSERT's column list specifically `(...)` — JOINs and `sa.appliance_id` references in same-string SELECTs would not match the INSERT pattern. False-positive risk = effectively zero.

## Phase 2 readiness — 14d quiet-soak START NOW?

**YES — APPROVED to start.** All Phase 1 deliverables shipped + CI green + runtime invariant live. Phase 2 = 14d window beginning 2026-05-16 watching `compliance_bundles_appliance_id_write_regression` open count. Cliff: 2026-05-30. If invariant remains 0 violations over 14d, task #136 Phase 3 DROP COLUMN unblocks pending its own Gate A.

The P2 regex cosmetic fix can be carried as TaskCreate followup without blocking Phase 2 start.

---
Word count: ~690
