# HIPAA Compliance Framework

## Legal Positioning

**You are a Business Associate (BA)** - but only for *operations*, not for *treatment or records*.

Your MCP + LLM system *supports* compliance (**HIPAA §164.308(a)(1)(ii)(D)**: "Information system activity review") by scanning logs for evidence of compliance, not by touching medical charts or patient identifiers.

**Key Citation:**
> 45 CFR 164.308(a)(1)(ii)(D) - "Implement procedures to regularly review records of information system activity, such as audit logs, access reports, and security incident tracking reports."

**Your Service Scope:**
"HIPAA operational safeguard verification" - NOT "clinical data processing"

## Framework Basis (Anduril Model)

- **Published NixOS STIG**: Listed in NIST's National Checklist Program
- **Deterministic builds**: Reproducible device images
- **Evidence generation**: Mapped to recognized control catalogs

## Compliance Strengths

- Clear scope boundary (infra-only)
- Deterministic flake with guardrails
- Backups/monitoring/VPN as default
- Credible 5-week MVP with metrics
- Metadata-only scanning - no PHI processing

## Data Boundary Zones

| Zone | Example Data | HIPAA Risk | Mitigation |
|------|-------------|-----------|-----------|
| **System** | syslog, SSH attempts, package hashes, backup status | Very low | Mask usernames/paths |
| **Application** | EHR audit logs, access events | Moderate | Tokenize IDs, redact payload |
| **Data** | PHI (lab results, notes, demographics) | High | **Never ingest** |

## Practical Controls

1. **Scrubbers at edge**: Fluent Bit filters PHI patterns before forwarding
2. **Regex + checksum**: Send hashes of identifiers, not values
3. **Metadata-only collector**: System events only, no file contents
4. **Access boundary**: Restrict to `/var/log`, `/etc`, `/nix/store` - NOT `/data/` or EHR mounts

## What Your LLM Can Do

**Allowed:**
- Parse logs for anomalies, missed backups, failed encryption
- Compare settings to baseline
- Generate remediation runbooks
- Produce evidence bundles

**Prohibited:**
- Read or infer patient-level data
- Suggest clinical actions
- Aggregate PHI from logs

## Monitoring Requirements by Tier

### Tier 1: Infrastructure (Easiest)

| Component | What to Monitor | HIPAA Citation |
|-----------|----------------|----------------|
| Firewalls | Rule changes, blocked traffic | §164.312(a)(1) |
| VPN | Login attempts, failed auth | §164.312(a)(2)(i) |
| Server OS | Login events, privilege escalation | §164.312(b) |
| Backups | Job completion, encryption status | §164.308(a)(7)(ii)(A) |
| Encryption | Disk encryption, cert expiry | §164.312(a)(2)(iv) |
| Time Sync | NTP drift, source validation | §164.312(b) |
| Patching | Pending updates, vuln scan results | §164.308(a)(5)(ii)(B) |

### Tier 1.5: Workstation Compliance (NEW - Session 33)

| Component | What to Check | HIPAA Citation |
|-----------|--------------|----------------|
| BitLocker | Drive encryption enabled | §164.312(a)(2)(iv) |
| Windows Defender | Real-time protection active | §164.308(a)(5)(ii)(B) |
| Patch Status | Recent updates within 30 days | §164.308(a)(5)(ii)(B) |
| Firewall | All profiles enabled | §164.312(a)(1) |
| Screen Lock | Inactivity timeout set | §164.312(a)(2)(iii) |

**Implementation:** AD-based discovery + WMI compliance checks via appliance.

### Tier 2: Application (Moderate)

| Component | What to Monitor | HIPAA Citation |
|-----------|----------------|----------------|
| EHR/EMR Access | Patient record access, break-glass | §164.312(a)(1) |
| Authentication | Failed logins, MFA events | §164.312(a)(2)(i) |
| Database | PHI queries, schema changes | §164.312(b) |
| File Access | PHI opens, bulk exports | §164.308(a)(3)(ii)(A) |
| Email | PHI transmission, encryption | §164.312(e)(1) |

### Tier 3: Business Process (Complex)

| Component | What to Monitor | HIPAA Citation |
|-----------|----------------|----------------|
| User Provisioning | Account creation, termination | §164.308(a)(3)(ii)(C) |
| BA Access | Vendor access, BAA compliance | §164.308(b)(1) |
| Incident Response | Detection, containment | §164.308(a)(6) |
| Training | Awareness completion | §164.308(a)(5)(i) |
| Risk Assessment | Remediation tracking | §164.308(a)(1)(ii)(A) |

## Critical Alert Triggers

1. Multiple failed logins (>5 in 10 min)
2. PHI access outside business hours
3. Bulk data export
4. Firewall rule changes
5. Backup failure
6. Cert expiry within 30 days
7. Privileged account usage without ticket
8. Database schema modifications
9. New user without HR ticket
10. External email with PHI, no encryption

## LLM Policy File

```yaml
llm_scope:
  allowed_inputs: [syslog, journald, auditd, restic_logs]
  prohibited_inputs: [ehr_exports, patient_data, attachments]

llm_output_actions:
  - classification
  - compliance_report
  - remediation_plan

prohibited_actions:
  - direct clinical recommendation
  - patient data synthesis
```

## BAA Template (Key Clauses)

**Scope-Limited Business Associate Agreement**

### Metadata-Only Operations

> "BA's services operate exclusively on system metadata and configurations to assist Covered Entity in verifying HIPAA Security Rule compliance. BA does not process PHI and shall treat any inadvertent PHI exposure as a security incident triggering the breach notification process per 45 CFR 164.404."

### No PHI Processing

- Collect only system-level logs (syslog, journald, auditd)
- Scrub accidental PHI at source before transmission
- Access restricted to /var/log, /etc, system directories only
- Never access patient data directories or EHR databases

### Sub-Processors

Identify all that handle metadata:
- Cloud Infrastructure: AWS/Azure/GCP
- Event Queue: Redis/NATS hosting
- Object Storage: S3/MinIO
- LLM Provider: OpenAI/Azure OpenAI

### Breach Notification

BA notifies CE within 24 hours of:
- Inadvertent PHI access by systems
- Unauthorized access to BA systems
- Data breach affecting metadata
- Security incident affecting monitoring

## Documentation Requirements

1. **Data Boundary Diagram**: Three zones with PHI prohibited annotation
2. **HIPAA Mapping File**: Control → Runbook → Evidence mapping
3. **Statement of Scope**: Attach to BAA
4. **LLM Policy File**: Allowed/prohibited actions
5. **Exceptions File**: Per-client with owner, risk, expiry

## Regulatory Reference

**HHS/OCR AI Guidance**: Called out AI use in healthcare as vector for discrimination risk. Urged covered entities to assess models for features acting as proxies for protected characteristics.

Your model-feature map is not just good practice - it's likely to be a regulatory conversation if the model touches care decisions.
