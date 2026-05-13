# Enterprise-Readiness Coach Audit — 2026-05-12

## Verdict: APPROVE-WITH-FIXES

15 commits over 5 themes ship code-true and runtime-true. Production probes confirm chain integrity, kit version parity, ratchet zeros, and substrate invariants firing as designed. ONE P1 finding requires a follow-up commit (substrate invariant `rename_site_immutable_list_drift` actively flagging the new `l2_escalations_missed` table — caught by the engine on its very first 60s tick post-deploy). ONE P1 carry-over (substrate_sla_breach on `l1_resolution_without_remediation_step`, 35.5h open) predates this session but was not closed by today's work. No P0s. The session's QC-engine narrative — substrate sees what code review misses — is itself the proof point of the verdict.

## Sweep evidence

- **Full pre-push sweep (`.githooks/full-test-sweep.sh`):** `241 passed, 0 skipped (need backend deps)`. Exit code 0.
- **Runtime sha (`curl https://www.osiriscare.net/api/version`):** `ff7be044c0ff2c3668bd738a567d273f4e115e37` — `runtime_sha == disk_sha == matches:true`.
- **Latest commit (main):** `ff7be044 fix(substrate): cover 17 task_defs loops in EXPECTED_INTERVAL_S`. Identical to runtime sha. CI deployed clean.
- **Substrate open (production, prod psql via SSH):**
  - `substrate_sla_breach` — 1 open (meta — fires because of `l1_resolution_without_remediation_step`)
  - `journal_upload_never_received` — 1 open (operator action queue, `north-valley-branch-2` re-image item; pre-existing)
  - `rename_site_immutable_list_drift` — 1 open (NEW — fired on `l2_escalations_missed`; see Carol P1)
  - `l2_recurrence_partitioning_disclosed` — 1 open (sev3 INFORMATIONAL, never auto-resolves — 5 backfilled rows surfaced)
  - `pre_mig175_privileged_unattested` — 1 open (3 pre-trigger rows, public-advisory OSIRIS-2026-04-13; pre-existing)
  - `l1_resolution_without_remediation_step` — 1 open (21 24h orphans, sev2; pre-existing chronic)
  - All 9 other invariants in top-15 — 0 open.
  - **`chronic_without_l2_escalation` — 0 open ✓.** Mig 308 backfill resolved every chronic row that would have triggered it. This is the load-bearing P1-persistence-drift exit gate; it passes.
- **mig 308 + 309 applied:** `308|l2_escalations_missed`, `309|l2_decisions_site_reason_idx` — both rows present in `schema_migrations`.
- **mig 309 index health:** `indisvalid=t, indisready=t` on `idx_l2_decisions_site_reason_created`. The manual `INSERT INTO schema_migrations` rescue after SSH died mid-CONCURRENTLY did NOT leave the index in `invalid` state — index actually completed before SSH dropped, only the bookkeeping row was orphaned. Safe.
- **mig 308 backfill rowcount:** `SELECT COUNT(*) FROM l2_escalations_missed` → `5`. Matches session log claim ("5 rows on prod") exactly.

## Findings by lens

### Steve — correctness / recovery state | P0:0 P1:0 P2:1

- **P2** — The manual `INSERT INTO schema_migrations` recovery for mig 309 (after SSH died mid-CONCURRENTLY in commit `3154c0b1`) worked in this instance because the CONCURRENTLY DDL had already committed pre-SSH-drop, but the recovery pattern itself ("if SSH dies during a manual psql, hand-insert the migration row") is dangerous as a precedent. Recommend: capture in `docs/lessons/sessions-220.md` (when written) that next CONCURRENTLY migration must be wrapped in a `migration_runner` retry helper, not run by hand. No fix required for this session — just a process artifact.

- **bg_loop_silent firings in last hour:** zero — but the prod `substrate_violations` table schema uses `detected_at`, not `created_at`, so the original probe failed and I re-queried. With correct column, no `bg_loop_silent` rows surface in the top-10-open list. The 17 new EXPECTED_INTERVAL_S entries (commit `ff7be044`) have not caused false-positives. PASS.

- **Commit-body sweep citation spot-check:** `94386c56` (EXPECTED_INTERVAL_S audit doc), `ff7be044` (implementation), `91397e1c` (P2 batch), and `b51c8b10` (BUG 2) — bodies are available in `git log`. I did not exhaustively grep every body for a sweep count citation; the Session 220 lock-in rule requires Gate B to cite the count, which the existing audit files (`audit/coach-p1-persistence-drift-l2-gate-b-2026-05-12.md` etc.) DO carry. Spot-checked: Gate B for P1 persistence-drift exists and references the gates. PARTIAL TRUST — full enforcement of "every commit body cites sweep count" is task #111-class follow-up, not a today-blocking gap.

### Maya — HIPAA / §164.528 / opaque-mode | P0:0 P1:0 P2:0

- **kit_version 2.2 parity across 5 surfaces in `evidence_chain.py`:** confirmed at lines 4499, 4677, 4724, 4796, 4828. All five literals match. PASS.
- **`SECURITY_ADVISORY_2026-05-12_RECURRENCE_DETECTOR_PARTITIONING.md` honesty review:** read end-to-end. Customer-impact framing is honest — does NOT use banned words (no "ensures"/"prevents"/"100%"/"guarantees" in the document body); uses "intact" (factual), "REMEDIATED + DISCLOSED" (verified by mig 308 rowcount), "zero customer reports affected" (testable claim); cites the SLA being a "contractual technical-control claim" and ties the disclosure to the Session 203 standing commitment. The MEDIUM severity grading is defensible: PHI scrubbing intact, compliance_bundles signing chain intact, only routing decision impacted. PASS.
- **Banned-word scan on full 18-commit diff (`7f878c77..ff7be044`):** zero hits on `ensures|prevents|protects|guarantees|audit-ready|100%|PHI never leaves`. PASS.
- **New customer-facing email helpers in today's diff:** none introduced. The advisory references opaque-mode emails for the 5 missed-escalation rows but routes them through existing `_send_operator_alert` infrastructure that already respects the opaque-mode rule. PASS.
- **`disclosed_in_kit_version` field plumbed correctly:** `evidence_chain.py:4838` carries the per-row disclosed_in_kit_version from `l2_escalations_missed` into the kit JSON. Determinism contract preserved (sort_keys + sorted iteration).

### Carol — DBA / data integrity | P0:0 P1:1 P2:0

- **P1** — `rename_site_immutable_list_drift` fired on `l2_escalations_missed`. Migration 308 created an INSERT-only table (DELETE-blocking trigger + UPDATE-blocking trigger, mig 308 lines 82-110) but did NOT update `_rename_site_immutable_tables()` (the SQL function from mig 257, last touched by mig 263). The substrate invariant from mig 258 caught it on the very first 60s tick post-deploy. Concrete remediation: write a follow-on migration (call it 310) that does `CREATE OR REPLACE FUNCTION _rename_site_immutable_tables()` and adds the row `RETURN QUERY VALUES (..., ('l2_escalations_missed'));`. The substrate engine itself wrote the remediation text in its `details->remediation` field. Cost: ~5 LOC SQL + 1 migration + a Gate-B fork. Not blocking enterprise-readiness because the engine caught it and the disclosure narrative is intact; **but it IS the kind of loose end that today's session should have caught BEFORE it surfaced in substrate.** Mig 308 was a new immutable table — adding it to the immutable-list function should have been part of the same PR.

- **mig 308 backfill verified:** 5 rows, matches session log.
- **mig 309 index valid + ready:** `indisvalid=t indisready=t`. Manual `schema_migrations` insert is safe in retrospect.
- **`chronic_without_l2_escalation` substrate invariant has 0 open violations** — the load-bearing exit gate for the P1 persistence-drift work passes.
- **Lockstep lists for privileged chain:** mig 305 (`delegate_signing_key`) shipped 2026-05-11, NOT touched by today's commits. `test_privileged_order_four_list_lockstep.py` passed (3 tests). No regression.

### Coach — lockstep / banned shapes / sibling parity | P0:0 P1:0 P2:1

- **`_LOOP_LOCATIONS` parity (both files):** 32 entries each, identical key sets. AST verified. PASS.
- **`test_no_anonymous_privileged_endpoints.py` standalone:** passed (2 tests inside the 70-test run; ratchet inspection confirms `RATCHET_ANONYMOUS` is empty — no remaining offenders).
- **`test_no_same_origin_credentials.py` BASELINE_MAX:** `=0` confirmed in source at line 56. Test passed.
- **`test_l2_resolution_requires_decision_record.py`:** 3 tests passed.
- **`test_no_appliance_id_partitioned_recurrence_count.py`:** 4 tests passed.
- **`test_l2_escalations_missed_immutable.py`:** 8 tests passed.
- **`test_substrate_docs_present.py`:** 71 tests passed — all runbooks for new substrate invariants exist.
- **Banned-shape scan on today's diff:** zero hits on `jsonb_build_object(unannotated)`, zero `except Exception: pass` near `conn.execute`, zero `datetime.now()` in kit-generation code, zero `NOW() - INTERVAL` in WHERE clauses of CREATE INDEX statements (mig 308/309 use `WHERE site_id IS NOT NULL` style only).
- **P2** — Two pending follow-up tasks (#32 SiteDetail.tsx URL fixes; #33 main.py inline loops → `_LIFESPAN_INLINE_LOOPS`) carried correctly. Neither blocks enterprise-readiness; both should be picked up next session.

## Required closures (P0)

None.

## Carry-as-followup (P1)

1. **mig 310 — add `l2_escalations_missed` to `_rename_site_immutable_tables()`.** Trigger: substrate `rename_site_immutable_list_drift` open. Cost: ~5 LOC. Pattern: copy mig 259 / mig 263 SQL shape verbatim. Must run through Gate A + Gate B fork (function-body-rewrite class — Session 220 lock-in pinned that `enforce_privileged_order_attestation` is ADDITIVE-ONLY; same body-rewrite hazard applies here).

2. **`l1_resolution_without_remediation_step` SLA breach (35.5h open, 21 24h-orphan incidents on `north-valley-branch-2`).** Pre-existing chronic. Either: (a) progress the operator action queue (re-image `north-valley-branch-2` per `journal_upload_never_received` remediation, which would close the upstream incident class), or (b) add to `_check_substrate_sla_breach.LONG_OPEN_BY_DESIGN` with round-table sign-off. This is the meta-substrate working as designed — the SLA-breach gate is escalating an unresolved sev2.

3. **Existing pending tasks #32 + #33** stay as-is.

## Enterprise-readiness verdict

The 15-commit session ships APPROVE-WITH-FIXES. None of today's 18 commits introduced a regression detectable by the 241-test sweep, 88 targeted gates, banned-word scan, or production substrate state. The one new P1 (`rename_site_immutable_list_drift`) is itself proof that the substrate-as-QC narrative is functioning — the engine surfaced a follow-up the human review missed. That's the right failure mode at enterprise scale: invariants catch the things test suites can't enumerate.

The session demonstrates the Session 219/220 TWO-GATE discipline working as designed:
- 5 zero-auth commits + 1 ratchet → 0 (BUG 2) + 1 ratchet 14→7 (BUG 3) + 1 coverage +17 (EXPECTED_INTERVAL_S) + 5 persistence-drift commits.
- Every theme has an audit digest or coach verdict file in `audit/` or `.agent/digests/`.
- Runtime sha matches latest commit (deploy verification rule, Session 215 #77).
- Disclosure-first posture maintained (advisory file, kit_version bump, parallel disclosure table).

The single P1 closure (mig 310) should ship next session, not blocking today's done-line.

## Honesty check

What I trusted vs. spot-checked:

- **Spot-checked** (cited evidence): sweep count, runtime_sha, all targeted gates ran locally, substrate violations queried on prod, mig 308/309 schema_migrations row verified, kit_version 5 surfaces grepped + cited line numbers, _LOOP_LOCATIONS parity computed via AST, banned-word diff scan run, security advisory read end-to-end.

- **Trusted without re-running** (called out): The audit-digest files for each theme (`.agent/digests/2026-05-12-bug2-*.md`, `2026-05-12-zero-auth-*.md`, `2026-05-12-expected-interval-*.md`) and the two P1-persistence-drift Gate A + Gate B fork verdicts in `audit/`. I read fragments to confirm they exist and reference the gates, but did NOT re-execute the adversarial reviews. The Gate A + Gate B forks themselves were the load-bearing artifacts that today's commits depended on; this audit is a meta-pass over the session, not a re-litigation of each theme.

- **Could not verify**: I did not exhaustively grep every commit body for "sweep count" citation per Session 220 lock-in. Gate B verdicts in `audit/` DO cite counts; commit bodies may or may not. A standalone `tests/test_commit_body_cites_sweep_count.py` AST gate is a sane next-session task (closes the class) but not implemented today. This is the one "trust" gap in the audit — flagged honestly.

- **Manual mig 309 recovery was risky**: it worked but is a process artifact, not a verified pattern. Steve P2.

15-commit sessions don't come back P0-clean often. Today's came back P0-clean because (a) the session was disciplined — fork-based Gate A + Gate B for the load-bearing P1 work, smaller batches with their own audit digests for the auth/UX/calibration commits — and (b) the substrate engine immediately surfaced the one gap human review missed (the immutable-list drift). That's a healthy QC-engine signature, not a clean bill of health by trust. APPROVE-WITH-FIXES, with the one new P1 carried as `mig 310 + Gate A/B` for next session.

---

**Audit ran:** 2026-05-12 20:01 ET
**Sweep:** 241 passed / 0 failed
**Runtime sha:** ff7be044 (matches)
**Open substrate (sev2+):** 4 (1 new P1 from today, 3 pre-existing)
**P0s:** 0
**P1s:** 1 new (mig 310 immutable-list drift) + 2 carry-overs
**Verdict:** APPROVE-WITH-FIXES
