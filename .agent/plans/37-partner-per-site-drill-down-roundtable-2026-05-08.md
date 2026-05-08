# Sprint-N+2 round-table prep — Partner per-site drill-down (`/partner/site/:siteId`)

**For:** Sprint-N+2 design round-table.
**Source:** Plan 36 Decision 3 (deferred); Plan 35 audit CRIT-1 + CRIT-2 (orphan navigations to a route that didn't exist; today deflected to `?site=` query).
**Date:** 2026-05-08.
**Format:** 4-voice round-table (Steve / Maya / Carol / Coach) verdict per major design decision. Each design choice gets explicit APPROVE / DENY / NEEDS-DISCUSSION before scope is locked.

---

## Context

The 2026-05-08 partner-portal adversarial audit (`.agent/plans/35`) found two orphan `navigate('/partner/site/${siteId}')` calls — buttons that landed on a route that didn't exist, bouncing the user via the `*` catch-all to PartnerLogin. Today's fix-up commit (`2207bfcc`) deflected those to `/partner/dashboard?site=<id>` — cheap-path fix. The deferred question (Decision 3 in plan 36): should `/partner/site/:siteId` become a first-class drill-down page, mirroring the admin-side `/sites/:siteId` SiteDetail (`pages/SiteDetail.tsx`, 823 lines, 9 sub-routes)?

**Why now:** the route-orphan CI gate (`tests/test_partner_button_to_route_audit.py`, sprint-N+1) catches future orphans structurally, but today's deflection-to-query-param is a ~stopgap. A real per-site partner drill-down is the right long-term shape; we just deliberately deferred it from sprint-N+1 to gather product input.

---

## Design decisions to round-table

### D1 — Route shape

**Question:** Separate route (`/partner/site/:siteId`) vs. side-panel slide-over vs. deeper PartnerDashboard view?

**Recommendation:** **Separate route** (`/partner/site/:siteId`).

**Rationale:**
- Bookmarkable URLs (Lisa-the-MSP-MD wants to send "look at this site" links to her techs in chat).
- Breadcrumb-friendly (admin SiteDetail uses breadcrumbs; partner should too).
- Reuses the admin SiteDetail component-pattern as a reference (pages/SiteDetail.tsx).
- Matches the existing partner-portal `site/:siteId/topology` + `site/:siteId/consent` precedent — those routes already exist; this fills in the missing parent.
- Side-panel slide-over breaks back-button (URL doesn't update); deeper PartnerDashboard view bloats already-busy dashboard.

**Round-table:**
- **Steve:** APPROVE. Separate route is the only choice that's bookmarkable + back-button-friendly + matches admin parity. Slide-over is a UX dead-end on a multi-tab dashboard.
- **Maya:** APPROVE. Separate route makes the auth-gate explicit (route-level `require_partner_role`); slide-over hides it. Reduces accidental-leak surface.
- **Carol:** APPROVE. Bookmarkable URLs let partners attach a site link to support tickets / clinic-owner emails — a customer-iterated workflow.
- **Coach:** APPROVE. Sibling parity with admin `/sites/:siteId`. Future reuse of composed components is cheaper.

**Verdict: ALL APPROVE.** Lock as separate route.

---

### D2 — Component composition: copy admin SiteDetail or build from scratch?

**Question:** Admin SiteDetail (`pages/SiteDetail.tsx`) is 823 lines + uses 14 admin-grade composed components (GlassCard, SiteComplianceHero, SiteActivityTimeline, ApplianceCard, multiple modals — Add Credential / Edit Site / Move Appliance / Transfer / Decommission / Portal Link). Should the partner version reuse those components or build a thinner partner-specific component set?

**Recommendation:** **Selective reuse — pull the read-only/operational pieces, OMIT the admin-only mutations.**

**Pull (read-only or partner-scoped operational):**
- `SiteHeader` — clinic name + status badge.
- `SiteComplianceHero` — score + framework badges. Reuses `getScoreStatus()` from `constants/status.ts`.
- `EvidenceChainStatus` — Ed25519 signing + chain head + OTS coverage. Read-only.
- `OnboardingProgress` — provisioning state (matches partner mental model).
- `ApplianceCard` — per-appliance read-only summary.
- `SiteActivityTimeline` — recent events; partner sees its own scope.

**Omit (admin-only / org-wide):**
- `Move Appliance` — admin-only operation; partners shouldn't move appliances between orgs.
- `Transfer Appliance` — same; admin-only.
- `Decommission` — admin-only; partners can't decommission.
- `Edit Site` — partner-side site-edit lives in PartnerDashboard's SiteRow inline edit; not duplicated here.
- `Portal Link` — admin's break-glass mint; partners don't have this capability.
- `Add Credential` modal — partner-side credential management is the existing `PartnerComplianceSettings` flow; not re-added here.

**Add (partner-specific):**
- "Open client portal" button — magic-link mint via `POST /api/partners/me/sites/:id/client-portal-link` (existing endpoint per audit; verify path).
- "Issue Compliance Letter" deep-link — opens F1 issuance modal in client_portal context (or opens a new tab if cross-portal).
- "Issue Wall Cert" deep-link — same shape.
- Per-site "Recent privileged-access events" list — reads `admin_audit_log` filtered by `target='site:<siteId>'`. Partners see their own scope.

**Round-table:**
- **Steve:** APPROVE-with-reservation. Partial component reuse is correct, but "selective copy" creates drift risk: when admin SiteHeader gets new props for an admin feature, partner-side may break silently. Mitigation: partner-side wraps admin components and ONLY passes known-good props; bump a CI gate that asserts partner-side imports admin composed components only by name (no inline copies).
- **Maya:** APPROVE. Read-only components are safe to share; mutations omitted by design closes the auth-bypass class. Verify each shared component does NOT embed an admin-mutation button-link inside.
- **Carol:** APPROVE. Partner copy may differ ("Clinic" vs. "Site"); ensure shared components accept a `presenter` prop or use the existing constants/copy.ts pattern. Don't fork copy.
- **Coach:** APPROVE-with-reservation. Need a CI gate (Sprint-N+3 maybe?) that asserts partner-side `pages/PartnerSiteDetail.tsx` imports DON'T pull in admin-only modals (Move/Transfer/Decommission/PortalLink). Sprint-N+1 route-orphan gate is a sibling pattern to copy.

**Verdict: ALL APPROVE WITH 2 RESERVATIONS** — bump 2 CI gates onto Sprint-N+3 backlog:
1. Component-import allowlist for partner-side reuse.
2. No-inline-copy assertion (forbid duplicating admin SiteHeader copy into partner-side; force shared component).

---

### D3 — Sub-routes: how deep does the partner drill-down go?

**Question:** Admin has 9 sub-routes per site (`/sites/:id/frameworks`, `/workstations`, `/workstations/rmm-compare`, `/agents`, `/devices`, `/protection`, `/protection/:profileId`, `/drift-config`). Partner already has 2 (`topology`, `consent`). Which others are partner-relevant?

**Recommendation:** **Add 3 partner-relevant sub-routes; keep the rest admin-only.**

**Add (partner sees):**
- `/partner/site/:siteId/agents` — list of Go workstation agents for this site. Partners need to see "is the agent installed + reporting?" for support calls. Reuses `SiteGoAgents.tsx`.
- `/partner/site/:siteId/devices` — list of devices netscan'd at this site. Partners need this for inventory verification. Reuses `SiteDevices.tsx`.
- `/partner/site/:siteId/drift-config` — partner-tier drift-monitoring configuration. Partners DO configure this today via PartnerDriftConfig → reuse the existing config but scoped to the per-site context.

**Keep admin-only (partner does NOT see):**
- `/sites/:id/frameworks` — framework selection is org-level admin; partners propose, admin approves.
- `/workstations/rmm-compare` — admin-grade fleet comparison; not partner-relevant.
- `/protection` + `/protection/:profileId` — protection profiles are org-level policy; admin scope.

**Existing partner-only (keep):**
- `/partner/site/:siteId/topology` — partner-grade mesh visibility.
- `/partner/site/:siteId/consent` — partner manages consent state.

**Round-table:**
- **Steve:** APPROVE. 3 added + 2 existing = 5 sub-routes = manageable surface. Reusing admin pages as starting points keeps engineering scope sane.
- **Maya:** APPROVE. Each sub-route's auth gate is `require_partner_role("admin", "tech")` for read; mutations on each are individually gated. Partners never see admin-only routes — RLS at the API layer is the canonical defense.
- **Carol:** APPROVE. No customer-facing copy changes here; sub-route labels follow the existing partner-portal naming.
- **Coach:** APPROVE. Sibling structure with admin `/sites/:id` matches; partial subset is the right shape.

**Verdict: ALL APPROVE.** Lock 3 added + 2 existing sub-routes.

---

### D4 — Cross-portal entry point: partner → client-portal magic link

**Question:** Should the partner per-site drill-down include a "Open this site as the client" button that mints a short-lived magic link to the client portal scoped to this site?

**Recommendation:** **YES, but flag it as a sensitive break-glass.**

**Rationale:**
- Lisa-the-MSP-MD's most common "I need to debug something" workflow today: ssh into the appliance + curl the appliance API. A magic-link-to-client-portal would let her see the site as the clinic owner sees it — same data, same UI, same evidence view. Faster + safer than ssh.
- The endpoint already exists in form (`POST /api/partners/me/sites/:id/client-portal-link` per the audit's button-registry CSV — verify exact path).
- Sensitive because (a) partner sees client-side data with elevated context, (b) the magic link is short-lived but could be screen-shared or copied. UX should make the sensitivity visible.

**UX shape:**
- Button labeled "Open client portal as client" in a "Cross-portal access" card.
- Click → modal with a copy-to-clipboard URL + 15-min expiry warning + "this action is logged to admin_audit_log" disclaimer.
- Use **DangerousActionModal tier-2** (sprint-N+1 component) — typed-confirm not needed since the action is reversible (the magic link expires; nothing destructive). Just a clear "are you sure?" with the disclaimer.

**Round-table:**
- **Steve:** APPROVE. Reuses DangerousActionModal tier-2; existing endpoint; admin_audit_log already records the magic-link mint event. Low engineering cost.
- **Maya:** APPROVE-with-conditions. (1) The magic link MUST be single-use (already shape per existing client-portal-link endpoint — verify). (2) admin_audit_log row MUST capture (partner_user_id, client_org_id, site_id, magic_link_id, ip_address, ttl_seconds). (3) UI MUST tell the user "this is logged" before mint, NOT only after. (4) Add a CI gate: every partner→client-portal mint emits a chain-of-custody attestation event in `privileged_access_attestation::ALLOWED_EVENTS` — check whether `partner_client_portal_link_minted` is already there (Session 216-class anchor convention).
- **Carol:** APPROVE-with-revision. Modal copy: "Open this site's client portal as the practice owner. This action is logged to the audit ledger and the link expires in 15 minutes." NOT "as the client" (Carol prefers explicit "as the practice owner" — the term "client" is partner-jargon that leaks).
- **Coach:** NEEDS-DISCUSSION. Is this an attested chain event (parallels P-F6's BAA-roster events) or an audit-log-only event? If chain: design needs a `partner_client_portal_link_minted` ALLOWED_EVENTS entry + anchor convention. If audit-log-only: simpler but less auditable. RECOMMEND: chain event, anchored at `partner_org:<partner_id>` (Session 216 convention). Round-table to confirm.

**Verdict: 3 APPROVE + 1 NEEDS-DISCUSSION on chain-attestation shape.** Sprint-N+2 must answer Coach's question before engineering starts.

**RESOLVED 2026-05-08 — chain-attested via ALLOWED_EVENTS.** User decision locked. Sprint-N+2 engineering scope additions:
- Add `partner_client_portal_link_minted` entry to `privileged_access_attestation::ALLOWED_EVENTS` (3-list lockstep with `fleet_cli.PRIVILEGED_ORDER_TYPES` MUST stay unchanged — this is a partner-action event, NOT a fleet order; verify via `test_privileged_chain_allowed_events_lockstep.py` adjusts for the new event).
- Anchor namespace `partner_org:<partner_id>` (Session 216 convention).
- Mint endpoint writes an Ed25519-signed `compliance_bundles` attestation row at the partner_org anchor (chain-anchored to the partner's prior attestation hash).
- admin_audit_log row alongside (NOT instead of) — both layers fire, parallel structure with the privileged_access_attestation pattern in P-F6.
- DangerousActionModal tier-2 invocation BEFORE the mint, copy revised per Carol: "Open this site's client portal as the practice owner. This action is logged to the cryptographic audit chain and the link expires in 15 minutes."
- Maya conditions a/b/c/d remain in force. Single-use magic link (verify endpoint shape); admin_audit_log row carries (partner_user_id, client_org_id, site_id, magic_link_id, ip_address, ttl_seconds); chain entry carries the attestation_hash + signature.

D4 engineering scope adjustment: **+0.25 day** for the ALLOWED_EVENTS entry + chain-attestation row write + lockstep test adjustment. Total Sprint-N+2 scope: **3.25-4.25 engineer-days**.

---

### D5 — Activity timeline: scope + retention

**Question:** Partner sees a "recent activity" timeline on the per-site page. What scope (whose activity) + what retention (last 7 days? 30? 90?)?

**Recommendation:** **Last 30 days, partner-scope events only.**

**Scope:** events visible to the partner:
- All `execution_telemetry` rows for this site (drift checks + remediation outcomes).
- All incidents created/resolved at this site.
- All `fleet_orders` issued by THIS partner (or accepted by appliances at this site).
- All `admin_audit_log` events where `target` matches this site_id AND `actor_kind = partner_user`.
- Privileged-access chain events for this site that were partner-initiated.

**Out of scope:** other partners' actions, admin-side actions invisible to partners, client-side actions (the practice's own audits are client-portal-scoped).

**Retention:** 30 days inline; older events accessible via "Load more" with a 90-day backstop. Beyond 90 days → "consult auditor kit" (the chain has it; UI doesn't surface beyond the operational window).

**Round-table:**
- **Steve:** APPROVE. 30-day window matches the F3 quarterly-summary aggregation default (compliance_score window_days). Sibling parity.
- **Maya:** APPROVE. Scope filters at the API layer via RLS + `partner_id = current_setting('app.current_partner')` — partner only sees own + practice activity, never other partners'.
- **Carol:** APPROVE. "30 days" is a clear retention window; matches the existing client-portal ClientReports posture.
- **Coach:** APPROVE. SiteActivityTimeline (admin component) already has a `windowDays` prop — reuse it with windowDays=30.

**Verdict: ALL APPROVE.** Lock 30-day window + partner-scoped events.

---

## Summary — Sprint-N+2 design lock

| Decision | Status | Notes |
|---|---|---|
| D1 — Separate route | ✅ ALL APPROVE | `/partner/site/:siteId` |
| D2 — Component composition | ✅ ALL APPROVE w/ 2 reservations | Selective reuse; CI gates pushed to N+3 |
| D3 — Sub-routes | ✅ ALL APPROVE | 3 added (agents/devices/drift-config) + 2 existing (topology/consent) |
| D4 — Cross-portal magic link | ✅ ALL APPROVE (D4 resolved 2026-05-08) | Chain-attested via ALLOWED_EVENTS; user decision locked |
| D5 — Activity timeline | ✅ ALL APPROVE | 30 days + partner-scoped |

**Engineering scope estimate (post-round-table-answer):**
- New page `pages/PartnerSiteDetail.tsx` ~600 lines (mirrors pattern from SiteDetail at ~50% scope).
- New routes wired in App.tsx (1 parent + 3 sub-routes).
- 3 sub-route components — mostly imports of existing admin pages with partner-context wrappers.
- DangerousActionModal tier-2 invocation for D4 magic-link mint.
- If D4 is chain-attested: backend gets `partner_client_portal_link_minted` in ALLOWED_EVENTS + anchor convention + an attestation row write at mint time.
- Tests: 25+ vitest source-shape gates + the existing route-orphan + header-parity gates run as regression.

**Total: 3-4 engineer-days.**

**Per-gate round-table required at Sprint-N+2 build time** per `feedback_round_table_at_gates_enterprise.md` — ALL 4 voices must APPROVE at each phase (route+shell, component composition, each sub-route, magic-link flow, tests, final pre-commit).

---

## D4 RESOLVED — chain-attested

User decision 2026-05-08: **chain-attested via ALLOWED_EVENTS**. Coach + Maya verdicts locked in. Sprint-N+2 engineering can start.

---

## Companion artifacts

- `.agent/plans/35-partner-portal-adversarial-audit-2026-05-08.md` — audit findings.
- `.agent/plans/36-next-sprint-ui-queue-2026-05-08.md` — Sprint-N+1 plan; Decision 3 deferred to here.
- `mcp-server/central-command/frontend/src/pages/SiteDetail.tsx` — admin-side reference (823 lines).
- `feedback_round_table_at_gates_enterprise.md` — gate structure for Sprint-N+2 build.
- `audit/partner-portal-buttons-2026-05-08.csv` — re-run after Sprint-N+2 to confirm zero new orphans.
