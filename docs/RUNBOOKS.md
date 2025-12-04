# Runbooks & Evidence Bundles

## Runbook Structure

Each runbook is a YAML file with pre-approved steps, HIPAA citations, and required evidence fields.

```yaml
id: RB-BACKUP-001
name: "Backup Failure Remediation"
hipaa_controls:
  - "164.308(a)(7)(ii)(A)"
  - "164.310(d)(2)(iv)"
severity: high
steps:
  - action: check_backup_logs
    timeout: 30s
  - action: verify_disk_space
    timeout: 10s
  - action: restart_backup_service
    timeout: 60s
  - action: trigger_manual_backup
    timeout: 300s
rollback:
  - action: alert_administrator
evidence_required:
  - backup_log_excerpt
  - disk_usage_before
  - disk_usage_after
  - service_status
  - backup_completion_hash
```

## Standard Runbooks

### RB-BACKUP-001: Backup Failure
- Check backup logs
- Verify disk space
- Restart backup service
- Trigger manual backup
- HIPAA: §164.308(a)(7)(ii)(A), §164.310(d)(2)(iv)

### RB-CERT-001: Certificate Expiry
- Check certificate expiry dates
- Generate new CSR
- Request renewal
- Install new certificate
- HIPAA: §164.312(a)(2)(iv), §164.312(e)(1)

### RB-DISK-001: Disk Full
- Identify large files
- Clear old logs
- Clean temp directories
- Verify free space
- HIPAA: §164.308(a)(1)(ii)(D)

### RB-SERVICE-001: Service Crash
- Check service status
- Review crash logs
- Restart service
- Verify health
- HIPAA: §164.312(b)

### RB-CPU-001: High CPU
- Identify top processes
- Check for runaway jobs
- Kill or restart offender
- Verify normalization
- HIPAA: §164.308(a)(1)(ii)(D)

### RB-RESTORE-001: Weekly Test Restore
- Select random backup
- Create scratch VM
- Restore to scratch
- Verify checksums
- Cleanup scratch VM
- HIPAA: §164.308(a)(7)(ii)(A), §164.310(d)(2)(iv)

## Evidence Bundle Structure

Every monitored event generates standardized evidence:

```json
{
  "bundle_id": "EB-20251023-0001",
  "client_id": "clinic-001",
  "incident_id": "INC-20251023-0001",
  "runbook_id": "RB-BACKUP-001",
  "timestamp_start": "2025-10-23T14:32:01Z",
  "timestamp_end": "2025-10-23T14:35:23Z",
  "operator": "service:mcp-executor",
  "hipaa_controls": ["164.308(a)(7)(ii)(A)", "164.310(d)(2)(iv)"],
  "inputs": {
    "log_excerpt_hash": "sha256:a1b2c3...",
    "disk_usage_before": "87%"
  },
  "actions_taken": [
    {
      "step": 1,
      "action": "check_backup_logs",
      "result": "failed",
      "script_hash": "sha256:d4e5f6..."
    },
    {
      "step": 2,
      "action": "verify_disk_space",
      "result": "ok",
      "script_hash": "sha256:g7h8i9..."
    },
    {
      "step": 3,
      "action": "restart_backup_service",
      "result": "ok",
      "script_hash": "sha256:j1k2l3..."
    }
  ],
  "outputs": {
    "backup_completion_hash": "sha256:m4n5o6...",
    "disk_usage_after": "62%"
  },
  "sla_met": true,
  "mttr_seconds": 202,
  "evidence_bundle_hash": "sha256:p7q8r9...",
  "storage_locations": [
    "local:/var/lib/msp/evidence/EB-20251023-0001.json",
    "s3://compliance-worm/clinic-001/2025/10/EB-20251023-0001.json"
  ]
}
```

## Monthly Compliance Packet Template

```markdown
# HIPAA Compliance Report
**Client:** Clinic ABC
**Period:** October 1-31, 2025
**Baseline:** NixOS-HIPAA v1.2

## Executive Summary
- Incidents detected: 12
- Automatically remediated: 10
- Escalated to administrator: 2
- SLA compliance: 98.3%
- MTTR average: 4.2 minutes

## Controls Status
| Control | Status | Evidence Count | Exceptions |
|---------|--------|---------------|-----------|
| 164.308(a)(1)(ii)(D) | ✅ Compliant | 45 audit logs | 0 |
| 164.308(a)(7)(ii)(A) | ✅ Compliant | 4 backup tests | 0 |
| 164.312(a)(2)(iv) | ⚠️ Attention | 1 cert renewal | 1 |

## Incidents Summary
[Table of incidents with runbook IDs, timestamps, MTTR]

## Baseline Exceptions
[List of approved exceptions with expiry dates]

## Test Restore Verification
- Week 1: ✅ Successful (3 files, 1 DB table)
- Week 2: ✅ Successful (5 files)
- Week 3: ✅ Successful (2 files, 1 DB)
- Week 4: ✅ Successful (4 files)

## Evidence Artifacts
[Links to WORM storage for all bundles]

---
Generated: 2025-11-01 00:05:00 UTC
Signature: sha256:x9y8z7...
```

## HIPAA Control Mapping

```yaml
164.308(a)(1)(ii)(D): RB-AUDIT-001 → evidence/auditlog-checksum.json
164.308(a)(7)(ii)(A): RB-BACKUP-001 → evidence/backup-success.json
164.310(d)(2)(iv): RB-RESTORE-001 → evidence/restore-test.json
164.312(a)(2)(iv): RB-CERT-001 → evidence/encryption-status.json
164.312(b): RB-AUDIT-002 → evidence/auditd-forwarding.json
```

## Evidence Pipeline

For every incident/runbook:
1. **Inputs**: logs/snippets with hashes
2. **Actions**: scripts + hashes + results
3. **Outputs**: final state
4. **Operator**: service principal
5. **Timestamps**: start/end
6. **SLA timers**: met/missed
7. **Control IDs**: HIPAA citations

Written to:
- Append-only local log (hash-chained)
- WORM S3/MinIO bucket with object lock

Nightly job emits Compliance Packet PDF per client.
