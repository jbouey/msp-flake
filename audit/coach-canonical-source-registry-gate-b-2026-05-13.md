# Task #50 canonical-source registry — Gate B re-fork

**Reviewer:** Fresh-context Gate B fork (4-lens; Engineering / HIPAA-auditor / Coach / Attorney)
**Date:** 2026-05-13
**Design under review:** `audit/canonical-source-registry-design-2026-05-13.md` (v2)
**Prior gate:** `audit/coach-canonical-source-registry-gate-a-2026-05-13.md` — APPROVE-WITH-FIXES, 5 P0s + 3 P1s

**Overall:** APPROVE-WITH-FIXES

---

## Gate A P0 closure matrix

| # | P0 finding (Gate A) | v2 evidence | Status |
|---|---|---|---|
| 1 | Drop `orders_status_completion: TBD` (TBD entries are themselves Rule 1 violations) | §2.F explicitly defers to its own Class-B Gate A; §3 dict has inline comment `# orders_status_completion: DEFERRED to own Gate A per v2 §2.F. Not present in this registry — TBD entries are themselves Rule 1 violations.`; absent from `CANONICAL_METRICS`. | **CLOSED** |
| 2 | Promote substrate invariant `canonical_metric_drift` from Phase 5 → Phase 2 (parallel with CI gate, runtime detection during drive-down) | §6 Phase 2 header: *"(promoted from Phase 5 per Gate A P0 #2): ship substrate invariant `canonical_metric_drift` (sev2) … MUST land BEFORE drive-down begins so runtime detection is available DURING the migration window. Static AST gate alone is not Article 3.2 attestation-grade."* | **CLOSED** |
| 3 | Replace `(file, line)` tuples with function-name + AST-node match | §3 renames key to `non_canonical_function_signatures`; entries are dotted function paths (`metrics.calculate_compliance_score`, `db_queries.get_compliance_scores_for_site`, etc.). Inline comment cites Gate A P0 #3. | **CLOSED** |
| 4 | Expand scope: add `appliance_liveness`, `partner_portfolio_score`, `evidence_chain_count`, `availability_uptime` | §2.G–J + §3 dict entries for all four (each with `canonical_helper: TBD` + `already_gated: False` + cross-link to Task #40 for liveness). | **CLOSED** |
| 5 | CI-assert per-tenant correctness from authenticated principal + display-time None-passthrough for BAA-expired sentinel | §3 dict adds `display_null_passthrough_required: True` on `compliance_score`. New §3 subsection "Per-tenant correctness assertion" specifies `test_canonical_metric_endpoints_tenant_scoped.py` AST gate verifying `site_ids` derives from auth principal (e.g. `Depends(require_client_user) → current_user.client_org_id → site_ids_for_org(...)`) not request body. | **CLOSED** |
| Extra | REJECTED v1 §7(e) per-computation audit-log emission removed (double-build vs `compliance_bundles` Ed25519 chain) | §7 "REJECTED proposals" section documents the rejection + cites Gate A reasoning. Not added to v2 anywhere. | **CLOSED** |

**All 5 Gate A P0s + the rejected §7(e) verification: CLOSED.**

---

## Lens findings (new regressions only)

### Lens 1 — Engineering

- **Section-numbering bug (cosmetic P1):** v2 has TWO `## §7` headers — one for "Open questions for Class-B Gate B (post-v2)" and one for "REJECTED proposals (per Gate A)". The second should be §8. Renders fine in most markdown engines but breaks anchor links. P1.
- **P0 #3 fix is sound but the `partner_portfolio_attestation.*` glob pattern in §3 is a wildcard — AST gates can match wildcards but the spec needs to say "every function defined in module X" vs "every callsite calling module X". Recommend tightening to a concrete enumeration once source-grep runs (acknowledged in §7(a) open question). P1.
- **Gate A P1 carry-forward — `<reason>` requirement on marker.** Gate A P1 said `# canonical-migration: <metric_class> — <reason>` must require `<reason>`. v2 §6 Phase 3 says `# canonical-migration: <metric_class> — <reason>` inline marker — good. Engineering accepts.
- **No new engineering regressions.**

### Lens 2 — HIPAA auditor

- **Phase 2 promotion is auditor-grade.** v2 §6 makes the substrate invariant a hard prerequisite to Phase 3 drive-down — exactly the runtime detection OCR will ask about. Pass.
- **Per-tenant correctness CI-asserted via auth principal extraction** is materially stronger than "RLS will catch it" — it prevents the failure mode where a developer threads an attacker-supplied `site_ids` list through `compute_compliance_score()`. Pass.
- **One new auditor-grade concern (P1):** §3 `already_gated: True` for `runbook_id_canonical` + `l2_resolution_tier` is correct, but the registry doesn't cite the test file that proves they're gated. Auditor will ask "where's the test that says `runbook_id_drift` substrate invariant is wired up?" Recommend adding `evidence_test: "assertions.py:2143"` / `evidence_test: "tests/test_l2_resolution_requires_decision_record.py"` field. P1.
- **No new auditor regressions.**

### Lens 3 — Coach (double-build / overengineering)

- **§7(e) rejection holds.** Per-computation audit-log emission is OUT. Substrate invariant `canonical_metric_drift` is the single display-time enforcement layer. No double-build.
- **Per-tenant CI gate vs RLS:** The new `test_canonical_metric_endpoints_tenant_scoped.py` gate verifies the *source-shape* of how `site_ids` is derived — it does NOT re-implement RLS, it asserts the call-site shape that feeds RLS. This is the correct narrow scope. RLS still does the row-level enforcement at SQL time. Coach: no double-build.
- **Phase-2 substrate invariant vs `compliance_bundles` chain:** v2 §6 frames Phase 2 as "drift detector" — the Gate A coach guidance was to re-frame as "display-time vs chain-time equality" rather than "endpoint output sampling" to avoid double-attesting against the Ed25519 chain. v2 doesn't fully internalize this — §6 Phase 2 still says "periodically samples customer-facing endpoint responses and compares against canonical-helper-recomputed values" (not chain-time recomputation). §7(d) open question acknowledges the in-process vs HTTP question but doesn't pin the chain-time framing. **NEW P0**: v2 must explicitly state the invariant compares display-time output against the value derivable from the most recent signed `compliance_bundles` row for that tenant — NOT against a fresh re-query (fresh re-query is just two calls to the same helper, which proves nothing). P0.
- **Lockstep meta-pattern documentation (Gate A P1):** v2 does NOT add `canonical_metrics.py` to the named lockstep-peer list (`feedback_directive_must_cite_producers_and_consumers.md` or equivalent). Carried forward as P1. Non-blocking but ratchets cohesion.
- **One overengineering concern surfaced (P1):** §3 dict has both `non_canonical_function_signatures` AND `operator_only_modules`. The Gate A precedent (`test_no_direct_site_id_update.py`) uses a single allowlist. Splitting into two lists creates two-way classification ambiguity for paths that are *both* legacy AND operator-only. Recommend collapsing to one allowlist with `classification: "operator_only" | "migrate"` per entry. P1.

### Lens 4 — Attorney

- **TBD entries fully removed from registry data.** Article 3.2 attestation defense holds — the registry asserts only what has a canonical helper today.
- **Phase 2 substrate invariant is the load-bearing Article 3.2 piece** and is now correctly sequenced (P0 #2 closed).
- **One new attorney concern (P1):** v2 §3 entries with `canonical_helper: "(TBD — ...)"` (appliance_liveness, partner_portfolio_score, evidence_chain_count, availability_uptime) are listed in `CANONICAL_METRICS` but have no canonical helper. Per Gate A P0 #1's own logic — *"a registry entry with `TBD` is itself a Rule 1 violation"* — this is technically the same shape that disqualified `orders_status_completion`. v2 §6 Phase 4 says these land "once Tasks #40 (D1 backend-verify) and #52 (BAA-gated workflows) close" — but they're already IN the dict today. Recommend: either (a) move the 4 TBD entries to a `PLANNED_METRICS = {}` constant (out of the gate's enforcement scope until helper lands) OR (b) gate them behind `already_gated: False` + a CI assertion that no customer-facing surface currently exposes them. v2 has `already_gated: False` but no gate that says "until helper exists, no surface may expose this." P0.
- **Rule 1 enforcement is auditor-defensible** for the metrics that have canonical helpers (compliance_score, baa_on_file, runbook_id, l2_resolution_tier). The 4 PLANNED entries are the only soft spot.

---

## New P0 findings (Gate B, not from Gate A)

1. **Phase 2 substrate invariant must compare display-time vs chain-time, not display-time vs fresh-helper-recompute.** v2 §6 Phase 2 + §7(d) frame the invariant as sampling endpoint responses vs canonical-helper-recomputed values — two calls to the same helper proves nothing. The invariant must read the most recent signed `compliance_bundles` row per tenant and verify display-time output matches the bundle's signed content. This is the actual Article 3.2 attestation claim. (Lens 3)
2. **4 TBD-helper entries (appliance_liveness, partner_portfolio_score, evidence_chain_count, availability_uptime) are Rule-1-violating registry entries by Gate A's own logic.** Either move to a separate `PLANNED_METRICS = {}` constant out of CI-gate enforcement scope, OR add a CI gate "until helper exists, no customer-facing surface may expose this metric." Today they sit in `CANONICAL_METRICS` with `canonical_helper: TBD` — the same shape that disqualified `orders_status_completion` in P0 #1. (Lens 4)

## New P1 findings (Gate B)

- v2 has two `## §7` headers; second should be `## §8`. (Lens 1)
- Add `evidence_test: <file>` field to `already_gated: True` entries so auditors can cite the test. (Lens 2)
- `non_canonical_function_signatures` vs `operator_only_modules` two-list split creates classification ambiguity; collapse to one allowlist with `classification:` per entry. (Lens 3)
- Document `canonical_metrics.py` as the 5th lockstep meta-pattern peer (Gate A P1 carry-forward; not addressed in v2). (Lens 3)

---

## Final recommendation

**APPROVE-WITH-FIXES**

The author closed all 5 Gate A P0s + dropped the rejected §7(e). v2 is a materially stronger artifact. Two new P0s surfaced from the deeper Gate B read:

- **P0-A:** Phase 2 substrate invariant must be display-vs-chain, not display-vs-fresh-recompute (otherwise we double-build vs `compliance_bundles` rather than leveraging it).
- **P0-B:** The 4 TBD-helper entries (appliance_liveness, partner_portfolio_score, evidence_chain_count, availability_uptime) sit in `CANONICAL_METRICS` with `canonical_helper: TBD` — exactly the shape Gate A P0 #1 disqualified for `orders_status_completion`. Move to a `PLANNED_METRICS` constant OR add an explicit "no surface may expose until helper lands" gate.

Both P0s must close before Phase 0+1 PR lands. P1s should land as named TaskCreate followups per the two-gate lock-in rule.

— Class-B Gate B fork, 2026-05-13
