# MSP HIPAA Compliance Platform - Documentation Index

**Last Updated:** November 1, 2025
**Platform Version:** Week 5 (Evidence Pipeline Implementation)

---

## Quick Navigation

**For Operators:**
- [📋 Manual Operations Checklist](operations/MANUAL_OPERATIONS_CHECKLIST.md) - **START HERE**
- [⚡ Quick Reference Card](operations/OPERATOR_QUICK_REFERENCE.md) - Print and keep handy

**For Implementation:**
- [📝 Implementation Progress](#implementation-progress)
- [🔧 SOPs (Standard Operating Procedures)](#sops)
- [🚨 Emergency Procedures](#emergency-procedures)

**For Compliance:**
- [📊 HIPAA Control Mapping](#hipaa-compliance)
- [🔐 Evidence Bundle Format](#evidence-bundles)

---

## Implementation Progress

### Week 5: Evidence Pipeline (Current)

| Day | Status | Documentation |
|-----|--------|---------------|
| Day 1 | ✅ Complete | [WEEK_5_DAY_1_COMPLETE.md](implementation/WEEK_5_DAY_1_COMPLETE.md) |
| Day 2 | ✅ Complete | [WEEK_5_DAY_2_COMPLETE.md](implementation/WEEK_5_DAY_2_COMPLETE.md) |
| Day 3 | ✅ Complete | [WEEK_5_DAY_3_COMPLETE.md](implementation/WEEK_5_DAY_3_COMPLETE.md) |
| Day 4 | ✅ Complete | [WEEK_5_DAY_4_COMPLETE.md](implementation/WEEK_5_DAY_4_COMPLETE.md) |
| Day 5 | 🔄 In Progress | End-to-End Testing |

**Completed Components:**
- ✅ Evidence Bundler (`bundler.py`)
- ✅ Cryptographic Signer (`signer.py`) with cosign v3
- ✅ JSON Schema Validation (`evidence-bundle-v1.schema.json`)
- ✅ Integration Pipeline (`pipeline.py`)
- ✅ Configuration Management (`config.py`)
- ✅ Integration Test Suite (`test_integration.py`) - 5/5 passing
- ✅ WORM Storage Uploader (`uploader.py`) with S3 Object Lock
- ✅ Terraform WORM Storage Module (`terraform/modules/worm-storage/`)
- ✅ MCP Executor (`executor.py`) - Runbook execution engine
- ✅ Core Runbook Library (6 runbooks)

---

## SOPs

### Core Operations

1. **[SOP-011: Compliance Audit Support](sop/SOP-011_COMPLIANCE_AUDIT_SUPPORT.md)**
   - Preparing evidence bundles for auditors
   - Auditor verification workflow
   - Common audit questions and responses

2. **[SOP-013: Evidence Bundle Verification](sop/SOP-013_EVIDENCE_BUNDLE_VERIFICATION.md)**
   - Daily evidence bundle spot-checking
   - Signature verification procedures
   - Troubleshooting verification failures

3. **[EMERG-002: Data Breach Response](sop/EMERG-002_DATA_BREACH_RESPONSE.md)**
   - Immediate containment procedures
   - Evidence preservation
   - HIPAA breach notification timeline

### Planned SOPs (Week 6+)

- SOP-001: Client Onboarding
- SOP-002: Incident Response
- SOP-003: Runbook Management
- SOP-004: Baseline Exception Approval
- SOP-005: Monthly Compliance Packet Generation
- SOP-006: Signing Key Rotation
- SOP-007: WORM Storage Management
- SOP-008: Service Health Monitoring
- SOP-009: Failed Incident Escalation
- SOP-010: Client Offboarding
- SOP-012: Quarterly Business Review

---

## Emergency Procedures

### Critical Incidents

**Private Key Compromise:**
See [MANUAL_OPERATIONS_CHECKLIST.md](operations/MANUAL_OPERATIONS_CHECKLIST.md#private-key-compromise-critical)
- Disable service immediately
- Revoke key access
- Alert all clients
- Rotate keys within 48 hours

**Evidence Bundle Corruption:**
See [MANUAL_OPERATIONS_CHECKLIST.md](operations/MANUAL_OPERATIONS_CHECKLIST.md#evidence-bundle-corruption)
- Isolate corrupted bundle
- Restore from WORM storage
- Verify restored bundle
- Document incident

**WORM Storage Failure:**
See [MANUAL_OPERATIONS_CHECKLIST.md](operations/MANUAL_OPERATIONS_CHECKLIST.md#worm-storage-failure-day-3-implementation)
- Extend local retention
- Monitor disk space
- Batch upload when restored

---

## HIPAA Compliance

### Control Coverage

| HIPAA Control | Description | Implementation | Evidence Location |
|---------------|-------------|----------------|-------------------|
| §164.308(a)(1)(ii)(D) | Information System Activity Review | Evidence pipeline auto-collection | Evidence bundles |
| §164.308(a)(5)(ii)(B) | Protection from Malicious Software | Patch management automation | Evidence bundles |
| §164.308(a)(7)(ii)(A) | Data Backup Plan | Automated backup verification | Evidence bundles |
| §164.310(d)(1) | Device and Media Controls | Baseline enforcement | NixOS flakes |
| §164.310(d)(2)(iv) | Data Backup and Storage | WORM storage | S3 Object Lock |
| §164.312(a)(1) | Access Control | SSH cert auth | Baseline config |
| §164.312(a)(2)(i) | Unique User Identification | Service principals | MCP executor |
| §164.312(a)(2)(iv) | Encryption and Decryption | LUKS, TLS | NixOS modules |
| §164.312(b) | Audit Controls | Evidence bundles | Signed bundles |
| §164.312(c)(1) | Integrity Controls | Cryptographic signing | Cosign |
| §164.312(e)(1) | Transmission Security | TLS/mTLS | WireGuard VPN |
| §164.316(b)(1) | Policies and Procedures | Documentation | This repo |

### Compliance Documents

- [CLAUDE.md](../CLAUDE.md) - Complete compliance framework
- [Evidence Bundle Schema](../opt/msp/evidence/schema/evidence-bundle-v1.schema.json)
- [Baseline Profile](../baseline/hipaa-v1.yaml) - Planned
- [BAA Template](../legal/BAA_TEMPLATE.md) - Planned

---

## Evidence Bundles

### Format Specification

**Bundle ID Format:** `EB-YYYYMMDD-NNNN`
- `EB` = Evidence Bundle prefix
- `YYYYMMDD` = Generation date
- `NNNN` = Sequential number (0001-9999)

**Example:** `EB-20251101-0042.json`

**Signature Format:** `{bundle-id}.json.bundle`
- Cosign v3 signature bundle format
- Includes transparency log entry
- Verifiable with public key only

### Bundle Structure

```json
{
  "bundle_id": "EB-YYYYMMDD-NNNN",
  "bundle_version": "1.0",
  "client_id": "clinic-NNN",
  "generated_at": "ISO8601 timestamp",

  "incident": {
    "incident_id": "INC-YYYYMMDD-NNNN",
    "event_type": "backup_failure | cert_expiry | disk_full | ...",
    "severity": "critical | high | medium | low",
    "detected_at": "ISO8601 timestamp",
    "hostname": "srv-primary.clinic.local",
    "details": {},
    "hipaa_controls": ["164.308(a)(7)(ii)(A)", ...]
  },

  "runbook": {
    "runbook_id": "RB-BACKUP-001",
    "runbook_version": "1.0",
    "runbook_hash": "sha256:...",
    "steps_total": 4,
    "steps_executed": 4
  },

  "execution": {
    "timestamp_start": "ISO8601 timestamp",
    "timestamp_end": "ISO8601 timestamp",
    "operator": "service:mcp-executor",
    "mttr_seconds": 322,
    "sla_target_seconds": 14400,
    "sla_met": true,
    "resolution_type": "auto | manual | partial"
  },

  "actions_taken": [
    {
      "step": 1,
      "action": "check_backup_logs",
      "script_hash": "sha256:...",
      "result": "ok | failed | skipped",
      "exit_code": 0,
      "timestamp": "ISO8601 timestamp",
      "stdout_excerpt": "...",
      "stderr_excerpt": "...",
      "error_message": "..."
    }
  ],

  "artifacts": {
    "log_excerpts": {},
    "checksums": {},
    "configurations": {},
    "outputs": {}
  },

  "outputs": {
    "resolution_status": "success | partial | failed"
  },

  "evidence_bundle_hash": "sha256:...",
  "signatures": {},
  "storage_locations": []
}
```

### Verification

```bash
# Verify bundle signature
cosign verify-blob \
  --key /etc/msp/signing-keys/private-key.pub \
  --bundle {bundle-id}.json.bundle \
  {bundle-id}.json

# View bundle contents
cat {bundle-id}.json | jq

# Extract specific fields
cat {bundle-id}.json | jq '.incident.hipaa_controls'
```

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│ Client Infrastructure (NixOS Flake)                         │
│                                                              │
│  ┌──────────┐      ┌──────────┐      ┌──────────┐         │
│  │  Log     │ ───→ │  Health  │ ───→ │  Event   │         │
│  │ Watcher  │      │  Checks  │      │  Queue   │         │
│  └──────────┘      └──────────┘      └──────────┘         │
└───────────────────────────┼─────────────────────────────────┘
                            │
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ MCP Server (Central)                                        │
│                                                              │
│  ┌──────────┐      ┌──────────┐      ┌──────────┐         │
│  │  Planner │ ───→ │ Executor │ ───→ │ Evidence │         │
│  │   GPT-4o │      │ Runbooks │      │ Pipeline │         │
│  └──────────┘      └──────────┘      └──────────┘         │
│                                             │               │
└─────────────────────────────────────────────┼───────────────┘
                                              │
                                              ↓
┌─────────────────────────────────────────────────────────────┐
│ Evidence Pipeline (Week 5)                                  │
│                                                              │
│  ┌──────────┐      ┌──────────┐      ┌──────────┐         │
│  │ Bundler  │ ───→ │  Signer  │ ───→ │ Uploader │         │
│  │   .py    │      │  cosign  │      │  WORM S3 │         │
│  └──────────┘      └──────────┘      └──────────┘         │
└─────────────────────────────────────────────────────────────┘
```

---

## Development Roadmap

### Completed (Weeks 1-5)

- ✅ Service catalog definition
- ✅ NixOS baseline architecture
- ✅ Evidence bundler implementation
- ✅ Cryptographic signing with cosign
- ✅ JSON schema validation
- ✅ Integration pipeline
- ✅ Configuration management
- ✅ Integration test suite

### In Progress (Week 5)

- 🔄 WORM storage (S3 Object Lock)
- 🔄 MCP executor integration
- 🔄 End-to-end testing

### Upcoming (Weeks 6-8)

- ⏳ Core runbook library (6 runbooks)
- ⏳ MCP planner (GPT-4o integration)
- ⏳ Guardrails and rate limiting
- ⏳ Client deployment automation
- ⏳ Monitoring and alerting
- ⏳ Compliance packet generation

### Future (Weeks 9+)

- ⏳ First pilot client
- ⏳ Dashboard implementation
- ⏳ Advanced reporting
- ⏳ Key rotation automation
- ⏳ Multi-region deployment

---

## File Organization

```
docs/
├── README.md (this file)
│
├── operations/                      # Operator guides
│   ├── MANUAL_OPERATIONS_CHECKLIST.md
│   └── OPERATOR_QUICK_REFERENCE.md
│
├── sop/                             # Standard Operating Procedures
│   ├── SOP-011_COMPLIANCE_AUDIT_SUPPORT.md
│   ├── SOP-013_EVIDENCE_BUNDLE_VERIFICATION.md
│   └── EMERG-002_DATA_BREACH_RESPONSE.md
│
├── implementation/                  # Implementation logs
│   ├── WEEK_5_DAY_1_COMPLETE.md
│   └── WEEK_5_DAY_2_COMPLETE.md
│
└── architecture/                    # Technical architecture (planned)
    ├── evidence-pipeline.md
    ├── mcp-integration.md
    └── nixos-baseline.md
```

---

## Key Dependencies

| Dependency | Version | Purpose |
|------------|---------|---------|
| Python | 3.9+ | Evidence pipeline runtime |
| cosign | v3.0.2 | Cryptographic signing |
| jsonschema | 4.25.1 | Bundle validation |
| boto3 | 1.40.64 | AWS S3 integration |
| SOPS | 3.7.3+ | Secrets management |
| NixOS | 24.05 | Deterministic infrastructure |

---

## Support and Escalation

### Internal Support

**Lead Operator:** Primary contact for day-to-day operations
**Security Lead:** Escalation for security incidents
**On-Call Engineer:** 24/7 emergency support

### External Support

**cosign Issues:** https://github.com/sigstore/cosign/issues
**NixOS Issues:** https://discourse.nixos.org
**AWS Support:** https://console.aws.amazon.com/support

### Documentation Issues

Found an error? Documentation unclear?
1. Note the issue in `/var/log/msp/operations-journal.log`
2. Propose update to Lead Operator
3. Update this documentation after approval

---

## Changelog

### November 1, 2025
- Added Manual Operations Checklist
- Added Operator Quick Reference Card
- Created Documentation Index
- Completed Week 5 Day 2 (Integration Pipeline)

### October 31, 2025
- Completed Week 5 Day 1 (Evidence Bundler & Signer)
- Created initial SOPs (011, 013, EMERG-002)

---

**Documentation Ownership:**
- Lead Operator: Monthly review
- Team: Quarterly review
- Full audit: Annually

**Last Review:** November 1, 2025
**Next Review:** December 1, 2025
