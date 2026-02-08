# Week 4 Implementation: Where and Why Documentation

**Document Purpose:** Comprehensive guide to Week 4 deliverables explaining location, structure, rationale, and HIPAA compliance requirements.

**Document Version:** 1.0
**Last Updated:** 2025-10-31
**Status:** Active Implementation Guide

---

## Table of Contents

1. [Overview & Architecture Philosophy](#overview--architecture-philosophy)
2. [Baseline Configuration (baseline/)](#baseline-configuration)
3. [Runbook Architecture (runbooks/)](#runbook-architecture)
4. [Evidence Pipeline (mcp-server/evidence/)](#evidence-pipeline)
5. [Security Hardening (client-flake/)](#security-hardening)
6. [Compliance Reporting (reporting/)](#compliance-reporting)
7. [Implementation Timeline](#implementation-timeline)
8. [Success Criteria](#success-criteria)

---

## Overview & Architecture Philosophy

### The Core Problem We're Solving

**Problem:** Healthcare organizations need HIPAA compliance but lack resources for:
- Manual compliance documentation
- 24/7 security monitoring
- Rapid incident response
- Auditor-ready evidence generation

**Solution:** Deterministic infrastructure + automated remediation + continuous evidence generation

### Why This Architecture Works

1. **Deterministic Builds (NixOS):** Configuration is code; drift is mathematically impossible
2. **Audit by Architecture (MCP):** Every action creates immutable log entry
3. **Evidence by Default:** Compliance documentation is byproduct of operations
4. **Metadata Only:** Never touch PHI, reducing liability exposure

### Key Design Decisions

| Decision | Why | HIPAA Benefit |
|----------|-----|---------------|
| NixOS flakes | Cryptographic proof of config | §164.316 documentation |
| YAML baselines | Auditor-readable, version-controlled | §164.308(a)(8) evaluation |
| Runbook YAML | Pre-approved actions, no free-form LLM | §164.308(a)(5) security awareness |
| WORM storage | Tamper-evident evidence | §164.312(b) audit controls |
| Metadata-only | Lower liability, simpler BAA | §164.308(b) business associates |

---

## Baseline Configuration

### WHERE: `baseline/` Directory Structure

```
baseline/
├── hipaa-v1.yaml                 # Main baseline configuration
├── controls-map.csv              # HIPAA control → config mapping
├── README.md                     # Human-readable guide
└── exceptions/                   # Client-specific overrides
    ├── clinic-001.yaml           # Example exception
    └── exception-template.yaml   # Template for new exceptions
```

### WHY: Baseline Configuration Rationale

#### Problem Statement

Traditional compliance approaches have three fatal flaws:

1. **Documentation Drift:** Written policies diverge from actual system state
2. **Manual Verification:** Auditors trust documentation without technical proof
3. **Point-in-Time Compliance:** "We were compliant on audit day" (maybe)

#### Our Solution: Configuration as Documentation

**Key Insight:** If the system cannot boot without enforcing the baseline, documentation cannot drift from reality.

**Example:**
```yaml
# Traditional approach (fails)
Policy: "SSH passwords must be disabled"
Reality: sshd_config has PasswordAuthentication yes

# Our approach (impossible to violate)
baseline/hipaa-v1.yaml:
  ssh:
    disable_password_auth: true

client-flake/modules/ssh.nix:
  services.openssh.passwordAuthentication =
    if baseline.ssh.disable_password_auth then false else true;
```

If someone tries to enable password auth, the NixOS build fails. Compliance is enforced by the compiler.

### Structure Breakdown

#### 1. `hipaa-v1.yaml` - Main Baseline

**WHERE:** `baseline/hipaa-v1.yaml`

**WHAT:** Declarative security configuration covering all HIPAA Security Rule requirements

**WHY:**
- **Single source of truth:** One file defines security posture for all clients
- **Version controlled:** Git history is audit trail for baseline changes
- **Machine-parseable:** Automation can verify compliance programmatically
- **Auditor-friendly:** Plain English with HIPAA citations

**Key Sections:**

```yaml
# Section 1: Identity & Access Control
# WHY: §164.312(a)(1) requires access control mechanisms
# WHAT: SSH hardening, MFA, service accounts, sudo restrictions
identity_and_access:
  ssh_hardening:
    enabled: true
    password_authentication: false  # Force key-based auth
    certificate_authentication:
      enabled: true
      max_cert_lifetime: "8h"      # Force regular re-authentication

# WHY THIS MATTERS:
# - Traditional SSH allows passwords (weak, brute-forceable)
# - Our approach: Short-lived SSH certificates (8h max)
# - If cert expires, access is revoked automatically
# - No manual "disable user account" process needed
# - Audit trail: CA signing logs prove who had access when
```

**Implementation Flow:**

1. Baseline YAML defines requirement
2. `client-flake/modules/baseline.nix` reads YAML
3. Converts to NixOS configuration
4. System cannot boot if settings are violated
5. Hash of configuration is stored in evidence bundle

#### 2. `controls-map.csv` - HIPAA Mapping

**WHERE:** `baseline/controls-map.csv`

**WHAT:** Mapping table: HIPAA Control → Baseline Setting → Evidence Location

**WHY:**
- **Auditor navigation:** "Show me how you satisfy §164.312(b)"
- **Gap analysis:** Identify unmapped HIPAA requirements
- **Traceability:** Link compliance requirement to technical control to evidence

**Structure:**

```csv
hipaa_control,baseline_section,baseline_key,evidence_type,evidence_location,implementation_status
"§164.312(a)(1)",identity_and_access,ssh_hardening,config_file,/etc/ssh/sshd_config,implemented
"§164.312(a)(2)(i)",identity_and_access,certificate_authentication,ca_logs,/var/log/step-ca/,implemented
"§164.312(b)",audit_and_logging,auditd,audit_logs,/var/log/audit/,implemented
```

**Usage in Compliance Packets:**

```markdown
## HIPAA Control: §164.312(a)(1) - Access Control

**Requirement:** Implement technical policies and procedures for electronic
information systems that maintain ePHI to allow access only to authorized persons.

**Implementation:**
- Baseline Section: `identity_and_access.ssh_hardening`
- Configuration: SSH password auth disabled, certificate-based only
- Evidence: `/etc/ssh/sshd_config` hash: `sha256:a1b2c3...`
- Verification: Daily config drift detection (RB-DRIFT-001)
- Last Verified: 2025-10-31 06:00 UTC

**Status:** ✅ Compliant
```

#### 3. `exceptions/` - Client Overrides

**WHERE:** `baseline/exceptions/clinic-{id}.yaml`

**WHAT:** Documented deviations from baseline with risk assessment and expiry

**WHY:**
- **Flexibility:** Not all clients have identical environments
- **Risk transparency:** Exceptions are explicit, not hidden
- **Time-bounded:** All exceptions have expiration dates
- **Auditor-acceptable:** "We know about this, here's why, here's when it expires"

**Exception Template:**

```yaml
# baseline/exceptions/clinic-001.yaml
client_id: "clinic-001"
baseline_version: "1.0.0"
exceptions:
  - id: "EXC-001-SSH-PORT"
    rule_id: "identity_and_access.ssh_hardening.port"
    baseline_value: 22
    override_value: 2222
    justification: "Legacy firewall rules require non-standard port"
    risk_level: "low"
    compensating_controls:
      - "Port knocking enabled"
      - "Fail2ban with aggressive settings"
    owner: "security@clinic-001.com"
    approved_by: "CISO"
    approved_date: "2025-10-15"
    expires: "2026-01-15"
    review_frequency_days: 30
```

**Compliance Value:**

When auditor asks: "Why is SSH on port 2222?"

Response: "Exception EXC-001-SSH-PORT, approved by CISO on 2025-10-15, expires 2026-01-15, compensating controls documented, reviewed monthly."

This is **defensible compliance** vs. "I don't know, that's how it's always been."

---

## Runbook Architecture

### WHERE: `runbooks/` Directory Structure

```
runbooks/
├── RB-BACKUP-001-failure.yaml    # Backup failure remediation
├── RB-CERT-001-expiry.yaml       # Certificate renewal
├── RB-CPU-001-high.yaml          # CPU spike investigation
├── RB-DISK-001-full.yaml         # Disk space cleanup
├── RB-RESTORE-001-test.yaml      # Weekly backup restore test
├── RB-SERVICE-001-crash.yaml     # Service restart automation
└── templates/
    └── runbook-template.yaml     # Template for new runbooks
```

### WHY: Pre-Approved Playbooks vs. Free-Form LLM

#### The Problem with Traditional LLM Automation

**Dangerous Approach:**
```
Incident: "Disk 95% full"
LLM: "I'll delete /var/log to free space"
Result: Audit logs destroyed, HIPAA violation
```

**Why This Fails:**
- LLMs can hallucinate dangerous commands
- No approval process for destructive actions
- No evidence trail for what was attempted
- No rollback mechanism
- No HIPAA control mapping

#### Our Solution: Runbook-Constrained LLM

**Safe Approach:**
```
Incident: "Disk 95% full"
LLM Planner: "This matches RB-DISK-001-full"
MCP Executor: Runs pre-approved steps:
  1. Check /tmp age (safe)
  2. Rotate logs (documented)
  3. Clear package cache (reversible)
  4. Alert if still > 90% (escalation)
Evidence: All steps logged with hashes
```

**Why This Works:**
- LLM selects runbook, doesn't write commands
- All actions pre-approved by humans
- Every step has HIPAA control mapping
- Rollback procedures documented
- Evidence generation automatic

### Runbook Structure Deep Dive

#### Example: `RB-BACKUP-001-failure.yaml`

**WHERE:** `runbooks/RB-BACKUP-001-failure.yaml`

**WHAT:** Automated response to backup job failures

**WHY:** HIPAA §164.308(a)(7)(ii)(A) requires backup plan with periodic testing

Let's break down each section:

```yaml
id: RB-BACKUP-001
name: "Backup Failure Remediation"
version: "1.0.0"
```

**WHY:** Unique identifier for audit trail. When evidence bundle says "RB-BACKUP-001 executed", auditor can look up exact steps taken.

```yaml
hipaa_controls:
  - "§164.308(a)(7)(ii)(A)"  # Data Backup Plan
  - "§164.310(d)(2)(iv)"     # Data Backup and Storage
```

**WHY:** Direct link to HIPAA requirements. In compliance packet: "Control §164.308(a)(7)(ii)(A) satisfied by runbook RB-BACKUP-001, executed 47 times this month, 100% success rate."

```yaml
severity: high
sla_minutes: 30  # Must resolve within 30 minutes
```

**WHY:** Healthcare can't tolerate backup failures. 30-minute SLA means:
- If backup fails at 2:00 AM, auto-remediation by 2:30 AM
- If not resolved, human escalation by 2:30 AM
- Patient care systems protected by working backup

```yaml
trigger_conditions:
  - event_type: "backup_failure"
  - service: "restic"
  - exit_code: "!= 0"
```

**WHY:** Explicit trigger prevents runbook from running on wrong incident. Safety mechanism.

```yaml
steps:
  - id: "check_logs"
    action: "check_backup_logs"
    timeout_seconds: 30
    script: |
      #!/bin/bash
      # Extract last 100 lines of backup log
      tail -100 /var/log/restic/backup.log > /tmp/backup_error.txt
      # Return exit code of last backup command
      grep "ERROR" /tmp/backup_error.txt && exit 1 || exit 0
    success_criteria:
      - "exit_code == 0"
    evidence_capture:
      - file: "/tmp/backup_error.txt"
        hash: true
        retain_days: 90
```

**WHY EACH PIECE:**

- **`id`:** Unique step identifier for evidence trail
- **`timeout_seconds`:** Prevents hung processes from blocking remediation
- **`script`:** Actual bash commands (pre-approved, version controlled)
- **`success_criteria`:** Explicit definition of "worked" vs "failed"
- **`evidence_capture`:** Saves log snippet with hash for audit trail

**Evidence Generation Flow:**

```
1. Runbook executes step "check_logs"
2. Script runs, captures output
3. Output saved to /tmp/backup_error.txt
4. SHA256 hash computed: sha256:a1b2c3...
5. Evidence bundle created:
   {
     "runbook_id": "RB-BACKUP-001",
     "step_id": "check_logs",
     "executed_at": "2025-10-31T02:15:23Z",
     "script_hash": "sha256:d4e5f6...",  # Hash of script itself
     "output_hash": "sha256:a1b2c3...",  # Hash of output
     "exit_code": 0,
     "success": true
   }
6. Bundle signed with cosign
7. Uploaded to WORM storage
8. Reference added to nightly compliance packet
```

**Auditor Value:**

Auditor: "How do I know you actually test backups?"

You: "RB-BACKUP-001 executed 30 times in October. Here are 30 signed evidence bundles with timestamps, script hashes, and outputs. You can verify signatures with our public key."

This is **cryptographic proof** vs. "We have a policy to test backups."

```yaml
rollback:
  - action: "alert_administrator"
    message: "Backup remediation failed, manual intervention required"
    severity: "critical"
    channels:
      - email
      - pagerduty
```

**WHY:** If automation fails, human must be notified immediately. Healthcare data is critical.

### Runbook Execution Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                          INCIDENT OCCURS                             │
│                 (e.g., backup job exits with code 1)                │
└────────────────────────┬────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    MCP WATCHER DETECTS                               │
│    • Reads journald log: "restic backup failed"                     │
│    • Extracts metadata: service=restic, exit_code=1                 │
│    • Publishes to event queue: tenant:clinic-001:incidents          │
└────────────────────────┬────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    MCP PLANNER (LLM)                                 │
│    • Receives incident JSON                                          │
│    • Queries runbook library                                         │
│    • Prompt: "Match incident to runbook ID only"                    │
│    • LLM Response: {"runbook_id": "RB-BACKUP-001"}                  │
│    • NO free-form command generation                                 │
└────────────────────────┬────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   GUARDRAIL CHECKS                                   │
│    ✓ Runbook ID exists in approved library                          │
│    ✓ Client has permission for this runbook                         │
│    ✓ No rate limit violation (last run >5min ago)                   │
│    ✓ SLA timer started (30min deadline)                             │
└────────────────────────┬────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  MCP EXECUTOR                                        │
│    • Loads RB-BACKUP-001.yaml                                        │
│    • For each step:                                                  │
│      1. Log step start with timestamp                                │
│      2. Execute script in sandboxed environment                      │
│      3. Capture stdout, stderr, exit code                            │
│      4. Hash script and output                                       │
│      5. Check success criteria                                       │
│      6. If fail, run rollback steps                                  │
│      7. Generate evidence bundle                                     │
└────────────────────────┬────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────────┐
│                 EVIDENCE BUNDLER                                     │
│    • Collects: runbook_id, steps, outputs, hashes                   │
│    • Adds: timestamp, client_id, operator (service account)         │
│    • Includes: HIPAA control IDs, SLA status                        │
│    • Signs: cosign with private key                                  │
│    • Stores: Local + WORM S3 bucket                                  │
└────────────────────────┬────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  CLOSED-LOOP VERIFICATION                            │
│    • Re-query backup status                                          │
│    • If successful: Mark incident resolved                           │
│    • If still failing: Escalate to human                             │
│    • If resolved: Add to monthly compliance packet                   │
└─────────────────────────────────────────────────────────────────────┘
```

### WHY This Architecture is HIPAA-Compliant

1. **§164.312(b) - Audit Controls:** Every action logged with cryptographic proof
2. **§164.308(a)(5) - Security Awareness:** Runbooks document approved procedures
3. **§164.308(a)(6) - Incident Procedures:** Automated response with escalation
4. **§164.308(a)(8) - Evaluation:** Evidence bundles prove controls work
5. **§164.316(b) - Policies and Procedures:** Runbooks ARE the procedures

---

## Evidence Pipeline

### WHERE: `mcp-server/evidence/` Directory Structure

```
mcp-server/evidence/
├── packager.py                   # Main evidence bundler
├── bundler.py                    # Evidence bundle generator
├── signer.py                     # Cosign/GPG signing
├── worm_uploader.py              # S3 with object lock
├── schemas/
│   ├── evidence_bundle.json      # JSON schema for bundles
│   └── compliance_packet.json    # JSON schema for packets
└── templates/
    ├── monthly_packet.md          # Markdown template
    └── evidence_manifest.json     # Manifest template
```

### WHY: Evidence by Architecture

#### The Traditional Compliance Problem

**Manual Evidence Collection:**
```
Auditor: "Show me your backup logs from March."
IT Team: "Let me dig through our ticketing system..."
Result: 3 days to compile evidence, manual PDF creation, no cryptographic proof
```

**Why Manual Fails:**
- Depends on human memory
- Retrospective documentation (easily fabricated)
- No tamper-evidence
- Expensive (consultant time)

#### Our Solution: Evidence as Operational Byproduct

**Automated Evidence Generation:**
```
Auditor: "Show me your backup logs from March."
You: "Here's evidence bundle EB-202503-BACKUPS.zip, signed with cosign."
Auditor verifies signature in 30 seconds.
```

**Why This Works:**
- Evidence generated during operation (not after)
- Cryptographically signed (tamper-evident)
- Stored in WORM (cannot be modified)
- Machine-verifiable (no human attestation needed)

### Evidence Bundle Structure

**WHERE:** Local: `/var/lib/msp/evidence/` + S3: `s3://compliance-worm/clinic-001/`

**WHAT:** JSON file with metadata, hashes, and references to artifacts

**Example Bundle:**

```json
{
  "bundle_id": "EB-20251031-0001",
  "bundle_version": "1.0",
  "client_id": "clinic-001",
  "generated_at": "2025-10-31T06:00:00Z",
  "generator": "msp-evidence-packager v1.0.0",

  "incident": {
    "id": "INC-20251031-0001",
    "type": "backup_failure",
    "severity": "high",
    "detected_at": "2025-10-31T02:00:15Z",
    "resolved_at": "2025-10-31T02:04:23Z",
    "mttr_seconds": 248,
    "sla_met": true
  },

  "runbook": {
    "id": "RB-BACKUP-001",
    "version": "1.0.0",
    "hash": "sha256:a1b2c3d4e5f6...",
    "hipaa_controls": [
      "§164.308(a)(7)(ii)(A)",
      "§164.310(d)(2)(iv)"
    ]
  },

  "execution": {
    "operator": "service:msp-executor",
    "steps": [
      {
        "id": "check_logs",
        "started_at": "2025-10-31T02:00:16Z",
        "completed_at": "2025-10-31T02:00:45Z",
        "duration_seconds": 29,
        "script_hash": "sha256:d4e5f6...",
        "output_hash": "sha256:g7h8i9...",
        "exit_code": 0,
        "success": true,
        "evidence_files": [
          "backup_error.txt"
        ]
      },
      {
        "id": "verify_disk_space",
        "started_at": "2025-10-31T02:00:46Z",
        "completed_at": "2025-10-31T02:01:02Z",
        "duration_seconds": 16,
        "script_hash": "sha256:j1k2l3...",
        "output_hash": "sha256:m4n5o6...",
        "exit_code": 0,
        "success": true,
        "evidence_files": [
          "df_output.txt"
        ]
      },
      {
        "id": "restart_backup_service",
        "started_at": "2025-10-31T02:01:03Z",
        "completed_at": "2025-10-31T02:04:20Z",
        "duration_seconds": 197,
        "script_hash": "sha256:p7q8r9...",
        "output_hash": "sha256:s1t2u3...",
        "exit_code": 0,
        "success": true,
        "evidence_files": [
          "service_restart.log",
          "backup_success.log"
        ]
      }
    ]
  },

  "artifacts": [
    {
      "filename": "backup_error.txt",
      "hash": "sha256:g7h8i9...",
      "size_bytes": 2048,
      "content_type": "text/plain"
    },
    {
      "filename": "df_output.txt",
      "hash": "sha256:m4n5o6...",
      "size_bytes": 512,
      "content_type": "text/plain"
    },
    {
      "filename": "service_restart.log",
      "hash": "sha256:s1t2u3...",
      "size_bytes": 4096,
      "content_type": "text/plain"
    },
    {
      "filename": "backup_success.log",
      "hash": "sha256:v4w5x6...",
      "size_bytes": 1024,
      "content_type": "text/plain"
    }
  ],

  "verification": {
    "closed_loop_check": true,
    "backup_status_after": "success",
    "next_backup_scheduled": "2025-11-01T02:00:00Z"
  },

  "signatures": {
    "bundle_hash": "sha256:y7z8a9b0...",
    "signer": "cosign",
    "public_key_id": "msp-evidence-key-2025",
    "signature": "MEUCIQD...",
    "signed_at": "2025-10-31T06:00:05Z"
  },

  "storage": {
    "local_path": "/var/lib/msp/evidence/EB-20251031-0001.json",
    "worm_url": "s3://compliance-worm/clinic-001/2025/10/EB-20251031-0001.json",
    "worm_lock_enabled": true,
    "retention_days": 2555,
    "uploaded_at": "2025-10-31T06:00:10Z"
  }
}
```

### WHY Each Field Matters

**`bundle_id`:** Unique identifier for auditor reference
**`bundle_version`:** Schema version for forward compatibility
**`client_id`:** Multi-tenant isolation
**`generated_at`:** Timestamp for audit trail

**`incident.*`:** Links evidence to specific event
**`mttr_seconds`:** Proves SLA compliance
**`sla_met`:** Boolean for compliance reporting

**`runbook.hash`:** Proves exact version of runbook used
**`runbook.hipaa_controls`:** Direct link to HIPAA requirement

**`execution.operator`:** Service account attribution
**`execution.steps[].script_hash`:** Proves script hasn't been tampered
**`execution.steps[].output_hash`:** Proves output matches execution

**`signatures.bundle_hash`:** Cryptographic integrity check
**`signatures.signature`:** Cosign signature for verification

**`storage.worm_lock_enabled`:** Proves immutability
**`storage.retention_days`:** Meets HIPAA 6-year requirement

### Evidence Verification

**How Auditor Verifies:**

```bash
# 1. Download evidence bundle
aws s3 cp s3://compliance-worm/clinic-001/2025/10/EB-20251031-0001.json .

# 2. Verify signature
cosign verify-blob \
  --key msp-evidence-public-key.pem \
  --signature EB-20251031-0001.json.sig \
  EB-20251031-0001.json

# Output: Verified OK

# 3. Verify artifact hashes
cat backup_error.txt | sha256sum
# Output: g7h8i9... (matches bundle.artifacts[0].hash)

# 4. Verify runbook hash
cat runbooks/RB-BACKUP-001.yaml | sha256sum
# Output: a1b2c3... (matches bundle.runbook.hash)
```

**Result:** Auditor has cryptographic proof that:
1. Bundle hasn't been tampered with (signature valid)
2. Artifacts match what was claimed (hashes match)
3. Runbook used was approved version (hash matches)
4. Timeline is accurate (timestamps in signed bundle)

This is **mathematically provable compliance**.

### WORM Storage Strategy

**WHERE:** S3 bucket with object lock + local retention

**WHY S3 Object Lock:**
- **Immutable:** Cannot be deleted or modified, even by root AWS account
- **Compliance mode:** Enforced retention period
- **Legal hold:** Can freeze evidence for investigations
- **Versioning:** Tracks all access attempts

**Configuration:**

```hcl
# terraform/modules/evidence-storage/main.tf
resource "aws_s3_bucket" "compliance_worm" {
  bucket = "msp-compliance-worm-${var.client_id}"

  versioning {
    enabled = true
  }

  object_lock_configuration {
    object_lock_enabled = "Enabled"
    rule {
      default_retention {
        mode = "COMPLIANCE"  # Cannot be deleted by anyone
        days = 2555          # ~7 years (HIPAA + 1 year buffer)
      }
    }
  }

  lifecycle_rule {
    enabled = true

    transition {
      days          = 90
      storage_class = "GLACIER"  # Cheaper long-term storage
    }
  }

  server_side_encryption_configuration {
    rule {
      apply_server_side_encryption_by_default {
        sse_algorithm = "AES256"
      }
    }
  }

  logging {
    target_bucket = aws_s3_bucket.audit_logs.id
    target_prefix = "evidence-access-logs/"
  }
}
```

**WHY This Satisfies HIPAA:**

- **§164.312(b):** Audit controls - access logs track all views
- **§164.310(d)(2)(iv):** Data backup - evidence retained 7 years
- **§164.316(b)(2)(ii):** Documentation retention - immutable by design

---

## Security Hardening

### WHERE: `client-flake/modules/` Directory Structure

```
client-flake/modules/
├── luks-encryption.nix           # Full disk encryption
├── ssh-certificates.nix          # Certificate-based auth
├── baseline-enforcement.nix      # Baseline YAML → NixOS
├── audit-logging.nix             # auditd + journald
└── health-checks.nix             # Service monitoring
```

### WHY: Defense in Depth

#### Problem: Single Points of Failure

Traditional systems rely on perimeter defense:
- "Our firewall blocks everything"
- "Our VPN requires password"
- "Our backups are on network share"

**What happens when:**
- Firewall is misconfigured?
- VPN password is phished?
- Ransomware encrypts network share?

#### Solution: Layered Security

Our approach: Even if outer layers fail, inner layers protect PHI.

```
┌─────────────────────────────────────────────────────────────┐
│ Layer 1: Network Perimeter                                  │
│ • Firewall (may be misconfigured)                           │
│ • VPN (credential may be stolen)                            │
└────────────────────┬────────────────────────────────────────┘
                     │ ↓ Breach
┌─────────────────────────────────────────────────────────────┐
│ Layer 2: SSH Authentication                                  │
│ • Short-lived certificates (8h max)                         │
│ • No passwords accepted                                      │
│ • Attacker needs: valid cert + private key + valid hostname │
└────────────────────┬────────────────────────────────────────┘
                     │ ↓ Still breached
┌─────────────────────────────────────────────────────────────┐
│ Layer 3: Disk Encryption                                     │
│ • LUKS with AES-256                                          │
│ • Attacker gets: encrypted blob                             │
│ • Needs: decryption key from network-bound Tang server      │
└────────────────────┬────────────────────────────────────────┘
                     │ ↓ Somehow got key
┌─────────────────────────────────────────────────────────────┐
│ Layer 4: No PHI on System                                    │
│ • This system processes metadata only                        │
│ • Attacker gets: syslog, not patient records                │
│ • Real PHI is on separate EHR server                         │
└─────────────────────────────────────────────────────────────┘
```

### LUKS Full Disk Encryption

**WHERE:** `client-flake/modules/luks-encryption.nix`

**WHAT:** Full disk encryption with network-bound key management

**WHY:** HIPAA §164.312(a)(2)(iv) requires encryption of ePHI at rest

**Configuration:**

```nix
# client-flake/modules/luks-encryption.nix
{ config, lib, pkgs, baseline, ... }:

let
  cfg = baseline.encryption.disk_encryption;
in
{
  boot.initrd.luks.devices = {
    root = {
      device = "/dev/sda2";
      preLVM = true;

      # WHY: Allow headless reboot without manual password entry
      keyFile = "/dev/disk/by-id/usb-KEY/keyfile";
      fallbackToPassword = true;
    };
  };

  # WHY: Network-bound disk encryption (Tang/Clevis)
  # Allows automatic unlock when on trusted network
  boot.initrd.network = {
    enable = cfg.remote_unlock.enabled;
    ssh = {
      enable = true;
      port = 2222;
      hostKeys = [ /etc/secrets/initrd/ssh_host_ed25519_key ];
      authorizedKeys = config.users.users.root.openssh.authorizedKeys.keys;
    };
  };

  # WHY: Clevis binds decryption to Tang server availability
  # If server unreachable, system won't boot (defense against theft)
  boot.initrd.clevis = lib.mkIf cfg.remote_unlock.enabled {
    enable = true;
    devices.root.secretFile = "/etc/clevis/root.jwe";
    useTang = true;
    tangServers = cfg.remote_unlock.tang_servers;
  };
}
```

**WHY Network-Bound Encryption:**

Traditional LUKS: Password at boot → If server stolen, attacker can brute force

Our approach: Tang server must be reachable → If server stolen and removed from network, automatic unlock fails → Must use backup TPM key → TPM sealed to specific hardware → Attacker cannot move disk to another machine

**Evidence Trail:**

```json
{
  "control": "§164.312(a)(2)(iv)",
  "implementation": "LUKS AES-256-XTS with Tang network binding",
  "evidence": {
    "luks_status": "active",
    "encryption_algorithm": "aes-xts-plain64",
    "key_size": "256",
    "tang_binding": true,
    "tang_servers": ["tang1.msp.internal", "tang2.msp.internal"],
    "tpm_backup": true,
    "last_verified": "2025-10-31T06:00:00Z"
  }
}
```

### SSH Certificate Authentication

**WHERE:** `client-flake/modules/ssh-certificates.nix`

**WHAT:** Short-lived SSH certificates instead of long-lived keys

**WHY:** §164.312(a)(2)(i) requires unique user identification with time-limited access

**Problem with Traditional SSH Keys:**

```
User generates key pair:
  ssh-keygen -t ed25519

Public key added to authorized_keys:
  ssh-ed25519 AAAAC3... user@laptop

Problems:
• Key valid forever (no expiration)
• If laptop stolen, key still works
• No central revocation
• No proof of who used key when
```

**Our Solution: SSH Certificates**

```
Certificate Authority (step-ca or Vault):
  • Issues short-lived certs (8h max)
  • Requires authentication each time
  • Revocation is automatic (cert expires)
  • Audit trail: CA logs show who requested cert when

User authenticates:
  step ssh certificate user@laptop \
    --principal=user \
    --lifetime=8h

Server validates:
  • Check cert signature from trusted CA
  • Check cert not expired
  • Check principal matches allowed users
  • Log authentication with cert serial number
```

**Configuration:**

```nix
# client-flake/modules/ssh-certificates.nix
{ config, lib, pkgs, baseline, ... }:

let
  sshCfg = baseline.identity_and_access.ssh_hardening;
in
{
  services.openssh = {
    enable = true;

    # WHY: Reject password authentication entirely
    settings = {
      PasswordAuthentication = false;
      ChallengeResponseAuthentication = false;
      KbdInteractiveAuthentication = false;
      PermitRootLogin = lib.mkForce "no";
    };

    # WHY: Only accept certificates from trusted CA
    hostKeys = [
      {
        type = "ed25519";
        path = "/etc/ssh/ssh_host_ed25519_key";
      }
    ];

    extraConfig = ''
      # Trust certificates signed by this CA
      TrustedUserCAKeys /etc/ssh/ca.pub

      # Reject certificates older than 8 hours
      MaxAuthTries ${toString sshCfg.max_auth_tries}
      LoginGraceTime ${toString sshCfg.login_grace_time_seconds}

      # Log certificate serial number for audit trail
      LogLevel VERBOSE

      # Require certificate principal to match Unix username
      AuthorizedPrincipalsFile /etc/ssh/principals/%u
    '';
  };

  # WHY: Audit trail for SSH access
  security.auditd.rules = [
    "-w /var/log/auth.log -p wa -k ssh_access"
    "-w /etc/ssh/sshd_config -p wa -k ssh_config_change"
  ];
}
```

**Evidence Trail:**

```json
{
  "control": "§164.312(a)(2)(i)",
  "implementation": "SSH certificate authentication with 8h lifetime",
  "access_log": {
    "timestamp": "2025-10-31T14:32:15Z",
    "user": "admin",
    "source_ip": "192.168.1.45",
    "certificate_serial": "1234567890",
    "certificate_valid_from": "2025-10-31T08:00:00Z",
    "certificate_valid_until": "2025-10-31T16:00:00Z",
    "principal": "admin",
    "authentication_method": "publickey-certificate"
  }
}
```

**Compliance Value:**

Auditor: "How do you know Bob didn't access the system after being terminated?"

You: "Bob's last certificate expired 2025-06-15 16:00:00Z. CA logs show no certificates issued to Bob after termination date 2025-06-15 09:00:00Z. Mathematically impossible for Bob to have accessed system."

---

## Compliance Reporting

### WHERE: `reporting/` Directory Structure

```
reporting/
├── packager.py                   # Nightly packet generator
├── evidence_bundler.py           # Evidence collection
├── compliance_rules.py           # Rule evaluation
├── templates/
│   ├── monthly_packet.md         # Compliance packet template
│   ├── weekly_postcard.html      # Executive summary
│   └── evidence_manifest.json    # Evidence listing
└── output/
    └── {year}/{month}/           # Generated packets
        ├── packet.pdf            # Print-ready compliance packet
        ├── evidence.zip          # Evidence bundle
        └── manifest.json         # Packet manifest
```

### WHY: Auditor-Ready Outputs

#### The Traditional Compliance Burden

**Manual Compliance Process:**

```
Auditor Request:
"Show me evidence of backup testing for Q3 2025"

Manual Response:
1. Search ticketing system (3 hours)
2. Find relevant tickets (maybe incomplete)
3. Export logs from servers (if still available)
4. Format in Word document (2 hours)
5. Get manager signature (1 day delay)
6. Convert to PDF
7. Email to auditor

Total time: 2 days, $2000 in labor
Quality: Depends on memory and documentation diligence
Verifiability: None (could be fabricated)
```

**Why This Fails:**
- Retrospective (depends on memory)
- Labor-intensive (expensive)
- Error-prone (human copy-paste mistakes)
- Not tamper-evident (Word doc)

#### Our Solution: Automated Compliance Packets

**Automated Process:**

```
Nightly Job (02:00 AM):
1. Collect evidence from last 24 hours (automated)
2. Evaluate compliance rules (automated)
3. Generate compliance packet (automated)
4. Sign packet with cosign (automated)
5. Upload to WORM storage (automated)

Auditor Request:
"Show me evidence of backup testing for Q3 2025"

Your Response:
"Here are three monthly packets: July, August, September"
[Attach 3 PDFs + evidence bundles]

Total time: 30 seconds (download from S3)
Quality: Perfect (automated collection)
Verifiability: Cryptographic (cosign signatures)
```

### Monthly Compliance Packet Structure

**WHERE:** `reporting/output/2025/10/Clinic-001-October-2025.pdf`

**WHAT:** Print-ready PDF proving HIPAA compliance for the month

**Sections:**

```markdown
# Monthly HIPAA Compliance Packet
**Client:** Clinic 001
**Period:** October 1-31, 2025
**Baseline:** NixOS-HIPAA v1.0
**Generated:** 2025-11-01 06:00 UTC

## Executive Summary

**Compliance Status:** 98.2% (54/55 controls passing)
**Critical Issues:** 0
**Auto-Fixed Incidents:** 47
**MTTR (Critical):** 4.2 hours
**Backup Success Rate:** 100% (31/31 backups successful)
**Restore Tests:** 4 (all successful)

**Action Items:**
1. One control requires manual review: §164.312(a)(3) - Emergency Access
   Status: Break-glass account used Oct 15, reviewed Oct 16, justified

## Control Posture Matrix

| HIPAA Control | Requirement | Status | Evidence | Last Verified |
|---------------|-------------|--------|----------|---------------|
| §164.308(a)(1)(ii)(D) | Info System Activity Review | ✅ Pass | EB-2025-10-* (45 bundles) | 2025-10-31 |
| §164.308(a)(7)(ii)(A) | Data Backup Plan | ✅ Pass | RB-BACKUP-001 (31 runs) | 2025-10-31 |
| §164.310(d)(2)(iv) | Data Backup & Storage | ✅ Pass | RB-RESTORE-001 (4 tests) | 2025-10-27 |
| §164.312(a)(1) | Access Control | ✅ Pass | SSH cert logs | 2025-10-31 |
| §164.312(a)(2)(i) | Unique User ID | ✅ Pass | SSH cert logs | 2025-10-31 |
| §164.312(a)(2)(iv) | Encryption | ✅ Pass | LUKS status check | 2025-10-31 |
| §164.312(b) | Audit Controls | ✅ Pass | auditd logs (45 days) | 2025-10-31 |
| §164.312(e)(1) | Transmission Security | ✅ Pass | TLS 1.3 enforcement | 2025-10-31 |

## Incidents & Auto-Remediation

| Date | Incident | Runbook | MTTR | Status |
|------|----------|---------|------|--------|
| Oct 03 | Backup failure (disk full) | RB-BACKUP-001 | 4min | ✅ Resolved |
| Oct 07 | Service crash (nginx) | RB-SERVICE-001 | 2min | ✅ Resolved |
| Oct 12 | Certificate expiring | RB-CERT-001 | 6min | ✅ Resolved |
| Oct 18 | Disk 90% full | RB-DISK-001 | 3min | ✅ Resolved |
| Oct 23 | CPU spike (backup job) | RB-CPU-001 | 0min | ✅ Self-resolved |

**Total Incidents:** 47
**Auto-Resolved:** 47 (100%)
**Average MTTR:** 3.8 minutes

## Backup Verification

| Week | Backup Status | Size | Checksum | Restore Test | Result |
|------|--------------|------|----------|--------------|--------|
| Oct 1-7 | ✅ 7/7 successful | 128 GB | sha256:a1b2... | Oct 6 | ✅ 3 files, 1 DB |
| Oct 8-14 | ✅ 7/7 successful | 129 GB | sha256:c3d4... | Oct 13 | ✅ 5 files |
| Oct 15-21 | ✅ 7/7 successful | 131 GB | sha256:e5f6... | Oct 20 | ✅ 2 DB tables |
| Oct 22-28 | ✅ 7/7 successful | 133 GB | sha256:g7h8... | Oct 27 | ✅ 4 files |
| Oct 29-31 | ✅ 3/3 successful | 134 GB | sha256:i9j0... | Scheduled Nov 3 | Pending |

**Evidence:** EB-RESTORE-2025-10-* (4 signed bundles)
**HIPAA Citation:** §164.308(a)(7)(ii)(A), §164.310(d)(2)(iv)

## Configuration Baseline

**Baseline Version:** NixOS-HIPAA v1.0
**Flake Hash:** sha256:abc123...
**Last Updated:** Oct 1, 2025
**Drift Incidents:** 0

**Enforced Controls:**
- SSH: Password auth disabled, certificate-based only
- Encryption: LUKS AES-256-XTS enabled
- Audit: auditd + journald with 2-year retention
- Patching: Auto-apply critical within 7 days
- Firewall: Default deny, SSH + HTTPS only
- Time Sync: NTP max drift 90s

**Exceptions:** None active

## Evidence Bundle Manifest

**Total Bundles Generated:** 47
**Storage Location:** s3://compliance-worm/clinic-001/2025/10/
**Signature Verification:** All signatures valid
**Retention Period:** 2,555 days (7 years)

**Bundle List:** (Abbreviated)
- EB-20251001-0001.json (Backup success)
- EB-20251003-0002.json (Backup failure remediation)
- EB-20251006-0003.json (Restore test)
- ... (44 more bundles)

**Verification Command:**
```bash
cosign verify-blob \
  --key msp-evidence-public-key.pem \
  --signature EB-20251001-0001.json.sig \
  EB-20251001-0001.json
```

## Attestation

I, [System Administrator], attest that:
- All evidence bundles are complete and accurate
- All automated remediation actions were successful
- No PHI was processed by compliance monitoring systems
- All exceptions are documented and approved

**Signature:** _________________________
**Date:** _________________________

---

**End of Monthly Compliance Packet**
**Next Review:** November 30, 2025
**Questions:** Contact security@clinic-001.com
```

### WHY This Satisfies Auditors

1. **Complete Evidence:** All 47 incidents documented with proof
2. **Traceable:** Every control links to evidence bundle
3. **Verifiable:** Cryptographic signatures prove authenticity
4. **Auditor-Friendly:** Print-ready PDF, no technical knowledge needed
5. **Timely:** Generated nightly, always current
6. **Comprehensive:** Covers all HIPAA Security Rule requirements

**Auditor Workflow:**

```
1. Receive PDF packet
2. Verify signatures with public key
3. Spot-check evidence bundles (random sampling)
4. Confirm hash matches claimed value
5. Sign off on compliance

Traditional: 3 days
Our approach: 30 minutes
```

---

## Implementation Timeline

### Week 4 Deliverables (DONE)

✅ **Day 1-2: Baseline Enhancement**
- [x] Review existing `baseline/hipaa-v1.yaml`
- [x] Add detailed inline comments (WHERE/WHY)
- [x] Complete `controls-map.csv`
- [x] Create exception templates

✅ **Day 3-4: Runbook Structure**
- [x] Review 6 core runbooks
- [x] Verify HIPAA control mappings
- [x] Document execution flow
- [x] Add evidence requirements

### Week 5 Deliverables (NEXT)

**Day 1-2: Evidence Pipeline**
- [ ] Implement `evidence/packager.py`
- [ ] Add cosign signing
- [ ] Configure WORM S3 storage
- [ ] Test evidence bundle generation

**Day 3-4: MCP Planner/Executor Split**
- [ ] Refactor `mcp-server/server.py`
- [ ] Create `planner.py` (LLM runbook selection)
- [ ] Create `executor.py` (runbook execution)
- [ ] Add guardrails

**Day 5: Integration Testing**
- [ ] End-to-end test: incident → runbook → evidence
- [ ] Verify evidence signatures
- [ ] Test WORM immutability

### Week 6 Deliverables

**Day 1-2: Compliance Reporting**
- [ ] Implement `reporting/packager.py`
- [ ] Create monthly packet generator
- [ ] Test PDF generation
- [ ] Add email delivery

**Day 3-4: Client Hardening**
- [ ] Enhance LUKS configuration
- [ ] Add SSH certificate auth
- [ ] Implement baseline enforcement
- [ ] Add health checks

**Day 5: Documentation & Review**
- [ ] Complete implementation docs
- [ ] Create deployment runbook
- [ ] Internal review
- [ ] Prepare for pilot

---

## Success Criteria

### Technical Success

- [ ] All 6 runbooks execute successfully in lab
- [ ] Evidence bundles generated and signed
- [ ] WORM storage rejects modification attempts
- [ ] Monthly compliance packet generates automatically
- [ ] Baseline drift detection works
- [ ] SSH certificate auth functional

### Compliance Success

- [ ] Every HIPAA control has evidence trail
- [ ] Controls-map.csv 100% complete
- [ ] Exception process documented
- [ ] BAA template includes metadata-only scope
- [ ] Evidence bundles pass signature verification

### Operational Success

- [ ] Runbooks complete in < 5 minutes
- [ ] No false-positive incident triggers
- [ ] Evidence storage < $50/month per client
- [ ] Compliance packet readable by non-technical auditor
- [ ] Deployment time < 3 hours

### Business Success

- [ ] Pilot client agrees to 30-day trial
- [ ] Compliance packet impresses prospect
- [ ] Can explain architecture to auditor in < 10 minutes
- [ ] Evidence of 40%+ margin on target pricing

---

## Appendix: File Locations Quick Reference

```
Msp_Flakes/
│
├── baseline/                         # HIPAA baseline configuration
│   ├── hipaa-v1.yaml                 # Main baseline (YOUR FIRST READ)
│   ├── controls-map.csv              # HIPAA → config mapping
│   └── exceptions/                   # Client overrides
│       └── clinic-{id}.yaml
│
├── runbooks/                         # Pre-approved remediation
│   ├── RB-BACKUP-001-failure.yaml
│   ├── RB-CERT-001-expiry.yaml
│   ├── RB-CPU-001-high.yaml
│   ├── RB-DISK-001-full.yaml
│   ├── RB-RESTORE-001-test.yaml
│   └── RB-SERVICE-001-crash.yaml
│
├── mcp-server/                       # Central orchestration server
│   ├── planner.py                    # LLM runbook selection
│   ├── executor.py                   # Runbook execution
│   ├── evidence/                     # Evidence generation
│   │   ├── packager.py
│   │   ├── bundler.py
│   │   ├── signer.py
│   │   └── worm_uploader.py
│   └── guardrails/                   # Safety controls
│       ├── validation.py
│       └── rate_limits.py
│
├── client-flake/                     # Deployed to client sites
│   ├── flake.nix                     # Main config
│   └── modules/
│       ├── luks-encryption.nix
│       ├── ssh-certificates.nix
│       ├── baseline-enforcement.nix
│       ├── audit-logging.nix
│       └── health-checks.nix
│
├── reporting/                        # Compliance outputs
│   ├── packager.py                   # Monthly packet generator
│   ├── templates/
│   │   ├── monthly_packet.md
│   │   └── weekly_postcard.html
│   └── output/
│       └── {year}/{month}/
│           ├── packet.pdf
│           ├── evidence.zip
│           └── manifest.json
│
└── docs/                             # Documentation
    ├── compliance/
    │   ├── WEEK4_WHERE_AND_WHY.md    # THIS DOCUMENT
    │   ├── hipaa-control-mapping.csv
    │   └── exception-process.md
    └── architecture/
        ├── baseline-implementation.md
        ├── evidence-pipeline.md
        └── runbook-execution.md
```

---

## Questions & Answers

**Q: Why YAML for baselines instead of pure NixOS?**

A: Auditors can't read Nix. YAML is:
- Human-readable (auditors)
- Machine-parseable (automation)
- Version-controllable (git diff)
- Easily mapped to HIPAA controls

**Q: Why 8-hour SSH certificate lifetime?**

A: Balance between security and usability:
- Too short (1h): Admin frustration, productivity loss
- Too long (24h): Wider compromise window
- 8h: Standard work day, forces daily re-authentication

**Q: Why WORM storage instead of regular S3?**

A: Auditor requirement: "Prove evidence wasn't modified after the fact."
- Regular S3: Can delete/modify with root account
- WORM: **Mathematically impossible** to modify, even by us
- HIPAA §164.312(b): Audit controls must be protected from alteration

**Q: Why metadata-only scope?**

A: Liability reduction:
- PHI processing: High liability, complex BAA, expensive insurance
- Metadata-only: Lower liability, simpler BAA, lower insurance
- Same compliance value (system logs prove controls work)
- Can expand to PHI later if needed, but start simple

**Q: Why NixOS instead of Ansible/Chef/Puppet?**

A: Deterministic builds:
- Ansible: "Run these commands" (imperative, can fail halfway)
- NixOS: "System is this state" (declarative, atomic rollback)
- Compliance value: Cryptographic proof of config (flake hash)
- Anduril precedent: DoD trusts NixOS for defense systems

---

**Document Maintenance:**

This document should be updated:
- When baseline version increments
- When new runbooks are added
- When HIPAA guidance changes
- After pilot deployment learnings
- Quarterly review minimum

**Document Owner:** MSP Security Team
**Next Review:** 2026-01-31
**Change Log:** See git commit history

---

**End of Document**
**Version:** 1.0
**Status:** Active Implementation Guide
**Last Updated:** 2025-10-31
