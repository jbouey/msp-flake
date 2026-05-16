# Data Flow Disclosure & Subprocessor Registry

<!-- updated 2026-05-16 — Session-220 doc refresh -->

**Entity:** OsirisCare ("Business Associate")
**Document Version:** v2.1
**Effective Date:** 2026-05-13 (re-affirmed 2026-05-16 with Counsel Rule 8 actual-flow re-audit)
**Classification:** Exhibit A to the OsirisCare Master Business Associate Agreement (`docs/legal/MASTER_BAA_v1.0_INTERIM.md`)
**Re-audit cadence:** Quarterly. Next scheduled re-audit: 2026-08-13.

---

## 1. Purpose

This Data Flow Disclosure and Subprocessor Registry identifies all subprocessors engaged by OsirisCare in the delivery of its HIPAA compliance attestation substrate. Under HIPAA 45 CFR §164.502(e) and §164.504(e), business associates must maintain transparency regarding downstream entities that may access, store, process, or transmit Protected Health Information (PHI) or electronic Protected Health Information (ePHI) on behalf of covered entities.

OsirisCare commits to providing covered entities at least thirty (30) calendar days advance notice of any addition or removal of a subprocessor that handles PHI.

### 1.1 — Classification convention (Counsel Rule 8)

Per Counsel's 7 Hard Rules (2026-05-13, Rule 8): "Subprocessors classified by ACTUAL data flow, not hopeful labeling." Every row below is classified by the data class that actually traverses the boundary, not by the vendor's marketing posture.

- **PHI-receiver** — receives data that may include PHI; BAA execution required.
- **Metadata-receiver — structural** — receives only routing metadata (recipient address, alert destination) but that metadata structurally identifies the covered entity; treated as BAA-required.
- **Operational / no-PHI** — receives only operator-side, identity-grant, or infrastructure data; BAA not required but documented for supply-chain transparency.
- **Self-managed** — co-located inside the OsirisCare trust boundary; no third-party access.

---

## 2. Subprocessor Registry — 19 entries

| # | Subprocessor | Service | Data Class (actual flow) | Location | BAA Status |
|---|---|---|---|---|---|
| 1 | **Hetzner Online GmbH (Central Command VPS)** | Primary VPS hosting Docker stack: application server, PostgreSQL, MinIO, Caddy. | Compliance telemetry, incident metadata (PHI-scrubbed at appliance edge per §3), evidence bundles, tenant configuration, audit logs. | Germany / Finland (EU) | **Required — PHI-receiver** (data at rest on hosted infrastructure) |
| 2 | **Hetzner Online GmbH (Vault Transit VPS)** | Secondary VPS hosting HashiCorp Vault Transit for Ed25519 non-exportable signing. 89.167.76.203 / WG 10.100.0.3. WireGuard-internal. Shadow mode 2026-05; hot-cutover pending. | Ed25519 private key material (non-exportable; sign/verify API only). No customer data; no PHI. | Germany / Finland (EU) | **Not required — operational** (distinct machine, distinct data class) |
| 3 | **PostgreSQL (self-hosted)** | Primary relational database, hosted in Docker on the Central Command VPS. | All tenant data: sites, incidents, scores, users, audit logs, evidence chain. | Co-located | **N/A — self-managed** |
| 4 | **MinIO (self-hosted)** | Object storage for evidence artifacts on Central Command VPS. | Evidence bundles, OpenTimestamps proof files. | Co-located | **N/A — self-managed** |
| 5 | **Caddy (self-hosted)** | TLS termination and reverse proxy. | Transit-only TLS + proxy. No data storage. | Co-located | **N/A — self-managed** |
| 6 | **Anthropic, PBC** | Claude API for L2 incident analysis (LLM planner). Primary LLM path when `ANTHROPIC_API_KEY` is set. | Incident metadata only (check type, severity, timestamps, remediation context). PHI scrubbed at appliance edge by 14-pattern scrubber (`appliance/internal/phiscrub/scrubber.go`) before transmission. | United States | **Not required — operational** (no PHI transmitted; substrate scrub posture verified) |
| 7 | **OpenAI, Inc.** | Alternate LLM path when `OPENAI_API_KEY` is set instead of Anthropic. | Same data class as Anthropic. PHI scrubbed pre-egress. | United States | **Not required — operational** (operator selects active LLM provider) |
| 8 | **Microsoft Corporation (Azure OpenAI Service)** | Alternate LLM path when `AZURE_OPENAI_ENDPOINT` is set. | Same data class as Anthropic / OpenAI. PHI scrubbed pre-egress. | United States | **Required by default.** Downgrade-path: operator may downgrade to "Not required — operational" upon confirming HIPAA-aligned tier with Microsoft BAA on file (operator-config UI gate enforces this). Without HIPAA-tier confirmation, this entry is BAA-required. |
| 9 | **Twilio Inc. (SendGrid)** | **Primary email transport** when `SENDGRID_API_KEY` is set. Magic-link authentication, customer notifications, operator alerts. | Customer email addresses (structurally identifying), magic-link tokens, opaque-mode body content. Per Counsel Rule 7, customer-facing email bodies are opaque-by-default. | United States | **Required — metadata-receiver / structural.** Recipient address structurally identifies the covered entity regardless of opaque-mode body content. |
| 10 | **Namecheap Inc. (PrivateEmail SMTP)** | Fallback email transport when SendGrid not configured. Currently active for operator alerts. | Same data class as SendGrid. | United States | **Required — metadata-receiver / structural** |
| 11 | **PagerDuty, Inc.** | Operator/partner-configurable alert routing destination. | Alert event content (site_id, incident_type, severity, summary). Customer-org-identifying. PHI scrubbed pre-egress. | United States | **Required — metadata-receiver / structural.** Partner-config UI BAA-on-file precondition is in progress. |
| 12 | **Stripe, Inc.** | Billing and payment processing for the OsirisCare subscription tiers (`osiris-pilot-onetime`, `osiris-essentials-monthly`, `osiris-professional-monthly`, `osiris-enterprise-monthly`). PHI-free scope enforced at DB CHECK constraint level. | Billing contact information: name, email, payment method. No clinical or compliance data; no PHI. | United States | **Not required — billing-only, no PHI** (PCI-DSS scope; payment data only) |
| 13 | **Google LLC (Google OAuth / Workspace identity)** | Operator/partner Single Sign-On via Google identity. | OAuth identity grant (operator/partner email + name). OsirisCare does not transmit customer data to Google. | United States | **Not required — operational** (identity-provider scope) |
| 14 | **Microsoft Corporation (Azure AD / Microsoft Graph identity)** | Alternate operator/partner SSO via Microsoft 365 identity. | Same as Google OAuth — identity grant only. | United States | **Not required — operational** (distinct from Azure OpenAI data-plane row #8) |
| 15 | **GitHub, Inc.** | Source code hosting and CI/CD (GitHub Actions). Deploy workflow rsyncs to VPS on push to main. | Application source code, build artifacts, deployment automation. No PHI in repository. | United States | **Not required — operational** |
| 16 | **SSL.com (DigitalSignTrust LLC)** | EV code signing certificate for appliance ISO and agent binary. CodeSignTool / eSigner cloud signing. | Code-signing requests + EV identity verification. No customer data; no PHI. | United States | **Not required — operational** (supply-chain trust anchor) |
| 17 | **OpenTimestamps / Bitcoin network** | Cryptographic proof anchoring for evidence chain integrity. Public chain; public-key-discoverable. | SHA-256 hashes of evidence bundles. One-way; not PHI under HIPAA. | Decentralized | **Not required — operational** |
| 18 | **Let's Encrypt (ISRG)** | TLS certificate issuance via ACME protocol. | Domain names for cert issuance. No PHI. | United States | **Not required — operational** |
| 19 | **1Password (AgileBits Inc.)** | Operator-side credential vault (Vault unseal shares, SMTP credentials, EV signing tokens, owner of Vault unseal share material). | Operator secrets only — never customer data, never PHI. | Canada (operator desktop access) | **Not required — operational** (operator-only access; documented for supply-chain transparency) |

---

## 3. PHI Data Flow

OsirisCare's substrate is architected such that PHI is scrubbed at the on-premises appliance edge before any data egresses to OsirisCare Central Command. Central Command holds no PHI under normal operation. This is an architectural commitment, not an absence-proof (Counsel's 7 Hard Rules, Rule 2: "PHI-free Central Command is a compiler rule, not a posture preference").

The scrubbing implementation applies **fourteen (14) pattern-matching rules** — twelve (12) regex patterns plus two (2) contextual patterns — covering Social Security Numbers, Medical Record Numbers, Patient IDs, telephone numbers, email addresses, credit card numbers, dates of birth, street addresses, ZIP+4 codes, account numbers, insurance IDs, Medicare Beneficiary Numbers, patient-identifying hostname tokens (PATIENT, ROOM, BED, WARD, DR, MR, MS), and patient-data URL path segments (`/patient/`, `/ehr/`, `/medical/`). Matched content is replaced with redacted placeholders and SHA-256-derived hash suffixes for one-way correlation without identification.

Authoritative source: `appliance/internal/phiscrub/scrubber.go` (12 regex patterns in `compilePatterns()` + 2 contextual patterns at package init). The full pattern catalogue is enumerated in the OsirisCare Master Business Associate Agreement Exhibit B.

What the central platform receives:

- **Compliance check results** — pass/fail status for each drift check. Contains check type and boolean outcome.
- **Incident metadata** — check type, severity level, timestamps, affected hostname, and remediation status. All fields processed through the appliance PHI scrubber before transmission.
- **Evidence bundles** — Ed25519-signed, hash-chained, OpenTimestamps-anchored cryptographic representations of compliance state at a point in time. Contain check results and system configuration summaries.
- **Device inventory** — hostnames, MAC addresses, IP addresses, OS versions. Infrastructure identifiers, not PHI under HIPAA. Deduplicated into `canonical_devices` (mig 319) as the device-count source of truth.

---

## 4. Technical Controls

### 4.1 Encryption in transit and at rest
TLS 1.3 between appliances and Central Command; encrypted storage volumes for persisted Substrate data.

### 4.2 Access control
Row-Level Security at the database layer enforces tenant isolation. Org-scoped policies (`tenant_org_isolation` via `rls_site_belongs_to_current_org`) gate client-portal reads (mig 278). Bcrypt password hashing (12 rounds). Session-based authentication with HMAC-SHA256 session token hashing. Rate limiting on authentication failures.

### 4.3 Multi-Factor Authentication
TOTP-based 2FA available for all portal types (admin, partner, client). Admin-MFA override flow (mig 276) with attested chain.

### 4.4 Audit logging
Append-only audit tables record administrative actions, authentication events, data modifications, BAA-enforcement bypass events, and auditor-kit downloads. Immutability triggers prevent retroactive alteration.

### 4.5 PHI Scrubbing
The on-appliance PHI scrubber applies 14 pattern-matching rules (12 regex + 2 contextual) to all outbound data before it leaves the customer's network. Matched content is replaced with redacted placeholders and one-way hashes.

### 4.6 Privileged-action chain of custody
Five privileged order types (`enable_emergency_access`, `disable_emergency_access`, `bulk_remediation`, `signing_key_rotation`, `delegate_signing_key`) require an unbroken `client identity → policy approval → execution → attestation` chain enforced at CLI (`backend/fleet_cli.py`) + API (`backend/privileged_access_api.py`) + DB-trigger (mig 175 `trg_enforce_privileged_chain` + mig 305) layers. Lockstep CI gate enforces three-list parity.

### 4.7 BAA enforcement (Counsel Rule 6)
Five sensitive workflows are machine-gated on the covered entity's BAA execution status: `owner_transfer`, `cross_org_relocate`, `evidence_export`, `new_site_onboarding`, `new_credential_entry`. Build-time CI lockstep (`tests/test_baa_gated_workflows_lockstep.py`) + runtime substrate invariant (`sensitive_workflow_advanced_without_baa`, sev1) prevent advancement without an active BAA signature. Source: `backend/baa_enforcement.py::BAA_GATED_WORKFLOWS`. Cliff: 2026-06-12 (30 days after v1.0-INTERIM effective date).

---

## 5. Future engineering work (in progress as of 2026-05-16)

These items are committed engineering work tied to this registry but not yet shipped. Customers may rely on the registry contents above; the items below harden the surrounding posture:

1. **Master BAA v2.0** — counsel-hardened commercial/legal terms (term, termination, indemnity limits, audit rights, governing law, dispute resolution). Target 2026-06-03. Drafting preconditions at `docs/legal/v2.0-hardening-prerequisites.md` (PRE-1: D1 heartbeat-signature backend verification ≥7-day clean soak before any per-event/per-heartbeat verification language).
2. **`baa_subprocessors_lockstep` CI gate** — automated lockstep between this document, the backend's `BACKEND_THIRD_PARTY_INTEGRATIONS` constant, and the actual external-call set in the codebase. Prevents drift recurrence.
3. **No-PHI-in-email egress gate** — AST gate detecting any string interpolation into email bodies/subjects containing customer-org-identifying fields. Pairs with Rule 7 opaque-mode harmonization (already shipped for `cross_org_site_relocate.py`, `client_owner_transfer.py`, `client_user_email_rename.py`).
4. **Per-subprocessor data-class invariant** (`subprocessor_dataflow_drift` sev2) — substrate invariant that periodically samples outbound traffic patterns and asserts actual data class matches the documented class. Operator alerts if drift detected.
5. **Azure OpenAI HIPAA-tier check** — operator-config UI surfaces the Azure OpenAI HIPAA-tier requirement (entry #8) and refuses activation without operator confirmation of HIPAA-tier deployment + Microsoft BAA on file.
6. **PagerDuty BAA-on-file precondition** — partner-config UI for PagerDuty routing requires partner-side BAA with PagerDuty on file before activation.
7. **Vault Transit hot-cutover** — promote shadow-mode Hetzner Vault instance (entry #2) to authoritative Ed25519 signer; current cutover gated on read-replica + dual-signer soak.

---

## 6. Review Schedule

| Activity | Frequency | Responsible Party |
|---|---|---|
| Subprocessor registry re-audit (Counsel Rule 8 actual-flow check) | Quarterly | OsirisCare Compliance |
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

*This document is Exhibit A to the OsirisCare Master Business Associate Agreement (`docs/legal/MASTER_BAA_v1.0_INTERIM.md`, v1.0-INTERIM effective 2026-05-13; v2.0 target 2026-06-03) and is subject to the terms and conditions of that agreement.*

— OsirisCare engineering
   on behalf of the privacy officer
   Effective 2026-05-13 — re-audited 2026-05-16
