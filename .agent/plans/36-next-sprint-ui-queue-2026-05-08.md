# Next-Sprint UI Queue — Partner Portal Follow-up (2026-05-08)

**Source:** Partner-portal adversarial audit `.agent/plans/35-partner-portal-adversarial-audit-2026-05-08.md` (commit `6f305d2f`).
**Deferred from today's sprint:** 5 CRITICAL UI gaps (P-F5 + P-F6 backend endpoints with no frontend) + 3 product-design decisions answered below + queued.

This document is the engineering queue for the next sprint. Each item carries: recommendation, rationale, scope estimate, coach-gated build pattern.

---

## Decision 1 — P-F5 + P-F6 UI placement

**Question:** P-F5 (Partner Portfolio Attestation) and P-F6 (BA Compliance Attestation + downstream-BAA roster) shipped backend-only today. Where do their UIs live in the partner portal nav?

**Decision:** **New top-level tab "Attestations"** (NOT nested under "Compliance" or "Agreements").

**Rationale:**
- P-F5 + P-F6 + the future Quarterly Summary (F3 sister UI) are a coherent customer-facing artifact bucket. Partners issue these for **sales** (Greg's website trust badge), **auditor** (Tony's three-party-BAA-chain visibility), **insurance underwriting** (Maria's wall cert via the F1 series — though that's owner-side). They are NOT operational drift-monitoring; nesting under "Compliance" muddles operational compliance with attestation paperwork.
- Nesting under "Agreements" fits P-F6 (BAA roster) but NOT P-F5 (portfolio attestation aggregates sites + appliances + bundle counts; not agreement metadata).
- Adding a top-level tab matches the partner mental model: "Sites · Appliances · Users · Audit Log · **Attestations** · Billing · Settings."
- Single tab with 2 inner sub-views (cards on the same page): **Portfolio Attestation** (P-F5 download + roster of past attestations) and **BA Compliance** (P-F6 BAA roster CRUD + BA Compliance Letter download).

**Scope estimate:** ~2-3 days
- New route: `/partner/attestations`
- New page: `PartnerAttestations.tsx` with two top-of-page cards (Portfolio + BA Compliance)
- Portfolio card: "Download latest" button → `GET /api/partners/me/portfolio-attestation/letter.pdf` (binary blob); "History" expander listing past attestations from `partner_portfolio_attestations`
- BA Compliance card: "Download latest" button + roster sub-table with add-BAA modal + revoke action; calls existing P-F6 endpoints (POST/GET/DELETE `/me/ba-roster` + GET `/me/ba-attestation`)
- Empty-state copy: "No attestations issued yet. Issue your first portfolio attestation."
- Coach gate: 17-dim verdict in commit body; banned-words on copy; CSRF posture via `fetchApi`; 401/429 handling per Session 217 RT31 rate-limited-download UX rule (extract retry-after header → toast).

**Companion CI gate:** add `tests/test_partner_button_to_route_audit.py` that intersects every `navigate('/partner/...')` string in `frontend/src/partner/**` against the route table in `App.tsx`. Catches the orphan-navigation class structurally so future commits can't reintroduce it.

---

## Decision 2 — Confirm-modal pattern unification

**Question:** Three confirm patterns coexist in the partner portal:
- `PartnerUsersScreen` uses `window.prompt` (×2 — invite-confirm + delete-confirm).
- `PartnerDashboard` uses inline-confirm-banner.
- `PartnerBilling` uses `window.confirm`.

Worth a `<DangerousActionModal>` cleanup PR?

**Decision:** **Yes — build `<DangerousActionModal>` and migrate all three call sites in one PR.**

**Rationale:**
- `window.prompt` and `window.confirm` are browser-native dialogs that block the entire page, can't be styled, can't be accessibility-tuned (no proper aria role, no keyboard nav match to the design system), and on some browsers (Safari iframes, etc.) silently fail.
- Three patterns × one platform = drift; future engineers add a fourth pattern when they hit a fifth danger-action because there's no canonical example to copy.
- The pattern matters because **danger actions are exactly where a misclick is catastrophic** (delete user, revoke BAA, transfer ownership, cancel billing). Visual + interaction consistency is a customer-trust contract.

**Scope estimate:** ~1-2 days
- Build `frontend/src/components/composed/DangerousActionModal.tsx` with two tiers:
  - **Tier-1 (irreversible):** type-to-confirm input matching `{action_target_label}` (e.g. type "DELETE jrelly@example.com" to confirm). Used for delete-user, revoke-BAA, cancel-billing.
  - **Tier-2 (reversible-with-effort):** simple "Are you sure?" with primary-danger button. Used for invite-user (since unsending is just delete-then-invite).
- Carol-approved copy: "This action will permanently {verb} {target}. This cannot be undone." for tier-1; banned-words-safe.
- Steve a11y: `role="dialog"`, `aria-labelledby`, focus-trap, ESC-to-cancel, ENTER-to-submit-only-when-typed-confirm-matches.
- Migrate the 3 call sites; delete the `window.prompt` / `window.confirm` calls.
- New tests: `test_dangerous_action_modal.tsx` (tier-1 typed-confirm gate, ESC cancels, focus trap).

---

## Decision 3 — `/partner/site/:siteId` route shape

**Question:** Today's audit fix deflects orphan `navigate('/partner/site/${id}')` to `/partner/dashboard?site=<id>`. Should this become a first-class drill-down page (per-site appliances + agents + recent activity + drift summary) or stay deflected?

**Decision:** **Keep deflection for now; queue first-class page for the SECOND-NEXT sprint** (after Attestations + DangerousActionModal land).

**Rationale:**
- Deflection works (PartnerDashboard already filters by site preselect via the `?site=` query). UX is not broken; it's just not the dedicated drill-down some users would expect.
- A first-class per-site drill-down is a meaningful new surface (~3-4 days) that overlaps with existing PartnerDashboard query logic. Building it well requires deciding: (a) is it a separate route with its own breadcrumb + URL? (b) is it a side-panel slide-over? (c) is it just a deeper view in PartnerDashboard? — these are product-design questions that benefit from a quick round-table BEFORE engineering starts.
- Today's sprint priority is closing the P-F5+P-F6 UI gaps + cleaning the danger-action class. Per-site drill-down is a "nice to have" once those land.

**Companion:** add to next-next-sprint discussion the question "do we want partner-portal site drill-down to mirror client-portal site drill-down?" (which exists today via `/client/sites/:id`). Symmetry + reuse of the same composed components would be ideal.

---

## Other deferred audit items (from `35-...md`)

### Scrappy-UX class — 4 MINOR findings

The audit identified 4 MINOR findings deferred from the fix-up commits because they need product input or are spread across files:
1. **Silent catch on `trigger-checkin` + `provision-create` error paths** (PartnerDashboard) — error is logged to console but no user-visible toast. Fix: add error toast via the design-system Toast composed component.
2. **PartnerBilling cancel-subscription flow uses `window.confirm`** — covered by Decision 2 above.
3. **PartnerUsersScreen invite-flow `window.prompt` ×2** — covered by Decision 2 above.
4. **`PartnerHomeDashboard` "View all" button on the alerts feed** — currently links to `/partner/audit-log` but the audit log is per-action, not per-alert. Either fix the link to point at a real per-alert filtered view OR remove the button. Needs product input on whether per-alert detail page exists in the roadmap.

These 4 should land in the same PR as Decision 2 if their fix overlaps, or in a follow-up "scrappy UX cleanup" PR.

---

## Summary — proposed sprint-N+1 sequence

| # | Item | Scope | Blocker |
|---|------|-------|---------|
| 1 | **PartnerAttestations** tab (P-F5 + P-F6 UI) | 2-3 days | none — backend endpoints already shipped today |
| 2 | **DangerousActionModal** + migrate 3 call sites | 1-2 days | none |
| 3 | **Scrappy-UX cleanup** (4 MINOR findings) | 0.5 day | needs product call on item #4 |
| 4 | **CI gate** for partner-portal route-orphan class | 0.5 day | none — pure new test file |
| — | (deferred) Partner per-site drill-down (`/partner/site/:siteId` first-class) | 3-4 days | Sprint N+2; needs product round-table |

Total sprint-N+1 estimated scope: **4-6 engineer-days** + product round-table inputs on item #3 #4 placement and the per-site drill-down design.

---

## Companion artifacts

- `.agent/plans/35-partner-portal-adversarial-audit-2026-05-08.md` — audit findings, button-by-button breakdown, fix-up commit references.
- `audit/partner-portal-buttons-2026-05-08.csv` — 67-row registry of every clickable in the partner portal (re-run before/after each UI sprint to catch new orphans).
- `feedback_consistency_coach_pre_completion_gate.md` — coach 17-dim gate that next sprint's commits must carry.
- `feedback_parallel_fork_isolation.md` — worktree isolation rule for any parallel-fork orchestration during sprint-N+1.
