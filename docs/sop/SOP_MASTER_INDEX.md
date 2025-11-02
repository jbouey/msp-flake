# Standard Operating Procedures: Master Index

**MSP HIPAA Compliance Platform**
**Version:** 1.0
**Last Updated:** 2025-10-31
**Owner:** MSP Operations Team

---

## Document Purpose

This master index provides a complete reference to all Standard Operating Procedures (SOPs) and Operator Manuals for the MSP HIPAA Compliance Platform. These documents ensure:

- **Transparency:** All operational procedures are documented and auditable
- **Consistency:** Operations are performed the same way every time
- **Training:** New operators can be onboarded quickly
- **Compliance:** Procedures meet HIPAA and SOC 2 requirements
- **Business Continuity:** Operations can continue if key personnel are unavailable

---

## Document Classification

### Level 1: Critical SOPs (Business-Critical Operations)

**Required Reading:** All operators must read and acknowledge understanding

| SOP ID | Title | Purpose | Review Cycle |
|--------|-------|---------|--------------|
| SOP-001 | [Daily Operations](#sop-001-daily-operations) | Daily monitoring and maintenance tasks | Monthly |
| SOP-002 | [Incident Response](#sop-002-incident-response) | Handling automated and manual incidents | Monthly |
| SOP-003 | [Disaster Recovery](#sop-003-disaster-recovery) | System recovery and business continuity | Quarterly |
| SOP-004 | [Client Escalation](#sop-004-client-escalation) | Handling client emergencies and issues | Monthly |

### Level 2: Operational SOPs (Regular Operations)

**Required Reading:** Operators in specific roles

| SOP ID | Title | Purpose | Review Cycle |
|--------|-------|---------|--------------|
| SOP-010 | [Client Onboarding](#sop-010-client-onboarding) | New client deployment process | Quarterly |
| SOP-011 | [Compliance Audit Support](#sop-011-compliance-audit-support) | Supporting client audits | Quarterly |
| SOP-012 | [Baseline Management](#sop-012-baseline-management) | Updating and deploying baselines | Quarterly |
| SOP-013 | [Evidence Bundle Verification](#sop-013-evidence-bundle-verification) | Verifying evidence integrity | Monthly |
| SOP-014 | [Runbook Management](#sop-014-runbook-management) | Creating and updating runbooks | Quarterly |

### Level 3: Operator Manuals (Reference Guides)

**As-Needed Reference:** Task-specific detailed instructions

| Manual ID | Title | Purpose | Review Cycle |
|-----------|-------|---------|--------------|
| OP-001 | [MCP Server Operations](#op-001-mcp-server-operations) | MCP server management | Quarterly |
| OP-002 | [Evidence Pipeline Operations](#op-002-evidence-pipeline-operations) | Evidence system management | Quarterly |
| OP-003 | [WORM Storage Management](#op-003-worm-storage-management) | Immutable storage operations | Quarterly |
| OP-004 | [Dashboard Administration](#op-004-dashboard-administration) | Grafana dashboard management | Quarterly |
| OP-005 | [Cryptographic Key Management](#op-005-cryptographic-key-management) | Key rotation and backup | Quarterly |

### Level 4: Emergency Procedures (Crisis Response)

**Critical Access:** Keep readily available for emergencies

| Procedure ID | Title | Purpose | Review Cycle |
|--------------|-------|---------|--------------|
| EMERG-001 | [Service Outage Response](#emerg-001-service-outage-response) | Total platform outage | Quarterly |
| EMERG-002 | [Data Breach Response](#emerg-002-data-breach-response) | Security incident handling | Quarterly |
| EMERG-003 | [Key Compromise Response](#emerg-003-key-compromise-response) | Signing key compromise | Quarterly |
| EMERG-004 | [Mass Client Impact](#emerg-004-mass-client-impact) | Bad deployment rollback | Quarterly |

---

## Quick Reference: Common Tasks

### I need to...

**Deploy a new client**
→ See [SOP-010: Client Onboarding](SOP-010_CLIENT_ONBOARDING.md)

**Handle an automated incident failure**
→ See [SOP-002: Incident Response](SOP-002_INCIDENT_RESPONSE.md)

**Support a client audit**
→ See [SOP-011: Compliance Audit Support](SOP-011_COMPLIANCE_AUDIT_SUPPORT.md)

**Update the baseline for new regulations**
→ See [SOP-012: Baseline Management](SOP-012_BASELINE_MANAGEMENT.md)

**Verify evidence bundle integrity**
→ See [SOP-013: Evidence Bundle Verification](SOP-013_EVIDENCE_BUNDLE_VERIFICATION.md)

**Create a new runbook**
→ See [SOP-014: Runbook Management](SOP-014_RUNBOOK_MANAGEMENT.md)

**Respond to a service outage**
→ See [EMERG-001: Service Outage Response](EMERG-001_SERVICE_OUTAGE.md)

**Handle a security incident**
→ See [EMERG-002: Data Breach Response](EMERG-002_DATA_BREACH_RESPONSE.md)

**Rotate cryptographic keys**
→ See [OP-005: Cryptographic Key Management](OP-005_KEY_MANAGEMENT.md)

**Manage WORM storage**
→ See [OP-003: WORM Storage Management](OP-003_WORM_STORAGE.md)

---

## Document Standards

### Format Requirements

All SOPs must include:
- **Version number** and last updated date
- **Purpose** statement (why this SOP exists)
- **Scope** (what's covered, what's not)
- **Roles and responsibilities**
- **Step-by-step procedures** with verification steps
- **Emergency contacts**
- **Revision history**

### Review and Approval Process

1. **Draft:** Document author creates initial version
2. **Technical Review:** Senior engineer reviews for accuracy
3. **Compliance Review:** Compliance team reviews for regulatory alignment
4. **Approval:** Operations manager approves for use
5. **Publication:** Document added to this index
6. **Training:** Relevant operators trained on new/updated SOP
7. **Periodic Review:** Reviewed per schedule (monthly/quarterly/annual)

### Version Control

- All SOPs stored in Git repository
- Changes tracked via commits
- Major revisions increment version (1.0 → 2.0)
- Minor updates increment decimal (1.0 → 1.1)
- Change log maintained at end of each document

---

## Role-Based Access

### Operations Engineer (Primary Operator)

**Must Read:**
- SOP-001: Daily Operations
- SOP-002: Incident Response
- SOP-010: Client Onboarding
- SOP-013: Evidence Bundle Verification
- OP-001: MCP Server Operations
- OP-002: Evidence Pipeline Operations

**Should Read:**
- SOP-012: Baseline Management
- SOP-014: Runbook Management
- OP-003: WORM Storage Management
- OP-004: Dashboard Administration

**Emergency Access:**
- EMERG-001: Service Outage Response
- EMERG-004: Mass Client Impact

---

### Compliance Officer

**Must Read:**
- SOP-011: Compliance Audit Support
- SOP-012: Baseline Management
- SOP-013: Evidence Bundle Verification
- EMERG-002: Data Breach Response

**Should Read:**
- SOP-001: Daily Operations (understand what's monitored)
- SOP-002: Incident Response (understand remediation)
- SOP-010: Client Onboarding (baseline deployment)

---

### Security Engineer

**Must Read:**
- SOP-003: Disaster Recovery
- SOP-012: Baseline Management
- OP-005: Cryptographic Key Management
- EMERG-002: Data Breach Response
- EMERG-003: Key Compromise Response

**Should Read:**
- SOP-002: Incident Response
- SOP-013: Evidence Bundle Verification
- OP-001: MCP Server Operations
- OP-003: WORM Storage Management

---

### Client Success Manager

**Must Read:**
- SOP-004: Client Escalation
- SOP-010: Client Onboarding
- SOP-011: Compliance Audit Support

**Should Read:**
- SOP-001: Daily Operations (understand what clients see)
- SOP-002: Incident Response (explain to clients)

---

## Training Requirements

### New Hire Onboarding (Week 1)

**Day 1: Platform Overview**
- Review architecture documentation
- Understand HIPAA compliance scope
- Review evidence-by-architecture concept

**Day 2-3: Core SOPs**
- Read and quiz: SOP-001 (Daily Operations)
- Shadow: Daily operations checklist
- Read and quiz: SOP-002 (Incident Response)
- Shadow: Incident investigation

**Day 4: Client Operations**
- Read: SOP-010 (Client Onboarding)
- Review: Sample client deployment
- Read: SOP-004 (Client Escalation)

**Day 5: Hands-On**
- Perform daily operations checklist (supervised)
- Investigate test incident (supervised)
- Verify evidence bundle (supervised)

### Quarterly Refreshers

All operators must:
- [ ] Review updated SOPs (changed in last quarter)
- [ ] Complete refresher quiz (80% pass required)
- [ ] Participate in tabletop exercise (emergency procedures)
- [ ] Acknowledge understanding in training log

---

## Emergency Contact Information

### Internal Escalation

| Role | Primary Contact | Backup | Phone | Email |
|------|----------------|--------|-------|-------|
| On-Call Engineer | [Name] | [Name] | [Phone] | oncall@msp.com |
| Operations Manager | [Name] | [Name] | [Phone] | ops@msp.com |
| Security Officer | [Name] | [Name] | [Phone] | security@msp.com |
| Compliance Officer | [Name] | [Name] | [Phone] | compliance@msp.com |
| CEO/Founder | [Name] | N/A | [Phone] | ceo@msp.com |

### External Contacts

| Service | Contact | Purpose |
|---------|---------|---------|
| AWS Support | 1-800-AWS-HELP | Infrastructure issues |
| Legal Counsel | [Law Firm] | Breach notification legal advice |
| Cyber Insurance | [Carrier] | Cyber incident reporting |
| HHS Breach Portal | breach.hhs.gov | Required breach reporting |

---

## Compliance & Audit

### HIPAA Documentation Requirements

These SOPs satisfy HIPAA documentation requirements:

| HIPAA Control | Documentation | SOP Reference |
|---------------|---------------|---------------|
| §164.308(a)(1) | Security Management Process | SOP-001, SOP-002, SOP-003 |
| §164.308(a)(2) | Risk Management | SOP-012, SOP-014 |
| §164.308(a)(6) | Incident Response | SOP-002, EMERG-002 |
| §164.308(a)(7) | Contingency Plan | SOP-003 |
| §164.308(a)(8) | Evaluation | SOP-011, SOP-013 |
| §164.312(b) | Audit Controls | SOP-013, OP-002 |
| §164.316(b)(1) | Documentation | All SOPs |
| §164.316(b)(2)(iii) | Retention | Version control (6 years) |

### SOC 2 Control Mapping

These SOPs satisfy SOC 2 Trust Services Criteria:

| TSC | Description | SOP Reference |
|-----|-------------|---------------|
| CC1 | Control Environment | All SOPs (documented procedures) |
| CC2 | Communication | SOP-004 (escalation) |
| CC5 | Control Activities | SOP-001, SOP-002 |
| CC6 | Logical Access | OP-005 (key management) |
| CC7 | System Operations | SOP-001, OP-001, OP-002 |
| CC8 | Change Management | SOP-012, SOP-014 |
| CC9 | Risk Mitigation | SOP-003, EMERG-* |

---

## Document Revision History

### Version 1.0 (2025-10-31)
- Initial release
- 14 SOPs created
- 5 Operator Manuals created
- 4 Emergency Procedures created

### Scheduled Reviews

| Document | Last Review | Next Review | Reviewer |
|----------|-------------|-------------|----------|
| SOP-001 | 2025-10-31 | 2025-11-30 | Ops Manager |
| SOP-002 | 2025-10-31 | 2025-11-30 | Ops Manager |
| SOP-003 | 2025-10-31 | 2026-01-31 | Security Officer |
| SOP-004 | 2025-10-31 | 2025-11-30 | Client Success |
| [All others] | 2025-10-31 | Per schedule | [Assigned] |

---

## Appendix: Document Templates

### SOP Template

```markdown
# SOP-XXX: [Title]

**Version:** X.X
**Last Updated:** YYYY-MM-DD
**Owner:** [Role]
**Review Cycle:** [Monthly/Quarterly/Annual]

## Purpose
[Why this SOP exists]

## Scope
[What's covered, what's not]

## Roles and Responsibilities
- [Role]: [Responsibility]

## Prerequisites
- [Requirement 1]
- [Requirement 2]

## Procedure
### Step 1: [Action]
[Detailed instructions]

**Verification:** [How to verify success]

### Step 2: [Action]
[Detailed instructions]

**Verification:** [How to verify success]

## Emergency Contacts
- [Role]: [Contact info]

## Related Documents
- [SOP-XXX]
- [OP-XXX]

## Revision History
| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | YYYY-MM-DD | Initial version | [Name] |
```

---

**Document Index Status:**
- ✅ Master index complete
- 🔄 Individual SOPs in progress (see files below)
- 📅 Review scheduled: 2026-01-31

**For Questions:**
- Technical: engineering@msp.com
- Compliance: compliance@msp.com
- Operations: ops@msp.com
