# Round-Table: Cross-Org Site Relocate (Task #21)

**Date:** 2026-05-05
**Format:** PM-led adversarial (Camila + Brian + Linda + Steve + Adam) + Maya 2nd-eye
**Status:** DESIGN APPROVED with explicit BAA-counsel-review preconditions
**Scope:** ~1-2 days post-design + outside-counsel BAA review (multi-day async)

---

## Problem statement

`POST /api/sites/{site_id}/appliances/{appliance_id}/relocate` (sites.py:1938) handles same-org relocations. Cross-org returns HTTP 403 with comment `"coming soon at /api/admin/cross-org-relocate flow (admin-only, attestation-gated)"`. Real-world demand: a clinic switches MSPs (partner change), or a clinic is acquired (client_org change), or a partner sells a clinic to another partner.

**Why this is harder than same-org relocate:**

1. **BAA continuity.** When a site moves orgs, the BAA between Osiris-as-substrate and the receiving client_org governs the data. PHI is scrubbed at appliance egress (per Session 185 rule), so site_id-keyed evidence already complies. But the *cryptographic chain* anchors to the original site_id forever (Ed25519 + OTS). The receiving org's auditor needs to walk the chain across the org boundary.
2. **Cross-tenant access.** Both source and target orgs need explicit consent. Different from same-org where one operator just clicks a button.
3. **Privileged-action chain length.** Multi-step state machine: source-org-release → receiving-org-accept → admin-execute. Three actors, three attestation events minimum.

## Camila — PM lead

**Customer + ops framing.** Real-world demand is bounded but real:

- A clinic merges with another clinic (consolidation under new client_org).
- A clinic switches MSPs (partner_id swap on the SAME client_org, NOT a cross-org relocate — covered by existing `PUT /api/sites/{id}` with partner_id field).
- A clinic is acquired by a hospital network (client_org acquired by parent client_org).

This is **operator-class workflow, not customer self-service.** Both orgs' owners explicitly approve, but the actual mutation runs through Osiris admin — there's no two-clinic-owners-can-do-this-by-themselves path. Same friction model as `customer_subscription_cancel` (Session 215 #72): admin-API class, requires actor email + reason ≥20ch + Ed25519 attestation + per-site chain bundle.

**Priority:** SHIP_AFTER_BAA_COUNSEL_REVIEW. Implementation is multi-day; legal review is async with outside HIPAA counsel and not blocking on engineering's calendar. Ship the engineering work first; gate the endpoint behind a feature flag until BAA counsel signs off.

## Brian — Principal SWE

**State machine (5 states):**

```
pending_source_release  →release→  pending_target_accept
pending_target_accept   →accept→   pending_admin_execute
pending_admin_execute   →execute→  completed
                                   (with role + chain transitions)
any pending             →cancel→   canceled
any pending             →expires→  expired
```

**Endpoint surface (6 total):**

```
POST /api/admin/cross-org-relocate/initiate         (Osiris admin only;
                                                     creates pending_source_release)
POST /api/admin/cross-org-relocate/{id}/source-release    (source-org owner)
POST /api/admin/cross-org-relocate/{id}/target-accept     (target-org owner)
POST /api/admin/cross-org-relocate/{id}/execute            (Osiris admin)
POST /api/admin/cross-org-relocate/{id}/cancel             (any of the 3 actors)
GET  /api/admin/cross-org-relocate/{id}                    (visibility)
```

**Schema (mig 277):** `cross_org_site_relocate_requests` table with the 5 states + 3 actor email columns + 3 actor approval timestamps + reason fields + attestation bundle IDs. Plus a `prevent_cross_org_relocate_deletion` trigger (audit-class).

**Cryptographic chain re-anchoring** is the load-bearing engineering decision. Two options:

- **Option A — chain stays at source site_id forever.** Auditor for new org has to walk the chain back into the old org. Works because compliance_bundles.site_id is immutable (mig 273 immutable list); the chain attests to the site under the original org. Discoverable via `chain.json["site_canonical_aliases"]` (Session 213 mechanism, F1-followup).
- **Option B — boundary attestation marks a chain split.** A `cross_org_site_relocate_executed` attestation bundle is the LAST entry under source org and FIRST entry under target org (one event, two anchors via dual chain link). Auditor walks forward from this boundary into target-org chain, backward into source-org chain.

**Brian's verdict:** Option A. Simpler. The chain is immutable BY DESIGN (Ed25519 hash-chained); pretending we can split it is fiction. The auditor walks back across the boundary using the existing `site_canonical_aliases` mechanism extended with a new `prior_client_org_id` field.

## Linda — DBA

**Two schema migrations:**

**Migration 277:** `cross_org_site_relocate_requests` table.
- 5-state CHECK constraint + partial unique index on `(site_id) WHERE status IN (pending states)` so one site can't be in flight on two cross-org relocates simultaneously.
- Append-only audit-class table.
- `attestation_bundle_ids JSONB` carries 6 bundle IDs across the lifecycle.

**Migration 278:** add `prior_client_org_id UUID` column to `sites` table; backfill NULL. After a successful cross-org relocate, this column is set to the previous `client_org_id`. The `site_canonical_aliases` view (mig 258) is extended to also project `prior_client_org_id` so auditor kits can walk back across the boundary.

**1-owner-min trigger** (mig 273) is unaffected — moving a site between orgs doesn't change the count of owners on either org.

## Steve — Adversary

**Threat model is dense here. Six attack scenarios:**

1. **Compromised source-org owner releases site to attacker-controlled org.** Mitigation: target-org-accept step requires auth from the target org's owner; if the target org is attacker-controlled, the chain captures the social engineer's identity AND the target-org-owner's identity (both verified via authenticated session). Auditor sees both. Operator alert P1 on initiate + accept + execute.
2. **Compromised target-org owner accepts a site they don't actually want.** Mitigation: cooling-off window between target-accept and admin-execute (default 24h, per-org configurable via task #20 mechanism). Either party can cancel during the window.
3. **Race: source-org-owner-A releases site, then source-org goes through owner-transfer to A's compromised account, A then bypasses cancel.** Mitigation: cross-org relocate refuses to initiate if EITHER org has a pending owner-transfer. Owner-transfer initiate refuses if source org has a pending cross-org relocate.
4. **Cross-tenant data leak via compliance_bundles.** PHI is scrubbed at appliance per Session 185, so this isn't a real leak vector. But operator should think about it. Substrate invariant `cross_org_relocate_chain_orphan` (sev1) catches the case where bundles continue to land under source org_id post-relocate (which would be a code bug + actual chain misattribution).
5. **BAA misalignment.** Source org's BAA limits Osiris's data handling; target org's BAA might be different. Mitigation: receiving client_org must have a `baa_on_file=true` row + `baa_signed_at` within the last 365 days BEFORE target-accept can complete. CHECK at the endpoint layer + recorded in attestation bundle.
6. **Partner-mediated cross-org abuse.** A compromised partner-admin initiates cross-org relocate of a managed-clinic to a partner-owned shell org. Mitigation: ONLY Osiris admin (not partner) can initiate cross-org relocate. Partner-mediated transfers go through the existing `PUT /api/sites/{id}` partner_id swap (which doesn't change client_org).

**Adversarial verdict:** SHIP with all 6 mitigations pinned in tests. The endpoint stays behind a feature flag (`CROSS_ORG_RELOCATE_ENABLED=false` default) until outside BAA counsel signs off on (5).

## Adam — Tech writer

**Three customer-facing emails:**

- **source-release notice** to source-org owner: "Cross-org site relocate initiated by [Osiris admin]. The site will move from your organization to [target org name] on [date+24h]. To approve this release, click the link in this email within 7 days. To cancel, click the cancel link."
- **target-accept notice** to target-org owner: similar. Includes a summary of WHAT data is being received (site name, partner_id, BAA status).
- **post-execute notice** to BOTH orgs: "Site [name] has been successfully moved from [source org] to [target org] on [timestamp]. The cryptographic evidence chain remains anchored to the original site_id; auditors walk across the boundary via the chain.json site_canonical_aliases array. Your auditor kit ZIP at /api/evidence/sites/{site_id}/auditor-kit reflects the move."

Per CLAUDE.md Session 199: NO banned words. Body uses "supports audit-readiness through cryptographic chain", "operator visibility", "PHI scrubbed at appliance".

## Maya — Consistency coach (2nd-eye)

| # | Item | Maya verdict |
|---|---|---|
| 1 | 5-state state machine + 6 endpoints | **DIFFERENT_SHAPE_NEEDED** — multi-actor flow needs more states than client_org_owner_transfer's 4. |
| 2 | Option A: chain stays at source site_id forever | **APPROVE** — chain is immutable; pretending otherwise is fiction. The `site_canonical_aliases` extension is the right mechanism. |
| 3 | Cross-org owner-transfer interlock (Steve mit 3) | **PARITY** with mfa-revoke interlock (task #19). One pattern, applied symmetrically. |
| 4 | BAA-on-file precondition (Steve mit 5) | **PARITY** — privileged action requiring legal-state precondition. Same shape as billing destructive actions checking customer status. |
| 5 | `cross_org_relocate_chain_orphan` substrate invariant (sev1) | **PARITY** — outcome-layer invariant, same pattern as `flywheel_orphan_telemetry` (Session 213 F3). |
| 6 | Feature-flag the endpoint until outside-counsel signs off | **APPROVE** — engineering work + BAA review can run in parallel. Code ships behind flag; flag flip is a separate operational change. |
| 7 | Per-org configurable cooling-off (task #20 mechanism) re-used | **PARITY** — same window-config hooks. Source org's `transfer_cooling_off_hours` applies. |

**6 new ALLOWED_EVENTS:**
- `cross_org_site_relocate_initiated` (Osiris admin)
- `cross_org_site_relocate_source_released` (source owner)
- `cross_org_site_relocate_target_accepted` (target owner)
- `cross_org_site_relocate_executed` (Osiris admin)
- `cross_org_site_relocate_canceled`
- `cross_org_site_relocate_expired`

Total ALLOWED_EVENTS post-ship: 43 (after task #19) → 49.

**Anchor namespace:** all 6 events anchor at the SOURCE org's primary site_id (the site being moved). The receiving org's auditor walks back via `site_canonical_aliases`.

## Implementation checklist

1. **Migration 277:** `cross_org_site_relocate_requests` table + delete-block trigger.
2. **Migration 278:** `sites.prior_client_org_id` UUID column + extension to `site_canonical_aliases` view (mig 258).
3. **6 new ALLOWED_EVENTS** + lockstep test update.
4. **`cross_org_site_relocate.py` module:** 6 endpoints + sweep loop.
5. **Sweep loop** (60s cadence, heartbeat + EXPECTED_INTERVAL_S calibrated).
6. **Owner-transfer + mfa-revoke interlocks** (Steve mit 3).
7. **BAA-on-file precondition** at target-accept endpoint (Steve mit 5).
8. **Substrate invariant** `cross_org_relocate_chain_orphan` (sev1).
9. **Customer emails** (3 templates).
10. **Feature flag** `CROSS_ORG_RELOCATE_ENABLED` (default false). Endpoint returns 503 with "Feature pending outside-counsel BAA review" until set true.
11. **Tests:** ~30+ covering the 6 Steve threat-model scenarios + state machine transitions + lockstep + chain-orphan invariant.
12. **Frontend:** OUT OF SCOPE — separate task.

## Disposition

**SHIP_AFTER_BAA_COUNSEL_REVIEW.** Round-table 5/5 APPROVE_DESIGN with Maya 2nd-eye green on all 7 dispositions, BUT the feature flag stays false until outside HIPAA counsel signs off on:

- Cross-tenant data handling between source-org BAA and target-org BAA.
- Whether cryptographic chain re-anchoring (Option A staying at source) creates a §164.528 disclosure-accounting gap (audit logs were written under source org; target org's auditor walks them).
- Whether the receiving-org BAA must explicitly include "site relocations from prior orgs" or whether the standard substrate-BAA covers it.

Engineering work + flag-controlled deploy: **READY TO SHIP** ~1-2 days.
Counsel review: **separate, async, multi-day calendar**.

Total ALLOWED_EVENTS post-ship (with feature flag false): 49.
After flag flip: same 49, but events actually fire.

## Outstanding pre-implementation

- [ ] Confirm `sites.client_org_id` is the canonical column (not `sites.partner_id` — partner_id is a separate concept tied to the MSP, not the org-tenant).
- [ ] Engage outside HIPAA counsel asynchronously on the BAA questions above. Provide them with this design doc + the existing BAA template.
- [ ] Verify `site_canonical_aliases` view (mig 258) gracefully handles the new `prior_client_org_id` column without breaking auditor-kit JSON shape.
