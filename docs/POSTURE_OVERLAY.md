# POSTURE_OVERLAY.md (v2.1, effective 2026-05-13)

> **Status:** PUBLISHED. Class-B Gate A APPROVE-WITH-FIXES (5 P0s applied in v2) → Class-B Gate B APPROVE-WITH-FIXES (5 P1s + 1 P2 frontmatter parity applied in v2.1). Counsel Priority #3 (Rule 5: no stale document may outrank the current posture overlay). Task #51.
> **Context:** OsirisCare is in **multi-device scaled enterprise hardening** posture. The overlay reflects platform commitments at multi-tenant, multi-appliance, multi-org scale.
> **Authority of record:** Cite this overlay first for any topic-area authority question. Each owning doc carries its own state via frontmatter (§8); this overlay is the pointer-index.

---

## §1 — What this overlay is

This is the **canonical current-truth pointer-index** for the OsirisCare platform's operational, legal, security, and architectural posture. It does NOT enumerate state — it points at the owning document for each topic area. Each owning document carries its own state in its frontmatter, validated by the same `context-manager.py validate` infrastructure that enforces memory hygiene.

**Counsel Rule 5 (2026-05-13 gold authority):** *"No stale document may outrank the current posture overlay. Any operational or legal workflow must cite either the current posture overlay OR a refreshed doc that supersedes the old one."*

This overlay is the FIRST document any contributor or reviewer reads when they need to know "what does the platform commit to today, and where do I find the current authority?"

---

## §2 — Governing rules (gold-grade authority)

The platform operates under three load-bearing rule-sets, in priority order:

1. **Counsel's 7 Hard Rules** (laid down 2026-05-13; full enumeration at `~/.claude/projects/-Users-dad-Documents-Msp-Flakes/memory/feedback_enterprise_counsel_seven_rules.md` and pinned in `CLAUDE.md` §"Counsel's 7 Hard Rules"):
   - R1: No non-canonical metric leaves the building.
   - R2: No raw PHI crosses the appliance boundary.
   - R3: No privileged action without attested chain of custody.
   - R4: No segmentation design that creates silent orphan coverage. Orphan detection is sev1, not a tolerable warning. *(Engineering interpretation: at multi-device-enterprise fleet scale, the sev1 threshold applies across the fleet aggregate, not just per-tenant. This gloss is engineering interpretation, not counsel-verbatim.)*
   - R5: No stale document outranks the current posture overlay (this document is the overlay).
   - R6: No legal/BAA state lives only in human memory.
   - R7: No unauthenticated channel gets meaningful context by default.
   - Plus expanded rules R8 (subprocessors by actual data flow), R9 (determinism + provenance), R10 (no clinical authority drift).

2. **Privileged-Access Chain of Custody** (Session 205, INVIOLABLE): *client identity → policy approval → execution → attestation*. Enforced at CLI + API + DB layers. Three lists in lockstep.

3. **Class A vs Class B routing for legal questions** (2026-05-13 lock-in): pure-legal → outside counsel direct; combined auditor/legal-technical/product → Class-B 7-lens internal round-table first.

When any prior document conflicts with the above, the rule-sets win.

---

## §3 — Topic-area pointer index

**This index does NOT carry topic state.** Each owning doc carries its own `last_verified` + `decay_after_days` in YAML frontmatter (per §8). The reader resolves currency by reading the OWNING doc's frontmatter, not this overlay.

### Foundational / governance

| Topic area | Owning document | Authority class |
|---|---|---|
| Posture overlay (this index) | `docs/POSTURE_OVERLAY.md` | Gold-authority |
| Counsel's 7 hard rules | `CLAUDE.md` §"Counsel's 7 Hard Rules" + memory `feedback_enterprise_counsel_seven_rules.md` | Gold-authority |
| Privileged-access chain of custody (3-list lockstep) | `CLAUDE.md` §"Privileged-Access Chain of Custody" | INVIOLABLE |
| Round-table protocol (Class A / Class B routing) | memory `feedback_round_table_at_gates_enterprise.md` | Gold-authority |
| Master BAA contract | `docs/legal/MASTER_BAA_v1.0_INTERIM.md` | Customer-binding |
| Subprocessor Registry (Exhibit A of master BAA) | `docs/SUBPROCESSORS.md` | Customer-binding |
| PHI Data Flow Disclosure (Exhibit B of master BAA) | `docs/legal/MASTER_BAA_v1.0_INTERIM.md` Exhibit B | Customer-binding |

### Legal / compliance

| Topic area | Owning document | Status note |
|---|---|---|
| HIPAA framework + scope | **PENDING REFRESH** — existing `docs/HIPAA_FRAMEWORK.md` self-declares stale (counsel's §164.504(e) framing has moved); refresh task TBD post master-BAA-v2.0 | Stale |
| Cross-org site relocate (RT21) — v2.3 counsel approved + flag-disabled | `.agent/plans/21-counsel-briefing-packet-v2.4-2026-05-09.md` + code at `cross_org_site_relocate.py` | Flag-disabled |
| F6 federation phase 2 (Tier 2 platform-aggregated learning) | `.agent/plans/f6-phase-2-enforcement-deferred.md` (foundation slice shipped; WRITE-path blocked on counsel) | Deferred — counsel-bundle |
| BAA-gated workflows enforcement | TBD — Task #52 in flight; will land at `BAA_GATED_WORKFLOWS` constant + lockstep | In flight |
| PHI scrubbing implementation | `appliance/internal/phiscrub/scrubber.go` (14 patterns) + master BAA Exhibit B catalogue | Stable |

### Architecture / engineering

| Topic area | Owning document | Status note |
|---|---|---|
| Platform architecture overview | `docs/ARCHITECTURE.md` (last updated 2026-03-22; overdue refresh — flag for next sweep) | OVERDUE refresh |
| Multi-appliance architecture (Layers 1-4) | memory `project_multi_appliance_architecture.md` | Active; D1 (Layer 8 of 10-layer liveness defense) pending |
| Mesh + mDNS cross-subnet topology | memory `project_mesh_mdns_architecture.md` | Stable |
| Liveness defense layers (10-layer) | memory `project_liveness_defense_layers.md` | Layer 8 inert pending Task #40 |
| Substrate Integrity Engine (61+ invariants) | memory `project_substrate_integrity_engine.md` + `assertions.py` | Active; growing |
| Auditor-kit determinism contract | `CLAUDE.md` §"Auditor-kit determinism contract" + `auditor_kit_zip_primitives.py` | INVIOLABLE |
| Data model | `docs/DATA_MODEL.md` | Recent |
| Provenance + chain-of-custody mechanics | `docs/PROVENANCE.md` | Recent |
| Vault Transit (shadow → cutover) | memory `project_vault_transit_rollout.md` + `signing_method` column live | Shadow (cutover deferred) |

### Operational

| Topic area | Owning document | Status note |
|---|---|---|
| Network hosts + IPs | memory `reference_network.md` | Reference (365d decay) |
| Runbooks (per-incident YAML) | `docs/runbooks/` + `docs/RUNBOOKS.md` | Per-runbook frontmatter |
| Onboarding SOP | `docs/CLIENT_ONBOARDING_SOP.md` (overdue refresh) | OVERDUE refresh |
| SLO / SLA | `docs/SLO.md` | Active |
| WireGuard compliance | `docs/WIREGUARD_COMPLIANCE.md` | Active |
| Stripe billing | memory `project_stripe_billing.md` + `client_signup.py` | Active |

### Multi-device-enterprise scale

| Topic area | Owning document | Status note |
|---|---|---|
| Multi-tenant load harness (capacity numbers) | TBD — Task #38 redesign in flight (v1 Gate A BLOCKED) | BLOCKED |
| Substrate-MTTR 24h soak | TBD — Task #98 / plan-24 redesign in flight (v1 Gate A BLOCKED) | BLOCKED |
| Disaster recovery drill (§164.308(a)(7)) | TBD — Task #39 no design today | NOT INDEXED |
| Cross-org RLS policies (org_connection) | `CLAUDE.md` §"org_connection RLS coverage" + `test_org_scoped_rls_policies.py` | Active |
| Per-appliance signing keys (Session 196) | memory `feedback_critical_architectural_principles.md` §8 | Stable |
| Daemon-side heartbeat signing (Layer 8) | Implemented in `phonehome.go:827` + `daemon.go:867`; backend-verification + substrate-invariants in flight (Task #40 reframed) | Daemon ✓ / Backend in flight |
| Cross-org governance pattern (dual-admin approval, two-actor state machines — RT21 cross-org-relocate, owner-transfer mig 273, partner-admin-transfer mig 274) | `CLAUDE.md` §"Owner-transfer state machines (Session 216)" + RT21 plans | Stable (cross-org-relocate flag-disabled awaiting counsel) |
| Multi-tenant PgBouncer pooling (`admin_transaction` + `tenant_connection` invariant) | `CLAUDE.md` §"admin_transaction() for multi-statement admin paths" + `tenant_middleware.py` | Load-bearing |
| Multi-org evidence chain anchoring (`client_org:<id>` / `partner_org:<id>` synthetic anchors) | `CLAUDE.md` §"Anchor-namespace convention for cryptographic chains (Session 216)" | Stable |
| Fleet-wide enforcement at multi-tenant scale | TBD — once `BAA_GATED_WORKFLOWS` + `subprocessor_dataflow_drift` + canonical-source registry land, this row gets a doc | NOT INDEXED |

---

## §4 — Supersession registry (corrected per Gate A v1 P0 #5)

| Superseded doc | Superseded by | Reason | Date supersession EFFECTIVE |
|---|---|---|---|
| `docs/BAA_SUBPROCESSORS.md` (Effective Date 2026-03-11) | `docs/SUBPROCESSORS.md` v2 | Counsel Rule 8 re-audit; 4 missing subprocessors added; reframed as Exhibit A to master BAA | **2026-05-13** (effective date per `docs/SUBPROCESSORS.md` self-publication; this overlay records the fact + Gate A + Gate B forks cross-consistency-validated it on 2026-05-13. SUBPROCESSORS.md is the authority of record; the overlay's row here is a pointer + validation record, not the supersession-trigger.) |
| Click-through acknowledgment v1.0-2026-04-15 (SignupBaa.tsx ACKNOWLEDGMENT_TEXT) | `docs/legal/MASTER_BAA_v1.0_INTERIM.md` | Counsel directed v1.0-INTERIM master BAA derived from HHS sample | **Pending v2.0 outside-counsel hardening + customer re-sign** |
| `docs/HIPAA_FRAMEWORK.md` (last verified 2026-01-14) | **PENDING REFRESH** — task scheduled for post-master-BAA-v2.0 | Counsel adversarial review changed §164.504(e) framing 2026-05-06 (v2.3 RT21 packet) | Pending |

**Overlay-self-violation note (Gate A v1 P0 #5):** This overlay cites the superseded docs (HIPAA_FRAMEWORK, BAA_SUBPROCESSORS) in §4 above. Per the §7 CI gate, this is the only allowed citation form — explicit supersession reference. Other docs/code MUST NOT cite superseded docs as authority without the `posture-overlay:SUPERSEDED(<doc>, reason)` marker.

---

## §5 — §164.308 + §164.530 OCR audit checklist (added per Gate A v1 P0 #3; Gate B P1 — split §164.530(b) from §164.308(a)(5); §164.312 technical-safeguards scope-note)

**Scope:** this checklist covers HIPAA Security Rule administrative safeguards (§164.308) AND Privacy Rule administrative requirements (§164.530). HIPAA Security Rule technical safeguards (§164.312 — Access Control / Audit Controls / Integrity / Person Authentication / Transmission Security) are deferred to a §5.1 follow-up expansion. The deferral is self-disclosed-and-remediated rather than silently-missing: a follow-up task is named in the publish commit.

Counsel-grade requirement: every probe-area row gets a current authority pointer AND a remediation pointer. Self-disclosed-AND-remediated is HIPAA-enforcement-favorable; self-disclosed-without-remediated is the opposite.

### §164.308 Security Rule administrative safeguards

| §164.308 probe area | Current authority | Remediation task / pointer |
|---|---|---|
| §164.308(a)(1)(ii)(A) HIPAA Risk Analysis | TBD — no current doc indexed | Task TBD: schedule annual Risk Analysis (BA-side); customer Risk Analyses are CE-side responsibility under master BAA Article 4 |
| §164.308(a)(1)(ii)(B) Risk Management implementation | Substrate Integrity Engine (61+ invariants — `project_substrate_integrity_engine.md`) | Active; quarterly review cadence |
| §164.308(a)(1)(ii)(C) Sanction Policy | TBD — no current doc | Task TBD: draft Sanction Policy for workforce |
| §164.308(a)(1)(ii)(D) Information System Activity Review | append-only audit logs (`admin_audit_log`, `client_audit_log`) + Substrate Engine | Active; ongoing |
| §164.308(a)(5) Security Awareness and Training (Security Rule) | TBD — no current doc indexed | Task TBD: BA-side Security Awareness Training documentation; distinct from §164.530(b) below |
| §164.308(a)(7) Contingency Plan (DR) | TBD — Task #39 not yet designed | **PENDING — task #39** (DR drill) |
| §164.308(a)(8) Evaluation | this POSTURE_OVERLAY + quarterly re-verification cadence | Active |
| §164.308(b) Business Associate Contracts | `docs/legal/MASTER_BAA_v1.0_INTERIM.md` | v1.0-INTERIM live; v2.0 outside-counsel hardening pending |

### §164.530 Privacy Rule administrative requirements

| §164.530 probe area | Current authority | Remediation task / pointer |
|---|---|---|
| §164.530(b) Workforce Training (Privacy Rule) | TBD — no current doc indexed | Task TBD: BA-side Privacy Rule workforce training (distinct from §164.308(a)(5) Security Rule awareness training; OCR auditors probe both) |
| §164.530(j) Records Retention (6-year minimum) | Master BAA Article 5.3 + WORM evidence chain | Active; substrate-enforced via append-only `compliance_bundles` |

### §164.312 Technical Safeguards (deferred to §5.1 follow-up)

Probe areas in scope: §164.312(a)(1) Access Control, §164.312(b) Audit Controls, §164.312(c) Integrity, §164.312(d) Person or Entity Authentication, §164.312(e) Transmission Security. These are systematically probed by OCR alongside §164.308 + §164.530. Engineering action: add §5.1 expansion in a follow-up POSTURE_OVERLAY update with current-authority + remediation pointers. The current platform implementations exist (RLS, bcrypt 12-round password hashing, TOTP 2FA, TLS 1.3, append-only audit triggers) but are not yet inventoried in this overlay's pointer-index format.

**Whole-legal-document-inventory follow-up (BAA-drafting Gate A Coach lens):** the rows TBD above are part of the whole-legal-document-inventory audit that Coach lens called out as a non-negotiable parallel matter. Outside-counsel engagement during v2.0 master-BAA hardening should commission this audit.

---

## §6 — Governance — tiered update workflow (Gate A v1 P0 #4 fix)

Uniform Class-B Gate A + Gate B for EVERY overlay update produces stale-by-friction. Per `feedback_consistency_coach_pre_completion_gate.md`, governance must tier the workflow:

### Trivial updates (no gate)

- Updating a `last_verified:` date in a pointer-index row (no other field change)
- Fixing typos in this overlay or owning docs
- Updating a row's "Status note" from "Active" → "OVERDUE refresh" or vice versa

Just commit; commit body cites "POSTURE_OVERLAY trivial update."

### Minor updates (Gate A only, single-lens)

- Adding or removing a pointer-index row (§3)
- Updating a supersession-registry entry (§4)
- Cadence adjustments in §5 §164.308 checklist

Single-lens fork (Coach + Engineering): verify the change is self-consistent + doesn't violate Rule 5 anti-stale-doc rule. ~5-10 min.

### Major updates (full Class-B Gate A + Gate B)

- Adding or removing a governing rule (§2)
- Restructuring §3 organization (e.g. adding/removing a category)
- Adding a new §-citation area to §5
- Changing the frontmatter standard (§8)
- Changing the CI gate scope (§7)

Full 7-lens fork at Gate A; Gate B verification after fixes land. Standard bedrock TWO-GATE protocol.

### Self-violation guard

Overlay updates that themselves cite a superseded doc as authority MUST be classified as Major. The overlay is its own gate.

---

## §7 — CI gate — unified with memory-hygiene infrastructure (Gate A v1 P0 #2 fix)

The existing `.agent/scripts/context-manager.py validate` already enforces YAML frontmatter on memory files. The POSTURE_OVERLAY CI gate **extends** that script rather than building a parallel system:

1. **`context-manager.py validate`** is extended to also walk `docs/**/*.md`, applying the §8 frontmatter standard.
2. New mode: `context-manager.py validate --posture-overlay` reads POSTURE_OVERLAY.md §4 (supersession registry) and greps the codebase + docs for citations of superseded docs.
3. CI workflow `.github/workflows/memory-hygiene.yml` adds the `--posture-overlay` flag to its existing validate run.

**Citation forms (enforced by the gate):**

- ✅ `posture-overlay:CURRENT(<topic-area>)` — cite via the overlay's pointer-index
- ✅ `posture-overlay:SUPERSEDED(<doc>, reason)` — explicitly cite a superseded doc with context
- ✅ Direct citation of a doc with current frontmatter (`posture_overlay_authoritative: true` + non-stale `last_verified`)
- ❌ Bare references to docs in the supersession-registry without `SUPERSEDED` marker
- ❌ `See <stale-doc>` / `Per <stale-doc>` in commit bodies, code comments, or other docs

**Overlay-self-exemption (Gate A v1 P0 #5 fix):** the overlay itself is allowlisted as a producer of supersession-registry references — its own §4 citations are not gate violations. The allowlist is encoded in the gate as `OVERLAY_REGISTRY_FILES = {"docs/POSTURE_OVERLAY.md"}` — additions require Major-update review per §6.

**Producer note:** per `feedback_directive_must_cite_producers_and_consumers.md`, no CI gate ships without ≥2 producers + ≥1 consumer in the codebase. The gate ships AFTER (a) the pointer-index lands at `docs/POSTURE_OVERLAY.md`, (b) at least 2 owning docs adopt the §8 frontmatter, and (c) at least 1 consumer (an existing module's commit body citing `posture-overlay:CURRENT(...)`) exists.

**Named first-adopter docs** (Gate B P1 — specifies the 2 owning docs whose frontmatter adoption gates ship):

1. `docs/legal/MASTER_BAA_v1.0_INTERIM.md` — already carries effective-date + version + status metadata at lines 1-6 + Article 9. Backfill to formal §8 frontmatter schema is a small additive change.
2. `docs/SUBPROCESSORS.md` — already carries Effective Date / Document Version / Re-audit cadence metadata at lines 4-7. Backfill to formal §8 frontmatter schema is a small additive change.

Both first-adopter docs are gold-authority customer-binding artifacts already in scope for the overlay (§3 line 23 + §3 line 24). Adoption order: master BAA first (Article 9 already version-tracks), SUBPROCESSORS second (effective-date already explicit).

---

## §8 — Frontmatter standard (unified with memory-hygiene schema)

Memory files already use a YAML frontmatter (`name`, `description`, `type`, `decay_after_days`, `last_verified`). The POSTURE_OVERLAY frontmatter for `docs/` files is the SAME schema with three additive fields:

```yaml
---
title: "<doc title>"                    # memory schema field
description: "<one-line description>"   # memory schema field (optional but recommended for --posture-overlay diff output)
topic_area: "<topic-area-key>"          # memory schema field (replaces `type`)
last_verified: YYYY-MM-DD               # memory schema field
decay_after_days: N                     # memory schema field
# Additive for docs/ files:
supersedes: ["<prior-doc>"]             # optional; null for new docs
superseded_by: ["<successor-doc>"]      # optional; null until superseded
posture_overlay_authoritative: true | false  # is this doc the OWNING authority for its topic_area per §3 of POSTURE_OVERLAY
---
```

**Adoption strategy:**

- New docs in `docs/` MUST include this frontmatter from creation.
- Existing docs: adopt opportunistically as touched. Backfill priority: gold-authority + legal docs first (master BAA already has it; subprocessor registry pending), then architectural, then operational.
- `context-manager.py validate` warns (not errors) on docs/*.md without frontmatter for 30 days post-overlay-launch; then errors.

---

## §9 — This document's frontmatter

```yaml
---
title: "POSTURE_OVERLAY"
description: "Canonical pointer-index for OsirisCare platform posture; cite this overlay first for any topic-area authority"
topic_area: "platform-posture"
last_verified: 2026-05-13
decay_after_days: 30
supersedes: []
superseded_by: null
posture_overlay_authoritative: true
gate_a_status: "v1 APPROVED-WITH-FIXES — 5 P0 findings all applied in v2"
gate_b_status: "v2 APPROVED-WITH-FIXES (2026-05-13) — 5 P1 findings + 1 P2 frontmatter parity all applied inline; publish-to-docs sign-off YES"
---
```

---

## §10 — Class-B Gate B reviewer guidance

Gate B verifies the 5 Gate A v1 P0 findings closed cleanly:

| P0 | Status (Gate B verifies) |
|---|---|
| #1 §3 restructure → pointer-index | v2 §3 is a pointer-index (each row points to owning doc; state lives in owning doc's frontmatter, not in this overlay) |
| #2 §7/§8 unify with memory-hygiene | v2 §7 extends `context-manager.py validate` rather than parallel-building; v2 §8 unifies frontmatter schema with memory files |
| #3 §164.308 OCR rows + remediation pointers | v2 §5 enumerates 8 §164.308 probe areas with current authority + remediation task pointers |
| #4 Governance tiered (major/minor/trivial) | v2 §6 tiers updates; trivial = no gate, minor = single-lens, major = full Class-B Gate A + Gate B |
| #5 Supersession dates corrected + overlay-self-violation guard | v2 §4 supersession dates use Gate-B-passed dates (subprocessor v2 supersession effective 2026-05-13 only because Gate B passed today); §7 includes `OVERLAY_REGISTRY_FILES` allowlist for the overlay's own §4 citations |

Plus regression scan: no new banned-language, no urgency-overshoot framing, cross-fork consistency with master BAA v1.0-INTERIM + subprocessor v2 + counsel-edited Gate A doc.

— OsirisCare engineering
   on behalf of the privacy officer
   2026-05-13
