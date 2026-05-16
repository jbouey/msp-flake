# Gate A — multi-device P1 batch (#117 + #118 + #119)
Date: 2026-05-16
Reviewer: fork-based 7-lens (general-purpose subagent, fresh context, opus-4.7[1m])
Source coach: `audit/coach-enterprise-multi-device-feasibility-2026-05-16.md`
Verdict: per-task — see below. Overall batch APPROVE-WITH-FIXES.

---

## Per-task verdict + binding requirements

### #117 — chain-contention load (1-site × 20-appliance)

- **Verdict:** APPROVE-WITH-FIXES
- **P0 binding requirements (must be in implementation):**
  1. **Existing `chain_orphan` invariant audit BEFORE the run.** Grep shows `cross_org_relocate_chain_orphan` exists but no generic `chain_orphan` per-site. Inventory what the assertion suite ACTUALLY enforces against `compliance_bundles.chain_position`/`prev_hash` before running 20-way contention — if there is NO existing per-site chain-integrity invariant, ADD one (`bundle_chain_position_gap` sev1) FIRST, baseline 0, then run the load. Otherwise the load proves "no false positives" against a non-existent gate.
  2. **Synthetic marker on every load-emitted bundle.** Per the §"synthetic_traffic_marker_orphan" invariant (assertions.py:2848) ALL load-test rows in customer-facing aggregation tables MUST carry `details->>'synthetic_marker' IN ('load_test','mttr_soak')`. Verify `compliance_bundles` is in the marker-orphan scan scope; if not, add it. Otherwise the load contaminates evidence customer-side.
  3. **20 bearer provisioning — pre-test fixture, not per-run mint.** Issuing 20 new bearers per run leaks `site_appliances.bearer_revoked` rows (mig 324) and burns RLS-policy cache. Pre-provision 20 long-lived synthetic bearers via mig (or test fixture file), tagged `synthetic_marker='load_test'`. Bound to ONE synthetic site_id; revocation on test-suite teardown only.
  4. **`chain_lock_wait_seconds_histogram` registration must use the existing `prometheus_metrics.py` pattern** (gauge cache singleton, label set frozen at import). Sample code MUST be in the design doc before code lands — Gate B will diff against it.
- **P1 considerations:**
  - Lock-wait p99 budget needs a stated target (suggest p99 < 500ms at 20-bearer cadence; if higher, the chain becomes the bottleneck before any 50-clinic shape).
  - Verify `pg_advisory_xact_lock(hashtext($1))` site_id hashing — `hashtext` is 32-bit, collisions possible across 250 sites but acceptable; document explicitly.
- **Counsel's 7 Rules check:**
  - Rule 1 (canonical metric): chain_lock_wait_seconds is a NEW metric — must declare canonical source in same commit.
  - Rule 4 (orphan coverage): see P0-1 above — orphan invariant must EXIST before contention proves it holds.
  - Others N/A.
- **Mig needed?** Probably yes for the 20 pre-provisioned bearer rows (or a fixture/seed script if no schema change). No new column needed if leveraging `bearer_revoked` semantics. **No pre-claim required if fixture-only.**
- **Sibling-parity?** N/A — internal load tool, no customer-facing surface.
- **Pre-push sweep impact:** the new invariant must have `substrate_runbooks/bundle_chain_position_gap.md` — pinned by `test_substrate_docs_present` (Session 220 outage class). Gate B MUST run full sweep.

---

### #118 — fleet_cli `--target-appliance-id` / `--all-at-site` / `--all-at-partner`

- **Verdict:** APPROVE-WITH-FIXES (with `--all-at-partner` REMOVED from scope)
- **P0 binding requirements:**
  1. **REMOVE `--all-at-partner` from this scope.** 1 command → 250 fleet orders blast radius is its own design + Gate A + Counsel Rule 3 review. Maya's concern is correct: confirmation friction is not a substitute for a separate privileged-event class. Defer to a follow-up task (suggest #120) with its own bundle:fan-out model.
  2. **Bundle:order cardinality verified.** Migration 175 trigger `enforce_privileged_order_attestation` checks `parameters->>'attestation_bundle_id'` per-order against ONE bundle row. The 1:N model (one bundle, N fleet_orders citing the same bundle_id) is ALREADY supported — no schema change needed; tests must explicitly cover the N-orders-one-bundle path (currently 1:1 in callsites). Add `tests/test_privileged_bundle_fanout.py` in the same commit.
  3. **`--all-at-site` fan-out is a NEW privileged event class** when applied to privileged order types — must be registered in all 3 lockstep lists if the FAN-OUT itself is treated as one decision (suggest event name `bulk_fanout_at_site`). If fan-out is purely a client-side convenience (CLI expands to N normal orders each with their own per-appliance attestation bundle), NO lockstep change needed BUT the CLI MUST issue N distinct bundles. Pick one model in the design doc; do not ship both.
  4. **Confirmation UX:** for `--all-at-site` on privileged types, require operator to type back the COUNT of appliances ("`Type the number of affected appliances to confirm: _`"). Hard-coded yes/no insufficient at 20+ appliance scale (Carol/Maya concur per coach lens).
- **P1 considerations:**
  - `--dry-run` output: emit BOTH human-readable (default) and `--dry-run --json` for piping. UX bifurcation costs ~10 LOC.
  - `--target-appliance-id` validation MUST check `site_appliances.deleted_at IS NULL` (Session 218 soft-delete invariant) — issuing fleet orders to soft-deleted appliances is a class-bug waiting to happen.
- **Counsel's 7 Rules check:**
  - Rule 3 (privileged-chain attribution): the entire raison d'être of #118. P0-3 above resolves.
  - Rule 4: ensure `--all-at-site` does NOT silently skip non-checked-in appliances without explicit log — orphan-coverage class.
  - Rule 7: `--dry-run` output is operator-facing (authenticated CLI) — full context OK.
- **Mig needed?** Only if P0-3 picks the "fan-out is its own event" model — then mig for `v_privileged_types` entry + pre-claim. If client-side expansion only, NO mig.
- **Sibling-parity?** `test_privileged_order_four_list_lockstep.py` MUST pass; if new event added, all 4 lists update in same commit (Python `PYTHON_ONLY` allowlist applies since fan-out is backend/CLI-only, daemon never receives `bulk_fanout_at_site`).
- **Pre-push sweep impact:** new bundle:N-orders test path + lockstep update if applicable. Sweep mandatory in Gate B.

---

### #119 — Bulk-onboarding primitive

- **Verdict:** APPROVE-WITH-FIXES
- **P0 binding requirements:**
  1. **QR-code provisioning callback MUST require out-of-band token, not MAC-fingerprint alone.** Maya P0: a leaked install card (printed, photographed, emailed in transit) lets an attacker spoof checkin BEFORE the legit appliance arrives. The reserved-MAC binding alone is insufficient (MAC spoofable on bare-metal NIC config). Embed a 32-byte URL-safe per-row provisioning_token in the QR; checkin matches `(mac_prefix, provisioning_token)` to consume the reserved row. Token single-use, expires 60 days.
  2. **BAA on file gate at CSV ingest time, not at first-checkin.** Per spec ("BAA on file BEFORE bulk-onboarding") — call `require_active_baa("bulk_onboarding")` at CSV submit; REGISTER `bulk_onboarding` in `BAA_GATED_WORKFLOWS` (List 1) + add enforcement callsite (List 2) + ensure substrate invariant `sensitive_workflow_advanced_without_baa` catches it (List 3). Lockstep test must pass.
  3. **CSV opaque-mode handling in audit logs.** clinic_name + ship_to_address are PHI-shaped (clinic identity = patient population). Audit row for `bulk_onboarding_submitted` MUST emit `clinic_name_hash` + `row_count` only; raw values stay in the reserved-row table (RLS-scoped). Pinned by `tests/test_email_opacity_harmonized.py` pattern — extend or add `test_bulk_onboarding_audit_opaque.py`.
  4. **`bulk_onboarding_stalled` sev3 → 7 days, NOT 30.** Coach Maya is right: 30d is "we already lost the shipment". 7d catches shipping delay, lets ops chase. Sev3 stays correct (operator-actionable, not security-incident).
- **P1 considerations:**
  - Install-card PDF determinism: NOT required (not an audit artifact; verify by NOT extending auditor-kit determinism contract — but the `_kit_zwrite` discipline should NOT bleed into install_card generator either way; explicitly document non-deterministic OK in design).
  - Reserved-row schema: needs new table `bulk_onboarding_reservations` (clinic_name_enc, mac_prefix, expected_count, provisioning_token_hash, expires_at, consumed_at, baa_verified_at). Mig required — pre-claim.
  - Per-clinic shipping manifest may carry physical-shipping address → ensure access RLS-scoped to issuing partner_org_id only (Rule 7).
- **Counsel's 7 Rules check:**
  - Rule 2 (no raw PHI crosses boundary): clinic_name + ship_to are CE-identifying. CSV upload → Central Command is the boundary; CSV may be stored in opaque-mode column but NEVER logged/emailed in cleartext.
  - Rule 3: bulk-onboarding is a partner-issued action affecting future customer state; should `bulk_onboarding_submitted` itself be in `ALLOWED_EVENTS`? Recommend YES — bundle anchors the CSV hash + partner_user_id; gives auditor the "who provisioned this fleet" answer.
  - Rule 4: reserved-but-never-claimed = orphan class; P0-4 fix.
  - Rule 6: P0-2 fix.
- **Mig needed?** YES — (a) `bulk_onboarding_reservations` table; (b) `BAA_GATED_WORKFLOWS` enforcement requires no mig (Python frozenset) BUT the substrate invariant SQL may need a state-machine table to scan. **Pre-claim 2 numbers (325, 326)** via RESERVED_MIGRATIONS.md in the design-doc commit.
- **Sibling-parity?** `test_baa_gated_workflows_lockstep.py` will fail without P0-2; `test_substrate_docs_present` requires `bulk_onboarding_stalled.md` runbook.
- **Pre-push sweep impact:** lockstep + opacity + substrate-docs gates all in play. Gate B sweep mandatory.

---

## Sequencing recommendation

**Order: #118 → #117 → #119.**

- **#118 first (lightest, ~2-3 days):** scope after `--all-at-partner` removal is contained (3 flags + validation + 1 lockstep test). No new substrate invariants. Unblocks operator UX immediately. Fastest-win lane.
- **#117 second (medium, ~4-5 days):** depends on a new orphan invariant being SHIPPED FIRST (P0-1). Cannot run load against a chain whose integrity gate doesn't exist. The pre-work (invariant + runbook + baseline) is itself ~2 days; the load extension ~2 days.
- **#119 last (heaviest, ~7-10 days):** new table, new BAA gate registration, new substrate invariant, new PDF/QR generator, new provisioning callback endpoint, opacity gate, AND benefits from #118's `--target-appliance-id` being live (bulk-onboarded appliances are first-class fleet_cli targets immediately).

**Dependencies:**
- #119's first-checkin-after-bulk MAY want to issue an initial fleet order (e.g., `update_daemon` to bring brand-new appliances to current version). That uses #118's plumbing — soft dependency, not blocking, but cleaner if #118 ships first.
- #117 and #119 independent; can parallelize after #118 if two workstreams available. Reviewer recommends serial single-workstream — context-switch cost > calendar savings at this complexity.

---

## Top P0 across the batch

1. **#118 P0-1 (REMOVE `--all-at-partner` from scope)** — blast-radius blocker; without this, #118 itself becomes a BLOCK pending separate fan-out design.
2. **#119 P0-1 (QR provisioning_token)** — security boundary; without this, bulk-onboarding ships an attacker-friendly provisioning vector.
3. **#117 P0-1 (orphan invariant before load)** — empirical-validity boundary; without this, the load proves nothing.

None of these block STARTING the batch; all 3 MUST land in their respective task's first commit, verified at Gate B.

---

## Final

- **#117:** APPROVE-WITH-FIXES (4 P0)
- **#118:** APPROVE-WITH-FIXES (4 P0, scope-trimmed)
- **#119:** APPROVE-WITH-FIXES (4 P0)

Batch overall: **APPROVE-WITH-FIXES.** Proceed in sequence #118 → #117 → #119. Each task requires its own Gate A (design-level) once a concrete spec exists AND its own Gate B (pre-completion sweep) per the TWO-GATE lock-in. This Gate A is feasibility-level, not design-level — task-level Gate A still required before implementation begins.
