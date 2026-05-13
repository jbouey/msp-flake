# Data Flow Disclosure & Subprocessor Registry

**Entity:** OsirisCare ("Business Associate")
**Document Version:** v2.0
**Effective Date:** 2026-05-13
**Classification:** Exhibit A to the OsirisCare Master Business Associate Agreement (`docs/legal/MASTER_BAA_v1.0_INTERIM.md`).
**Re-audit cadence:** Quarterly. Next scheduled re-audit: 2026-08-13.

---

## 1. Purpose

This Data Flow Disclosure and Subprocessor Registry identifies all subprocessors engaged by OsirisCare in the delivery of its HIPAA compliance attestation substrate. Under HIPAA 45 CFR §164.502(e) and §164.504(e), business associates must maintain transparency regarding downstream entities that may access, store, process, or transmit Protected Health Information (PHI) or electronic Protected Health Information (ePHI) on behalf of covered entities.

OsirisCare commits to providing covered entities at least thirty (30) calendar days advance notice of any addition or removal of a subprocessor that handles PHI.

---

## 2. Subprocessor Registry — 19 entries

| # | Subprocessor | Service | Data Processed | Location | BAA Status |
|---|---|---|---|---|---|
| 1 | **Hetzner Online GmbH (Central Command VPS)** | Primary VPS hosting Docker stack: application server, PostgreSQL, MinIO, Caddy. | Compliance telemetry, incident metadata (PHI-scrubbed at appliance edge per §3), evidence bundles, tenant configuration, audit logs. | Germany / Finland (EU) | **Required** — data at rest on hosted infrastructure |
| 2 | **Hetzner Online GmbH (Vault Transit VPS)** | Secondary VPS hosting HashiCorp Vault Transit for Ed25519 non-exportable signing. WireGuard-internal. | Ed25519 private key material (non-exportable; sign/verify API only). No customer data; no PHI. | Germany / Finland (EU) | **Not required** — distinct machine, distinct data class; documented for transparency |
| 3 | **PostgreSQL (self-hosted)** | Primary relational database, hosted in Docker on the Central Command VPS. | All tenant data: site configurations, incidents, scores, users, audit logs, evidence chain. | Co-located with application | **N/A** — self-managed; no third-party access |
| 4 | **MinIO (self-hosted)** | Object storage for evidence artifacts, hosted in Docker on the Central Command VPS. | Evidence bundles, OpenTimestamps proof files. | Co-located | **N/A** — self-managed |
| 5 | **Caddy (self-hosted)** | TLS termination and reverse proxy. Docker on Central Command VPS. | Transit-only TLS + proxy. No data storage. | Co-located | **N/A** — self-managed |
| 6 | **Anthropic, PBC** | Claude API for L2 incident analysis (LLM planner). Primary LLM path when `ANTHROPIC_API_KEY` is set. | Incident metadata only (check type, severity, timestamps, remediation context). PHI scrubbed at appliance edge by 14-pattern scrubber before transmission. | United States | **Not required** — no PHI transmitted; substrate scrub posture verified |
| 7 | **OpenAI, Inc.** | Alternate LLM path when `OPENAI_API_KEY` is set instead of Anthropic. | Same data class as Anthropic. PHI scrubbed pre-egress. | United States | **Not required** — same posture as Anthropic; operator selects which LLM provider is active |
| 8 | **Microsoft Corporation (Azure OpenAI Service)** | Alternate LLM path when `AZURE_OPENAI_ENDPOINT` is set. | Same data class as Anthropic and OpenAI. PHI scrubbed pre-egress. | United States | **Required** by default. Downgrade-path: operator may downgrade to "Not required" upon confirming deployment uses Azure's HIPAA-aligned tier with Microsoft BAA on file (operator-config UI gate enforces this). Without HIPAA-tier confirmation, this entry is BAA-required. |
| 9 | **Twilio Inc. (SendGrid)** | **Primary email transport** when `SENDGRID_API_KEY` is set. Magic-link authentication, customer notifications, operator alerts. | Customer email addresses (structurally identifying), magic-link tokens, alert content. | United States | **Required — structural.** Recipient address structurally identifies the covered entity regardless of opaque-mode body content. |
| 10 | **Namecheap Inc. (PrivateEmail SMTP)** | Fallback email transport when SendGrid not configured. Currently active for operator alerts. | Same data class as SendGrid. | United States | **Required — structural** (same reasoning as SendGrid) |
| 11 | **PagerDuty, Inc.** | Operator/partner-configurable alert routing destination. | Alert event content (site_id, incident_type, severity, summary). Customer-org-identifying. PHI scrubbed pre-egress. | United States | **Required — structural.** Future engineering work (in progress): partner-config UI will require partner-side BAA-on-file precondition before partner can configure PagerDuty routing. |
| 12 | **Stripe, Inc.** | Billing and payment processing. | Billing contact information: name, email, payment method. No clinical or compliance data; no PHI. | United States | **Not required** — PCI-DSS scope; payment data only |
| 13 | **Google LLC (Google OAuth / Workspace identity)** | Operator/partner Single Sign-On via Google identity. | OAuth identity grant (operator/partner email, name). OsirisCare does not transmit customer data to Google. | United States | **Not required** — identity-provider scope; documented for supply-chain transparency |
| 14 | **Microsoft Corporation (Azure AD / Microsoft Graph identity)** | Alternate operator/partner SSO via Microsoft 365 identity. | Same as Google OAuth — identity grant only. | United States | **Not required** — identity-provider scope (distinct from Azure OpenAI data-plane relationship in entry #8) |
| 15 | **GitHub, Inc.** | Source code hosting and CI/CD (GitHub Actions). | Application source code, build artifacts, deployment automation. No PHI in repository. | United States | **Not required** — no PHI stored or processed |
| 16 | **SSL.com (DigitalSignTrust LLC)** | EV code signing certificate for appliance ISO and agent binary. CodeSignTool / eSigner cloud signing. | Code-signing requests + EV identity verification. No customer data; no PHI. | United States | **Not required** — code-signing-tool scope; supply-chain trust anchor |
| 17 | **OpenTimestamps / Bitcoin network** | Cryptographic proof anchoring for evidence chain integrity. | SHA-256 hashes of evidence bundles. One-way; not PHI. | Decentralized | **Not required** — SHA-256 hashes do not constitute PHI under HIPAA |
| 18 | **Let's Encrypt (ISRG)** | TLS certificate issuance via ACME protocol. | Domain names for cert issuance. No PHI. | United States | **Not required** — no PHI processed; domain validation only |
| 19 | **1Password (AgileBits Inc.)** | Operator-side credential vault (Vault unseal shares, SMTP credentials, EV signing tokens). | Operator secrets only — never customer data, never PHI. | Canada (operator desktop access) | **Not required** — operator-only access; documented for transparency on the operator-side secret-management chain |

---

## 3. PHI Data Flow

OsirisCare's substrate is architected such that PHI is scrubbed at the on-premises appliance edge before any data egresses to OsirisCare Central Command. Central Command holds no PHI under normal operation. This is an architectural commitment, not an absence-proof.

The scrubbing implementation applies **fourteen (14) pattern-matching rules** — twelve (12) regex patterns plus two (2) contextual patterns — covering Social Security Numbers, Medical Record Numbers, Patient IDs, telephone numbers, email addresses, credit card numbers, dates of birth, street addresses, ZIP+4 codes, account numbers, insurance IDs, Medicare Beneficiary Numbers, patient-identifying hostname tokens (PATIENT, ROOM, BED, WARD, DR, MR, MS), and patient-data URL path segments (`/patient/`, `/ehr/`, `/medical/`). Matched content is replaced with redacted placeholders and SHA-256-derived hash suffixes for one-way correlation without identification.

Authoritative source: `appliance/internal/phiscrub/scrubber.go` (12 regex patterns in `compilePatterns()` + 2 contextual patterns at package init). The full pattern catalogue is enumerated in the OsirisCare Master Business Associate Agreement Exhibit B.

What the central platform receives:

- **Compliance check results** — pass/fail status for each drift check. Contains check type and boolean outcome.
- **Incident metadata** — check type, severity level, timestamps, affected hostname, and remediation status. All fields processed through the appliance PHI scrubber before transmission.
- **Evidence bundles** — cryptographic hashes representing compliance state at a point in time. Bundles contain check results and system configuration summaries.
- **Device inventory** — hostnames, MAC addresses, IP addresses, and operating system versions. Infrastructure identifiers, not PHI under HIPAA.

---

## 4. Technical Controls

### 4.1 Encryption in transit and at rest
Modern TLS between appliances and the central platform; encrypted storage volumes for persisted Substrate data.

### 4.2 Access control
Row-Level Security at the database layer enforces tenant isolation. Bcrypt password hashing (12 rounds). Session-based authentication with HMAC-SHA256 session token hashing. Rate limiting on authentication failures.

### 4.3 Multi-Factor Authentication
TOTP-based 2FA available for all portal types (admin, partner, client).

### 4.4 Audit logging
Append-only audit tables record all administrative actions, authentication events, and data modifications. Immutability triggers prevent retroactive alteration of audit records.

### 4.5 PHI Scrubbing
The on-appliance PHI scrubber applies 14 pattern-matching rules (12 regex + 2 contextual) to all outbound data before it leaves the customer's network. Matched content is replaced with redacted placeholders and one-way hashes.

---

## 5. Future engineering work (in progress as of 2026-05-13)

These items are committed engineering work tied to this registry but not yet shipped. Customers may rely on the registry contents above; the items below harden the surrounding posture:

1. **`baa_subprocessors_lockstep` CI gate** — automated lockstep between this document, the backend's `BACKEND_THIRD_PARTY_INTEGRATIONS` constant, and the actual external-call set in the codebase. Prevents drift recurrence.
2. **No-PHI-in-email egress gate** — AST gate detecting any string interpolation into email bodies/subjects containing customer-org-identifying fields. Pairs with Rule 7 opaque-mode harmonization.
3. **Per-subprocessor data-class invariant** (`subprocessor_dataflow_drift` sev2) — substrate invariant that periodically samples outbound traffic patterns and asserts actual data class matches the documented class. Operator alerts if drift detected.
4. **Azure OpenAI HIPAA-tier check** — operator-config UI surfaces the Azure OpenAI HIPAA-tier requirement (entry #8) and refuses activation without operator confirmation of HIPAA-tier deployment + Microsoft BAA on file.
5. **PagerDuty BAA-on-file precondition** — partner-config UI for PagerDuty routing requires partner-side BAA with PagerDuty on file before activation.

---

## 6. Review Schedule

| Activity | Frequency | Responsible Party |
|---|---|---|
| Subprocessor registry re-audit | Quarterly | OsirisCare Compliance |
| BAA compliance audit | Annually | OsirisCare Compliance |
| Subprocessor change notification | 30 days advance notice to covered entities | OsirisCare Operations |
| Technical controls assessment | Semi-annually | OsirisCare Engineering |

Changes to this subprocessor registry, including the addition or removal of subprocessors, will be communicated to covered entities a minimum of thirty (30) calendar days prior to the effective date of the change. Covered entities retain the right to object to subprocessor changes as specified in their executed BAA.

---

## 7. Contact

For questions regarding this registry or to request an updated copy, contact:

**OsirisCare Compliance**
Email: compliance@osiriscare.net

---

*This document is Exhibit A to the OsirisCare Master Business Associate Agreement (`docs/legal/MASTER_BAA_v1.0_INTERIM.md`) and is subject to the terms and conditions of that agreement.*

— OsirisCare engineering
   on behalf of the privacy officer
   Effective 2026-05-13
