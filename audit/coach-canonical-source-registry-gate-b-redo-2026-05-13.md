# Task #50 canonical-source registry — Gate B re-fork (v3)

**Reviewer:** Fresh-context Gate B re-fork (4-lens; Engineering / HIPAA-auditor / Coach / Attorney)
**Date:** 2026-05-13
**Design under review:** `audit/canonical-source-registry-design-2026-05-13.md` (v3)
**Prior gate:** `audit/coach-canonical-source-registry-gate-b-2026-05-13.md` — APPROVE-WITH-FIXES, 2 P0s + 4 P1s
**Scope of this re-fork:** verify the 6 fixes the author applied in v3; surface any new regressions.

**Overall:** APPROVE

---

## 6-fix closure matrix

| # | Issue | v3 evidence | Status |
|---|---|---|---|
| **P0-A** | Phase 2 substrate invariant reframed from display-vs-helper-recompute to display-vs-chain-time using most recent signed `compliance_bundles` row | §6 Phase 2 body (lines 306) rewritten verbatim: *"This is NOT a display-vs-fresh-helper-recompute comparison (same helper, same data = same answer — proves nothing). Instead, it's a display-vs-chain-time comparison: when a customer-facing surface returns a metric, the invariant verifies it matches the value attested in the most recent Ed25519-signed evidence bundle. Cryptographic-attestation-chain claim materialized per master BAA Article 3.2 without double-building (the chain already exists; the invariant reads its head)."* Banned "proves nothing" framing is now in the doc itself. v2 contradictory "samples customer-facing endpoint responses and compares against canonical-helper-recomputed values" language is removed. §7(d) open question is now scoped to in-process-vs-HTTP (an orthogonal axis), not to the chain-vs-recompute axis Gate B P0-A targeted. | **CLOSED** |
| **P0-B** | 4 TBD entries (appliance_liveness, partner_portfolio_score, evidence_chain_count, availability_uptime) moved from `CANONICAL_METRICS` → `PLANNED_METRICS` | §3 dict now has two distinct constants: `CANONICAL_METRICS = {...}` (lines 148–196) contains only the 4 entries with real canonical helpers (compliance_score, baa_on_file, runbook_id_canonical, l2_resolution_tier). `PLANNED_METRICS = {...}` (lines 201–225) contains the 4 TBDs with `canonical_helper_pending` + `blocks_until` fields. Header comment (lines 144–146) explicitly says *"PLANNED_METRICS … Gate enforces only that no customer-facing surface exposes them until they migrate into CANONICAL_METRICS."* This is the "no surface may expose until helper lands" gate Gate B P0-B requested. The Rule-1-shape disqualification logic (Gate A P0 #1 on `orders_status_completion`) now applies symmetrically. | **CLOSED** |
| **P1-1** | §7 numbering bug — second §7 is now §8 | Line 314: `## §7 — Open questions for Class-B Gate B (post-v2)`. Line 321: `## §8 — REJECTED proposals (per Gate A)`. Numbering now monotonic. Anchor-link breakage closed. | **CLOSED** |
| **P1-2** | `evidence_test:` field added to `already_gated` entries | Line 184 (`runbook_id_canonical`): `"evidence_test": "assertions.py:2143 (substrate invariant runbook_id_drift)"`. Line 190 (`l2_resolution_tier`): `"evidence_test": "tests/test_l2_resolution_requires_decision_record.py"`. Both `already_gated: True` entries now cite the test that proves the gate is wired. Auditor-discoverability requirement satisfied. | **CLOSED** |
| **P1-3** | `non_canonical_function_signatures` + `operator_only_modules` collapsed into single `allowlist` with `classification:` field | Lines 157–165 (compliance_score) + lines 174–177 (baa_on_file): single `"allowlist": [...]` list with per-entry `{"signature": ..., "classification": "migrate" | "operator_only"}` shape. The `prometheus_metrics.*` glob is now `classification: "operator_only"`; the legacy paths are `classification: "migrate"`. Two-way classification ambiguity (a path being both legacy AND operator-only) is resolved by the per-entry tag rather than dual-list membership. Mirrors `test_no_direct_site_id_update.py` precedent. | **CLOSED** |
| **P1-4** | `canonical_metrics.py` documented as named lockstep-peer | Lines 228–235 add an explicit *"Gate B P1-4 — lockstep-peer documentation"* subsection enumerating the 7 peers: `fleet_cli.PRIVILEGED_ORDER_TYPES`, `privileged_access_attestation.ALLOWED_EVENTS`, mig `v_privileged_types`, `flywheel_state.EVENT_TYPES`, `BAA_GATED_WORKFLOWS` (Task #52), `BACKEND_THIRD_PARTY_INTEGRATIONS` (Task #55 §3), `CANONICAL_METRICS + PLANNED_METRICS`. Line 237 adds the load-bearing invariant: *"any change to the constants in this file requires lockstep with the CI gate + substrate invariant + customer-facing surface enumeration."* Carry-forward closed. | **CLOSED** |

**All 2 Gate B P0s + 4 Gate B P1s: CLOSED. No regressions.**

---

## Lens findings (new only — adversarial scan against v3 artifact-as-implemented)

### Lens 1 — Engineering

- v3 `PLANNED_METRICS` semantics are sound — separate dict, separate gate intent, explicit `blocks_until:` tracking. **No regressions.**
- One forward-looking nit: the §5 CI-gate skeleton references only `CANONICAL_METRICS` (line 263 import). The "no customer-facing surface exposes PLANNED metric" assertion needs its own test function (separate from `test_no_inline_score_computation_outside_canonical`). Acknowledged as Phase-1 implementation detail in §6; not a Gate B blocker. **Track as Phase-1 build item.**
- The `prometheus_metrics.*` wildcard inside `allowlist` (line 164) is acceptable because the classification is `operator_only` (substrate-internal, never customer-facing). Glob is bounded by the operator-only safety semantics. **OK.**

### Lens 2 — HIPAA auditor

- `evidence_test:` field gives the auditor a citable file per `already_gated` entry. Discoverability complete. **Pass.**
- P0-A reframe (display-vs-chain) is the load-bearing Article 3.2 attestation claim and is now correctly worded. Auditor will accept: the invariant reads the head of the Ed25519 + OTS-anchored chain that already exists; it does not double-build a parallel attestation. **Pass.**
- **No new auditor regressions.**

### Lens 3 — Coach (double-build / overengineering)

- P1-3 collapse is the correct shape — matches `test_no_direct_site_id_update.py` precedent. Single allowlist + classification tag = one source of truth. **No double-build.**
- P0-A reframe explicitly anchors the substrate invariant against the existing chain ("the chain already exists; the invariant reads its head"). This is the anti-double-build statement coach asked for verbatim. **Pass.**
- `PLANNED_METRICS` separation is the correct shape — not a parallel registry, just a forward-declaration constant with explicit blocked status. **No overengineering.**
- **No new coach regressions.**

### Lens 4 — Attorney

- P0-B closure: the registry now only asserts canonical truth for metric classes with a real helper. `PLANNED_METRICS` is forward-declaration + non-exposure gate, not an attestation claim. Article 3.2 surface tightened to exactly what we can defend.
- The line 7 v2-change preamble still says *"Expanded scope: added appliance_liveness, partner_portfolio_score, evidence_chain_count, availability_uptime metric_classes (NOTE: moved to `PLANNED_METRICS` in v3)"* — the parenthetical explicitly cross-references the v3 fix. Document history is auditable. **Pass.**
- **No new attorney regressions.**

---

## Forward-look (Phase 1 implementation items — non-blocking on this gate)

These are NOT Gate B P0/P1 findings — they are reasonable Phase 1 build items the CI-gate author should plan for:

1. `test_canonical_metrics_registry.py` needs a separate `test_no_customer_facing_surface_exposes_planned_metric()` assertion to enforce the PLANNED_METRICS non-exposure rule.
2. The `prometheus_metrics.*` glob entry needs a concrete AST-pattern definition for how the gate matches it (operator_only carve-out shape).
3. Per the two-gate lock-in rule, the implementation PR for Phase 0+1 will itself need a Gate A on the CI-gate's AST patterns + a Gate B on the as-implemented test before "shipped" can be claimed.

---

## Final recommendation

**APPROVE** (no further fixes required).

All 6 Gate-B-redo items (2 P0s + 4 P1s) are closed with verifiable evidence in v3. No new P0s or P1s surfaced from the adversarial re-read. The artifact is ready to move to Phase 0 implementation. Phase 0+1 PR itself will require its own Gate A + Gate B per the two-gate lock-in rule (Session 219 extension).

— Class-B Gate B re-fork, 2026-05-13
