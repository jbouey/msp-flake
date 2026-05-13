# Class-B 7-lens Gate A — Subprocessor re-audit draft v1

**Reviewer:** Fresh-context Gate A fork (subagent, isolated context window)
**Date:** 2026-05-13
**Draft under review:** `audit/baa-subprocessors-reaudit-draft-2026-05-13.md`
**Source-of-truth files independently re-verified:** `docs/BAA_SUBPROCESSORS.md`, `backend/l2_planner.py:549-1006`, `backend/email_alerts.py:21-29`, `backend/portal.py:80-175`, `backend/escalation_engine.py:180-212,548,730-743`, `backend/oauth_login.py:51-57,603`, `backend/partner_auth.py:554-841`, `backend/framework_sync.py:30`, `backend/evidence_chain.py:255-258`, `backend/iso_ca.py:252`, `backend/attestation_api.py:255`, `backend/integrations/oauth/{google,azure}_connector.py`, `appliance/internal/phiscrub/scrubber.go:35-93`, `memory/project_no_master_baa_contract.md`, `memory/feedback_enterprise_counsel_seven_rules.md`

---

## Per-lens verdict

| Lens | Verdict | Severity-graded findings |
|---|---|---|
| 1. Inside-counsel | APPROVE-WITH-FIXES | 1×P0 (SendGrid framing flipped — primary not secondary), 1×P0 (missing OpenAI + Azure OpenAI), 2×P1 (Namecheap reasoning sound but conclusion right for the wrong primary reason; Hetzner-split is legally correct) |
| 2. Attorney (outside-counsel mindset) | **BLOCK** | 1×P0 (4+ missing subprocessors: OpenAI, Azure OpenAI, PagerDuty, Google OAuth, Microsoft Azure OAuth — discoverable in 60s of grep), 1×P0 (no domain registrar / DNS provider entry), 1×P1 (Sigstore Fulcio / Rekor via iso_ca.py:252 also missing) |
| 3. HIPAA auditor | APPROVE-WITH-FIXES | 1×P1 (§5 review cadence preserved but quarterly drift check needs schedule-attestation not just an invariant), 1×P2 (subprocessor-change notice 30-day clock — verify last invocation lives in audit log) |
| 4. PM | APPROVE-WITH-FIXES | 1×P0 recommendation (hold publish until master BAA exists OR re-frame as "Data Flow Disclosure" not "BAA Exhibit") |
| 5. Engineering (Steve) | **BLOCK** | 1×P0 (SendGrid is PRIMARY when `SENDGRID_API_KEY` set, SMTP is FALLBACK — draft has the polarity exactly wrong at lines 17, 52, 53 and §6 verification ask), 1×P1 (PHI-scrubber count: 14 is correct, but the "12 core + 2 contextual" framing could be misread; recommend "14 total: 12 regex pattern definitions plus 2 contextual patterns (patient-hostname + PHI-path-segment) compiled at init"), 1×P1 (lockstep gate shape vs existing `test_three_list_lockstep_pg.py` — additive, not double-build) |
| 6. Coach (consistency / no double-build) | APPROVE-WITH-FIXES | 1×P1 (proposed `subprocessor_dataflow_drift` substrate invariant does NOT double-build — distinct from existing 11 invariants in `project_substrate_integrity_engine.md`; check it inherits the sev2 alert plumbing), 1×P1 (collapsing PostgreSQL + MinIO + Caddy into "Hetzner Central Command VPS (3 self-managed services)" sub-list is cleaner and avoids misleading the customer that these are 3 distinct subprocessor relationships) |
| 7. Medical-technical | APPROVE-WITH-FIXES | 1×P1 (clinic-readable exec summary needed — current doc opens with HIPAA cite, intimidating for SMB practice manager; 3-sentence plain-language opener helps) |

**Overall: BLOCK** — Attorney + Engineering lenses both BLOCK. The two BLOCK findings are factual / completeness issues that are correctable in <60 min, but they MUST be corrected before this draft is presentable to inside or outside counsel.

---

## Per-subprocessor classification verdict (14 entries in draft + 5 missing)

| # | Subprocessor | Draft status | Lens consensus | Dissent |
|---|---|---|---|---|
| 1 | Hetzner Central Command VPS | Required | **AGREE — Required** | None |
| 2 | Hetzner Vault Transit VPS | Not required | **AGREE — Not required** | Counsel may prefer "Required (transitive)" since same legal entity, single corporate-level BAA covers both machines; engineering split-by-machine is defensible but is a presentation choice, not a legal one. Recommend: one BAA with Hetzner GmbH, two machines listed as scoped components. |
| 3 | PostgreSQL self-hosted | N/A | **AGREE — N/A** | Coach suggests collapsing #3+#4+#5 into a Hetzner-VPS-services sub-list (see P1 finding) |
| 4 | MinIO self-hosted | N/A | **AGREE — N/A** | Same as above |
| 5 | Caddy self-hosted | N/A | **AGREE — N/A** | Same as above |
| 6 | Anthropic | Not required | **AGREE — Not required** | Inside-counsel +1: this only holds AS LONG AS the PHI scrubber is enforceable; recommend the AST gate + scrubber unit-test ratchet that draft proposes as a hard precondition for the Not-required classification |
| 7 | Namecheap PrivateEmail | Required | **AGREE — Required, with reservation** | Inside-counsel: BAA-required is the right answer but for a STRONGER reason than the draft gives. Even with Rule 7 opaque-mode + AST egress gate, SMTP transit handles RECIPIENT ADDRESSES which are PHI-adjacent identifiers in healthcare context. Recipient email at a clinic domain = clinic identification. Draft's "even with Rule 7" framing accidentally invites the counter-argument that opaque-mode could mitigate. Stronger framing: "Namecheap transits recipient addresses that, in healthcare context, identify the covered entity. BAA required regardless of body content." |
| 8 | Twilio SendGrid | "DORMANT or REQUIRED — engineering should verify activation" | **DISAGREE — Required, ACTIVE PRIMARY** | **P0 ENGINEERING FINDING.** `portal.py:118-128` reads: `if not SENDGRID_AVAILABLE or not SENDGRID_API_KEY:` then tries SMTP fallback. SendGrid is the PRIMARY path when `SENDGRID_API_KEY` is set. SMTP (Namecheap) is the FALLBACK. The draft has this exactly backwards across lines 17, 52, 53, §6. Production deploy status (verify with `ssh root@VPS env | grep SENDGRID_API_KEY`) determines which is currently the active primary. If `SENDGRID_API_KEY` is set in prod → SendGrid is the primary magic-link transport for client-portal auth. |
| 9 | Stripe | Not required | **AGREE — Not required** | None |
| 10 | SSL.com | Not required | **AGREE — Not required** | Attorney +1: classify as "Supply-chain dependency (trust anchor)" in a separate section, NOT in the BAA-subprocessor table. Customers may misread its presence in the table as implying a data relationship that does not exist. |
| 11 | GitHub | Not required | **AGREE — Not required** | None |
| 12 | OpenTimestamps / Bitcoin | Not required | **AGREE — Not required** | None |
| 13 | Let's Encrypt | Not required | **AGREE — Not required** | None |
| 14 | 1Password | Not required | **AGREE — Not required** | Inside-counsel +1: same recommendation as SSL.com — present in a separate "Operator-side trust anchors" section. Including 1Password in the subprocessor table risks implying customer data transits through it, which is false. |

---

## Missing subprocessors the draft DIDN'T catch

This is the **BLOCK-grade gap**. Five additional outbound network destinations exist in the codebase that the draft did not enumerate:

| Missing # | Subprocessor | Source | Data flow | Proposed status |
|---|---|---|---|---|
| **M1** | **OpenAI, L.L.C.** | `l2_planner.py:964-992`, env `OPENAI_API_KEY`, `LLM_MODEL` default `gpt-4o` | Same PHI-scrubbed incident metadata as Anthropic. Active alternate LLM path. | **Not required (parity with Anthropic)** — but MUST be documented; presence in code = production exposure as soon as the env var ships. |
| **M2** | **Microsoft Corporation (Azure OpenAI Service)** | `l2_planner.py:551-554`, env `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT` | Same PHI-scrubbed incident metadata. Active alternate LLM path. | **Not required (parity with Anthropic)** — also requires documentation; Azure-tenant scoping has different BAA implications than US-public OpenAI; if any clinic is in a regulated-region, Microsoft offers a BAA that may be required. |
| **M3** | **PagerDuty, Inc.** | `escalation_engine.py:194-210` — `https://events.pagerduty.com/v2/enqueue`. Per-tenant `pagerduty_enabled` + `pagerduty_routing_key` in DB (line 548, 730-743). | Incident metadata: site_name, incident_type, summary, hipaa_controls, recommended_action. Already scrubbed at appliance but the `summary` field is human-readable text that may carry less-redacted context. | **REQUIRED when enabled per-tenant** — exact same legal posture as Namecheap. Per-tenant gate (`pagerduty_enabled=true`) doesn't excuse the BAA requirement; it just defers it until activation. |
| **M4** | **Google LLC (Google OAuth + Workspace Directory API)** | `oauth_login.py:51-57`, `partner_auth.py:610-841`, `integrations/oauth/google_connector.py`. Active for portal SSO + admin directory integrations. | Auth tokens, user email, profile data, directory listings (when admin integration enabled). | **Required when enabled per-tenant** — same conditional structure as PagerDuty. Google offers a Workspace BAA; required for any customer authenticating via Google SSO. |
| **M5** | **Microsoft Corporation (Azure AD / Entra ID + Graph API)** | `oauth_login.py:55-57`, `partner_auth.py:554,751-770`, `integrations/oauth/azure_connector.py`. Active for portal SSO + admin directory integrations. | Auth tokens, user email, profile data, Graph API directory + device + security data. | **Required when enabled per-tenant** — Microsoft Online Services BAA covers Azure AD and Graph. Required for any customer authenticating via Azure SSO. |

**Borderline / mentioned for completeness:**

- **Sigstore (Fulcio CA + Rekor transparency log)** — `iso_ca.py:252` references `token.actions.githubusercontent.com` for keyless code signing. Public transparency log; no PHI; supply-chain trust anchor like SSL.com. **Not required** but should be disclosed under "Supply-chain trust anchors."
- **NIST OSCAL repository (raw.githubusercontent.com)** — `framework_sync.py:30`. Read-only fetch of public framework JSON. Covered under GitHub entry; no separate disclosure needed.
- **DNS registrar for osiriscare.net** — NOT FOUND in code (registrar is operator-side, not platform-runtime). The draft / current doc both omit this. Attorney lens: it's a supply-chain dependency, not a subprocessor under HIPAA §164.502(e), since the registrar doesn't transit customer data. **Confirm out of scope.**
- **NTP (time.cloudflare.com)** — `flake/Modules/timesync.nix:18`. Time sync only; no application data. **N/A.**

---

## Banned-language regression scan

Grep over draft (`audit/baa-subprocessors-reaudit-draft-2026-05-13.md`) for: `ensure[sd]?`, `prevent[sd]?`, `protect[sd]?`, `guarantee[sd]?`, `audit-ready`, `100%`, `PHI never leaves`, `continuously monitored`, `bulletproof`, `impenetrable`.

Results:

- **Line 27** (draft): correctly FLAGS the existing doc's banned phrase `"PHI never leaves"` and proposes replacement. Good.
- **Line 67**: "to make the Namecheap SMTP BAA status more defensible" — defensive, not banned.
- **Line 65**: "verifies every external-call import... corresponds to a documented subprocessor" — operational claim, not absolute claim. Fine.
- **No banned phrases asserted as platform behavior in the draft itself.**

Pass with one observation: §3 item 1 says "Maintain a SINGLE canonical `docs/BAA_SUBPROCESSORS.md`" — fine, but the §6 action items say "Update `docs/BAA_SUBPROCESSORS.md` to v2." Consistency check: also bump `Document Version: 1.0 → 2.0` and `Classification:` line should be revisited if PM lens recommends reframing (see below).

---

## Publish-now vs hold-until-master-BAA recommendation

**PM lens + Inside-counsel lens consensus: HOLD-AND-REFRAME.**

The current `docs/BAA_SUBPROCESSORS.md` is titled "Sub-Processor List — HIPAA Business Associate Agreement" and self-describes (line 14) as "an exhibit to Business Associate Agreements executed between OsirisCare and its covered entity customers." Per `project_no_master_baa_contract.md`, the master BAA does not exist. Publishing v2 with the same exhibit-to-BAA framing IMMEDIATELY worsens the existing problem:

- A more rigorous list (v2) with the same "BAA Exhibit" header creates STRONGER implied-contract signaling than v1 did, against an even more clearly absent contract.
- Counsel discovery: "You issued a more comprehensive subprocessor disclosure 2026-05-13 calling itself a BAA Exhibit. Where is the BAA?" — answer is still "doesn't exist," but now the discoverable artifact is more detailed.

**Recommended path:**

1. **Re-title and re-frame** v2 as "**Data Flow Disclosure & Subprocessor Registry**" (NOT "BAA Exhibit"). Change the Classification line and the §1 self-description to: *"This document discloses the actual data flows and third-party subprocessors involved in operating the OsirisCare platform. Once a Business Associate Agreement is executed between OsirisCare and a covered entity, this document is incorporated by reference as Exhibit A."*
2. **Publish v2 with the reframe** — transparency win, technically accurate, doesn't make the missing-BAA problem worse.
3. **Pair the v2 publish with a parallel master-BAA-drafting task** (already escalated as Task #56, blocking counsel work).
4. **Add a frontmatter banner** to v2: *"Note: a master Business Associate Agreement template is in active drafting with outside HIPAA counsel. Once executed, this registry becomes Exhibit A of that agreement."* This is honest and defensive.

**Do NOT** publish v2 with the existing "BAA Exhibit" framing while the master BAA doesn't exist. That's the worst of both worlds.

---

## Final recommendation

**BLOCK** — return to author for the 5 P0 fixes below, then re-submit for Gate A re-review (no need for another full 7-lens fork; a focused diff-review is sufficient if the P0s land cleanly).

### Top 5 P0 findings ranked

1. **[ENGINEERING] SendGrid polarity is inverted across the draft.** `portal.py:118-128` makes SendGrid PRIMARY when `SENDGRID_API_KEY` is set, with SMTP as FALLBACK. The draft says the opposite in 4 places (lines 17, 52, 53, §6 verification ask). This is a factual error that, if shipped to counsel, gives counsel an incorrect mental model of the email-transit data flow and may produce wrong legal advice. Fix: rewrite SendGrid entry as PRIMARY (when env set), Namecheap PrivateEmail as FALLBACK, and verify production env actually has `SENDGRID_API_KEY` set on Hetzner VPS before classifying SendGrid status as active.

2. **[ATTORNEY] Five subprocessors missing — discoverable in 60 seconds of grep.** OpenAI, Azure OpenAI, PagerDuty, Google OAuth, Microsoft Azure OAuth all have outbound HTTPS calls in the codebase. A counsel review that finds these on its own first will damage trust in the rest of the audit. Fix: add 5 entries with the data-flow classifications proposed in this verdict's "Missing subprocessors" table.

3. **[PM + INSIDE-COUNSEL] Reframe v2 as "Data Flow Disclosure & Subprocessor Registry," not "BAA Exhibit."** Publishing a more rigorous "BAA Exhibit" against a non-existent master BAA worsens the underlying legal exposure. Fix: change title, classification, §1 framing, and add the "master BAA in drafting" frontmatter banner. Publish v2 then; do not hold.

4. **[INSIDE-COUNSEL] Namecheap "Required" verdict is correct but reasoning needs strengthening.** Current framing ("even with Rule 7 opaque-mode") invites the counter-argument that opaque-mode could mitigate the BAA requirement. Correct framing: SMTP transit handles RECIPIENT ADDRESSES, which in healthcare context identify the covered entity regardless of body content. BAA required structurally, not conditionally. Apply same framing to PagerDuty + SendGrid.

5. **[ENGINEERING + COACH] PHI-scrubber count documentation needs precision.** "14 total: 12 regex pattern definitions in `compilePatterns()` plus 2 contextual patterns (`patientHostnameRe` + `phiPathSegmentRe`) compiled at `init()`" — the current draft uses "12 core + 2 contextual" which is technically correct but sounds like an apology. State both numbers, cite both source-code locations, and link to the AST gate + unit-test ratchet that engineering should build to keep the scrub posture audit-grade.

### P1 follow-ups (close in same PR or as named TaskCreate items)

- Collapse PostgreSQL + MinIO + Caddy into "Hetzner Central Command VPS — self-managed services" sub-list (Coach).
- Move SSL.com + 1Password to a separate "Supply-chain trust anchors" section, not the subprocessor table (Inside-counsel + Attorney).
- Hetzner Central Command + Hetzner Vault Transit: single legal-entity BAA, two machines listed as scoped components (Inside-counsel).
- 3-sentence plain-language exec summary at top of v2 doc for SMB practice managers (Medical-technical).
- Verify per-tenant gating in audit log: when a tenant flips `pagerduty_enabled=true` or sets `oauth_config.provider=google`, does the audit log capture it as a subprocessor-activation event? If not, add (HIPAA auditor).
- Verify the `subprocessor_dataflow_drift` substrate invariant does not double-build any of the 11 existing invariants in `project_substrate_integrity_engine.md` — initial scan says no, but confirm in implementation Gate A (Coach).
- Add explicit out-of-scope note: NTP (Cloudflare), DNS registrar, Sigstore Fulcio/Rekor — disclosed as supply-chain trust, not as subprocessors (Attorney completeness).

---

**Gate A verdict file path:** `/Users/dad/Documents/Msp_Flakes/audit/coach-baa-subprocessors-reaudit-gate-a-2026-05-13.md`
**Next gate:** after P0 closure, focused diff re-review (not full 7-lens). Once v2 ships to `docs/BAA_SUBPROCESSORS.md`, a separate Gate B fork verifies the as-implemented artifact matches this Gate A's accepted recommendations.
