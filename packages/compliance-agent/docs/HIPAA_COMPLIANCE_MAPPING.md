# HIPAA Security Rule Compliance Mapping

**Official Sources:**
- **Federal Register NPRM (2025):** [HIPAA Security Rule Updates](https://www.federalregister.gov/documents/2025/01/06/2024-30983/hipaa-security-rule-to-strengthen-the-cybersecurity-of-electronic-protected-health-information)
- **HHS/OCR Security Rule:** [45 CFR Part 160 and Part 164 Subparts A and C](https://www.hhs.gov/hipaa/for-professionals/security/laws-regulations/index.html)
- **HHS NPRM Fact Sheet:** [Proposed Cybersecurity Updates](https://www.hhs.gov/hipaa/for-professionals/security/hipaa-security-rule-nprm/factsheet/index.html)

**Last Updated:** 2025-11-24
**Current Rule Version:** 2013 (with 2025 NPRM pending - comment period closes March 7, 2025)

---

## Summary of 2025 Proposed Changes

**Key Context:**
- Reports of large breaches increased 102% (2018-2023)
- Individuals affected increased 1002% (primarily hacking/ransomware)
- 167 million individuals affected in 2023 (record high)

**Major Changes:**
1. **Removes "addressable" designation** - all specifications become **required**
2. **Adds specific technical requirements** (encryption, MFA, vulnerability scanning)
3. **Mandates written documentation** for all policies/procedures
4. **Compliance timeline:** 180 days after final rule (expected mid-2025)

---

## Administrative Safeguards (45 CFR §164.308)

### §164.308(a)(1)(ii)(D) - Information System Activity Review
**Requirement:** Regularly review records of information system activity

**Implementation:**
- Review audit logs, access reports, security incident tracking
- **Our Coverage:**
  - Windows Event Log collection (RB-WIN-LOGGING-001)
  - NixOS auditd forwarding
  - Evidence bundle generation with timestamps
  - Hash-chained log integrity

**Evidence Required:**
- Log review schedules
- Proof of log collection
- Incident tracking records

---

### §164.308(a)(3)(ii)(B) - Access Management - Access Authorization
**Requirement:** Implement policies/procedures for granting access

**Implementation (Proposed 2025):**
- **MFA required** for all user access
- Network segmentation to isolate ePHI
- **Our Coverage:**
  - Active Directory health checks (RB-WIN-AD-001)
  - Access review tracking
  - Privileged account monitoring

**Evidence Required:**
- User access lists
- MFA enrollment status
- Access approval records

---

### §164.308(a)(3)(ii)(C) - Access Management - Access Establishment and Modification
**Requirement:** Procedures for creating, modifying, terminating access

**Implementation:**
- Timely access removal for terminated users
- Review and approval for access changes
- **Our Coverage:**
  - AD account enumeration
  - Dormant account detection
  - Access audit trails

**Evidence Required:**
- Termination logs
- Access modification records
- Review cycle proof

---

### §164.308(a)(5)(ii)(B) - Security Awareness and Training - Protection from Malicious Software
**Requirement:** Procedures for detecting/reporting/protecting against malware

**Implementation (Proposed 2025):**
- **Anti-malware required** (no longer addressable)
- Real-time protection with current definitions
- **Our Coverage:**
  - Windows Defender health (RB-WIN-AV-001)
  - Definition update status
  - Scan completion tracking

**Evidence Required:**
- Malware protection status
- Definition update timestamps
- Scan logs and detections

---

### §164.308(a)(5)(ii)(B) - Security Awareness and Training - Patch Management
**Requirement (NEW 2025):** Timely application of security patches

**Implementation (Proposed 2025):**
- Patch management procedures required
- Vulnerability remediation tracking
- **Our Coverage:**
  - Windows Update status (RB-WIN-PATCH-001)
  - Critical patch age tracking
  - MTTR for critical patches

**Evidence Required:**
- Patch status reports
- Vulnerability scan results
- Remediation timelines

---

### §164.308(a)(7)(ii)(A) - Contingency Plan - Data Backup Plan
**Requirement:** Procedures to create/maintain retrievable exact copies of ePHI

**Implementation (Proposed 2025):**
- **Separate backup controls required**
- Regular backup verification
- Test restore procedures
- **Our Coverage:**
  - Backup configuration checks (RB-WIN-BACKUP-001)
  - Backup completion status
  - Test restore tracking

**Evidence Required:**
- Backup schedules
- Backup completion logs
- Restore test results

---

## Physical Safeguards (45 CFR §164.310)

### §164.310(d)(1) - Device and Media Controls
**Requirement:** Policies for receipt/removal of hardware/media with ePHI

**Implementation:**
- Inventory of devices with ePHI access
- Media disposal procedures
- **Our Coverage:**
  - Device enumeration
  - Encryption status verification
  - Asset tracking

**Evidence Required:**
- Device inventory
- Disposal logs
- Encryption verification

---

### §164.310(d)(2)(iv) - Device and Media Controls - Data Backup and Storage
**Requirement:** Maintain retrievable exact copies of ePHI

**Implementation:**
- Redundant with §164.308(a)(7)(ii)(A)
- **Our Coverage:** Same as backup controls above

**Evidence Required:**
- Backup storage verification
- Offsite/redundant backup proof
- Recovery capability tests

---

## Technical Safeguards (45 CFR §164.312)

### §164.312(a)(1) - Access Control
**Requirement:** Technical policies/procedures allowing only authorized access

**Implementation (Proposed 2025):**
- **MFA required** for remote access
- Session timeout enforcement
- Network segmentation
- **Our Coverage:**
  - Windows Firewall status (RB-WIN-FIREWALL-001)
  - Network access controls
  - Segmentation verification

**Evidence Required:**
- Access control configuration
- MFA enrollment proof
- Network segmentation diagrams

---

### §164.312(a)(2)(i) - Access Control - Unique User Identification
**Requirement:** Unique identifier for each user

**Implementation:**
- No shared accounts for ePHI access
- User accountability tracking
- **Our Coverage:**
  - AD user enumeration
  - Shared account detection
  - Account usage tracking

**Evidence Required:**
- User account lists
- Login audit trails
- Shared account reports (should be zero)

---

### §164.312(a)(2)(iv) - Access Control - Encryption and Decryption
**Requirement (UPDATED 2025):** Encrypt ePHI

**Implementation (Proposed 2025):**
- **Encryption at rest - REQUIRED** (no longer addressable)
- **Encryption in transit - REQUIRED**
- **Our Coverage:**
  - BitLocker encryption status (RB-WIN-ENCRYPTION-001)
  - Disk encryption verification
  - TLS/certificate monitoring

**Evidence Required:**
- Encryption configuration
- Encrypted volume lists
- Certificate validity

---

### §164.312(b) - Audit Controls
**Requirement:** Hardware/software mechanisms to record/examine activity

**Implementation (Proposed 2025):**
- **Written audit log procedures required**
- Centralized log collection
- Log retention (6 years recommended)
- **Our Coverage:**
  - Windows Event Logging (RB-WIN-LOGGING-001)
  - Audit log forwarding
  - Evidence bundle generation
  - Hash-chained integrity

**Evidence Required:**
- Audit log samples
- Log retention proof
- Log review records

---

### §164.312(e)(1) - Transmission Security
**Requirement:** Technical measures to guard against unauthorized access during transmission

**Implementation (Proposed 2025):**
- **Encryption in transit - REQUIRED**
- Secure protocols only (TLS 1.2+)
- **Our Coverage:**
  - Network encryption verification
  - Certificate monitoring
  - Insecure protocol detection

**Evidence Required:**
- Transmission encryption config
- Protocol inventory
- Certificate chains

---

### §164.312(e)(2)(ii) - Transmission Security - Encryption
**Requirement (UPDATED 2025):** Encrypt ePHI in transmission

**Implementation (Proposed 2025):**
- No longer addressable - **REQUIRED**
- TLS 1.2 minimum (1.3 recommended)
- **Our Coverage:**
  - TLS configuration audits
  - Weak cipher detection
  - Certificate expiry alerts

**Evidence Required:**
- TLS version inventory
- Cipher suite lists
- Encryption test results

---

## Organizational Requirements (45 CFR §164.314)

### §164.314(a)(1) - Business Associate Contracts
**Requirement:** Written contract ensuring BA protects ePHI

**Implementation:**
- BAA with all service providers
- Sub-processor disclosure
- **Our Coverage:**
  - Documented as metadata-only processor
  - No ePHI access by design
  - Evidence bundles contain system data only

**Evidence Required:**
- Executed BAAs
- Sub-processor list
- Data flow diagrams

---

## Policies and Procedures (45 CFR §164.316)

### §164.316(b)(1) - Policies and Procedures - Documentation
**Requirement (ENHANCED 2025):** Implement written policies/procedures

**Implementation (Proposed 2025):**
- **All policies MUST be written** (no longer addressable)
- Version control required
- Annual review procedures
- **Our Coverage:**
  - Baseline configuration documented (NixOS-HIPAA v1)
  - Runbook library with HIPAA citations
  - Evidence bundles linked to controls
  - Git-based version control

**Evidence Required:**
- Policy documents
- Review/approval records
- Version history

---

## New Requirements (2025 Proposed Rule)

### Vulnerability Management
**Requirement (NEW):** Regular vulnerability assessment

**Implementation:**
- **Vulnerability scanning:** At least every 6 months
- **Penetration testing:** At least once every 12 months
- Remediation tracking
- **Our Coverage:**
  - Patch status monitoring (surrogate for vuln scanning)
  - Critical vulnerability MTTR tracking

**Evidence Required:**
- Scan reports (6-month intervals)
- Penetration test reports (annual)
- Remediation timelines

---

### Network Segmentation
**Requirement (NEW):** Isolate systems with ePHI

**Implementation:**
- Separate ePHI systems from general network
- Firewall rules between segments
- **Our Coverage:**
  - Firewall status verification
  - Network topology documentation
  - Segmentation rule audits

**Evidence Required:**
- Network diagrams
- Firewall rule exports
- Segmentation test results

---

## Compliance Tier Mapping

### Essential Tier ($200-400/mo)
**Covers:**
- §164.308(a)(1)(ii)(D) - Audit logging ✓
- §164.308(a)(5)(ii)(B) - Anti-malware ✓
- §164.308(a)(7)(ii)(A) - Backups ✓
- §164.312(a)(2)(iv) - Encryption at rest ✓
- §164.312(b) - Audit controls ✓

**Limited:**
- Manual vulnerability scanning
- Basic patch tracking
- 30-day evidence retention

---

### Professional Tier ($600-1200/mo)
**Adds:**
- §164.308(a)(5)(ii)(B) - Automated patch management ✓
- Vulnerability scanning (automated, 6-month)
- Network segmentation verification ✓
- Multi-source time sync ✓
- Signed evidence bundles ✓
- 90-day evidence retention

---

### Enterprise Tier ($1500-3000/mo)
**Adds:**
- Penetration testing (annual) ✓
- Real-time vulnerability monitoring ✓
- Advanced network segmentation ✓
- Blockchain-anchored evidence ✓
- SBOM generation ✓
- 2-year evidence retention
- Dedicated compliance dashboard

---

## Official Resources for Reference

### Primary Sources
1. **45 CFR Part 164:** https://www.ecfr.gov/current/title-45/subtitle-A/subchapter-C/part-164
2. **HHS Security Rule Page:** https://www.hhs.gov/hipaa/for-professionals/security/index.html
3. **2025 NPRM:** https://www.federalregister.gov/d/2024-30983

### Guidance Documents
1. **NIST SP 800-66:** Implementing HIPAA Security Rule (healthcare sector)
2. **NIST Cybersecurity Framework:** Voluntary framework mapping to HIPAA
3. **HHS Cybersecurity Newsletter:** Monthly updates on security practices

### Comment Period
- **Open:** January 6, 2025
- **Closes:** March 7, 2025
- **Final Rule Expected:** Mid-2025
- **Compliance Date:** 180 days after final rule publication

---

## Integration with Our Platform

### Automated Control Mapping
Each runbook includes:
```yaml
hipaa_controls:
  - "164.308(a)(5)(ii)(B)"  # Anti-malware
  - "164.312(b)"            # Audit controls
```

### Evidence Generation
Every check produces:
```json
{
  "hipaa_controls": ["164.308(a)(7)(ii)(A)"],
  "evidence_type": "backup_verification",
  "timestamp": "2025-11-24T16:00:00Z",
  "outcome": "success",
  "signed": true
}
```

### Compliance Packets
Monthly reports include:
- Control-by-control status
- Evidence bundle references
- Exception documentation
- Remediation timelines

---

## Notes for Auditors

1. **Metadata Processing Only:** This platform processes system logs and configuration data. It does NOT access, process, or store Protected Health Information (PHI/ePHI).

2. **Evidence Integrity:** All evidence bundles are cryptographically signed and stored in append-only (WORM) storage.

3. **Control Citations:** Every automated check maps directly to specific CFR sections listed above.

4. **Current vs. Proposed:** This mapping includes both current requirements (2013 rule) and proposed requirements (2025 NPRM). Our platform implements proposed requirements proactively where feasible.

5. **Version Control:** This document is maintained in Git with full version history at: [repository URL]

---

**Disclaimer:** This document is for informational purposes and does not constitute legal advice. Covered entities should consult with qualified HIPAA compliance professionals and legal counsel to ensure full compliance with all applicable regulations.
