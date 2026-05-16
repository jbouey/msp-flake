# Sub-Processor List — HIPAA Business Associate Agreement

<!-- updated 2026-05-16 — Session-220 doc refresh -->

**Entity:** OsirisCare (MSP Compliance Platform)
**Document Version:** 2.0
**Effective Date:** 2026-05-13 (re-issued under Master BAA v1.0-INTERIM)
**Classification:** BAA Exhibit A — Sub-Processor Disclosure
**Master Instrument:** `docs/legal/MASTER_BAA_v1.0_INTERIM.md` (e-signed in the OsirisCare signup flow; v2.0 target 2026-06-03)
**Companion:** `docs/SUBPROCESSORS.md` (longer-form Data Flow Disclosure & 19-entry registry)

---

## 1. Purpose

This document identifies all sub-processors engaged by OsirisCare in the delivery of its HIPAA compliance attestation platform. Under HIPAA 45 CFR §164.502(e) and §164.504(e), business associates must maintain transparency regarding downstream entities that may access, store, process, or transmit Protected Health Information (PHI) or electronic Protected Health Information (ePHI) on behalf of covered entities.

This list is provided as Exhibit A to the Master BAA v1.0-INTERIM executed between OsirisCare and its covered-entity customers via e-signature in the signup flow. Customers receive at least thirty (30) calendar days' advance notice of any addition or removal of a sub-processor that handles PHI.

### 1.1 — Counsel Rule 8 classification convention

Sub-processors are classified by **actual data flow**, not by hopeful labeling (Counsel's 7 Hard Rules, Rule 8). Each entry below names the data class actually transmitted, not the data class the vendor's marketing implies.

- **PHI-receiver** — receives data that may include PHI; BAA execution with that sub-processor is required.
- **Metadata-receiver / structurally identifying** — receives no PHI body content but the routing metadata (e.g. recipient email address, alert destination) is structurally identifying of the covered entity. Treated as BAA-required.
- **Operational / no PHI** — receives only operator-side or infrastructure data; BAA not required but documented for supply-chain transparency.
- **Self-managed** — co-located service inside the OsirisCare trust boundary; no third-party access.

---

## 2. Sub-Processor Registry

| Sub-Processor | Service | Data Class (actual flow) | Location | BAA Status |
|---|---|---|---|---|
| **Hetzner Online GmbH — Central Command VPS** | Primary VPS hosting Docker stack (application server, PostgreSQL, MinIO, Caddy). | Compliance telemetry, incident metadata (PHI-scrubbed at appliance edge per §3), evidence bundles, tenant configuration, audit logs. | Germany / Finland (EU) | **Required — PHI-receiver** (data at rest, even after appliance-edge scrub) |
| **Hetzner Online GmbH — Vault Transit VPS** (89.167.76.203 / WG 10.100.0.3) | Secondary VPS hosting HashiCorp Vault Transit for Ed25519 non-exportable signing. WireGuard-internal. Shadow mode 2026-05; hot-cutover pending. | Ed25519 private key material (non-exportable; sign/verify API only). No customer data; no PHI. | Germany / Finland (EU) | **Not required — operational** (distinct machine, distinct data class) |
| **PostgreSQL** (self-hosted) | Primary relational database on Central Command VPS. | All tenant data: sites, incidents, scores, users, audit logs, evidence chain. | Co-located | **N/A — self-managed** |
| **MinIO** (self-hosted) | Object storage for evidence artifacts on Central Command VPS. | Evidence bundles, OpenTimestamps proof files. | Co-located | **N/A — self-managed** |
| **Caddy** (self-hosted) | TLS termination and reverse proxy. | Transit-only TLS + proxy. No data storage. | Co-located | **N/A — self-managed** |
| **Anthropic, PBC** | Claude API for L2 incident analysis (LLM planner). Primary LLM path when `ANTHROPIC_API_KEY` set. | Incident metadata only (check type, severity, timestamps, remediation context). PHI scrubbed at appliance edge by 14-pattern scrubber before transmission. | United States | **Not required — operational** (PHI scrubbed pre-egress; verified substrate posture) |
| **OpenAI, Inc.** | Alternate LLM path when `OPENAI_API_KEY` set instead of Anthropic. | Same data class as Anthropic. PHI scrubbed pre-egress. | United States | **Not required — operational** (operator selects active provider) |
| **Microsoft Corporation (Azure OpenAI)** | Alternate LLM path when `AZURE_OPENAI_ENDPOINT` set. | Same data class as Anthropic + OpenAI. PHI scrubbed pre-egress. | United States | **Required by default — operational only if** operator confirms HIPAA-tier deployment + Microsoft BAA on file (operator-config gate, in progress) |
| **Stripe, Inc.** (Hetzner-hosted webhook) | Billing and payment processing. PHI-free scope CHECK constraint at DB layer. | Billing contact: name, email, payment method. No clinical, no compliance, no PHI. | United States | **Not required — billing-only, no PHI** (PCI-DSS scope) |
| **Twilio Inc. (SendGrid)** | Primary email transport (magic-link auth, customer notifications, operator alerts) when `SENDGRID_API_KEY` set. | Recipient email address (structurally identifies CE), magic-link tokens, opaque-mode body. Per Counsel Rule 7, customer-facing email bodies are opaque-by-default. | United States | **Required — structural metadata-receiver** (recipient identifies CE regardless of body content) |
| **Namecheap Inc. (PrivateEmail SMTP)** | Fallback email transport when SendGrid not configured. Currently active for operator alerts. | Same class as SendGrid. | United States | **Required — structural metadata-receiver** |
| **PagerDuty, Inc.** | Operator/partner-configurable alert routing destination. | Alert event content (site_id, incident_type, severity, summary). CE-identifying. PHI scrubbed pre-egress. | United States | **Required — structural metadata-receiver** (partner-config BAA-on-file precondition in progress) |
| **Google LLC** (OAuth / Workspace identity) | Operator/partner Single Sign-On. | OAuth identity grant (operator/partner email + name). No customer data. | United States | **Not required — operational** (identity-provider scope) |
| **Microsoft Corporation** (Azure AD / Graph identity) | Alternate operator/partner SSO. | OAuth identity grant only. Distinct from the Azure OpenAI data-plane row above. | United States | **Not required — operational** |
| **GitHub, Inc.** | Source code hosting and CI/CD (GitHub Actions). Deploy workflow rsyncs to VPS on push to main. | Application source code, build artifacts, deployment automation. No PHI in repository. | United States | **Not required — operational** |
| **SSL.com (DigitalSignTrust LLC)** | EV code signing for appliance ISO + agent binary (CodeSignTool / eSigner). | Code-signing requests + EV identity verification. No customer data. | United States | **Not required — operational** (supply-chain trust anchor) |
| **OpenTimestamps / Bitcoin network** | Cryptographic proof anchoring for evidence chain integrity. Public chain; public-key-discoverable. | SHA-256 hashes of evidence bundles. One-way; not PHI under HIPAA. | Decentralized | **Not required — operational** |
| **Let's Encrypt (ISRG)** | TLS certificate issuance via ACME. | Domain names for cert issuance. No PHI. | United States | **Not required — operational** |
| **1Password (AgileBits Inc.)** | Operator-side credential vault (Vault unseal shares, SMTP credentials, EV signing tokens). | Operator secrets only — never customer data, never PHI. | Canada (operator desktop) | **Not required — operational** |

---

## 3. PHI Data Flow

OsirisCare is architected so that PHI is scrubbed at the on-premises appliance edge before any data egresses to OsirisCare Central Command. Central Command holds no PHI under normal operation. This is an architectural commitment, not an absence-proof (Counsel Rule 2: "PHI-free Central Command is a compiler rule, not a posture preference").

The scrubbing implementation applies **fourteen (14) pattern-matching rules** — twelve (12) regex patterns plus two (2) contextual patterns — covering Social Security Numbers, Medical Record Numbers, Patient IDs, telephone numbers, email addresses, credit card numbers, dates of birth, street addresses, ZIP+4 codes, account numbers, insurance IDs, Medicare Beneficiary Numbers, patient-identifying hostname tokens (PATIENT, ROOM, BED, WARD, DR, MR, MS), and patient-data URL path segments (`/patient/`, `/ehr/`, `/medical/`). Matched content is replaced with redacted placeholders and SHA-256-derived hash suffixes for one-way correlation without identification.

Authoritative source: `appliance/internal/phiscrub/scrubber.go` (12 regex patterns in `compilePatterns()` + 2 contextual patterns at package init). Full catalogue: Master BAA v1.0-INTERIM Exhibit B.

What Central Command receives after appliance-edge scrub:

- **Compliance check results** — pass/fail status for each drift check. Check type + boolean outcome.
- **Incident metadata** — check type, severity, timestamps, hostname, remediation status.
- **Evidence bundles** — Ed25519-signed, hash-chained, OTS-anchored. SHA-256 hashes of check results + system configuration summaries.
- **Device inventory** — hostnames, MAC addresses, IP addresses, OS versions. Infrastructure identifiers, not PHI under HIPAA.

---

## 4. Technical Controls

### 4.1 Encryption at Rest
PostgreSQL and MinIO data volumes are hosted on encrypted storage. Database backups are encrypted before storage.

### 4.2 Encryption in Transit
All communications between appliances and Central Command are encrypted using TLS 1.3, terminated by Caddy with auto-renewed certificates from Let's Encrypt. Internal service-to-service communication occurs over the Docker bridge network.

### 4.3 Access Control
- **Row-Level Security (RLS):** PostgreSQL enforces tenant isolation at the database layer. Org-scoped policies (`tenant_org_isolation` via `rls_site_belongs_to_current_org`) gate client-portal reads. Application connections use a restricted role (`mcp_app`) that cannot bypass RLS.
- **Authentication:** bcrypt (12 rounds) for password hashing. Session-based authentication with HMAC-SHA256 session token hashing. Rate limiting (5 failures triggers 15-minute lockout).
- **Multi-Factor Authentication:** TOTP-based 2FA available for all portal types (admin, partner, client). Admin-MFA override flow (mig 276) with attested chain.

### 4.4 Audit Logging
Append-only audit tables record administrative actions, authentication events, data modifications, and BAA-enforcement bypass events. Immutability triggers prevent retroactive alteration. Every `auditor_kit_download` writes a structured row to `admin_audit_log` with denormalized `site_id` + `client_org_id`.

### 4.5 PHI Scrubbing
The on-appliance PHI scrubber applies 14 pattern-matching rules to all outbound data before it leaves the customer's network. Matched content is replaced with redacted placeholders and one-way hashes.

### 4.6 Privileged-Action Chain of Custody
Five privileged order types (`enable_emergency_access`, `disable_emergency_access`, `bulk_remediation`, `signing_key_rotation`, `delegate_signing_key`) require an unbroken `client identity → policy approval → execution → attestation` chain enforced at CLI + API + DB-trigger layers (mig 175 + mig 305). Ed25519-signed, hash-chained, OTS-anchored.

### 4.7 BAA Enforcement (Counsel Rule 6 machine-enforcement)
Five sensitive workflows are gated on the covered entity's BAA execution status: `owner_transfer`, `cross_org_relocate`, `evidence_export`, `new_site_onboarding`, `new_credential_entry`. Build-time CI lockstep + runtime substrate invariant (`sensitive_workflow_advanced_without_baa`, sev1) prevent advancement without an active BAA signature. Source: `backend/baa_enforcement.py::BAA_GATED_WORKFLOWS`.

---

## 5. Review Schedule

| Activity | Frequency | Responsible Party |
|---|---|---|
| Sub-processor list re-audit (Counsel Rule 8 actual-flow check) | Quarterly | OsirisCare Compliance |
| BAA compliance audit | Annually | OsirisCare Compliance |
| Sub-processor change notification | 30 days advance notice to covered entities | OsirisCare Operations |
| Technical controls assessment | Semi-annually | OsirisCare Engineering |

Next scheduled re-audit: **2026-08-13**.

---

## 6. Contact

For questions regarding this sub-processor list or to request an updated copy, contact:

**OsirisCare Compliance**
Email: compliance@osiriscare.net

---

*This document is Exhibit A to the OsirisCare Master Business Associate Agreement v1.0-INTERIM (`docs/legal/MASTER_BAA_v1.0_INTERIM.md`). It is subject to the terms and conditions of that agreement.*
