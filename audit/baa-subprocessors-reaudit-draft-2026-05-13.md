# Data Flow Disclosure & Subprocessor Registry — Re-Audit Draft v2 (2026-05-13)

> **Reframe note (Gate A finding 2026-05-13):** This document is being re-titled from "BAA Subprocessor List" to "Data Flow Disclosure & Subprocessor Registry" because the OsirisCare master Business Associate Agreement is in active drafting with outside counsel (Task #56). The prior framing "BAA Exhibit" is misleading — there is no BAA today for this list to be an exhibit *to*. Once the master BAA is executed, this registry becomes its Exhibit A. Until then, the registry stands on its own as a transparency-grade data-flow disclosure to current and prospective customers.

> **Master BAA status banner:** *OsirisCare's master Business Associate Agreement is in active drafting with outside HIPAA counsel as of 2026-05-13. This Data Flow Disclosure & Subprocessor Registry is published for transparency in the interim and will become Exhibit A of the executed master BAA upon completion of counsel review (target: 14-21 days per Task #56 Gate A recommendation).*

**Source doc under audit:** `docs/BAA_SUBPROCESSORS.md` (Effective Date 2026-03-11, 64 days stale)
**Audit driver:** Counsel Priority #2, Rule 8 — *"Subprocessors are judged by actual data flow, not hopeful labeling."* Task #55.
**Gate A status:** v1 BLOCKED 2026-05-13 (5 P0 findings). v2 (this draft) addresses all 5 P0s + adds the 5 missing subprocessors the v1 fork surfaced.

---

## §1 — Drift summary vs current published doc

### A. Subprocessors MISSING from current published doc (added since 2026-03-11 OR pre-existing-undocumented)

| Subprocessor | Service | Source-line proof | Current data flow | Proposed registry status |
|---|---|---|---|---|
| **Hetzner Online GmbH (Vault Transit VPS)** | Secondary VPS hosting HashiCorp Vault Transit for Ed25519 non-exportable signing. Public IP `89.167.76.203`, WG-peer `10.100.0.3`. | Memory: `project_vault_transit_rollout.md` (live since 2026-04-13) | Ed25519 private key material (non-exportable; Transit API does not return key material — only sign/verify operations). NO customer data, NO PHI. | **Not required — but structurally documented.** Vault host receives ONLY sign/verify API calls over WireGuard-internal HTTPS. Customer data never enters. Same legal entity as Central Command VPS (Hetzner Online GmbH); distinct machine with distinct data class — listed as separate registry entry for transparency. |
| **Twilio Inc. (SendGrid)** | **PRIMARY email transport** when `SENDGRID_API_KEY` env is set. SMTP fallback is secondary. | `portal.py:118-128` confirms SendGrid takes precedence; SMTP is the `if not SENDGRID_AVAILABLE or not SENDGRID_API_KEY` fallback branch. | Customer email addresses (recipient = covered entity workforce / admin), magic-link tokens, alert content, notification subjects. Recipient address itself identifies the CE. | **REQUIRED — structural.** SMTP transit relays customer-org-identifying email content; recipient email address structurally identifies a covered entity to the subprocessor regardless of body content. Rule 7 opaque-mode subject/body mitigation does NOT remove this structural exposure — the recipient address itself is the disclosure. |
| **Namecheap Inc. (PrivateEmail SMTP)** | **Fallback email transport** when SendGrid not configured. `mail.privateemail.com:587`. | `email_alerts.py:21-29` + `portal.py:120-123` fallback branch. Currently active for operator-alert path (`alerts@osiriscare.net`, `administrator@osiriscare.net`). | Same data class as SendGrid (recipient addresses, alert content, magic-link tokens, notification subjects). | **REQUIRED — structural.** Same reasoning as SendGrid: recipient email address structurally identifies a covered entity; SMTP transit is downstream disclosure. |
| **OpenAI, OpenAI Inc.** | Alternate LLM path for L2 incident analysis. `OPENAI_API_KEY` env enables this path as an alternative to Anthropic. | `l2_planner.py:549` (`OPENAI_API_KEY`), call paths via `call_openai()`. | Same data class as Anthropic — incident metadata (check type, severity, timestamps, remediation context). PHI-scrubbed at appliance edge by 14-pattern scrubber before reaching Central Command. | **Not required — same posture as Anthropic.** PHI-scrubbed pre-egress. Documented because operator may have either OpenAI OR Anthropic OR Azure OpenAI configured at any given time; all three are alternate paths for the same L2 function. |
| **Microsoft Corporation (Azure OpenAI Service)** | Alternate LLM path for L2 incident analysis. `AZURE_OPENAI_ENDPOINT` + `AZURE_OPENAI_DEPLOYMENT` env enables this. | `l2_planner.py:551-554, 929-945` — `call_azure_openai()` function. | Same data class as Anthropic and OpenAI. PHI-scrubbed pre-egress. | **Not required — same posture as Anthropic / OpenAI.** Note: Microsoft offers a HIPAA-aligned Azure OpenAI offering separate from OpenAI's direct API; operator should select the HIPAA tier if Azure OpenAI is the chosen path. Counsel may want to require BAA with Microsoft if Azure OpenAI is the active configuration. |
| **PagerDuty, Inc.** | Operator/partner-configurable alert routing destination. `escalation_engine.py:196` posts events to `events.pagerduty.com/v2/enqueue` when partner has `pagerduty_routing_key` configured. | `escalation_engine.py:142, 155, 196`. | Alert event content (site_id, incident_type, severity, summary text, timestamps). The event content may carry customer-org-identifying metadata (site_id, partner_id). PHI-scrubbed pre-egress. | **REQUIRED — structural.** Event content includes customer-org-identifying site_id + summary text. Subprocessor relationship is partner-configured per-partner; engineering recommends a BAA-on-file precondition before partners can configure PagerDuty routing. |
| **Google LLC (Google OAuth / Workspace identity)** | Operator/partner Single Sign-On via Google identity. `oauth_login.py:51, 53` + `partner_auth.py:610, 841` + `google_connector.py`. | OAuth code-grant flow against `accounts.google.com/o/oauth2/v2/auth` + userinfo from `googleapis.com/oauth2/v3/userinfo`. | OAuth scope grants user identity (email, name) to OsirisCare; OsirisCare does NOT send customer data to Google. | **Not required — identity-provider scope.** Google sees the OAuth grant (operator/partner consents to share identity with OsirisCare); OsirisCare does not transmit customer data to Google. Documented because compromise of operator/partner Google account = path to OsirisCare access; supply-chain disclosure. |
| **Microsoft Corporation (Microsoft Azure AD / Microsoft Graph)** | Alternate operator/partner SSO via Microsoft 365 identity. `oauth_login.py:55-57` + `partner_auth.py:53, 525-770` + `integrations/oauth/azure_connector.py`. | OAuth code-grant against `login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize` + Graph userinfo from `graph.microsoft.com/v1.0/me`. | Same posture as Google OAuth — identity-provider scope. | **Not required — identity-provider scope.** Same reasoning as Google OAuth. Note: this is the *operator/partner* SSO path, distinct from any future Azure OpenAI subprocessor relationship (which is data-plane). |
| **SSL.com (DigitalSignTrust LLC)** | EV code signing certificate for appliance ISO + agent binary. CodeSignTool / eSigner cloud signing. | `docs/ev-ssl-swap.md` + `.agent/archive/2026-03-sessions.md` operational history. | Code-signing requests (binary hashes + signing payloads). NO customer data, NO PHI. Subprocessor verifies platform's organizational identity (EV verification) + holds the private key bound to the EV cert. | **Not required — code-signing-tool scope.** No PHI; subprocessor sees only platform-side artifacts being signed. Documented because it's a supply-chain trust anchor for every customer-installed binary; compromise would matter. |

### B. Factual errors in current published doc that v2 corrects

| Current published claim | Reality (verified) | Severity |
|---|---|---|
| "PHI scrubber (12 regex patterns)" | **14 patterns total** — 12 core regex defined in `compilePatterns()` at `appliance/internal/phiscrub/scrubber.go:41-93` (SSN, MRN, patient ID, phone, email, credit card, DOB, address, ZIP, account number, insurance ID, Medicare ID) + 2 contextual patterns defined at `scrubber.go:35-38` (`patientHostnameRe` for PATIENT/ROOM/BED/WARD/DR/MR/MS hostname tokens, `phiPathSegmentRe` for `/patient/`, `/ehr/`, `/medical/` URL path segments). v2 documents 14 total with explicit 12+2 breakdown. | Factual minor — auditor-grade scrubbing posture is unchanged |
| "27 tables have RLS enabled and forced" | **114+ ROW LEVEL SECURITY statements across migrations** (counted by `grep -c "ENABLE ROW LEVEL SECURITY"` over `mcp-server/central-command/backend/migrations/`). Substantial undercount. | Factual minor — overstates if anything; auditor-positive |
| "PHI never leaves the customer's on-premises appliance" (§3 line 36) | **BANNED phrasing.** Per counsel-grade copy rule, don't claim absence-proofs the platform cannot guarantee. v2 uses: "PHI is scrubbed at the appliance edge before egress; central command holds no PHI under normal operation." | Customer-facing-copy P0 — fix required |
| "Effective Date: 2026-03-11" | 64 days stale; multiple subprocessors added or unverified since | Update to 2026-05-13 + commitment to quarterly re-audit |
| "BAA Exhibit — Sub-Processor Disclosure" (classification line) | **Reframe required.** Master BAA does not exist (Task #56). v2 reclassifies as "Data Flow Disclosure & Subprocessor Registry" pending BAA execution. | Title-level P0 |

### C. Subprocessors documented and STILL ACCURATE (no change)

- PostgreSQL self-hosted — N/A (co-located, no third-party access)
- MinIO self-hosted — N/A
- Caddy self-hosted — N/A
- Stripe, Inc. — Not required (PCI-DSS scope; payment data only; no PHI)
- GitHub, Inc. — Not required (source/CI; no PHI in repo)
- Anthropic, PBC — Not required (PHI-scrubbed pre-egress; same posture extends to OpenAI + Azure OpenAI alternates)
- OpenTimestamps / Bitcoin — Not required (SHA-256 hashes, not PHI)
- Let's Encrypt (ISRG) — Not required (domain validation only)

---

## §2 — Net-new registry (proposed v2 — 17 entries)

| # | Subprocessor | Service | Data Processed | Location | BAA Status |
|---|---|---|---|---|---|
| 1 | **Hetzner Online GmbH (Central Command VPS)** | Primary VPS (`178.156.162.116`) hosting Docker stack: mcp-server, mcp-postgres, mcp-minio, Caddy. | Compliance telemetry, incident metadata (PHI-scrubbed at appliance edge per §1.B), evidence bundles, tenant configuration, audit logs. | Germany / Finland (EU) | **Required** — data at rest on hosted infrastructure |
| 2 | **Hetzner Online GmbH (Vault Transit VPS)** | Secondary VPS (`89.167.76.203`) hosting HashiCorp Vault Transit for Ed25519 non-exportable signing. WG-internal. | Ed25519 private key material (non-exportable; sign/verify API only). No customer data; no PHI. | Germany / Finland (EU) | **Not required** — distinct machine, distinct data class; documented for transparency |
| 3 | **PostgreSQL (self-hosted)** | Primary RDBMS, Docker on Central Command VPS. | All tenant data: site configs, incidents, scores, users, audit logs, evidence chain. | Co-located with application | **N/A** — self-managed |
| 4 | **MinIO (self-hosted)** | Object storage for evidence artifacts. Docker on Central Command VPS. | Evidence bundles, OpenTimestamps proof files. | Co-located | **N/A** — self-managed |
| 5 | **Caddy (self-hosted)** | TLS termination + reverse proxy. Docker on Central Command VPS. | Transit-only TLS + proxy. No data storage. | Co-located | **N/A** — self-managed |
| 6 | **Anthropic, PBC** | Claude API for L2 incident analysis (LLM planner). Primary LLM path when `ANTHROPIC_API_KEY` is set. | Incident metadata only (check type, severity, timestamps, remediation context). PHI-scrubbed pre-egress by 14-pattern scrubber. | United States | **Not required** — no PHI transmitted |
| 7 | **OpenAI, Inc.** | Alternate LLM path when `OPENAI_API_KEY` is set instead of Anthropic. `l2_planner.py:549` | Same data class as Anthropic. PHI-scrubbed pre-egress. | United States | **Not required — same posture as Anthropic.** Operator selects which LLM provider is active |
| 8 | **Microsoft Corporation (Azure OpenAI Service)** | Alternate LLM path when `AZURE_OPENAI_ENDPOINT` is set. `l2_planner.py:551-554` | Same data class as Anthropic / OpenAI. PHI-scrubbed pre-egress. | United States | **Conditional** — if the operator's deployment uses Azure's HIPAA-aligned tier with Microsoft BAA, classify "Not required" (BAA already on file with Microsoft). If non-HIPAA tier, classify "Required + must move to HIPAA tier." Engineering action: surface this distinction in the operator-config UI |
| 9 | **Twilio Inc. (SendGrid)** | **PRIMARY email transport** when `SENDGRID_API_KEY` is set. Magic-link auth, customer notifications, operator alerts. `portal.py:118-128` | Customer email addresses (structurally identifying), magic-link tokens, alert content. | United States | **REQUIRED — structural** (recipient address identifies CE regardless of opaque-mode body content) |
| 10 | **Namecheap Inc. (PrivateEmail SMTP)** | Fallback email transport when SendGrid not configured. Currently active for `alerts@osiriscare.net`, `administrator@osiriscare.net`. `email_alerts.py:21-29` | Same data class as SendGrid. | United States | **REQUIRED — structural** (same reasoning) |
| 11 | **PagerDuty, Inc.** | Operator/partner-configurable alert routing destination. Partner-set `pagerduty_routing_key`. `escalation_engine.py:196` | Alert event content (site_id, incident_type, severity, summary). Customer-org-identifying. PHI-scrubbed pre-egress. | United States | **REQUIRED — structural** (site_id + partner_id identify CE). Engineering recommends BAA-on-file precondition before partner can configure PagerDuty routing |
| 12 | **Stripe, Inc.** | Billing and payment processing. | Billing contact info (name, email, payment method). No PHI. | United States | **Not required** — PCI-DSS scope |
| 13 | **Google LLC (Google OAuth / Workspace identity)** | Operator/partner SSO. `oauth_login.py:51` + `partner_auth.py:610` | OAuth identity grant (operator/partner email, name). OsirisCare does not transmit customer data to Google. | United States | **Not required** — identity-provider scope; documented for supply-chain transparency |
| 14 | **Microsoft Corporation (Azure AD / Microsoft Graph)** | Alternate operator/partner SSO via M365. `oauth_login.py:55-57` + `partner_auth.py:53` | Same as Google OAuth — identity grant only. | United States | **Not required** — identity-provider scope (distinct from Azure OpenAI data-plane relationship in entry #8) |
| 15 | **GitHub, Inc.** | Source code hosting + CI/CD (Actions). | Source code, build artifacts, deployment automation. No PHI. | United States | **Not required** |
| 16 | **SSL.com (DigitalSignTrust LLC)** | EV code signing for appliance ISO + agent binary. CodeSignTool / eSigner cloud signing. | Code-signing requests + EV identity verification. No customer data; no PHI. | United States | **Not required** — code-signing-tool scope; supply-chain trust anchor |
| 17 | **OpenTimestamps / Bitcoin network** | Cryptographic proof anchoring. | SHA-256 hashes of evidence bundles. One-way; not PHI. | Decentralized | **Not required** |
| 18 | **Let's Encrypt (ISRG)** | TLS certificate issuance via ACME. | Domain names for cert issuance. No PHI. | United States | **Not required** |
| 19 | **1Password (AgileBits Inc.)** | Operator-side credential vault (Vault unseal shares, SMTP creds, EV tokens). | Operator secrets only — never customer data, never PHI. | Canada (operator desktop) | **Not required** — operator-only access; documented for transparency on operator-side secret-management chain |

---

## §3 — Data-flow gates to harden against Rule 8 drift recurring

1. **`baa_subprocessors_lockstep` CI gate** — maintain a SINGLE canonical `docs/BAA_SUBPROCESSORS.md` (or the renamed registry doc) + a code-side constant `BACKEND_THIRD_PARTY_INTEGRATIONS` enumerating every external network call (Anthropic, OpenAI, Azure OpenAI, SendGrid, PrivateEmail, PagerDuty, Stripe, Google OAuth, Microsoft Azure AD, SSL.com, OpenTimestamps, Let's Encrypt). CI test (`tests/test_baa_subprocessors_lockstep.py`) verifies every external-call import in the codebase corresponds to a documented subprocessor.

2. **No-PHI-in-email egress gate** — AST gate detecting any string interpolation into email bodies/subjects containing customer-org-identifying fields. Pairs with the Rule 7 opaque-mode harmonization (Task #53). **Note: this gate does NOT obviate the SendGrid/Namecheap BAA requirement** — recipient addresses themselves structurally identify CEs regardless of body content.

3. **Per-subprocessor data-class invariant** — substrate invariant `subprocessor_dataflow_drift` (sev2) that periodically samples outbound traffic patterns and asserts the actual data class matches the documented class. Operator alerts if drift detected.

4. **Azure OpenAI HIPAA-tier check** — operator-config UI must surface the Azure OpenAI HIPAA tier requirement (entry #8) and refuse activation unless operator confirms HIPAA-tier deployment + Microsoft BAA on file.

5. **PagerDuty BAA-on-file precondition** — partner-side UI to configure PagerDuty routing must precondition on partner-BAA-with-PagerDuty being on file (partner-side, not OsirisCare-side).

---

## §4 — Open questions for inside counsel (post-master-BAA)

- (a) Are entries #9 (SendGrid) and #10 (PrivateEmail) BAA-required structurally based on recipient-address-identifies-CE, OR is opaque-mode + AST-gate-PHI-in-body sufficient mitigation to declassify? Engineering's draft position: structurally REQUIRED.
- (b) Entry #8 (Azure OpenAI) — is the conditional HIPAA-tier framing right, or should it always be classified REQUIRED regardless of operator-config? Engineering proposes conditional + UI gate.
- (c) Entry #11 (PagerDuty) — is the BAA-on-file partner-side precondition the right mitigation, or should OsirisCare hold its own BAA with PagerDuty as primary protection?
- (d) Entries #13 (Google OAuth) + #14 (Microsoft Azure AD) — operator/partner SSO is identity-provider scope only; is "Not required" defensible OR should operator/partner identity-grant be treated as BAA-relevant?
- (e) Entry #19 (1Password) — operator-side vault: is "Not required" defensible OR is operator-secret-management a BAA-relevant subprocessor relationship?
- (f) Should this registry be published as a static document OR served via a live `/legal/subprocessors` endpoint that auto-refreshes from `BACKEND_THIRD_PARTY_INTEGRATIONS` constant?

---

## §5 — Publish-now decision (Gate A consensus)

Per the v1 Gate A fork's PM + Inside-counsel lens consensus: **publish v2 NOW with the reframed title + master-BAA-in-drafting banner.** Do NOT publish v2 with the existing "BAA Exhibit" framing. Holding entirely is the wrong play (transparency loss + Rule 8 drift continues unfixed); publishing as-titled worsens the no-master-BAA exposure. Reframe + publish is the only path that improves posture on both axes simultaneously.

---

## §6 — Engineering action items post-Gate-B-APPROVE

- Update `docs/BAA_SUBPROCESSORS.md` → rename to `docs/SUBPROCESSORS.md` (or keep filename for compat) with v2 content + reframed title.
- Fix banned-language ("PHI never leaves" → "PHI scrubbed at edge by design").
- Update PHI-scrubber pattern-count language to "14 total = 12 regex in `compilePatterns()` + 2 contextual at `init()`" with source-line cite.
- Build `tests/test_baa_subprocessors_lockstep.py` CI gate.
- Build no-PHI-in-email egress AST gate (depends on Task #53 Rule 7 expansion).
- Build `subprocessor_dataflow_drift` substrate invariant.
- Operator-config UI: Azure OpenAI HIPAA-tier check (entry #8).
- Partner-config UI: PagerDuty BAA-on-file precondition (entry #11).
- Add `Effective Date: 2026-05-13` + commitment to quarterly re-audit + frontmatter per POSTURE_OVERLAY §8 (when that lands per Task #51).

---

## §7 — Class-B Gate B reviewer guidance

When the Class-B 7-lens Gate B fork reviews v2, verify:
- All 5 v1 P0 findings are closed (SendGrid polarity ✓, 5 missing subprocessors added ✓, reframe ✓, structural BAA reasoning ✓, scrubber-count cite ✓).
- No regressions vs v1 (entries still accurate, banned-language still scrubbed).
- The 6 open counsel questions in §4 are inside-counsel-routable (none cross into outside-counsel statutory-interpretation territory).
- Cross-fork consistency with BAA-drafting Gate A (this draft assumes master BAA is being drafted in parallel; that's consistent).
- Cross-fork consistency with POSTURE_OVERLAY Gate A (this draft's frontmatter posture references POSTURE_OVERLAY §8 standard; consistent).

— OsirisCare engineering
   on behalf of the privacy officer
   2026-05-13
