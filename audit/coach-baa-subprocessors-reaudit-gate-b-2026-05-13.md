# Class-B Gate B — Subprocessor re-audit v2

**Reviewer:** Fresh-context Gate B fork (subagent, isolated context)
**Date:** 2026-05-13
**Draft under review:** `audit/baa-subprocessors-reaudit-draft-2026-05-13.md` (v2)
**Gate A under closure:** `audit/coach-baa-subprocessors-reaudit-gate-a-2026-05-13.md`
**Source-of-truth re-verified independently:** `portal.py:118-128`, `l2_planner.py:549, 551-554, 929-945`, `escalation_engine.py:194-199`, `oauth_login.py:51-57`

---

## Per-lens verdict

| Lens | Verdict | New findings (Gate A items NOT re-litigated) |
|---|---|---|
| 1. Inside-counsel (Maya) | APPROVE-WITH-FIXES | 1×P1 (entry #8 Azure OpenAI "Conditional" punts a binary legal call to the operator; counsel needs to set the default) |
| 2. Outside-counsel surrogate (Carol) | APPROVE-WITH-FIXES | 1×P1 (§4(b) explicitly forwards #8's conditional framing to inside counsel — that's correct; but the registry itself should not ship to a customer with a `Conditional` row whose resolution depends on counsel opinion not yet rendered) |
| 3. Medical-technical | APPROVE | none — no clinical-data new exposure introduced |
| 4. HIPAA auditor | APPROVE-WITH-FIXES | 1×P2 (Gate A asked for 3-sentence plain-language exec summary; v2 did not add one — carry as named followup, not blocker) |
| 5. Attorney / PM | APPROVE | reframe + banner + all 5 missing entries land cleanly; publishable as data-flow disclosure |
| 6. Engineering (Steve) | APPROVE-WITH-FIXES | 1×P1 (entry #11 PagerDuty BAA-on-file precondition is described as "engineering recommends" — it's a PROPOSAL not implemented code; v2 §6 lists it under action items, but the registry-row classification reads as if the mitigation is live. Reword to "Required — until engineering ships partner-side precondition (Task #__)") |
| 7. Coach | APPROVE-WITH-FIXES | 2×P1 (entry-count drift: §2 header says "17 entries" but table has 19; cross-fork dependency on POSTURE_OVERLAY §8 frontmatter is forward-referenced before that doc has cleared Gate A) |

**Overall: APPROVE-WITH-FIXES** — no BLOCK lens. All 5 Gate A P0s are CLOSED. Net-new findings are all P1/P2 and resolvable without another full fork: 2 wording tightenings + 1 count fix + 2 named followups.

---

## Gate A P0 closure matrix

| P0 | Status | Evidence (v2 line #) |
|---|---|---|
| 1 — SendGrid polarity (PRIMARY when env set, SMTP fallback) | **CLOSED** | Line 20 ("PRIMARY email transport when `SENDGRID_API_KEY` env is set. SMTP fallback is secondary") + line 64 entry #9 ("PRIMARY email transport when `SENDGRID_API_KEY` is set") + line 21 entry for Namecheap explicitly labels "Fallback". Verified against `portal.py:118-128` — `if not SENDGRID_AVAILABLE or not SENDGRID_API_KEY:` confirms SendGrid takes precedence. Polarity correct in all 4 places. |
| 2 — 5 missing subprocessors added (OpenAI, Azure OpenAI, PagerDuty, Google OAuth, Microsoft Azure AD) | **CLOSED** | OpenAI line 22 / entry #7. Azure OpenAI line 23 / entry #8. PagerDuty line 24 / entry #11. Google OAuth line 25 / entry #13. Microsoft Azure AD line 26 / entry #14. All 5 carry source-line citations matching independent re-verification of `l2_planner.py:549, 551-554`, `escalation_engine.py:196`, `oauth_login.py:51-57`. |
| 3 — Reframe from "BAA Exhibit" to "Data Flow Disclosure & Subprocessor Registry" + master-BAA-in-drafting banner | **CLOSED** | Title line 1 reframed. Reframe note line 3 explains the rename. Master-BAA-status banner line 5 is the explicit "in active drafting with outside HIPAA counsel as of 2026-05-13" banner. Both items present and prominent. |
| 4 — Strengthen Required reasoning for Namecheap/PagerDuty/SendGrid to structural (recipient-identifies-CE) not opaque-conditional | **CLOSED** | SendGrid line 20: "recipient email address structurally identifies a covered entity ... Rule 7 opaque-mode subject/body mitigation does NOT remove this structural exposure". Namecheap line 21: "Same reasoning as SendGrid: recipient email address structurally identifies". PagerDuty line 24: "site_id + summary text are customer-org-identifying". All three rewritten to structural framing. |
| 5 — PHI scrubber count: "14 = 12 regex + 2 contextual" with source-line cite | **CLOSED** | Line 33 ("14 patterns total — 12 core regex defined in `compilePatterns()` at `appliance/internal/phiscrub/scrubber.go:41-93` ... + 2 contextual patterns defined at `scrubber.go:35-38` (`patientHostnameRe` ... `phiPathSegmentRe` ...)") + line 113 in §6 action items repeats the precise breakdown. Source-line citations explicit and verifiable. |

**All 5 Gate A P0s: CLOSED. Zero NEW-REGRESSION.**

---

## Lens 1-7 NEW findings (P1/P2 only — no P0, no BLOCK)

### Inside-counsel (Maya) — P1
Entry #8 (Azure OpenAI) ships a `Conditional` verdict that pushes the HIPAA-tier-or-not decision to the operator at config-time. This is engineering punting a binary legal classification (BAA required or not?) to a non-lawyer operator. The conditional framing is defensible IF counsel sets the default + the operator-config UI gate (§3 item 4) ships as a hard precondition. Recommendation: v2 should state the DEFAULT classification as "Required" with the path-to-downgrade being "operator certifies HIPAA-tier + Microsoft BAA on file." Today's framing reads as "operator picks" which is the wrong default-fail-direction for HIPAA work.

### Engineering (Steve) — P1
Entry #11 PagerDuty row reads "Engineering recommends BAA-on-file precondition" — the mitigation is a PROPOSAL (§6 action item), not engineering code that exists today. A customer reading the registry would reasonably assume the precondition is enforced. Rewrite the row's mitigation column to: "Required structurally. Partner-side BAA-on-file precondition is a planned engineering gate (Task #__); not yet enforced." Same posture honesty as the master-BAA banner.

### Coach — P1 (×2)
- **Count drift:** §2 header line 52 says "proposed v2 — 17 entries" but the table has rows numbered 1-19. The table grew during the v1→v2 rewrite (adding Hetzner Vault Transit split + Azure OpenAI + Google OAuth + Microsoft Azure AD + 1Password) and the section header didn't update. Fix to "19 entries."
- **Forward-reference risk:** Line 119 (§6 last bullet) and line 130 (§7 reviewer guidance) both reference "POSTURE_OVERLAY §8 frontmatter" — POSTURE_OVERLAY draft is itself in Gate A right now and not yet approved. Two failure modes: (a) if POSTURE_OVERLAY changes its §8 numbering or content, this draft becomes inconsistent; (b) if POSTURE_OVERLAY is rejected, this draft references a non-existent standard. Mitigation: change to "POSTURE_OVERLAY frontmatter standard (when Task #51 lands)" — drop the §8 ordinal so the reference survives renumbering.

### HIPAA auditor — P2
Gate A's medical-technical lens recommended a 3-sentence plain-language exec summary at the top of v2 for SMB practice managers. Not added in v2. Carry as named TaskCreate followup per the two-gate rule's "P1+ carried as named items in same commit" provision; not a blocker.

### Outside-counsel surrogate (Carol) — P1
§4(b) correctly forwards entry #8's conditional framing to inside counsel as an open question. That's procedurally right. But shipping a customer-facing registry with a `Conditional` row whose verdict is open is a defect — customers should see a deterministic classification. Pair with Maya's P1: set default to "Required" and treat the conditional as an internal note pending counsel review.

---

## Banned-word scan (re-run on v2)

Grep targets: `ensure[sd]?`, `prevent[sd]?`, `protect[sd]?`, `guarantee[sd]?`, `audit-ready`, `100%`, `PHI never leaves`, `continuously monitored`, `bulletproof`, `impenetrable`.

- Line 35 (correctly flags the EXISTING doc's banned `"PHI never leaves"` phrasing and proposes counsel-grade replacement) — defensive citation, not a v2 assertion.
- No NEW banned phrases asserted as platform behavior.
- Line 24 ("identifies CE") — operational claim, not absolute.
- Line 33 ("auditor-grade scrubbing posture is unchanged") — "auditor-grade" is acceptable framing per existing copy rules (analogous to "audit-supportive"); not in the banned list.

**Pass.** Zero new violations.

---

## Cross-fork consistency

- **vs. POSTURE_OVERLAY Gate A (in flight):** v2 forward-references POSTURE_OVERLAY §8 in 2 places (§6 action items + §7 reviewer guidance). The POSTURE_OVERLAY draft is itself in Gate A and may not approve, may renumber sections, or may not exist on the target timeline. Coach P1 above documents the fix (drop the §8 ordinal). Not a BLOCK because v2's content doesn't DEPEND on POSTURE_OVERLAY §8 substance — only the frontmatter style does, and frontmatter is a §6 deliverable, not a §1-§5 registry artifact.
- **vs. BAA-drafting Gate A (in flight to outside counsel):** v2's master-BAA-in-drafting banner is consistent with Task #56 status. The reframe explicitly anticipates becoming Exhibit A on master-BAA execution. No conflict.
- **vs. `project_no_master_baa_contract.md`:** v2's reframe is the recommended fix from that memory. Consistent.

---

## Publish-now-as-v2 sign-off

**YES — publish v2 with the following 4 minor fixes applied in the same PR (not requiring re-fork):**

1. Fix §2 header count `17 → 19`.
2. Rewrite entry #8 (Azure OpenAI) default to "Required" with downgrade-path note (Maya P1 + Carol P1).
3. Rewrite entry #11 (PagerDuty) mitigation column to explicitly label the BAA-on-file precondition as planned-not-shipped (Steve P1).
4. Drop the §8 ordinal from POSTURE_OVERLAY references (Coach P1b).

Named followups (TaskCreate items, NOT blockers):
- 3-sentence plain-language exec summary (HIPAA auditor P2).
- Engineering tasks #4-8 in §6 already enumerate the lockstep gate / dataflow-drift invariant / UI preconditions; carry as-is.

Publish-now is APPROVED because: the 5 Gate A P0s are all closed; the 4 net-new P1s are wording fixes resolvable in <15 min without changing the registry's substantive content; and the parallel-track posture (engineering moves while master BAA drafts with counsel) is exactly the operating mode the user authorized.

---

## Final recommendation

**APPROVE-WITH-FIXES.**

Apply the 4 P1 wording fixes inline (no re-fork required). Ship v2 to `docs/SUBPROCESSORS.md` (or keep `docs/BAA_SUBPROCESSORS.md` filename for compat per §6). Open named TaskCreate items for the 5 engineering deliverables in §6 + the exec summary in HIPAA-auditor P2. Cross-link to Task #56 (master BAA drafting) so the parallel-track audit trail is explicit.

Gate B does not need to re-run on the as-implemented `docs/SUBPROCESSORS.md` if the only diff is the 4 P1 fixes above; a focused diff-review by the author suffices. If the as-implemented artifact diverges further from this draft, re-fork.
