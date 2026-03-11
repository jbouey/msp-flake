# Sub-Processor List — HIPAA Business Associate Agreement

**Entity:** OsirisCare (MSP Compliance Platform)
**Document Version:** 1.0
**Effective Date:** 2026-03-11
**Classification:** BAA Exhibit — Sub-Processor Disclosure

---

## 1. Purpose

This document identifies all sub-processors engaged by OsirisCare in the delivery of its HIPAA compliance attestation platform. Under HIPAA 45 CFR 164.502(e) and 164.504(e), business associates must maintain transparency regarding downstream entities that may access, store, process, or transmit Protected Health Information (PHI) or electronic Protected Health Information (ePHI) on behalf of covered entities.

OsirisCare operates a compliance attestation substrate for healthcare small and medium businesses. The platform provides drift detection, evidence-grade observability, and operator-authorized remediation workflows. This sub-processor list is provided as an exhibit to Business Associate Agreements executed between OsirisCare and its covered entity customers.

---

## 2. Sub-Processor Registry

| Sub-Processor | Service | Data Processed | Location | BAA Status |
|---|---|---|---|---|
| **Hetzner Online GmbH** | VPS hosting (compute and storage) | Compliance telemetry, incident metadata, evidence bundles, tenant configuration | Germany / Finland (EU) | **Required** — data at rest on hosted infrastructure |
| **PostgreSQL** (self-hosted) | Primary relational database, hosted in Docker on Hetzner VPS | All tenant data: site configurations, incident records, compliance scores, user accounts, audit logs | Co-located with application (Hetzner VPS) | **N/A** — self-managed; no third-party access |
| **MinIO** (self-hosted) | Object storage for evidence artifacts, hosted in Docker on Hetzner VPS | Evidence bundles, OpenTimestamps proof files | Co-located with application (Hetzner VPS) | **N/A** — self-managed; no third-party access |
| **Anthropic** | Claude API — L2 incident analysis (LLM planner) | Incident metadata only: check type, severity, timestamps, remediation context. All data is pre-scrubbed by the on-appliance PHI scrubber (12 regex patterns) before transmission | United States | **Not required** — no PHI transmitted; incident metadata contains no patient identifiers |
| **Stripe, Inc.** | Billing and payment processing | Billing contact information: name, email, payment method. No clinical or compliance data | United States | **Not required** — no PHI processed; Stripe handles payment data under PCI-DSS |
| **GitHub, Inc.** | Source code hosting and CI/CD (GitHub Actions) | Application source code, build artifacts, deployment automation. No PHI in repository | United States | **Not required** — no PHI stored or processed |
| **Caddy** (self-hosted) | TLS termination and reverse proxy | Transit only — terminates TLS connections and proxies to application services. No data storage | Co-located with application (Hetzner VPS) | **N/A** — self-managed; transit-only with no persistence |
| **OpenTimestamps / Bitcoin** | Cryptographic proof anchoring for evidence chain integrity | SHA-256 hashes of evidence bundles only. Hashes are one-way and cannot be reversed to recover source data | Decentralized | **Not required** — SHA-256 hashes do not constitute PHI under HIPAA |
| **Let's Encrypt (ISRG)** | TLS certificate issuance via ACME protocol | Domain names for certificate issuance. No application data or PHI | United States | **Not required** — no PHI processed; domain validation only |

---

## 3. PHI Data Flow

OsirisCare is architected so that PHI never leaves the customer's on-premises appliance. The central platform receives only de-identified operational telemetry:

- **Compliance check results** — Pass/fail status for each drift check (e.g., patching, encryption, firewall). Contains check type and boolean outcome. No patient data.
- **Incident metadata** — Check type, severity level, timestamps, affected hostname, and remediation status. All fields are processed through the on-appliance PHI scrubber (12 regex patterns covering SSNs, MRNs, names, dates of birth, and other HIPAA identifiers) before transmission. Matched patterns are replaced with `[REDACTED]` and a one-way hash.
- **Evidence bundles** — Cryptographic hashes representing compliance state at a point in time. Bundles contain check results and system configuration summaries, not clinical data or patient records.
- **Device inventory** — Hostnames, MAC addresses, IP addresses, and operating system versions. These are infrastructure identifiers, not PHI under HIPAA.

No clinical records, patient names, medical record numbers, diagnoses, treatment information, or other individually identifiable health information is transmitted to or stored on the central platform.

---

## 4. Technical Controls

### 4.1 Encryption at Rest
PostgreSQL and MinIO data volumes are hosted on encrypted storage. Database backups are encrypted before storage.

### 4.2 Encryption in Transit
All communications between appliances and the central platform are encrypted using TLS 1.3, terminated by Caddy with auto-renewed certificates from Let's Encrypt. Internal service-to-service communication within the VPS occurs over the Docker bridge network and does not traverse public networks.

### 4.3 Access Control
- **Row-Level Security (RLS):** PostgreSQL enforces tenant isolation at the database level. 27 tables have RLS enabled and forced. Application connections use a restricted role (`mcp_app`) that cannot bypass RLS.
- **Authentication:** bcrypt (12 rounds) for password hashing. Session-based authentication with HMAC-SHA256 session token hashing. Rate limiting (5 failures triggers 15-minute lockout).
- **Multi-Factor Authentication:** TOTP-based 2FA available for all portal types (admin, partner, client).

### 4.4 Audit Logging
Append-only audit tables record all administrative actions, authentication events, and data modifications. Immutability triggers prevent retroactive alteration of audit records.

### 4.5 PHI Scrubbing
The on-appliance PHI scrubber applies 12 regex patterns to all outbound data before it leaves the customer's network. Matched content is replaced with redacted placeholders and one-way hashes. This ensures that even in the event of a misconfiguration, PHI cannot reach the central platform.

---

## 5. Review Schedule

| Activity | Frequency | Responsible Party |
|---|---|---|
| Sub-processor list review | Quarterly | OsirisCare Compliance |
| BAA compliance audit | Annually | OsirisCare Compliance |
| Sub-processor change notification | 30 days advance notice to covered entities | OsirisCare Operations |
| Technical controls assessment | Semi-annually | OsirisCare Engineering |

Changes to this sub-processor list, including the addition or removal of sub-processors, will be communicated to covered entities a minimum of 30 calendar days prior to the effective date of the change. Covered entities retain the right to object to sub-processor changes as specified in their executed BAA.

---

## 6. Contact

For questions regarding this sub-processor list or to request an updated copy, contact:

**OsirisCare Compliance**
Email: compliance@osiriscare.net

---

*This document is an exhibit to the Business Associate Agreement between OsirisCare and the covered entity. It is subject to the terms and conditions of that agreement.*
