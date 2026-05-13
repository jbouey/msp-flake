# Class-B 7-lens Gate A — Canonical-source registry design

**Reviewer:** Fresh-context Gate A fork (Class-B 7-lens)
**Date:** 2026-05-13
**Design under review:** `audit/canonical-source-registry-design-2026-05-13.md`
**Counsel-rule binding:** Rule 1 (No non-canonical metric leaves the building); priority #4 in counsel's legal-exposure-closure order (after R6 BAA-gating, R8 subprocessor reclassification, R5 posture overlay).

---

## Per-lens verdicts

| Lens | Verdict | Notes |
|---|---|---|
| 1 — Engineering (Steve) | APPROVE-WITH-FIXES | Registry shape implementable; AST patterns need concrete spec; ratchet shape proven by `test_no_direct_site_id_update.py` precedent. |
| 2 — HIPAA auditor (OCR) | APPROVE-WITH-FIXES | Registry is necessary but not yet sufficient as Rule 1 evidence — needs runtime-drift detector + per-tenant correctness assertion to be auditor-grade. |
| 3 — Coach (no double-build) | APPROVE-WITH-FIXES | **Most consequential lens.** Two duplication risks: (a) `compliance_bundles` Ed25519 chain already provides per-metric attestation; (b) registry partially overlaps existing lockstep gates. See §"Coordination". |
| 4 — Attorney surrogate | APPROVE-WITH-FIXES | Closing Rule 1 is materially defensible for Article 3.2 attestation claim ONLY if the registry covers liveness ("appliance online") and includes a runtime drift assertion. Static AST gate alone = static promise, not attestation. |
| 5 — Product manager | APPROVE-WITH-FIXES | Drive-down is N sprints, not 1. `db_queries.py:502` is consumed by 1506+ and likely admin-only — the scope question dominates the cost. |
| 6 — Medical-technical | APPROVE-WITH-FIXES | Scope §1 misses: SLA-related uptime/availability claims, evidence-chain-count claims ("X bundles signed"), and any operator-deck/marketing copy that quotes a customer-specific number. |
| 7 — Legal-internal (Maya/Carol) | APPROVE-WITH-FIXES | No banned words in the design draft itself. Substrate-invariant runbook copy will need a Maya/Carol pass before mig lands. |

**Overall verdict:** **APPROVE-WITH-FIXES** (P0 findings must close before ratchet baseline is frozen + before substrate invariant `canonical_metric_drift` is added to mig).

---

## Counsel-rule binding (Rule 1 + multi-device-enterprise scale)

Rule 1 framing: *"No non-canonical metric leaves the building. Every exposed metric must have a declared canonical source."*

Three observations relevant to gate scope:

1. **Counsel-cited examples are heterogeneous.** `runbook_id` (Session 213/mig 284), L2 resolution recording (mig 300), `orders.status` (no canonical resolution rule today) are three different shapes of canonical-truth problem. The registry handles them as separate `metric_class` entries — correct shape, but `orders_status_completion` has `canonical_helper: TBD`. **A registry entry with `TBD` is itself a Rule 1 violation if it ships before the helper exists** — it documents a known broken truth path without remediation. P0.

2. **Multi-device-enterprise scaling factor is non-linear, not linear** (design says "N×M false claims"). At N tenants × M appliances, a non-canonical metric is N×M *plus* cross-tenant aggregation (partner-portfolio attestations sum across multiple orgs). A non-canonical metric in `partner_portfolio_attestation.py` is a single artifact asserting a non-canonical number across the entire BA's customer base — auditor exposure scales with portfolio size. The design's enumeration in §1 captures partner artifacts but §3's registry doesn't have a `partner_portfolio_score` metric_class. P0.

3. **Counsel-priority-order coordination.** Rule 6 (BAA-gating) lands first per counsel; the BAA-on-file canonical helper just shipped today (`baa_status.py:51`). The registry's `baa_on_file` entry must NOT lock in until the in-flight Task #52 `BAA_GATED_WORKFLOWS` constant lands — otherwise the registry pre-commits to a canonical shape that the Rule-6 work may overwrite. Sequencing P1.

---

## Lens 1-7 findings

### Lens 1 — Engineering (Steve)

- **Registry shape implementable.** `CANONICAL_METRICS` dict + `permitted_inline_paths` / `non_canonical_paths_to_migrate` / `operator_only_paths` is the same shape as proven precedents (`PRIVILEGED_ORDER_TYPES` 4-list lockstep, mig 257 site-rename allowlist). Engineering trusts this shape.
- **AST scan tractable but underspecified.** §5's `test_no_inline_score_computation_outside_canonical` says "AST scan for inline patterns (e.g. `passed / total * 100`) vs helper-call patterns." Concretely:
  - `passed / total * 100` is one of many inline shapes — `correct/(correct+failed)`, `compliant_count / check_count`, `score = ... if total else 100` (the 100.0% fallback antipattern) all need explicit AST patterns. The design needs an enumeration of inline shapes the gate detects. P0.
  - `helper-call patterns`: how does the gate distinguish a call to `compute_compliance_score()` from a call to `some_other_score(...)` that re-implements the same math? Recommendation: gate on import path + function name resolution, not just call shape.
- **Ratchet shape proven.** `test_no_direct_site_id_update.py` ratchets at `NOQA_BASELINE_MAX = 6` with per-line `# noqa: rename-site-gate — <reason>` markers + file-level exemption list. The proposed `# canonical-migration: <metric_class>` mirror is fine. **Gap:** the existing ratchet pattern requires a `<reason>` on every marker — the design draft omits this. Adding the `<reason>` makes the ratchet self-documenting. P1.
- **`non_canonical_paths_to_migrate` baseline staleness.** `db_queries.py:502` is a real callsite today; line numbers will drift as the file is edited. Recommendation: pattern-match by function name + AST node, not (file, line). The design's tuple shape `("db_queries.py", 502)` is brittle. P0.

### Lens 2 — HIPAA auditor surrogate (OCR)

- **OCR question:** *"Show me how you guarantee compliance scores in customer artifacts match canonical truth."*
- **Static gate answer alone is weak.** CI gate proves source-code shape at commit time. OCR will ask: "But what asserts that the deployed code, at runtime, on customer T's data, returned the canonical number?" The design's §6 step 5 (substrate invariant `canonical_metric_drift` periodically sampling endpoint responses vs canonical-helper recomputation) is the right runtime answer — **but it's listed as the last implementation step**, not the load-bearing one. Recommendation: substrate invariant ships BEFORE or ALONGSIDE the CI gate, not after. P0.
- **Per-tenant correctness assertion** (§4) needs concrete shape. The design says "verified by the existing RLS posture" — true at the SQL layer, but the customer-facing artifact path goes through endpoint handlers that may apply post-RLS aggregation. The substrate invariant must sample per-tenant, not in aggregate. P1.
- **Auditor-grade artifact.** Today's Ed25519-signed `compliance_bundles` chain is the strongest per-customer attestation we have. The registry should explicitly state: *the canonical helper's output for any signed bundle MUST equal the value derived from the bundle's `checks` array at the time of signing.* This is a chain-binding claim — the strongest possible. P1.

### Lens 3 — Coach (most consequential)

- **Coordination with §164.504(e) lockstep patterns.** Master-BAA Article 9 + privileged-chain ALLOWED_EVENTS + `BAA_GATED_WORKFLOWS` (in flight) are all 3-or-4-list lockstep mechanisms. `CANONICAL_METRICS` is a similar shape — single dict, single source of truth. **No duplication risk**, but Coach recommends:
  - **Unify the lockstep meta-pattern.** Either reuse `tests/test_privileged_order_four_list_lockstep.py` shape OR document that `canonical_metrics.py` is a peer-pattern in `feedback_directive_must_cite_producers_and_consumers.md`. The design should explicitly name its 4 lockstep peers (privileged-chain, ALLOWED_EVENTS, BAA-gated, mig 257 rename allowlist). P1.
- **Test-infrastructure shape.** Should `test_canonical_metrics_registry.py` extend an existing gate? Coach answer: **no — distinct concern**. Existing gates (`test_no_direct_site_id_update.py`, `test_no_anonymous_privileged_endpoints.py`, `test_email_opacity_harmonized.py`, `test_baa_subprocessors_lockstep.py`) each enforce a single class of contract. Adding canonical-metric enforcement to one of them blurs the single-responsibility shape. Standalone test file is correct.
- **Double-build risk: `compliance_bundles` Ed25519 chain.** Today every customer-facing compliance score IS already evidence-of-computation via the signed bundle. Counsel's Rule 1 framing focuses on *display-time canonical sourcing*, not *write-time chain attestation*. The registry's runtime drift assertion (substrate invariant) **must not** duplicate the existing chain-bundle attestation — it should ASSERT that the canonical helper's display-time output matches the bundle's write-time content. Recommendation: re-frame the substrate invariant as "display-time vs chain-time equality" rather than "endpoint output sampling." P0.
- **Multi-device-enterprise per-tenant correctness.** Coach's read: the registry as drafted assumes single-tenant correctness — `compute_compliance_score(site_ids=[...])` is per-tenant by site_ids, but the CI gate doesn't assert that callers pass tenant-scoped `site_ids`. A future caller that passes the wrong site_ids list would compute a canonically-shaped but tenant-wrong number. P0.

### Lens 4 — Attorney surrogate

- **Article 3.2 cryptographic-attestation-chain claim** asserts customer-facing metrics are derived from signed evidence. The registry materializes the *source-shape* of that claim, not the *runtime-result* of that claim. Static CI gate ≠ attestation. The substrate invariant (drift detector) is the load-bearing piece for Article 3.2 — promote it to P0 implementation order.
- **Auditor-defensible** only if the registry has zero `TBD` entries at ship time. `orders_status_completion: TBD` documents a known weakness without remediation — counsel-grade Rule 1 violation. Either resolve the canonical rule before ship OR exclude `orders_status_completion` from the registry and file it as a separate open work item. P0.

### Lens 5 — Product manager

- **Drive-down cost estimate.** 7 paths listed under `compliance_score.non_canonical_paths_to_migrate`. Per-path estimate:
  - `db_queries.py:502` — admin-CRUD; safe to migrate in 1 PR (low risk, callsite consumed by `get_compliance_scores_for_site` at line 606).
  - `db_queries.py:606 + 832` — admin-facing; medium risk, ~1 PR each.
  - `compliance_packet.py:437` — customer-facing PDF generator; high risk, needs Gate A+B (touches an artifact).
  - `metrics.py:191` — likely Prometheus-shaped; verify operator-only-path eligibility first.
  - `frameworks.py:216 + 425` — framework-scoped scoring; medium risk per PR.
  - Realistic: **3-5 sprints** to zero, not 1. Acceptable for Rule 1 closure if the substrate invariant ships first (so we have runtime detection during migration).
- **`# canonical-migration: <metric_class>` UX friction:** low. Mirror of `# noqa: rename-site-gate` and `# noqa: ...` patterns devs already accept. Recommendation: require `<reason>` after `<metric_class>` in line with the existing noqa convention. P1.

### Lens 6 — Medical-technical

- **Scope gaps** (additions to §1):
  - **Availability / uptime claims** ("X% uptime last 30 days") — clinic admins screenshot these for board meetings. P0 add to scope.
  - **Evidence-chain-count claims** ("Y signed bundles") — customer-facing in auditor-kit + reports. P0 add to scope.
  - **Liveness claims** ("appliance online") — answer to §7(d): YES, must be in scope. The 10-layer liveness defense + D1 backend-verification work is precisely the canonical-source problem for liveness. Counsel Rule 4 (orphan coverage) reinforces — an "online" claim that's wrong is an orphan-coverage violation AND a Rule 1 violation. P0 add `appliance_liveness` metric_class with canonical helper TBD (currently MV+heartbeat hybrid — needs decanonization).
  - **Incident counts / "open issues"** — customer-portal headline.
- **Customer-facing surface enumeration is incomplete.** Operator deck + marketing copy are listed but the design has no scan strategy for them (not source-grep-able). Recommendation: add a manual-review checkpoint to §5 for `pricing/` + `marketing-copy/` + customer-facing slides. P1.

### Lens 7 — Legal-internal (Maya + Carol)

- **Banned-word scan on design draft:** clean. No "ensures / prevents / guarantees / 100%" outside quotation of counsel's gold-authority text. Pass.
- **Substrate invariant runbook copy** (when `canonical_metric_drift` mig lands) needs Maya/Carol pass — recommend draft language for review:
  > *"Substrate detected divergence between a customer-facing metric value and its canonical-helper-recomputed value. Customer artifact integrity may be impacted; quarantine the affected metric and investigate before resuming display."*
  - Avoid "guarantees integrity"; "may be impacted" is the correct uncertainty framing.

---

## Specific cross-cutting verifications

### CI gate ratchet vs hard-blocker

**Verdict: ratchet is correct.** Hard-blocking ALL non-canonical paths from day-one would:
- Force a 3-5 sprint migration BEFORE Rule 1 has any teeth (no enforcement during migration)
- Block unrelated PRs that happen to touch a non-canonical file
- Violate the 2-producer + 1-consumer rule (no consumers exist until first migration lands)

Ratchet at today's count is the proven pattern (`NOQA_BASELINE_MAX = 6` precedent). **Additional requirement:** ratchet baseline MUST decrease only — never increase. Frozen-baseline + named TaskCreate followups per remaining path. Substrate invariant `canonical_metric_drift` runs IN PARALLEL providing runtime detection during the static migration drive-down.

### §7(d) liveness metrics

**Yes — include.** `appliance_liveness` as a registered metric_class. Counsel Rule 4 (orphan coverage sev1) makes the case stronger: a wrong-liveness claim IS an orphan-coverage violation. The canonical helper today is split across `appliance_status_rollup` MV + `phonehome.go` heartbeat signing + Layer 8 backend verification (Task #40 in flight). Register `appliance_liveness` with `canonical_helper: "TBD post-Task-#40"` and explicitly couple Task #50 progress to Task #40 closure.

### §7(e) per-computation audit log

**Verdict: NO — would duplicate `compliance_bundles` chain.** Every customer-facing compliance metric ALREADY has chain-of-custody via the signed bundle. Adding `metric_class + canonical_source_id` per-computation rows would:
- Multiply log volume by display-frequency (1000× per-customer per-day at scale)
- Duplicate the existing Ed25519 chain attestation
- Provide weaker evidence than the chain (audit log is mutable; bundles are signed)

**Recommended alternative:** the substrate invariant `canonical_metric_drift` (display-vs-chain equality check) is the right enforcement mechanism. Auditor evidence is: signed bundle (write-time) + drift assertion never tripping (display-time integrity).

### Compatibility with `score=null` sentinel for expired BAA

**Verdict: registry must explicitly handle sentinel returns.** `compute_compliance_score()` returns `{"score": null, "state": "baa_expired"}` per D1 Gate A v1 P0 #1. The registry's "customer-facing metric path uses canonical helper" check must NOT regress this — a downstream caller that converts `null` to `100.0` for display would be a Rule 1 + Rule 6 double-violation. Add to CI gate: every customer-facing endpoint that consumes `compute_compliance_score()` must pass-through `None` to the frontend (frontend renders "—" / "Awaiting BAA"). P0.

### Coordination with audit-log proposal §7(e)

Resolved by §7(e) verdict above: drop the audit-log emission; rely on `compliance_bundles` chain + display-time drift assertion. The two mechanisms compose:
- **Chain (write-time):** every metric value was derived from signed evidence.
- **Drift assertion (display-time):** every metric value currently displayed equals what the canonical helper recomputes from the chain.

---

## Recommended ratchet strategy + scope adjustments

### Ratchet strategy

1. **Phase 0 (this PR):** Register `canonical_metrics.py` with the 5 metric_classes currently in §3 (compliance_score, baa_on_file, runbook_id_canonical, l2_resolution_tier, orders_status_completion — but ONLY if orders helper is resolved; otherwise drop and file separately).
2. **Phase 1 (this PR):** Ship `test_canonical_metrics_registry.py` with frozen-baseline ratchet at today's count. Per-line `# canonical-migration: <metric_class> — <reason>` markers. Decrement-only.
3. **Phase 2 (next PR, BEFORE drive-down):** Ship substrate invariant `canonical_metric_drift` (sev2). Display-time vs chain-time equality, per-tenant, sampled at 1-per-tenant-per-hour cadence.
4. **Phase 3 (3-5 sprints):** Drive `non_canonical_paths_to_migrate` to zero, one PR per path, each with coach pass.
5. **Phase 4:** Add `appliance_liveness`, `evidence_chain_count`, `uptime_30d` as additional metric_classes once their canonical helpers exist.

### Scope adjustments

- **Add** `appliance_liveness` (couples to Task #40).
- **Add** `evidence_chain_count` and `availability_uptime`.
- **Add** `partner_portfolio_score` for partner-portfolio attestation.
- **Drop** `orders_status_completion` from this PR — file as separate task with `canonical_helper` design as gate.
- **Add** to §1 scope: operator deck / marketing copy with manual-review checkpoint.

---

## Implementation order (post-Gate-A approval)

1. Resolve P0 findings (this verdict).
2. Update design draft v2 with scope additions + ratchet pattern fixes.
3. Maya/Carol pass on substrate invariant runbook copy.
4. **Phase 0+1** PR: `canonical_metrics.py` + CI gate + frozen baseline.
5. **Phase 2** PR: substrate invariant `canonical_metric_drift` mig + assertion implementation.
6. **Phase 3** drive-down begins. Each migration is its own coach-pass-required PR.
7. **Phase 4** scope expansion once Tasks #40, #52 close.

---

## Open questions for user-gate

1. **Sequencing vs Rule 6 (BAA gating, Task #52).** Should `canonical_metrics.py` registry wait for `BAA_GATED_WORKFLOWS` to land first so the `baa_on_file` entry doesn't pre-commit? Engineering recommendation: yes, sequence Rule 6 → Rule 1.
2. **`orders_status_completion` design.** Counsel cited this as a broken truth path. Acceptable to defer to a separate task with its own design + Gate A? Or must it ship in scope of this PR?
3. **Substrate invariant cadence.** 1-per-tenant-per-hour sampling — too frequent / too sparse? At 50 tenants × 24 samples/day = 1,200 invariant runs/day, manageable. Alternative: only on chain-head update (event-driven).
4. **Manual-review checkpoint for marketing/deck copy.** Who owns the manual review — product, legal, or engineering? Coach recommends product + legal joint sign-off.

---

## Final recommendation

**APPROVE-WITH-FIXES**

### Top 5 P0 findings (must close before Phase 0+1 PR lands)

1. **Drop `orders_status_completion: TBD` from this PR.** A `TBD` registry entry is itself a Rule 1 violation (documents broken truth without remediation). File as separate task with its own Gate A. (Lenses 4, 1)
2. **Substrate invariant `canonical_metric_drift` must be Phase 2, not last.** Static AST gate alone is not Rule-1-sufficient. Runtime drift detection is the load-bearing Article 3.2 attestation. (Lenses 2, 4)
3. **Replace `(file, line)` tuple in `non_canonical_paths_to_migrate` with function-name + AST-node match.** Line numbers drift; baseline becomes stale within sprints. (Lens 1)
4. **Add scope: `appliance_liveness`, `partner_portfolio_score`, `evidence_chain_count`, `availability_uptime`.** Counsel Rule 4 (orphan coverage) intersects Rule 1 on liveness; partner-portfolio scales N×M×portfolio. (Lenses 3, 6)
5. **Per-tenant correctness must be CI-asserted, not just RLS-assumed.** Add gate: every customer-facing endpoint that calls `compute_compliance_score()` passes a tenant-scoped `site_ids` list derived from the authenticated principal — not from a request parameter. Add display-time `None`-passthrough requirement for `score=null` (BAA-expired sentinel). (Lens 3, cross-cutting verification)

### Top 3 P1 findings

- Concrete enumeration of inline AST shapes the gate detects (`passed/total*100`, `correct/(correct+failed)`, fallback-100 antipattern).
- `# canonical-migration: <metric_class> — <reason>` marker requires `<reason>` mirror of existing noqa convention.
- Document `canonical_metrics.py` as the 5th peer in the lockstep meta-pattern alongside privileged-chain / ALLOWED_EVENTS / BAA-gated / mig 257 rename allowlist.

— Class-B Gate A fork, 2026-05-13
