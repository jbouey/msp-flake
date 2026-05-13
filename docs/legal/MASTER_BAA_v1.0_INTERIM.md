# Business Associate Agreement (v1.0-INTERIM-2026-05-13)

> **Version status:** v1.0 INTERIM. This is a **HIPAA-core compliance instrument** derived from the U.S. Department of Health & Human Services sample BAA contract provisions (45 CFR §164.504(e)(1)). It is NOT the commercial/legal completion. Outside HIPAA counsel is hardening the commercial/legal terms (term, termination, indemnity limits, audit rights, governing law, dispute resolution) within 14-21 days of this version's effective date. v2.0 will supersede v1.0 once counsel-hardening lands.

> **Effective Date:** 2026-05-13
> **Last Verified:** 2026-05-13
> **Decay-after:** 14 days (interim) — superseded by counsel-hardened v2.0
> **Issuer:** OsirisCare ("Business Associate")
> **Status:** EFFECTIVE upon publication; binding upon customer e-signature in the OsirisCare signup flow.

---

## Article 1 — Preamble and Definitions

This Business Associate Agreement ("Agreement") is entered into between **OsirisCare** ("Business Associate" or "BA") and the customer organization ("Covered Entity" or "CE") that has executed the signup flow at https://www.osiriscare.net and electronically signed this Agreement.

### 1.1 — Definitions

Capitalized terms used in this Agreement but not otherwise defined have the meanings ascribed to them in the HIPAA Privacy, Security, Breach Notification, and Enforcement Rules at 45 CFR Parts 160 and 164 (collectively, "HIPAA Rules"). Without limiting the foregoing:

- **"Protected Health Information" (PHI)** has the meaning given in 45 CFR §160.103, limited to PHI created, received, maintained, or transmitted by Business Associate on behalf of Covered Entity.
- **"Electronic Protected Health Information" (ePHI)** has the meaning given in 45 CFR §160.103.
- **"Designated Record Set"** has the meaning given in 45 CFR §164.501.
- **"Required by Law"** has the meaning given in 45 CFR §164.103.
- **"Secretary"** means the Secretary of the U.S. Department of Health & Human Services or designee.
- **"Subcontractor"** means a person to whom Business Associate delegates a function, activity, or service involving PHI, other than as a member of the workforce of Business Associate.
- **"Substrate"** means the OsirisCare compliance attestation platform — the technical infrastructure that performs drift detection, evidence capture, and operator-authorized remediation workflows on Covered Entity's behalf, as described in the OsirisCare Subprocessor Registry (Exhibit A) and Data Flow Disclosure.
- **"Appliance"** means the on-premises OsirisCare device installed on Covered Entity's network, which performs PHI scrubbing at the network egress boundary before any data is transmitted to the OsirisCare Central Command.

### 1.2 — Substrate posture acknowledgment

Covered Entity acknowledges and agrees that the OsirisCare platform is architected such that **PHI is scrubbed at the Appliance edge by design** before any data egresses to OsirisCare Central Command. Under normal operation, OsirisCare Central Command does not receive, maintain, or transmit PHI. Business Associate makes this an architectural commitment and not an absence-proof. The technical implementation is documented in Exhibit B (Data Flow Disclosure).

---

## Article 2 — Permitted Uses and Disclosures of PHI by Business Associate

### 2.1 — Performance of services for Covered Entity

Business Associate may use and disclose PHI only as follows:

(a) **To perform the services for Covered Entity** as set forth in the OsirisCare Master Services Agreement or service description, including drift detection, evidence-grade observability, operator-authorized remediation workflows, and audit-supportive technical evidence generation.

(b) **For the proper management and administration of Business Associate** or to carry out the legal responsibilities of Business Associate, provided that:
  (i) any disclosure is Required by Law, or
  (ii) Business Associate obtains reasonable assurances from the recipient that the PHI will be held confidentially and used or further disclosed only as Required by Law or for the purpose for which it was disclosed, and that the recipient will notify Business Associate of any instances of which it is aware in which the confidentiality has been breached.

(c) **To provide data aggregation services** relating to the health care operations of Covered Entity, as permitted by 45 CFR §164.504(e)(2)(i)(B).

(d) **To report violations of law** to appropriate federal and state authorities, consistent with 45 CFR §164.502(j)(1).

### 2.2 — Minimum necessary

Business Associate will use, disclose, and request only the minimum necessary PHI to accomplish the intended purpose of each use, disclosure, or request, consistent with 45 CFR §164.502(b) and §164.514(d).

---

## Article 3 — Obligations and Activities of Business Associate

### 3.1 — Use limitation

Business Associate shall not use or disclose PHI other than as permitted or required by this Agreement or as Required by Law.

### 3.2 — Safeguards

Business Associate shall use appropriate administrative, physical, and technical safeguards, and comply with Subpart C of 45 CFR Part 164 (the HIPAA Security Rule) with respect to ePHI, to prevent use or disclosure of PHI other than as provided for by this Agreement. The categories of safeguards Business Associate maintains include (without limitation): Appliance-side PHI scrubbing at the network egress boundary; encryption in transit; encryption at rest; database-layer tenant isolation; append-only audit logging; and cryptographic attestation chains for evidence-grade observability.

The specific algorithms, parameters, and implementation details of these safeguards are documented in Exhibit B (Data Flow Disclosure) and may be rotated or upgraded over time consistent with industry best practice and Subpart C requirements without requiring amendment of this Agreement.

### 3.3 — Reporting

Business Associate shall report to Covered Entity:

(a) **Use or disclosure of PHI not provided for by this Agreement** of which Business Associate becomes aware, including any Security Incident, in accordance with 45 CFR §164.504(e)(2)(ii)(C) and §164.314(a)(2)(i)(C). Reporting shall occur without unreasonable delay and in no case later than thirty (30) calendar days after discovery, except where shorter timing is Required by Law (including breach notification under 45 CFR §164.410). **[v2.0 hardening note: the 30-day outer bound is at the slow edge of OCR-tolerable practice. Outside counsel is reviewing whether to tighten this to ten (10) business days in v2.0 commercial/legal hardening per audit-grade norms.]**

(b) **Breaches of Unsecured PHI** as required by 45 CFR §164.410, including the information specified in §164.410(c). Notification shall be without unreasonable delay and in no case later than sixty (60) calendar days after discovery.

### 3.4 — Subcontractors

In accordance with 45 CFR §164.502(e)(1)(ii) and §164.308(b)(2), Business Associate shall ensure that any Subcontractor that creates, receives, maintains, or transmits PHI on behalf of Business Associate agrees in writing to the same restrictions, conditions, and requirements that apply to Business Associate with respect to such information.

The current list of Business Associate's Subcontractors is maintained in **Exhibit A — Subprocessor Registry**. Business Associate shall provide Covered Entity with at least thirty (30) days advance notice of any addition or removal of a Subcontractor that handles PHI.

### 3.5 — Individual access (§164.524)

Within fifteen (15) business days of a request from Covered Entity, Business Associate shall make available PHI in a Designated Record Set to Covered Entity (or, if directed by Covered Entity, to an Individual) as necessary to satisfy Covered Entity's obligations under 45 CFR §164.524. Because the OsirisCare Substrate is architected to be PHI-free at Central Command (per Article 1.2), Business Associate's records in scope under this provision are limited to any incidental PHI that may have been retained outside the scrubbing boundary.

### 3.6 — Amendment (§164.526)

Within thirty (30) business days of a request from Covered Entity, Business Associate shall make any amendment to PHI in a Designated Record Set that Covered Entity directs or agrees to in accordance with 45 CFR §164.526.

### 3.7 — Accounting of disclosures (§164.528)

Within sixty (60) business days of a request from Covered Entity, Business Associate shall document and make available the information required for Covered Entity to provide an accounting of disclosures in accordance with 45 CFR §164.528.

### 3.8 — HHS access

Business Associate shall make its internal practices, books, and records relating to the use and disclosure of PHI available to the Secretary for purposes of determining Covered Entity's compliance with the HIPAA Rules.

### 3.9 — Mitigation

Business Associate shall mitigate, to the extent practicable, any harmful effect known to Business Associate of a use or disclosure of PHI by Business Associate in violation of this Agreement.

---

## Article 4 — Obligations of Covered Entity

### 4.1 — Notice of Privacy Practices

Covered Entity shall notify Business Associate of any limitation(s) in the Notice of Privacy Practices of Covered Entity under 45 CFR §164.520, to the extent that such limitation may affect Business Associate's use or disclosure of PHI.

### 4.2 — Individual permissions

Covered Entity shall notify Business Associate of any changes in, or revocation of, permission by an Individual to use or disclose PHI, to the extent that such changes may affect Business Associate's use or disclosure of PHI.

### 4.3 — Restrictions

Covered Entity shall notify Business Associate of any restriction on the use or disclosure of PHI that Covered Entity has agreed to in accordance with 45 CFR §164.522, to the extent that such restriction may affect Business Associate's use or disclosure of PHI.

### 4.4 — Permissible requests

Covered Entity shall not request Business Associate to use or disclose PHI in any manner that would not be permissible under the HIPAA Rules if done by Covered Entity, except as permitted by 45 CFR §164.504(e)(4) (data aggregation or management/administration of Business Associate).

---

## Article 5 — Term and Termination

### 5.1 — Term

This Agreement is effective as of the date the Covered Entity executes electronic signature in the OsirisCare signup flow ("Effective Date") and shall remain in effect for the duration of Covered Entity's active OsirisCare subscription, unless earlier terminated as provided herein.

**[COMMERCIAL TERM — outside-counsel hardening required:** Specific contract term, renewal mechanism, and notice periods to be finalized in v2.0. Until v2.0 lands, the Agreement renews automatically on the same cadence as Covered Entity's billing relationship, terminable on thirty (30) days written notice by either party.**]**

### 5.2 — Termination for cause

Upon Covered Entity's knowledge of a material breach by Business Associate, Covered Entity shall either:

(a) Provide an opportunity for Business Associate to cure the breach or end the violation. If Business Associate does not cure the breach or end the violation within a reasonable time, Covered Entity shall terminate this Agreement; or

(b) If cure is not feasible, immediately terminate this Agreement.

The same termination rights flow in the reverse direction to Business Associate upon Covered Entity's material breach.

### 5.3 — Obligations upon termination

Upon termination of this Agreement for any reason, Business Associate shall:

(a) **Return or destroy all PHI** received from Covered Entity, or created, maintained, or received by Business Associate on behalf of Covered Entity, that Business Associate still maintains in any form, and retain no copies.

(b) **If return or destruction is infeasible**, Business Associate shall extend the protections of this Agreement to such PHI and limit further uses and disclosures to those purposes that make the return or destruction infeasible.

(c) Given the OsirisCare Substrate's architectural commitment under Article 1.2, the practical effect of this provision is: (i) Business Associate emits the `org_deprovisioned` chain event as the destruction attestation; (ii) Appliance-side wipe receipt confirms on-premises Appliance PHI destruction; (iii) the WORM evidence chain retained at Central Command (PHI-scrubbed by design) is acknowledged by both Parties as supporting Covered Entity's §164.530(j) six (6) year records-retention obligation. Business Associate maintains this WORM evidence in service of Covered Entity's retention obligation; Business Associate will not unilaterally delete such evidence prior to Covered Entity's retention period expiring without Covered Entity's written direction.

### 5.4 — Survival

The respective rights and obligations of Business Associate under Article 3 and Article 5.3 of this Agreement shall survive termination of this Agreement.

---

## Article 6 — Notices

Notices required under this Agreement shall be delivered to:

- **To Business Associate:** compliance@osiriscare.net
- **To Covered Entity:** the primary contact email and BAA-signer email on file in the OsirisCare account.

Routine operational notifications may be delivered via in-product banners and email; legal-class notices (breach notification, termination, material breach cure requests) require email delivery to the addresses above and shall not be considered opaque-mode for purposes of routing certainty.

---

## Article 7 — Miscellaneous

### 7.1 — Regulatory references

Regulatory references herein to the HIPAA Rules are to those Rules as amended from time to time.

### 7.2 — Amendment

The Parties agree to take such action as is necessary to amend this Agreement from time to time as is necessary for compliance with the requirements of the HIPAA Rules. Material amendments require electronic re-signature by Covered Entity to take effect.

### 7.3 — Interpretation

Any ambiguity in this Agreement shall be resolved to permit Business Associate and Covered Entity to comply with the HIPAA Rules.

### 7.4 — No third-party beneficiaries

Nothing in this Agreement is intended to create third-party beneficiary rights. Individuals whose PHI is subject to this Agreement do not have rights of enforcement under this Agreement (their rights under HIPAA remain unaffected).

### 7.5 — Governing law

**[COMMERCIAL TERM — outside-counsel hardening required:** Governing-law clause and venue selection to be finalized in v2.0.**]**

### 7.6 — Indemnification

**[COMMERCIAL TERM — outside-counsel hardening required:** Mutual indemnification, limitations of liability, and insurance requirements to be finalized in v2.0.**]**

### 7.7 — Audit rights

**[COMMERCIAL TERM — outside-counsel hardening required:** Audit-rights scope (frequency, notice, scope-of-records, on-site vs remote, cost allocation) to be finalized in v2.0. Until v2.0 lands, Business Associate commits to providing to Covered Entity, upon reasonable request: (i) the Subprocessor Registry (Exhibit A); (ii) Substrate posture documentation; (iii) attestation chain export via the standard auditor kit endpoint; (iv) any breach notifications, all without additional cost.**]**

### 7.8 — Severability

If any provision of this Agreement is held to be invalid or unenforceable, the remainder shall continue in full force and effect.

### 7.9 — Entire agreement

This Agreement, together with the OsirisCare Master Services Agreement (when executed) and the Exhibits attached hereto, constitutes the entire agreement between the Parties with respect to the subject matter hereof.

---

## Article 8 — Bridge Clause for Prior Acknowledgments

### 8.1 — Supersession

Effective upon Covered Entity's electronic signature of this v1.0-INTERIM Agreement, **this Agreement supersedes and replaces** any prior click-through acknowledgment Covered Entity executed at the OsirisCare signup flow under BAA version `v1.0-2026-04-15` ("Prior Acknowledgment"). The Prior Acknowledgment was a five-bullet statement of intent referenced at /legal/baa version v1.0-2026-04-15.

### 8.2 — Ratification of operation period

The Parties ratify, as of the Effective Date of this v1.0-INTERIM Agreement, the period between Covered Entity's Prior Acknowledgment date and this Agreement's Effective Date. The Parties acknowledge that:

(a) Covered Entity's Prior Acknowledgment constituted evidence of intent and part performance toward a Business Associate Agreement, but was insufficient as a complete HIPAA-compliant Business Associate Agreement under 45 CFR §164.504(e).

(b) Business Associate operated under the architectural commitments and technical safeguards described in Article 1.2 and Exhibit B throughout the Prior Acknowledgment period.

(c) The terms of this v1.0-INTERIM Agreement, including Article 3 (Obligations and Activities of Business Associate), are deemed to have governed the Prior Acknowledgment period to the extent consistent with the Prior Acknowledgment's recitals.

(d) No party admits or asserts any prior-period non-compliance by virtue of executing this Bridge Clause; the Bridge Clause memorializes the contractual terms that the Prior Acknowledgment evidenced an intent to bind.

### 8.3 — Notice to existing customers

Covered Entities who executed the Prior Acknowledgment will receive in-product banner + email notification within seven (7) days of this v1.0-INTERIM Agreement's effective date, with the following honest-anchor language (counsel-approved 2026-05-13):

> *"Prior acknowledgment is being replaced with a formal contract text."*
>
> *"Re-signing is required to keep records current."*

Customers have thirty (30) days from notice to execute electronic signature of this Agreement. Customers who do not re-sign by Day 30 will be blocked from "sensitive workflow advancement" as enumerated in Exhibit C (BAA Gated Workflows). Existing-data access remains unaffected.

---

## Article 9 — Versioning and Future Amendments

### 9.1 — Version

This Agreement is **v1.0-INTERIM-2026-05-13**. It is the HIPAA-core compliance instrument derived from the U.S. Department of Health & Human Services sample BAA contract provisions (45 CFR §164.504(e)(1)).

### 9.2 — Pending v2.0

OsirisCare has engaged outside HIPAA counsel to harden this v1.0-INTERIM Agreement with commercial/legal completion (term, termination, indemnity limits, audit rights, governing law, dispute resolution). Target effective date for v2.0: 2026-06-03 (21 days from this v1.0-INTERIM effective date). Covered Entities will be notified prior to v2.0 effective date and will re-sign on next portal login.

### 9.3 — Substantial v1.0-to-v2.0 changes

Substantial changes from v1.0-INTERIM to v2.0 will be enumerated in a change-log appended to v2.0. Material changes that alter Covered Entity's substantive rights or obligations require explicit re-acknowledgment per Article 7.2.

---

## Exhibit A — Subprocessor Registry

The current Subprocessor Registry is published in the OsirisCare engineering repository at `docs/SUBPROCESSORS.md` and will be served at `/legal/subprocessors` once that route ships. As of this Agreement's effective date (2026-05-13), the registry enumerates 19 entries: Hetzner Online GmbH (Central Command + Vault Transit VPS), self-hosted PostgreSQL / MinIO / Caddy, Anthropic, OpenAI, Microsoft Azure OpenAI Service, Twilio (SendGrid), Namecheap PrivateEmail, PagerDuty, Stripe, Google (OAuth/Workspace identity), Microsoft (Azure AD / Graph identity), GitHub, SSL.com, OpenTimestamps, Let's Encrypt, and 1Password. Per-entry data-flow classification, location, and BAA-required status are documented in the registry.

OsirisCare commits to a quarterly re-audit cadence on the Subprocessor Registry and to providing Covered Entity at least thirty (30) days advance notice of any addition or removal of a Subprocessor that handles PHI.

---

## Exhibit B — Data Flow Disclosure (PHI Scrubbing at the Appliance Edge)

### B.1 — Architectural commitment

OsirisCare's Substrate is architected such that PHI is scrubbed at the on-premises Appliance edge before any data egresses to OsirisCare Central Command. Under normal operation, OsirisCare Central Command does not receive, maintain, or transmit raw PHI. This is an architectural commitment, not an absence-proof.

### B.2 — Scrubbing pattern catalogue (as of v1.0-INTERIM effective date)

The scrubbing implementation applies **fourteen (14) pattern-matching rules** — twelve (12) regex patterns plus two (2) contextual patterns:

**Twelve regex patterns:**

| # | Category | Pattern intent | Redaction tag |
|---|---|---|---|
| 1 | SSN | Social Security Numbers | `[SSN-REDACTED-{hash}]` |
| 2 | MRN | Medical Record Numbers | `[MRN-REDACTED-{hash}]` |
| 3 | Patient ID | Patient identifier fields | `[PATIENT-ID-REDACTED-{hash}]` |
| 4 | Phone | Telephone numbers (multiple formats) | `[PHONE-REDACTED-{hash}]` |
| 5 | Email | Email addresses | `[EMAIL-REDACTED-{hash}]` |
| 6 | Credit card | Credit card numbers (multiple formats) | `[CC-REDACTED-{hash}]` |
| 7 | DOB | Dates of birth | `[DOB-REDACTED-{hash}]` |
| 8 | Address | Street addresses | `[ADDRESS-REDACTED-{hash}]` |
| 9 | ZIP+4 | ZIP+4 codes | `[ZIP-REDACTED-{hash}]` |
| 10 | Account number | Account / Acct numbers | `[ACCOUNT-REDACTED-{hash}]` |
| 11 | Insurance ID | Insurance / Policy ID | `[INSURANCE-REDACTED-{hash}]` |
| 12 | Medicare | Medicare Beneficiary Numbers | `[MEDICARE-REDACTED-{hash}]` |

**Two contextual patterns:**

| # | Category | Pattern intent | Redaction tag |
|---|---|---|---|
| 13 | Hostname | Patient-identifying hostname tokens (PATIENT, ROOM, BED, WARD, DR, MR, MS) | `[HOSTNAME-REDACTED-{hash}]` |
| 14 | Path segment | Patient-data URL path segments (`/patient/`, `/ehr/`, `/medical/`) | `[PATH-REDACTED-{hash}]` |

Matched content is replaced with redacted placeholders. The `{hash}` suffix is the first 4 bytes of the SHA-256 hash of the matched content (one-way correlation without identification — enables Business Associate to confirm two redactions correspond to the same underlying value without recovering the value).

### B.3 — Source-of-truth

Business Associate maintains the scrubbing implementation as version-controlled source code. The authoritative implementation is published at the OsirisCare engineering repository in the file `appliance/internal/phiscrub/scrubber.go`. The pattern catalogue above is a snapshot of the v1.0-INTERIM-effective implementation; the pattern set may be expanded (never narrowed) over time consistent with industry best practice and Subpart C requirements without requiring amendment of this Agreement. New pattern additions are documented in Business Associate's changelog and communicated to Covered Entity in the same channel as Subprocessor changes (Exhibit A § quarterly re-audit + thirty-day advance notice).

### B.4 — Other technical safeguards

In addition to PHI scrubbing, the Substrate maintains the following categories of technical safeguard. Specific algorithms and parameters are version-controlled in the engineering repository and may be rotated:

- **Encryption in transit:** modern TLS between Appliance and Central Command.
- **Encryption at rest:** persisted Substrate data is stored on encrypted volumes.
- **Database tenant isolation:** row-level security policies enforce per-tenant data segregation at the database layer.
- **Append-only audit logging:** administrative actions and authentication events are recorded in tables with append-only constraints.
- **Cryptographic attestation chains:** evidence bundles are cryptographically signed and chained to support tamper-evident audit trails.

---

## Exhibit C — BAA Gated Workflows (engineering commitment, future-tense)

Beginning thirty (30) days after this Agreement's effective date, Business Associate **will** enforce the following workflow blocks for Covered Entities who have NOT executed electronic signature of this v1.0-INTERIM Agreement (counsel-approved enumeration 2026-05-13):

- New site onboarding will be blocked (additional clinic locations cannot be enrolled).
- New credential entry will be blocked (privileged credentials cannot be added for assessment).
- Cross-org transfer and org-management sensitive actions will be blocked (cross-org-relocate-source, owner-transfer, partner-swap initiation).
- New evidence export to third parties will be blocked (auditor-kit downloads, F-series PDF generation).
- **Ingest:** Business Associate's working position is that new ingest will be blocked for non-re-signed Covered Entities; read access to existing data will remain unaffected. Final determination pending inside-counsel verdict on the BAA-enforcement engagement scope.

Existing-data access will remain unaffected by these enforcement gates. The enforcement mechanism will be engineered in the platform as the `BAA_GATED_WORKFLOWS` lockstep constant with corresponding CI gate and substrate invariant (engineering work in progress as of this Agreement's effective date; ship target: prior to the 30-day enforcement cliff). Until the enforcement mechanism ships, Business Associate manages the transition operationally via in-product banner + email reminder.

---

## Article 10 — Signature

By executing electronic signature in the OsirisCare signup flow, Covered Entity affirms:

1. Authority to bind the practice or organization on whose behalf signature is being executed.
2. Receipt and review of the full text of this v1.0-INTERIM Agreement, including Exhibits A, B, and C.
3. Acknowledgment that this Agreement is v1.0-INTERIM and that v2.0 with outside-counsel-hardened commercial/legal terms is forthcoming within approximately 21 days.
4. Acknowledgment that any Prior Acknowledgment under version `v1.0-2026-04-15` is superseded by this Agreement per Article 8.

---

**Effective Date:** Upon electronic signature.
**Version:** v1.0-INTERIM-2026-05-13.
**Effective until:** Superseded by v2.0 (target 2026-06-03) or earlier amendment.

— OsirisCare, Business Associate
— [Covered Entity name auto-populated from signup]
