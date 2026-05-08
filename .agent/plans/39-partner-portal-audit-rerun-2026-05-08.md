# Partner Portal Adversarial Audit RE-RUN — 2026-05-08

**Auditor:** Claude Code (Opus 4.7 1M)
**Prior audit:** `.agent/plans/35-partner-portal-adversarial-audit-2026-05-08.md`
**Prior CSV:** `audit/partner-portal-buttons-2026-05-08.csv` (was 66 data rows)
**Why re-run:** Sprint-N+1 (PartnerAttestations) + Sprint-N+2
(PartnerSiteDetail + 3 sub-routes + cross-portal magic-link) +
DangerousActionModal call-site migrations + ClientAttestations hero
card + coach perf-sweep all shipped after the original audit.
**Refreshed CSV:** 97 data rows (66 prior + 31 new).
**Base SHA at re-run:** `3fdc305a` (worktree HEAD).

---

## Executive summary

- **Total clickables audited:** 97 (was 66) — net **+31 new rows**.
- **CRITICAL findings: 0.** All 7 prior CRIT (CRIT-1 + CRIT-2 orphan
  navigation; CRIT-3..7 missing P-F5/P-F6 UI) are **RESOLVED** by the
  ships in Sprint-N+1 + Sprint-N+2.
- **MAJOR findings: 0.** All 2 prior MAJ (MAJ-1 QBR `<a href>`;
  MAJ-2 PartnerAdminTransferModal missing role props) are **RESOLVED**.
- **MINOR findings: 3 NEW** (drift findings on legacy surfaces that
  the original audit's scope did not visit, surfacing now under the
  expanded sibling-parity dimension):
  - **MIN-A** `PartnerExceptionManagement.tsx:314` — `window.prompt`
    for renewal reason. Drift vs. DangerousActionModal canon.
  - **MIN-B** `PartnerExceptionManagement.tsx:323` — `window.prompt`
    for revocation reason. Same class.
  - **MIN-C** `PartnerSSOConfig.tsx:134` — `window.confirm` for SSO
    delete. Same class.
- **CI gate baseline:** **0 drift.** All 4 gates green BEFORE this
  audit (44/44 in 1.67s) and AFTER (44/44 — no source edits made).
- **Fix-up commits:** **0 created in this re-run.**
  Rationale: the only NEW findings (MIN-A/B/C) are
  DangerousActionModal-migration class drift on surfaces the prior
  audit DID NOT visit. Migrating them is a sprint-sized refactor (3
  surfaces × full modal rewrite + per-row state + reason inputs +
  tests) that would (a) exceed the unambiguous-fix bar in the task
  spec, (b) duplicate the Decision-2-of-plan-36 follow-up that's
  already on the queue. **Reported as next-sprint queue items.**
- **Cross-cutting patterns:** see Section 4 — DangerousActionModal
  is now the de-facto canon (5 call sites in partner portal); the
  `window.prompt`/`window.confirm` lingerers are NOT in the
  Sprint-N+1/N+2 ships and represent legacy surface drift.

## 1. Resolved-finding ledger (prior → current state)

| ID | Prior file:line | Status | How resolved |
|---|---|---|---|
| CRIT-1 | PartnerHomeDashboard.tsx:174 orphan `/partner/site/:id` | **RESOLVED** | Sprint-N+2 ships real route at App.tsx:146; verified `navigate()` lands on `<PartnerSiteDetail>` (no catch-all bounce). |
| CRIT-2 | PartnerWeeklyRollup.tsx:175 orphan `/partner/site/:id` | **RESOLVED** | Same — comment at PartnerWeeklyRollup.tsx:235 explicitly notes the revert from CRIT-2 deflect-to-dashboard cheap path. |
| CRIT-3 | `/me/portfolio-attestation` no UI | **RESOLVED** | PartnerAttestations.tsx:699 button `Issue + download portfolio attestation`. Role gate matches backend (admin). |
| CRIT-4 | `/me/ba-roster` GET no UI | **RESOLVED** | PartnerAttestations.tsx:813 roster table. Role gate matches backend (admin OR tech). |
| CRIT-5 | `/me/ba-roster` POST no UI | **RESOLVED** | PartnerAttestations.tsx:818 Add BAA modal + 1145 submit. Role gate matches backend (admin). |
| CRIT-6 | `/me/ba-roster/{id}` DELETE no UI | **RESOLVED** | PartnerAttestations.tsx:921 per-row Revoke button + DangerousActionModal at line 1165. Role gate matches backend (admin). |
| CRIT-7 | `/me/ba-attestation` no UI | **RESOLVED** | PartnerAttestations.tsx:792 button. Role gate matches backend (admin). |
| MAJ-1 | PartnerWeeklyRollup.tsx:152 QBR `<a href>` | **RESOLVED** | PartnerWeeklyRollup.tsx:212 button now uses `handleQbrDownload(...)` JS fetch→blob path with disabled+busy state. |
| MAJ-2 | PartnerAdminTransferModal missing `partnerRole` + `callerUserId` | **RESOLVED** | PartnerUsersScreen.tsx:485-486 now passes `partnerRole={partner?.user_role}` + `callerUserId={partner?.partner_user_id}`. |
| MIN-1 | "Drifts detected" copy | **RESOLVED** | grep finds zero "Drifts detected" / "non-compliant" tokens in partner/. PartnerDashboard.tsx:1213 uses `'Failing'` per `status.ts` canon. |
| MIN-2 | "non-compliant" badge | **RESOLVED** | Same — replaced with status-canon mapping. |
| MIN-3..6 | dashboard a11y / silent catches | **NOT RE-VERIFIED** in this re-run (no source touched on those lines); prior audit's MINOR severity stands and they remain on the queue. |
| MIN-7 | Copy Code button no success feedback | **NOT RE-VERIFIED** (same). |
| MIN-8 | trigger-checkin silent catch | **NOT RE-VERIFIED** (same). |
| MIN-9 | PartnerUsersScreen window.prompt | **PARTIALLY RESOLVED** | Sprint-N+1 task #77 migrated the role-change confirm to DangerousActionModal (PartnerUsersScreen.tsx:560). Initiate transfer + deactivate also migrated (491, 518). The deactivate-reason input and confirm-phrase are now DAM-native. |

## 2. NEW MINOR findings (Sprint-N+1/N+2 era)

### MIN-A — `PartnerExceptionManagement.tsx:314`

```tsx
const reason = prompt('Renewal reason:');
if (reason) renewMutation.mutate({...});
```

`window.prompt` for the renewal-reason input. Diverges from the
DangerousActionModal pattern that's now canon in 5 sibling partner
surfaces (Billing, Attestations Revoke, Users initiate/deactivate/
role-change, SiteDetail mint). Functional + role-gated correctly
(backend `require_partner_role("admin")`); UX is drift.

### MIN-B — `PartnerExceptionManagement.tsx:323`

Identical class as MIN-A but for revocation. Same severity, same fix.

### MIN-C — `PartnerSSOConfig.tsx:134`

```tsx
if (!confirm(SSO_LABELS.sso_delete_confirm)) return;
```

`window.confirm` for SSO config delete. DangerousActionModal canon
violation. Backend gates correctly via `buildAuthedHeaders` + admin
role; UX drift only.

### Triage

All 3 are **MINOR/UX-DRIFT**, not security. None block this audit's
sign-off. Recommend bundling into a single follow-up commit:

> `audit(partner-portal): migrate 3× window.prompt/confirm to
> DangerousActionModal (MIN-A, MIN-B, MIN-C)`

with per-call-site round-table sign-off in the body.

## 3. Sibling-parity verification (NEW dimension since prior audit)

The Sprint-N+1/N+2 ships introduced a **canon DangerousActionModal**
pattern that earlier surfaces did not have. I verified parity across
the 5 partner-portal call sites:

| Call site | File:line | Tier | Reason input | Confirm-phrase | Backend POST |
|---|---|---|---|---|---|
| Cancel subscription | PartnerBilling.tsx:575 | tier-1 | n/a | yes (typed) | `/api/billing/subscription/cancel` |
| Revoke BAA | PartnerAttestations.tsx:1165 | tier-1 (irreversible) | yes (≥20ch) | yes (counterparty label) | `/api/partners/me/ba-roster/{id}` |
| Initiate admin transfer | PartnerUsersScreen.tsx:491 | tier-1 | yes (≥20ch) | yes (target email) | `/api/partners/me/admin-transfer/initiate` |
| Deactivate user | PartnerUsersScreen.tsx:518 | tier-1 | yes (≥20ch) | yes (target email) | `/api/partners/me/users/{id}/deactivate` |
| Change user role | PartnerUsersScreen.tsx:560 | tier-1 | yes (≥20ch) | yes (target email) | `/api/partners/me/users/{id}/role` |
| Mint client-portal magic link | PartnerSiteDetail.tsx:495 | reversible | yes (≥20ch) | n/a (link expires) | `/api/partners/me/sites/{id}/client-portal-link` |

**Steve verdict:** consistent. Tier choice (irreversible vs.
reversible) tracks the underlying audit-chain semantics; the mint is
correctly classified as reversible because the magic link itself
expires + the chain anchors the audit.

**Coach verdict:** the partner-portal DAM canon is now established.
Migrating MIN-A/B/C to this canon is the right next step.

## 4. Cross-cutting patterns (NEW since prior audit)

### Pattern P-1: portalFetch is the canonical mutation helper

All 6 new files (PartnerAttestations + PartnerSiteDetail + 3
sub-routes + PartnerAdminTransferModal call site) use
`portalFetch.{getJson, postJson, deleteJson, fetchBlob}`. Zero raw
mutation `fetch()` outside of GETs. CSRF baseline test confirms 0
violations.

### Pattern P-2: aria-busy + spinner on every issuance button

Both Issue+Download buttons in PartnerAttestations + the Mint button
in PartnerSiteDetail use `aria-busy={busy === 'issuing'}` +
disabled+spinner. Sibling-parity with ClientAttestations hero card
verified.

### Pattern P-3: 401/403/429/409 distinct-toast paths

All issuance + mutation paths surface 401 (session expired), 403 (no
permission), 429 (rate-limited with retry hint from
`retryAfter` header), 409 (semantic conflict — e.g. roster empty for
BA letter). This is a stronger UX bar than the prior audit's
existing surfaces (which often surfaced only error.message).

### Pattern P-4: read-only sub-routes via getJson

PartnerSiteAgents/Devices/DriftConfig are pure read views with
breadcrumb Links + table render. Zero mutation surface — by design,
matches plan 37 D3.

### Pattern P-5: DangerousActionModal as canon

Now 5 call sites, drift evident on 3 legacy surfaces (MIN-A/B/C).
Recommend ratchet: a CI gate that scrapes `window.prompt|confirm`
under `partner/` and pins the count downward.

## 5. CI gate baseline before/after

| Gate | Pre-audit | Post-audit | Δ |
|---|---|---|---|
| `test_partner_button_to_route_audit` | 11 PASSED | 11 PASSED | 0 |
| `test_artifact_endpoint_header_parity` | 13 PASSED | 13 PASSED | 0 |
| `test_frontend_mutation_csrf` | 14 PASSED | 14 PASSED | 0 |
| `test_partner_admin_component_reuse_allowlist` | 5 PASSED | 5 PASSED | 0 |
| **Total** | **44 PASSED** | **44 PASSED** | **0** |

Run time: 2.01s. Zero source edits made — no fix-up commits in this
re-run, so post-audit numbers identical by construction.

CSRF ratchet (`CSRF_BASELINE_MAX = 0`): unchanged at 0 — partner-
portal contributes no violations.

## 6. Round-table 2nd-eye verdicts

### CRITICAL findings: 0

No round-table needed.

### MAJOR findings: 0

No round-table needed.

### MINOR findings (3): MIN-A, MIN-B, MIN-C

**Steve (Principal SWE):** these 3 are pure UX/canon drift, not
security. Backend role-gates everything; client-side `prompt/confirm`
pattern just diverges from the modern DAM pattern. Migrate as a
single fix-up PR; expand `test_partner_admin_component_reuse_allowlist`
(or a sibling) to ratchet a `no_window_prompt_in_partner` gate.

**Maya (Sec):** vacuous from her angle. The mutations themselves
travel through `csrfHeaders` + `buildAuthedHeaders`. No CSRF gap, no
auth-bypass class. PASS — but agree with Steve that the canon
ratchet would lock the class shut.

**Carol (Compliance/Legal Lang):** scanned MIN-A/B/C copy. "Renewal
reason:" + "Revocation reason:" + the SSO_LABELS.sso_delete_confirm
text (loaded from constants/copy.ts) — none use banned legal verbs.
PASS.

**Coach (DRY/Consistency):** **DRIFT** on 3 surfaces. Fix the canon
or expand the ratchet. Coach's preference: ratchet first (cheap,
deterministic), migrate as natural touch-points open the surfaces.
**APPROVE** for sign-off as MINOR; do not block.

**Sarah (PM):** these 3 don't impact the printable-artifact-sprint
narrative or the cross-portal magic-link narrative. Park as
hygiene-debt MINOR-class, queue for any sprint that touches
PartnerExceptionManagement or PartnerSSOConfig. **APPROVE**.

## 7. Final round-table verdict

**APPROVE — sign-off, follow-up queued.**

- 0 CRITICAL, 0 MAJOR, 3 MINOR (all UX drift on legacy surfaces).
- Sprint-N+1 + Sprint-N+2 ships are **clean** under all 9 audit
  dimensions on every new clickable.
- All prior CRIT/MAJ from `.agent/plans/35` ledger-resolved.
- 0 CI gate drift; baselines hold.
- 0 fix-up commits this re-run (rationale: legacy-surface UX-drift
  scope > unambiguous-fix bar; queued as next-sprint follow-up).

## 8. Open questions / blockers

**Blockers:** none.

**Recommended next-sprint items:**
1. Migrate MIN-A/B/C (PartnerExceptionManagement.tsx + PartnerSSOConfig.tsx)
   to DangerousActionModal canon in a single fix-up PR.
2. Add CI ratchet `test_no_window_prompt_in_partner` to lock the
   class — start at baseline 3 (the current count), drive to 0.
3. Add deferred follow-up from prior-audit MIN-3..MIN-8 (a11y /
   silent catches in PartnerDashboard) — none re-verified in this
   re-run because no source touched those lines.

## 9. Lessons-candidate for memory

**`feedback_partner_portal_dam_canon.md`** —
DangerousActionModal is now the canon for partner-portal scary
actions (5 call sites). Any new partner mutation that would
otherwise reach for `window.prompt`/`window.confirm`/inline confirm
spans MUST use DangerousActionModal. Pin a CI gate that scrapes
`window\.(prompt|confirm)` under `partner/` and ratchets the count
downward (current 3, target 0).
