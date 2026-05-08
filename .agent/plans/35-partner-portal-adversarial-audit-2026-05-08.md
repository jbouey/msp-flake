# Partner Portal Adversarial Audit — 2026-05-08

**Auditor:** Claude Code (Opus 4.7 1M)
**Base SHA:** `5e985089` (today's printable-artifact sprint head)
**Scope:** Partner portal frontend (4 areas) — login + onboarding,
dashboard + portfolio surfaces, BAA roster + BA Compliance Letter UI,
user/role/transfer + profile/billing.

## Executive summary

- **Buttons / clickables audited:** 67 across 12 routed surfaces +
  1 modal + 1 missing UI (P-F5/P-F6 backend-only).
- **CRITICAL findings:** 7 — 2 orphan navigation actions on the
  hottest dashboard surfaces (Open arrow → 404 fallback), 5 missing
  UI for shipped P-F5 + P-F6 backend endpoints (portfolio attestation,
  BAA roster CRUD ×3, BA Compliance Letter download).
- **MAJOR findings:** 2 — QBR `<a href>` bypasses Session 217 RT31
  rate-limited-download UX rule; PartnerAdminTransferModal not given
  `partnerRole` + `callerUserId` props.
- **MINOR findings:** 9 — accessibility (tablist semantics + aria-
  label on icon-only button), copy drift ("Drifts detected" / "non-
  compliant"), `window.prompt` / `window.confirm` UX scrappy spots,
  silent catch on trigger-checkin + provision-create error paths.
- **CSRF baseline:** 15 tests, all pass before AND after fixes
  (no regressions).
- **Fix-up commits planned:** 4 — one per area where actionable.
  Critical UI gap (P-F5 + P-F6 frontend) is **deferred**: it is a
  full sprint of UI work, not a one-line drift fix. Reported as the
  top finding for next sprint queue.

## CRITICAL findings

### CRIT-1 — `PartnerHomeDashboard.tsx:174` — orphan "Open" button

`navigate('/partner/site/${a.site_id}')` lands on a route that does
not exist. `App.tsx:130-148` only routes `/partner/login | dashboard
| security | users | appliances | audit-log | site/:siteId/topology
| site/:siteId/consent`. The `*` catch-all redirects to PartnerLogin,
so a logged-in admin clicking "Open" on the most-attention client
gets bounced to a re-login page. **This is the partner's primary
triage action and it is broken.**

**Fix:** route to existing surface. Best v1: navigate to
`/partner/dashboard?site=${id}` and have PartnerDashboard scroll to
that site's row in the Sites tab — single state-set, no new route.
Alternative: add a true `/partner/site/:siteId` overview route in a
follow-up sprint. **Shipped fix: route to dashboard with site query
param**, so the partner at minimum lands on the live dashboard.

### CRIT-2 — `PartnerWeeklyRollup.tsx:175` — same orphan

Same `/partner/site/${id}` target. Same bug, same fix.

### CRIT-3..7 — P-F5 + P-F6 backend endpoints with NO frontend UI

The backend ships these 5 endpoints (all RT31 role-gated correctly):

| Endpoint | Method | Role gate | Frontend | Severity |
|---|---|---|---|---|
| `/api/partners/me/portfolio-attestation` | GET | admin | none | CRIT |
| `/api/partners/me/ba-roster` | GET | admin/tech | none | CRIT |
| `/api/partners/me/ba-roster` | POST | admin | none | CRIT |
| `/api/partners/me/ba-roster/{id}` | DELETE | admin | none | CRIT |
| `/api/partners/me/ba-attestation` | GET | admin | none | CRIT |

These were the load-bearing artifacts of today's printable sprint
(P-F5 + P-F6). The backend, attestation chain, audit log, public
verify routes, Jinja2 templates, and 26 tests all ship — but a
partner cannot invoke them through the UI. The current path is
*curl with an API key*, which is acceptable for power users but not
for the 15×/day MSP technician persona round-table targeted.

**Status:** **DEFERRED** — UI is a 200+ line addition with 5 buttons,
3 modals, and a roster table. Cannot be done in a fix-up commit
without product input on placement (own tab? sub-tab inside
"Compliance"? sub-tab inside a new "Attestations" tab?). **Reported
as #1 priority for next sprint.**

## MAJOR findings

### MAJ-1 — `PartnerWeeklyRollup.tsx:152` — QBR `<a href>` bypasses 401/429 UX

Per `CLAUDE.md` "Partner-side mutation role-gating (Session 217 RT31)":
> auditor-kit + similar rate-limited downloads must use JS fetch→blob
> (not `<a href>`) so customers see actionable error copy on 401/429.

The QBR endpoint `/api/partners/me/sites/{id}/qbr` is rate-limited
and PDF-streaming — exact same shape as auditor-kit. An `<a href>`
download fails silently for the customer on 429: browser opens a
new tab, gets JSON, looks broken. **Fixed in this audit:** convert
to `fetch → response.blob() → URL.createObjectURL → click anchor`,
with toast on 401 / 429 / 500.

### MAJ-2 — `PartnerUsersScreen.tsx:404` — modal missing role context

`PartnerAdminTransferModal` accepts `partnerRole` + `callerUserId`
props. PartnerUsersScreen.tsx does not pass them. Effect: defensive
default `isAdmin = false` hides the initiate form even for legitimate
admins. **Backend gates correctly** (`require_partner_role("admin")`
on every state-changing endpoint), so no security leak — but the
modal renders as a passive "no transfer in progress" page even for
the admin who would otherwise initiate. **Fixed in this audit:**
read role + user_id from PartnerContext and pass through.

## MINOR findings

| # | File:line | Issue |
|---|---|---|
| MIN-1 | PartnerHomeDashboard.tsx:236 | "Drifts detected" — Session 199 banned drift in display copy. → "Findings detected" |
| MIN-2 | PartnerDashboard.tsx:1125 | "non-compliant" badge — coach: client portal uses "Failing" (status.ts canon). → use mapping helper |
| MIN-3 | PartnerDashboard.tsx:299 | Welcome-guide icon button has no aria-label, only `title`. Add `aria-label="Show welcome guide"` |
| MIN-4 | PartnerDashboard.tsx:402-545 | Tab buttons missing `role="tab"` + `aria-selected` + `role="tablist"` parent. PartnerLogin's tab pattern does it correctly — partner dashboard's main tabs do not |
| MIN-5 | PartnerDashboard.tsx:751 | Create Code single-provision: failure path is `console.error` only, no visible toast |
| MIN-6 | PartnerDashboard.tsx:889 | Yes (revoke confirm): same — silent failure |
| MIN-7 | PartnerDashboard.tsx:808 | Copy Code button has no success feedback |
| MIN-8 | PartnerOnboarding.tsx:77 | trigger-checkin: silent catch (`Trigger checkin failed silently — non-critical`) — comment intentional but partner doesn't see anything |
| MIN-9 | PartnerUsersScreen.tsx:130, 154-167 | `window.prompt` × 2 + confirm-phrase via prompt — scrappy UX, but functional and gates correctly |

## Cross-cutting patterns

1. **Two parallel CSRF wrappers.** `portalFetch.ts` (postJson/etc)
   and `csrf.ts` (csrfHeaders + buildAuthedHeaders) both work. Newer
   surfaces (PartnerUsersScreen, PartnerAdminTransferModal) use the
   former; older (PartnerDashboard, PartnerInvites, PartnerBilling)
   use the latter. **Both ship CSRF correctly** — no security finding,
   but consistency would help next-comer engineers.

2. **Two patterns for "scary action" confirmation.** PartnerUsersScreen
   uses `window.prompt` for both reason + confirm-phrase. PartnerDashboard
   uses inline confirm spans (Yes/No). PartnerBilling uses
   `window.confirm`. Three patterns, one platform. Coach DRIFT.

3. **Orphan navigation:** the `/partner/site/:siteId` fall-through is
   identical between PartnerHomeDashboard and PartnerWeeklyRollup —
   exactly the kind of class regression that suggests both files were
   written assuming an unbuilt route. Audit class: every `navigate(`/
   partner/...`)` should match a route in `App.tsx`.

## Round-table 2nd-eye verdict per area

### Area 1 — Login + onboarding

- **Steve:** PASS. CSRF + disabled-during-submit + spinner + clean
  error blocks. MFA flow shipped with backup-code escape hatch.
  Magic-link uses POST + body (not URL log leak). OAuth providers
  feature-flagged correctly.
- **Maya:** PASS. No state-changing GET, no missing CSRF. MFA token
  scoped to a one-shot exchange, not a session. Signup is rate-
  limited and pending-approval gated (server-side).
- **Carol:** PASS. No banned legal words. Wording is plain
  ("Sign in", "Request a partner account", "Verification Code").
- **Coach:** PASS. Tab pattern uses `role="tab"` + `aria-selected`
  — should be the canon for the rest of the partner portal.

### Area 2 — Dashboard + portfolio surfaces

- **Steve:** DRIFT — 2 orphan navigations + 1 download-via-anchor
  + missing tablist semantics on the main dashboard tabs.
- **Maya:** PASS — every mutation has CSRF; CSRF baseline 15/15.
- **Carol:** DRIFT — "Drifts detected" + "non-compliant" violate
  Session 199 / status.ts canon.
- **Coach:** DRIFT — coach finds drift against the F-series shipped
  pattern: client portal uses `getScoreStatus()` for every score
  display + `cleanAttentionTitle()` for backend titles. Partner
  surfaces should mirror.

### Area 3 — BAA roster + BA Compliance Letter UI (P-F6)

- **Steve:** FAIL — backend ships, frontend is empty. The user
  cannot click anything to invoke these endpoints. Either the
  power-user-curl-only posture is intentional (then docs must say
  so) or this is a sprint-1 UI gap.
- **Maya:** PASS (vacuously) — there's nothing to attack.
- **Carol:** N/A — no UI copy to scrub.
- **Coach:** FAIL against today's sprint goal. Today shipped 9
  printable artifacts; 5 of them have no portal entry. Next sprint
  must close.

### Area 4 — User/role/transfer + profile/billing

- **Steve:** DRIFT — Modal missing role context (MAJ-2), three
  parallel "scary action confirm" patterns. Otherwise solid:
  portalFetch on every mutation, role/deactivate gated by reason +
  confirm-phrase + DB trigger backstop.
- **Maya:** PASS — every mutation has CSRF, role gates match
  backend (admin for state, admin/tech for read on most),
  attestations are written for every role-change.
- **Carol:** PASS — minor "prevents accidental zero-admin" wording
  is benign (operator help text, not customer compliance claim).
- **Coach:** DRIFT — `window.prompt`-driven confirm-phrase pattern
  diverges from PartnerDashboard's inline confirm + design-system
  patterns elsewhere.

## Fix-up commits planned

1. `audit(area-2): fix orphan /partner/site navigation +
   QBR fetch→blob + drift copy` (CRITICAL CRIT-1, CRIT-2; MAJOR
   MAJ-1; MINOR MIN-1, MIN-2, MIN-3, MIN-4 partial).
2. `audit(area-4): pass partnerRole + callerUserId to admin-
   transfer modal` (MAJOR MAJ-2).
3. (deferred) `audit(area-3): partner BAA roster + portfolio
   attestation UI` — too large for a fix-up commit; reported as
   sprint-1 priority.
4. (post-CSRF-test re-run + final commit) — audit deliverables.

## CSRF gate baseline before/after

- **Before:** 15 / 15 passing.
- **After fix-up commits:** 15 / 15 passing.
- **Ratchet baseline (CLAUDE.md says drive to 0):** unchanged. No
  new raw fetches added; every fix uses portalFetch or buildAuthedHeaders.

## Open questions for the user

1. Should P-F5 + P-F6 UI be its own dashboard tab ("Attestations") or
   nested inside an existing tab ("Compliance" or "Agreements")? This
   blocks the next-sprint UI work.
2. Should the 3 confirm-modal patterns (window.prompt / window.confirm
   / inline) be unified to one shared `<DangerousActionModal>`
   component? If yes, that's a separate cleanup PR.
3. The `/partner/site/:siteId` overview route — should it exist as a
   first-class page (drill-down with appliances, agents, recent
   activity) or stay deflected to PartnerDashboard with site preselect?
   Today's CRIT-1/CRIT-2 fix is the deflect-to-dashboard cheap path.

## Lessons-candidate for memory

`feedback_partner_portal_navigation_orphan_class.md` — every
`navigate('/partner/...')` MUST match a `<Route>` in `App.tsx`.
Today found 2 orphan-action sites in 2 different files; the class
suggests writing both files in the same sprint without confirming
the App.tsx route table. Pin a CI gate that scrapes `navigate(`/
partner/...`)` strings and intersects with the App.tsx Route paths
under `<PartnerRoutes>` — fail on any orphan.

Also: `feedback_p_series_must_ship_with_ui.md` — every P-F-series
backend endpoint that targets a customer-visible PDF artifact must
ship with at least a "click to download" button. P-F5 + P-F6 today
shipped backend-only and would have rotted in production.
