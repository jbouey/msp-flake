# Enterprise-Readiness Backlog Audit — 2026-05-12 (broad scope)

**Reviewer:** Fresh-context fork
**Scope:** Multi-session backlog. NOT today's 15-commit diff.
**Sources:** memory/project_*.md, .agent/plans/, audit/, git log since 2026-04-25, claude-progress.json recent_milestones, .agent/sessions/.

---

## Verdict on user's question: "is the broader enterprise stack ready end-to-end?"

**Mostly yes for the artifact / multi-appliance / printable-evidence story; NOT yet for capacity, federation phase-2, vault key-cutover, and counsel-gated cross-org features.** The printable-artifact sprint (F1+F2+F3+F4+F5 + P-F5..P-F8) shipped end-to-end between 2026-05-06 and 2026-05-09, closing the demo-loss pattern Maria/Greg surfaced. Multi-appliance Layers 1-3 are SHIPPED and Layer 4 dashboard UX shipped Session 197 (`2b289b09` + `d4a1e61d`). What's STILL OPEN is operational/capacity (load harness BLOCKED at Gate A today, MTTR soak BLOCKED at Gate A 2026-05-11) and policy-gated (F6 phase 2 federation enforcement DEFERRED to counsel, RT21 cross-org relocate flag-disabled awaiting counsel v2.4 sign-off, Vault Phase C cutover prereqs unmet). The substrate is enterprise-presentable; the throughput-and-scale story is undemonstrated.

---

## Status matrix

### Owner-facing artifacts (Maria persona, F-series)

| Item | Status | Last activity | Blast radius |
|---|---|---|---|
| F1 Compliance Attestation Letter (1-page PDF, 1-800, BAA footer, 90-day validity) | SHIPPED | `721008af` + `cec92d3f` + `5e53ac60` 2026-05-08 | DEMO-LOSS — closed |
| F2 Privacy Officer designation wizard (§164.308(a)(2) signed attestation bundle) | SHIPPED | `548db80e` 2026-05-08 | AUDITOR-EXPOSED — closed |
| F3 Quarterly 4-page Practice Compliance Summary | SHIPPED | `751c9e00` 2026-05-08 | DEMO-LOSS — closed |
| F4 Public `/verify/{hash}` page | SHIPPED | `721008af` 2026-05-08 | DEMO-LOSS — closed |
| F5 Wall Certificate + ClientDashboard print stylesheet | SHIPPED | `d69b9c73` 2026-05-08 | POLISH — closed |
| ClientAttestations tab (F1+F3+F5 UI surface) | SHIPPED | `f56730a6` + `d73af453` Session 219 | POLISH — closed |

### Partner-facing artifacts (Greg/Lisa/Tony/Anna/Brendan, P-F series)

| Item | Status | Last activity | Blast radius |
|---|---|---|---|
| P-F5 Partner Portfolio Attestation + public `/verify/portfolio/{id}` | SHIPPED | `ad770b4b` + `177a1ecd` 2026-05-08 | DEMO-LOSS — closed |
| P-F6 BA Compliance Attestation + downstream-BAA roster | SHIPPED | `9a92b402` + `88dd5e49` 2026-05-08 | AUDITOR-EXPOSED — closed |
| P-F7 Technician Weekly Digest PDF | SHIPPED | `ad770b4b` 2026-05-08 | OPERATOR-PAIN — closed |
| P-F8 Per-incident Response Timeline PDF | SHIPPED | `c5d81db2` 2026-05-08 | AUDITOR-EXPOSED — closed |
| P-F9 Partner Profitability Packet (Brendan CFO) | OPEN — design only | `p-f9-partner-profitability-design-2026-05-09.md` | OPERATOR-PAIN — partly blocked on Stripe Connect for paid-commission history |
| PartnerAttestations tab UI | SHIPPED | `d5b3c68a` 2026-05-08 | POLISH — closed |
| Partner per-site drill-down `/partner/site/:siteId` | SHIPPED Sprint-N+2 | `06a9c1c7` 2026-05-08 | OPERATOR-PAIN — closed |

### Multi-appliance architecture (4 layers)

| Item | Status | Last activity | Blast radius |
|---|---|---|---|
| Layer 1 — Per-appliance identity/auth (mig 119, mig 126 signing keys) | SHIPPED | Session 196 | — |
| Layer 2 — Mesh scan coordination (backend-authoritative, hash ring) | SHIPPED | Session 196 mesh.go | — |
| Layer 3 — Cross-appliance dedup + alert routing | SHIPPED | Session 197 alert_router.py | — |
| Layer 4 — Expandable appliance cards + per-appliance incident filter | SHIPPED | `2b289b09` Session 197 | DEMO-LOSS — closed |
| Layer 4 — Client-portal appliance fleet view (RLS-protected) | SHIPPED | `d4a1e61d` RT33 P2 | DEMO-LOSS — closed |
| D1 daemon Ed25519 signs heartbeat_hash (column exists, signing loop pending) | OPEN | nothing since 2026-04-14 plan | OPERATOR-PAIN — defense layer 8 not yet load-bearing |
| D2 audit_cross_appliance_update trigger flip from AUDIT to REJECT | SHIPPED | `bc24e405` (#154/#151) | — |
| M1 legacy `appliances` table DROP | SHIPPED | `bdd919ef` mig 213 | — |
| M3 daemon-side mesh ACK to `/api/appliances/mesh/ack` | OPEN | server-side live, daemon caller pending | OPERATOR-PAIN |

### Federation (F6/F7) — flywheel cross-org

| Item | Status | Last activity | Blast radius |
|---|---|---|---|
| F6 MVP scaffolding (mig 261 + 262, feature flag OFF) | SHIPPED | `7dee6d6c` Session 214 | — |
| F6 phase 2 read-path eligibility helpers + F7 integration | SHIPPED | `a91794ce` Session 214 | — |
| F6 phase 2 WRITE-path enforcement (Tier 1 org-aggregated, Tier 2 platform-aggregated) | DEFERRED — counsel | `7cb01b80` 2026-04-30; `f6-phase-2-enforcement-deferred.md` | AUDITOR-EXPOSED — §164.528 disclosure-accounting question OPEN; HIPAA counsel sign-off required before Tier 2 |
| F7 operator endpoint `/api/admin/sites/{id}/flywheel-diagnostic` | SHIPPED | Session 214 flywheel_diagnostic.py | — |
| flywheel_federation_misconfigured sev3 invariant | SHIPPED | `21bdda0a` Session 214 | — |

### Multi-framework support

| Item | Status | Last activity | Blast radius |
|---|---|---|---|
| 9 framework crosswalk (control_mappings.yaml + framework_mapper.py) | SHIPPED | Session 197 | DEMO-LOSS — closed for HIPAA + SOC2/PCI/GLBA/NIST/SOX/GDPR/CMMC/ISO27001 |
| Non-HIPAA assessment question banks (SOC 2 / GLBA template question banks) | OPEN | hipaa_templates.py is single-framework | DEMO-LOSS for non-HIPAA prospects |

### Mesh + mDNS (cross-subnet)

| Item | Status | Last activity | Blast radius |
|---|---|---|---|
| Backend-delivered mesh_peers + TLS probe + WG-filter | SHIPPED | Session 194 | — |
| mDNS service discovery (`_osiris-grpc._tcp.local`) | SHIPPED | Session 194 avahi config | — |
| Consumer-router handling (`mesh_topology` + `network_mode`) | SHIPPED | Session 194 | — |
| 24h persistent-isolation alert reclassification | SHIPPED | Session 194 | — |

### Substrate Integrity Engine

| Item | Status | Last activity | Blast radius |
|---|---|---|---|
| 32 invariants → 58 invariants today | SHIPPED + GROWING | `57960d4b` per-assertion admin_transaction 2026-05-11 | — |
| `/admin/substrate-health` panel | SHIPPED | Session 207 `e87721cb` | — |
| Watcher-the-watcher (`substrate_assertions_meta_silent`) | SHIPPED | Session 214 | — |
| Substrate SLA meta-invariant (sev1≤4h, sev2≤24h, sev3≤30d) | SHIPPED | Session 218 `9163921e` | — |

### Vault Transit (Phase 15 gap #1)

| Item | Status | Last activity | Blast radius |
|---|---|---|---|
| Vault live on Hetzner WG-only, Ed25519 non-exportable | SHIPPED shadow-mode | 2026-04-13 | — |
| One-week flat divergence-counter observation | UNCLEAR — never claimed complete | claude-progress.json silent on Phase B closure | AUDITOR-EXPOSED — substrate signing key still file-based primary |
| Phase C cutover (file→Vault primary, fleet rotation OR import) | OPEN | mig 177 `signing_method` awaiting trigger | AUDITOR-EXPOSED — single-host signing-key blast radius |

### Operational / capacity / soak

| Item | Status | Last activity | Blast radius |
|---|---|---|---|
| Multi-tenant load harness (task #97, k6, 100-appliance sim) | BLOCKED at Gate A | `coach-load-test-harness-design-gate-a-2026-05-12.md` BLOCK — 3 P0s | OPERATOR-PAIN — zero production-capacity number |
| Substrate-MTTR 24h soak (task #98 / #94 / plan-24) | BLOCKED at Gate A | `coach-phase4-mttr-soak-review-2026-05-11.md` BLOCK — 3 dealbreakers | OPERATOR-PAIN — SLA invariant never fired against synthetic |
| Disaster-recovery / backup-restore drill | OPEN — no design | never indexed | AUDITOR-EXPOSED — §164.308(a)(7) contingency-plan untested |
| Adversarial pen-test on attestation chain | SHIPPED phase 15 | `2edfebfd` | — |
| L1 orphan Phase 3 (backend gate + daemon fix + mig 306 backfill) | PARTIAL — Phases 1+2 SHIPPED, Phase 3 mig 306 blocked on daemon 24h soak | `3f0e5104` + `3b2b8480` Session 220 | AUDITOR-EXPOSED — 1,137 historical L1-tier rows pending §164.528 retroactive-PDF call (task #117) |

### Counsel-gated features

| Item | Status | Last activity | Blast radius |
|---|---|---|---|
| RT21 cross-org site relocate (feature-flagged, default OFF, dual-admin enforced) | SHIPPED behind flag — counsel pending | `be980fc7` + `bf602c6c` mig 282 + 283; `21-counsel-briefing-packet-v2.4-2026-05-09.md` | AUDITOR-EXPOSED if enabled without counsel — 4 §-questions open |
| §164.524 ex-workforce kit-access policy | OPEN — counsel | `34-counsel-queue-deferred-2026-05-08.md` Item 1 | AUDITOR-EXPOSED |
| Three other §-questions in counsel queue | OPEN — counsel | same packet items 2-4 | AUDITOR-EXPOSED |

---

## Critical OPEN / PARTIAL items by blast radius

### DEMO-LOSS (close before next sales conversation)

1. **Non-HIPAA assessment question banks** (SOC 2 / GLBA / PCI / NIST 800-171 template banks). The crosswalk maps controls but the assessment templates in `hipaa_templates.py` are HIPAA-only. A SOC 2 prospect cannot complete a self-assessment without HIPAA-shaped questions feeling wrong. **Effort:** 1-2 days per framework. **Recommendation:** ship SOC 2 first (most-asked non-HIPAA framework for NEPA SMB-IT prospects).

### AUDITOR-EXPOSED (close before next audit)

1. **Vault Phase C cutover.** Vault has been in shadow mode for ~30 days; the divergence-counter Phase B closure was never explicitly claimed in `claude-progress.json`. Until cutover, compromising the mcp-server VPS alone is sufficient to forge signatures — the two-host enterprise-grade delta is undelivered. **Recommendation:** verify divergence counter `osiriscare_signing_backend_divergence_total` has flat-zeroed for ≥7 days, then schedule cutover.

2. **F6 phase 2 federation Tier 2 (platform-aggregated WRITE path).** Currently DEFERRED to a "rested session with HIPAA counsel." Engineering opinion in `f6-phase-2-enforcement-deferred.md` Q3 already says "YES, §164.528 work needed" but counsel has not answered. Until then, the data-flywheel's promised cross-customer learning is single-org only. **Recommendation:** bundle this with the existing 4-item counsel queue (`34-counsel-queue-deferred-2026-05-08.md`) into the next counsel engagement.

3. **RT21 cross-org site relocate enablement.** Code shipped behind dual-admin attestation-gated feature flag; counsel packet v2.4 sent 2026-05-09. Until counsel returns, the flag returns 503. **Recommendation:** treat as part of the counsel-queue bundle above.

4. **L1 orphan mig 306 backfill** (1,137 historical L1-tier rows that should be L3/monitoring). Task #117. Phases 1+2 of the fix shipped (daemon + backend gate); mig 306 is blocked on daemon 24h soak verification AND Maya's §164.528 retroactive-PDF impact deep-dive. **Recommendation:** run Gate A for the §164.528 question before scheduling the daemon soak.

5. **Disaster-recovery drill.** §164.308(a)(7) contingency plan requires periodic testing. Never indexed in our backlog. A 7-year evidence-retention claim with zero documented restore drill is an audit finding waiting to happen. **Recommendation:** schedule a quarterly DR rehearsal with a restored-from-backup substrate verification.

### OPERATOR-PAIN

1. **Multi-tenant load harness (task #97).** Gate A BLOCKED today on 3 P0s (endpoint paths wrong, compliance_bundles isolation pattern incompatible, duplicates plan-24 soak-isolation). **Recommendation:** v2 redesign explicitly addresses each P0; resubmit Gate A. Until shipped, we cannot answer "how many simultaneous checkins survive PgBouncer?" — a question Greg-the-MSP-owner will ask before he commits to 30+ clinics.

2. **Substrate-MTTR 24h soak (plan-24).** Gate A BLOCKED 2026-05-11 on 3 dealbreakers (engine doesn't fire on synthetic incidents, auto-resolve window bounds every measurement, mig 303 contaminating prod admin surfaces — quarantined in `c0dde985`). **Recommendation:** the v2 redesign should pick a real substrate invariant the engine actually fires on (e.g., orphan L2 decision) and inject *that* shape, not raw incidents.

3. **P-F9 Partner Profitability Packet (Brendan CFO).** Design shipped 2026-05-09, partially blocked on Stripe Connect for paid-commission history. **Recommendation:** ship the "estimated profitability" version now using subscription ledger only; defer paid-commission columns until Stripe Connect lands.

4. **D1 daemon-side Ed25519 signing of heartbeat_hash.** Column `agent_signature` exists, signing loop is a separate Go PR. Liveness-defense layer 8 is structurally inert until daemon signs. **Recommendation:** queue a small Go PR — substrate already verifies signatures if present.

5. **M3 daemon-side mesh ACK to `/api/appliances/mesh/ack`.** Server-side live; daemon caller pending. Mesh-reassignment retry loop currently relies on TTL not ACK. **Recommendation:** add to next daemon release.

### POLISH

1. **Followup #32** — SiteDetail.tsx 3 likely-404 endpoint URL fixes (in-flight).
2. **Followup #33** — main.py 5 inline background-loops → `_LIFESPAN_INLINE_LOOPS` registration (pending).
3. Cosmetic ots_proofs `'verified'` reader trim (4 callsites returning constant 0 after mig 307).

---

## Recommended next 3 priorities

### 1. Counsel-queue bundle (single engagement, parallelizable)

Send outside HIPAA counsel ONE consolidated packet covering: (a) RT21 cross-org relocate v2.4 (already drafted), (b) F6 phase 2 Tier 2 federation §164.528 question, (c) §164.524 ex-workforce kit access (item 1 of counsel queue), (d) the three sibling §-questions in `34-counsel-queue-deferred-2026-05-08.md`. Five questions, one engagement, unblocks ~$30K of strategic features and closes 4 AUDITOR-EXPOSED items at once. **Cost-effective:** counsel charges by engagement, not by question.

### 2. Vault Phase C cutover

The divergence counter has been silent since 2026-04-13 — confirm it then cut over. This is the highest-leverage AUDITOR-EXPOSED close because it raises the signing-key blast radius from 1 host to 2 hosts. After cutover, your story to a security-conscious prospect changes from "the VPS signs evidence" to "evidence is signed by a separately-hardened Vault host on a WG-only network — compromising either alone is insufficient to forge."

### 3. Load harness v2 + MTTR soak v2 (Gate A re-runs, sequentially)

Both are BLOCKED at Gate A today. Both are necessary for the "are we ready to onboard 30 clinics?" answer Greg will ask. They share infrastructure (k6 + Prometheus + synthetic injection) and the MTTR soak's load floor depends on the harness. Sequence: redesign load harness (close 3 P0s, fix endpoint paths, pick an isolation pattern compatible with compliance_bundles), then redesign MTTR soak to inject a synthetic-invariant the engine actually fires on, then run both. Gate-A-to-prod walltime estimate: 3-5 days of focused work.

---

## Honesty check

**What memory entries the user may not be tracking actively:**

- **Vault Phase C** has been silent since 2026-04-13 — 30 days of shadow mode with no closure note in `claude-progress.json`. This is the single largest unrealized enterprise-grade delta.
- **D1 daemon-side heartbeat signing** has not moved since 2026-04-14. Layer 8 of the 10-layer liveness defense is structurally inert. The 10-layer story is only true if D1 lands.
- **Disaster-recovery drill** is not indexed in any memory file or backlog item, but §164.308(a)(7) demands it. The omission itself is the finding.
- **Non-HIPAA assessment question banks** were called out as "intentionally left HIPAA-coupled" in `project_multi_framework_compliance.md` but that decision was made when there were zero paying customers — if a SOC 2 prospect appears tomorrow, this becomes a demo-loss item the same week.
- **F6 phase 2** has been parked for 12 days. The counsel question is the gate, not the engineering.

**What prior round-table priorities have decayed:**

- The 2026-05-08 round-table closeout (`round-table-final-closeout-2026-05-08.md`) presumably had follow-ups that may now be 4 days stale; today's coach audit did not re-check them.
- The 2026-04-30 Phase 4 substrate-MTTR soak was BLOCKED at Gate A 2026-05-11 but the v2 redesign is not yet scheduled — task #98 is sitting in limbo.
- The "memory hygiene validate must pass on every push" rule (Session 205) is enforced in CI, but the memory files themselves carry `last_verified` dates 4-28 days old; several decayed past their `decay_after_days` thresholds. Specifically `project_multi_appliance_architecture.md` is 28 days old with `last_verified` missing — re-verification is overdue.
- The 8 non-negotiable principles in `feedback_critical_architectural_principles.md` are codified but no quarterly architecture-review cadence exists; principles drift quietly.

**Net read on "enterprise ready end-to-end":** the *customer-facing* story (PDFs, attestations, evidence, dashboards) is in better shape than the *operator-facing* story (capacity numbers, DR, Vault Phase C, counsel-gated features). If a prospect asks "show me the binder you'd hand my insurance person," we win. If they ask "show me your last load test and DR drill," we lose.
