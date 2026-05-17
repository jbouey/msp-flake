# Gate A — Task #122 P2-3: finish `compliance_bundles.appliance_id` deprecation

**Date:** 2026-05-16
**Reviewer:** fork-based 7-lens (general-purpose subagent, fresh context, opus-4.7[1m])
**Scope reviewed:** mig 012a, mig 047, mig 013, mig 138 (v_control_status), mig 268, mig 271; evidence_chain.py:1085-1596, runbook_consent.py:460, appliance_relocation.py:222/383, privileged_access_attestation.py:497; RESERVED_MIGRATIONS.md.

## Overall verdict: APPROVE-WITH-FIXES

Column is already de-facto dead — all 4 backend writers omit it; mig 268 marked it DEPRECATED 2026-05-01; per-appliance binding lives canonically in `evidence_chain.matched_appliance_id` → `site_appliances` per Session 196 rule. **BUT** a hard DROP is premature and out-of-scope for #122. Ship a **Phase-1 lock-down** (CI gate + substrate invariant + view rewrite + index removal); defer DROP to a sibling task gated on N-day quiet-soak.

## Critical-question answers

1. **Safe to drop?** Not in this task. Column is **not in the signed payload** (verified: evidence_chain.py:1443 INSERT omits `appliance_id`; `signed_data` = `canonical` JSON built upstream — signature is independent). DROP is signature-safe but partition-DDL-risky (`compliance_bundles` is monthly-partitioned per mig 138 + DELETE+INSERT-upsert pattern) and view-dependency-risky (mig 138's `v_control_status` still references `cb.appliance_id` + `cb.outcome`).
2. **Downstream consumers:** (a) `v_control_status` VIEW (mig 138) — STALE since mig 268, likely unused; (b) index `idx_compliance_bundles_appliance_type` (mig 047) on `(appliance_id, reported_at DESC, check_type)` — DEAD INDEX (column always NULL); (c) zero Python readers in `auditor_kit_*`, `client_portal`, `partners`, `prometheus_metrics`. Per-appliance binding everywhere uses `evidence_chain.matched_appliance_id` → `site_appliances.agent_public_key` JOIN on signing-key fingerprint.
3. **CI gate against new writers:** YES — add `tests/test_no_compliance_bundles_appliance_id_writes.py` AST gate. Scan every `INSERT INTO compliance_bundles (...)` and `UPDATE compliance_bundles SET ...`; fail if `appliance_id` appears in the column list. Ratchet baseline = 0 (true today).
4. **Migration shape:** #122 is **Phase 1** only.
   - **Phase 1 (#122):** CI gate + substrate invariant + REWRITE `v_control_status` (use `site_appliances` JOIN + `check_result` instead of `outcome`) + DROP CONCURRENTLY dead index. NO column DROP.
   - **Phase 2 (sibling task):** 14-day quiet-soak via the invariant; verify zero reads in `pg_stat_statements`.
   - **Phase 3 (sibling task):** `ALTER TABLE compliance_bundles DROP COLUMN appliance_id` — visit all 25+ monthly partitions; maintenance window.
5. **Substrate invariant:** does NOT exist. Add `compliance_bundles_appliance_id_write_regression` (sev2 — column is dead, not customer-facing). SQL: `SELECT count(*) FROM compliance_bundles WHERE appliance_id IS NOT NULL AND created_at > NOW() - INTERVAL '1 hour'`. Threshold > 0 = fail.

## Per-lens verdict

- **Steve (architecture):** APPROVE — Phase 1 structurally correct; DROP-as-Phase-1 a footgun on 25-partition table.
- **Maya (legal/audit):** APPROVE — column not in signed payload; mig 268 COMMENT is auditor-visible deprecation evidence; chain integrity untouched.
- **Carol (security):** APPROVE — dead-index removal reduces attack surface; AST gate prevents future accidental id-leak via reused column.
- **Coach (consistency):** APPROVE-WITH-FIX — REQUIRES rewriting `v_control_status` in same commit; leaving a deprecated-column-reading VIEW is the exact antipattern mig 268 warned against.
- **Brian (DB):** APPROVE — ratchet-baseline-0 AST gate + sev2 substrate invariant is the right belt-and-suspenders for the soak window.
- **Diana (DBA):** APPROVE-WITH-FIX — dead-index DROP is a separate single-statement file using `DROP INDEX CONCURRENTLY` per CLAUDE.md (no BEGIN/COMMIT/trailing COMMENT).
- **Priya (PM/scope):** APPROVE — Phases 2+3 must be tracked as named TaskCreate followups in same commit per TWO-GATE rule on P1s.

## P0 bindings (close before Phase 1 ships)

- **P0-1:** Rewrite `v_control_status` to JOIN `site_appliances` + use `check_result` (mirror mig 268). OR `DROP VIEW IF EXISTS` if `pg_stat_user_tables` shows zero seq+idx scans for ≥30d.
- **P0-2:** AST CI gate must land **in the same commit** as the substrate invariant — otherwise gap window between deploy and first invariant tick.

## P1 bindings (same-commit TaskCreate followups)

- **P1-1:** TaskCreate "Phase 2 — 14d quiet-soak for `compliance_bundles.appliance_id` DROP".
- **P1-2:** TaskCreate "Phase 3 — DROP COLUMN appliance_id (25+ partitions, maintenance window)".
- **P1-3:** `DROP INDEX CONCURRENTLY idx_compliance_bundles_appliance_type` — separate single-statement mig file.

## P2 bindings

- **P2-1:** `<!-- mig-claim:326 task:#122 -->` + `<!-- mig-claim:327 task:#122 -->` markers in design doc + rows in RESERVED_MIGRATIONS.md.
- **P2-2:** Substrate invariant runbook at `substrate_runbooks/compliance_bundles_appliance_id_write_regression.md` (CI gate `test_substrate_docs_present` requires it — Session 220 lesson from `39c31ade`).
- **P2-3:** Add CLAUDE.md sentence under the `compliance_bundles` rule: `appliance_id` is DEPRECATED — never write; resolve per-appliance binding via `site_appliances` JOIN on `agent_public_key` fingerprint (see `evidence_chain.matched_appliance_id`).

## Anti-scope

- DROP COLUMN itself (Phase 3 sibling).
- `evidence_bundles` legacy table (mig 213 already dropped its FK).
- Refactoring `evidence_chain.matched_appliance_id` — it IS the correct binding.
- `canonical_site_id()` — BANNED on `compliance_bundles` per CLAUDE.md.

## Migration claim

**Mig 326 + 327 IN SCOPE** for Phase 1 — split: `326_rewrite_v_control_status.sql` (transactional, CREATE OR REPLACE VIEW) + `327_drop_dead_appliance_id_index.sql` (single-statement, `DROP INDEX CONCURRENTLY` only). Add both rows to RESERVED_MIGRATIONS.md in design-doc commit; remove on ship. Phases 2 + 3 will claim later numbers when they enter design.

## Counsel-7-Rule filter

- **R1 (canonical metric):** PASS — strengthens canonicality (one binding path: site_appliances JOIN, not NULL-always column).
- **R2 (PHI boundary):** PASS — metadata-only.
- **R3 (privileged chain):** PASS — `privileged_access_attestation` writer already omits the column; chain unaffected.
- **R4 (orphan coverage):** PASS — removes a fake binding that could mask multi-appliance orphan detection at scale.
- **R5 (stale-doc authority):** PASS — mig 268 COMMENT becomes machine-enforced.
- **R6 (BAA in memory):** N/A.
- **R7 (unauth context):** N/A.
