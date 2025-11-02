# SOP-002: Incident Response

**Version:** 1.0
**Last Updated:** 2025-10-31
**Owner:** Operations Manager
**Review Cycle:** Monthly
**Required Reading:** All Operations Engineers

---

## Purpose

This Standard Operating Procedure defines the processes for detecting, triaging, remediating, and documenting infrastructure incidents across all client deployments. This SOP covers:

- Automated incident detection via monitoring systems
- LLM-based runbook selection and execution
- Manual intervention procedures when automation fails
- Evidence collection and audit trail requirements
- Client communication protocols
- Post-incident review procedures

**HIPAA Controls:**
- §164.308(a)(6)(i) - Security incident procedures
- §164.308(a)(6)(ii) - Response and reporting
- §164.312(b) - Audit controls

---

## Scope

### In Scope
- Infrastructure-layer incidents (servers, network, storage, services)
- HIPAA compliance violations detected by monitoring
- Automated remediation failures requiring manual intervention
- Security incidents affecting system integrity
- Service degradation impacting SLAs

### Out of Scope
- Client application issues (EHR crashes, database errors) - client responsibility
- End-user support (password resets, workstation issues) - out of service scope
- Planned maintenance - documented as change management, not incidents
- Network attacks requiring law enforcement - escalate to EMERG-002

---

## Roles and Responsibilities

| Role | Responsibilities |
|------|-----------------|
| **MCP Server** | Automated incident detection, runbook selection, remediation execution, evidence generation |
| **Operations Engineer** | Monitor incident queue, manual intervention for failed auto-fixes, client communication |
| **Operations Manager** | SLA compliance oversight, escalation approval, post-incident review |
| **Security Officer** | Security incident classification, breach determination, forensic investigation |
| **On-Call Engineer** | After-hours incident response, emergency escalation |
| **Client Contact** | Incident notification recipient, approval for high-risk actions |

---

## Prerequisites

Before performing incident response duties, ensure you have:

- [ ] Access to incident management system (https://compliance.msp.internal/incidents)
- [ ] Access to MCP server logs (`/var/log/mcp/`)
- [ ] Access to client infrastructure (SSH, VPN, cloud consoles)
- [ ] Runbook library access (git clone github:yourorg/msp-platform)
- [ ] Client contact list with escalation procedures
- [ ] Evidence signing keys (`/etc/msp/signing-keys/`)
- [ ] PagerDuty/alerting system credentials

**Required Training:**
- SOP-001: Daily Operations (prerequisite)
- Runbook execution training (shadow experience)
- Client communication protocols
- Evidence bundle generation procedures

---

## Incident Lifecycle

```
Detection → Classification → Auto-Remediation → Verification → Documentation
     ↓             ↓                ↓                ↓              ↓
  Monitoring   Runbook         MCP Executor    Validation    Evidence Bundle
   System      Selection         Execution      Checks         Generation
                                     ↓
                              Failed? → Manual Intervention → Client Communication
                                            ↓                        ↓
                                     Root Cause              Escalation if
                                      Analysis                 Needed
```

---

## Procedure

### Phase 1: Detection & Triage (Automated)

#### 1.1 Incident Detection Sources

**Log Watchers (Client Nodes)**
- Fluent-bit + Python tailer on each managed node
- Monitors: syslog, journald, auditd, application logs
- Detects: service crashes, failed backups, cert expiries, disk full, unauthorized access

**Monitoring Agents**
- Telegraf/Prometheus agents on infrastructure
- Metrics: CPU/memory/disk usage, service health, network connectivity
- Thresholds: Configurable per client in baseline

**Compliance Checks**
- Nightly baseline drift detection
- Patch status verification
- Encryption configuration validation
- Access control audits

**Example Detection Event:**
```json
{
  "timestamp": "2025-10-31T14:32:01Z",
  "client_id": "clinic-001",
  "hostname": "srv-primary",
  "event_type": "backup_failure",
  "severity": "high",
  "source": "restic_backup",
  "details": {
    "exit_code": 1,
    "error_message": "Failed to connect to backup repository",
    "last_successful_backup": "2025-10-30T02:00:00Z",
    "backup_age_hours": 36.5
  },
  "hipaa_controls": ["164.308(a)(7)(ii)(A)", "164.310(d)(2)(iv)"]
}
```

**Verification:** Event published to Redis stream `incidents:{client_id}`

---

#### 1.2 Automated Classification & Severity Assignment

**MCP Planner Receives Event**

The MCP planner analyzes the incident and classifies:

**Severity Levels:**

| Severity | Definition | Examples | SLA Target |
|----------|-----------|----------|------------|
| **Critical** | Service outage, data loss risk, active breach | Total backup failure >48h, unauthorized root access, encryption disabled | 15 minutes |
| **High** | Compliance violation, service degradation | Backup failure <48h, cert expiring <7 days, patch >7 days overdue | 4 hours |
| **Medium** | Warning threshold breach, partial degradation | Disk >85% full, service restart needed, high CPU | 24 hours |
| **Low** | Informational, proactive maintenance | Cert expiring 30-90 days, package updates available | 7 days |

**Classification Logic:**
```python
def classify_incident(event):
    """Classify incident severity based on event details"""

    if event['event_type'] == 'backup_failure':
        age_hours = event['details']['backup_age_hours']
        if age_hours > 48:
            return 'critical'
        elif age_hours > 24:
            return 'high'
        else:
            return 'medium'

    elif event['event_type'] == 'cert_expiry':
        days_remaining = event['details']['days_until_expiry']
        if days_remaining < 7:
            return 'high'
        elif days_remaining < 30:
            return 'medium'
        else:
            return 'low'

    elif event['event_type'] == 'unauthorized_access':
        return 'critical'  # Always critical

    # ... additional logic
```

**Verification:** Incident assigned severity and SLA deadline

---

#### 1.3 Runbook Selection (LLM-Based)

**MCP Planner Calls LLM**

Prompt template:
```
Given this infrastructure incident, select the appropriate runbook ID from the approved library.

Incident Details:
- Type: {event_type}
- Severity: {severity}
- Client: {client_id}
- Hostname: {hostname}
- Details: {details}

Available Runbooks:
- RB-BACKUP-001: Backup Failure Remediation
- RB-CERT-001: Certificate Renewal
- RB-DISK-001: Disk Space Cleanup
- RB-SERVICE-001: Service Restart & Health Check
- RB-CPU-001: High CPU Investigation
- RB-RESTORE-001: Weekly Backup Restore Test

Respond with ONLY the runbook ID (e.g., RB-BACKUP-001).
If no runbook matches, respond with "ESCALATE".
```

**LLM Response:**
```
RB-BACKUP-001
```

**Guardrails:**
- LLM can ONLY select from pre-approved runbook library
- No free-form command generation
- Invalid responses trigger automatic escalation
- All selections logged for audit

**Verification:** Valid runbook ID selected or escalation triggered

---

### Phase 2: Automated Remediation

#### 2.1 Runbook Execution (MCP Executor)

**MCP Executor Loads Runbook**

Example runbook: `runbooks/RB-BACKUP-001-failure.yaml`

```yaml
id: RB-BACKUP-001
name: "Backup Failure Remediation"
version: 1.0
hipaa_controls:
  - "164.308(a)(7)(ii)(A)"
  - "164.310(d)(2)(iv)"
severity: high
sla_target_minutes: 240

prerequisites:
  - backup_service: restic
  - required_permissions: [root, backup_operator]
  - client_approval_required: false  # Auto-approved for backup issues

steps:
  - step_id: 1
    name: "Check Backup Logs"
    action: check_backup_logs
    timeout_seconds: 30
    script: |
      journalctl -u restic-backup -n 100 --no-pager
    success_criteria:
      - exit_code: 0
    evidence_capture:
      - log_excerpt: last_100_lines
      - error_patterns: true

  - step_id: 2
    name: "Verify Disk Space"
    action: verify_disk_space
    timeout_seconds: 10
    script: |
      df -h /var/backups
    success_criteria:
      - exit_code: 0
      - available_space_gb: '>= 10'
    evidence_capture:
      - disk_usage_before: true

  - step_id: 3
    name: "Restart Backup Service"
    action: restart_service
    timeout_seconds: 60
    script: |
      systemctl restart restic-backup
      sleep 5
      systemctl status restic-backup
    success_criteria:
      - exit_code: 0
      - service_state: active
    evidence_capture:
      - service_status: true

  - step_id: 4
    name: "Trigger Manual Backup"
    action: trigger_backup
    timeout_seconds: 300
    script: |
      /usr/local/bin/restic-backup-now.sh
    success_criteria:
      - exit_code: 0
      - backup_completion_status: success
    evidence_capture:
      - backup_log: true
      - backup_checksum: true

rollback:
  - action: alert_administrator
    message: "Automated backup remediation failed. Manual intervention required."
    notify: [operations_team, client_contact]

evidence_required:
  - backup_log_excerpt
  - disk_usage_before
  - disk_usage_after
  - service_status
  - backup_completion_hash

post_execution:
  - verify_backup_completion: true
  - update_compliance_status: true
  - generate_evidence_bundle: true
```

**Execution Flow:**

1. MCP Executor validates prerequisites
2. Executes each step sequentially
3. Captures evidence at each step
4. Checks success criteria after each step
5. Rolls back if any step fails
6. Generates evidence bundle on completion

**Guardrails During Execution:**

- **Rate Limiting:** 5-minute cooldown per host+runbook combination
- **Concurrent Execution:** Max 3 runbooks per client simultaneously
- **Timeout Enforcement:** Kill script if exceeds timeout
- **Permission Validation:** Verify service account has required permissions
- **Client Impact Assessment:** Some runbooks require manual approval
- **Dry-Run Mode:** Available for testing (not used in production)

**Verification:** All steps execute successfully or rollback triggered

---

#### 2.2 Evidence Collection

**During Runbook Execution:**

The MCP Executor collects evidence at each step:

```json
{
  "bundle_id": "EB-20251031-0042",
  "client_id": "clinic-001",
  "incident_id": "INC-20251031-0042",
  "runbook_id": "RB-BACKUP-001",
  "timestamp_start": "2025-10-31T14:32:01Z",
  "timestamp_end": "2025-10-31T14:37:23Z",
  "operator": "service:mcp-executor",
  "hipaa_controls": ["164.308(a)(7)(ii)(A)", "164.310(d)(2)(iv)"],

  "inputs": {
    "incident_details": {
      "event_type": "backup_failure",
      "backup_age_hours": 36.5,
      "last_successful_backup": "2025-10-30T02:00:00Z"
    },
    "log_excerpt_hash": "sha256:a1b2c3d4...",
    "disk_usage_before": "87%"
  },

  "actions_taken": [
    {
      "step": 1,
      "action": "check_backup_logs",
      "result": "failed",
      "exit_code": 0,
      "error_pattern": "Connection timeout to backup repository",
      "script_hash": "sha256:d4e5f6g7...",
      "timestamp": "2025-10-31T14:32:15Z"
    },
    {
      "step": 2,
      "action": "verify_disk_space",
      "result": "ok",
      "exit_code": 0,
      "available_space_gb": 45.2,
      "script_hash": "sha256:g7h8i9j0...",
      "timestamp": "2025-10-31T14:32:30Z"
    },
    {
      "step": 3,
      "action": "restart_backup_service",
      "result": "ok",
      "exit_code": 0,
      "service_state": "active",
      "script_hash": "sha256:j1k2l3m4...",
      "timestamp": "2025-10-31T14:33:45Z"
    },
    {
      "step": 4,
      "action": "trigger_manual_backup",
      "result": "ok",
      "exit_code": 0,
      "backup_checksum": "sha256:m4n5o6p7...",
      "script_hash": "sha256:p7q8r9s0...",
      "timestamp": "2025-10-31T14:37:23Z"
    }
  ],

  "outputs": {
    "backup_completion_hash": "sha256:m4n5o6p7...",
    "disk_usage_after": "62%",
    "service_status": "active (running)",
    "resolution_status": "success"
  },

  "sla_met": true,
  "mttr_seconds": 322,
  "evidence_bundle_hash": "sha256:s0t1u2v3...",
  "signature": null,  # Added by signer

  "storage_locations": [
    "local:/var/lib/msp/evidence/EB-20251031-0042.json",
    "worm:s3://msp-compliance-worm/clinic-001/2025/10/EB-20251031-0042.json"
  ]
}
```

**Signing & Storage:**

1. Evidence bundle written to local disk
2. Bundle hashed (SHA-256)
3. Signed with cosign private key
4. Uploaded to WORM storage (S3 Object Lock)
5. Signature uploaded alongside bundle

```bash
# Automated signing process
cosign sign-blob \
  --key /etc/msp/signing-keys/private-key.pem \
  --output-signature /var/lib/msp/evidence/EB-20251031-0042.json.sig \
  /var/lib/msp/evidence/EB-20251031-0042.json

aws s3 cp /var/lib/msp/evidence/EB-20251031-0042.json \
  s3://msp-compliance-worm/clinic-001/2025/10/ \
  --profile msp-ops

aws s3 cp /var/lib/msp/evidence/EB-20251031-0042.json.sig \
  s3://msp-compliance-worm/clinic-001/2025/10/ \
  --profile msp-ops
```

**Verification:** Evidence bundle signed and uploaded to WORM storage

---

#### 2.3 Post-Remediation Verification

**Automated Validation:**

After runbook execution, the system re-checks the condition:

```python
def verify_remediation(incident_id, runbook_id):
    """Verify that incident has been resolved"""

    # Wait for monitoring to refresh (30 seconds)
    time.sleep(30)

    # Re-query the condition that triggered the incident
    current_state = check_backup_status(client_id, hostname)

    if current_state['last_successful_backup_age_hours'] < 24:
        # Incident resolved
        close_incident(incident_id, status='auto_resolved', mttr=322)
        return True
    else:
        # Incident persists - escalate
        escalate_incident(incident_id, reason='remediation_ineffective')
        return False
```

**Escalation Triggers:**

- Remediation failed (script error, timeout)
- Verification check failed (condition still present)
- Runbook execution exceeded retry limit (3 attempts)
- Client approval required but not obtained
- Security incident (auto-escalate to Security Officer)

**Verification:** Incident marked resolved or escalated appropriately

---

### Phase 3: Manual Intervention (When Automation Fails)

#### 3.1 Incident Queue Review

**Operations Engineer Monitors Queue:**

Access: https://compliance.msp.internal/incidents?status=escalated

**Escalated Incident View:**
```
Incident ID: INC-20251031-0042
Client: clinic-001 (Anytown Family Medicine)
Severity: High
Type: backup_failure
Status: ESCALATED - Manual Intervention Required
SLA Deadline: 2025-10-31 18:32:01 UTC (3h 12m remaining)

Auto-Fix Attempts: 2
Last Attempt: RB-BACKUP-001 (Failed at step 4: Trigger Manual Backup)
Failure Reason: Backup repository unreachable (network timeout)

Evidence Bundles:
- EB-20251031-0042 (attempt 1)
- EB-20251031-0043 (attempt 2)

Client Contact:
- Primary: Dr. Jane Smith (jane@clinic001.com, 555-0123)
- IT Contact: Bob Johnson (bob@clinic001.com, 555-0124)

Recommended Actions:
1. Verify network connectivity to backup repository
2. Check backup repository status (disk space, service health)
3. Coordinate with client IT if on-premise backup target
4. Consider temporary backup target if primary unavailable
```

**Verification:** Incident details reviewed, SLA deadline noted

---

#### 3.2 Root Cause Investigation

**Standard Investigation Checklist:**

- [ ] Review evidence bundles from auto-fix attempts
- [ ] Check MCP executor logs for execution details
- [ ] SSH to affected host and verify current state
- [ ] Review related incidents (same client, same issue type)
- [ ] Check external dependencies (network, backup target, cloud services)
- [ ] Verify baseline configuration correctness
- [ ] Review recent changes (deployments, updates, client modifications)

**Investigation Commands:**

```bash
# Review auto-fix logs
ssh mcp-server.msp.internal
journalctl -u mcp-executor -n 500 --no-pager | grep INC-20251031-0042

# SSH to affected host
ssh root@srv-primary.clinic-001.msp.internal

# Check backup service status
systemctl status restic-backup
journalctl -u restic-backup -n 100 --no-pager

# Test backup repository connectivity
restic -r s3:s3.amazonaws.com/clinic-001-backups snapshots

# Check network connectivity
ping -c 5 s3.amazonaws.com
traceroute s3.amazonaws.com

# Verify credentials
cat /etc/restic/backup-repo.env | grep AWS_ACCESS_KEY_ID
```

**Common Root Causes:**

| Root Cause | Detection Method | Resolution |
|-----------|-----------------|------------|
| Network connectivity | Ping/traceroute failure | Work with client IT to restore connectivity |
| Credential expiration | Authentication error in logs | Rotate credentials (see OP-005) |
| Disk space (backup target) | Repository full error | Expand storage or adjust retention |
| Service misconfiguration | Config validation failure | Correct configuration, redeploy baseline |
| External service outage | Provider status page | Wait for resolution, use backup target |
| Permission issue | Access denied errors | Fix service account permissions |

**Documentation:**

Create investigation notes in incident ticket:
```
Root Cause Analysis - INC-20251031-0042

Investigation performed by: [Your Name]
Time: 2025-10-31 15:45 UTC

Findings:
- Backup repository (S3 bucket) is accessible from MCP server
- SSH to client node successful
- Backup service is running but failing with "Connection timeout"
- Traceroute shows packet loss to AWS S3 endpoint
- Client firewall logs show outbound HTTPS blocked to S3 IP range

Root Cause: Client firewall rule recently changed, blocking S3 access

Resolution Plan:
1. Contact client IT (Bob Johnson) to whitelist S3 IP range
2. Test backup manually after firewall update
3. Update baseline with firewall rule documentation to prevent recurrence
```

**Verification:** Root cause identified and documented

---

#### 3.3 Manual Remediation

**Client Communication (Required Before Action):**

**Email Template:**
```
To: Bob Johnson <bob@clinic001.com>
Cc: Dr. Jane Smith <jane@clinic001.com>
Subject: [MSP Compliance] Backup Failure - Action Required

Priority: High
Incident ID: INC-20251031-0042

Summary:
We have detected that backups have not completed successfully for
srv-primary.clinic-001 in the past 36 hours. Our automated remediation
attempts have been unsuccessful.

Root Cause:
Our investigation shows that recent firewall changes are blocking
outbound HTTPS connections to AWS S3 (backup repository). This is
preventing the backup service from connecting to the backup target.

Required Action:
Please whitelist the following IP ranges in your firewall:
- AWS S3 (us-east-1): 52.216.0.0/15, 54.231.0.0/17

We can provide detailed firewall rules if needed.

SLA Impact:
This is a High-severity incident with a 4-hour SLA. We have 3 hours
remaining to resolve this issue to maintain compliance.

Next Steps:
1. [Client IT] Update firewall rules
2. [MSP] Verify backup connectivity
3. [MSP] Trigger manual backup and verify success
4. [MSP] Update baseline to document required firewall rules

Please confirm receipt and estimated time for firewall update.

If you have any questions, please contact our operations team:
Phone: 555-MSP-HELP (24/7)
Email: ops@msp.com

Best regards,
MSP Operations Team
```

**Phone Call (If SLA Deadline Approaching):**

Script:
```
"Hello, this is [Your Name] from [MSP Company]. I'm calling about a
critical backup issue affecting your server. We've sent an email with
details, but I wanted to reach out directly since we're approaching our
4-hour SLA deadline.

Our monitoring detected that backups have failed due to a recent firewall
change blocking access to our backup repository. We need to whitelist
some AWS S3 IP ranges to restore backup functionality.

Do you have a few minutes to review the firewall change with me now?"
```

**Verification:** Client contacted and issue explained

**Manual Remediation Steps:**

Once client approves/implements fix:

```bash
# 1. Verify firewall change
ssh root@srv-primary.clinic-001.msp.internal
curl -I https://s3.amazonaws.com

# Expected: HTTP 200 or 403 (accessible, just not authenticated)

# 2. Test backup connectivity
restic -r s3:s3.amazonaws.com/clinic-001-backups snapshots

# Expected: List of existing snapshots

# 3. Trigger manual backup
/usr/local/bin/restic-backup-now.sh

# Monitor progress
tail -f /var/log/restic-backup.log

# 4. Verify backup completion
restic -r s3:s3.amazonaws.com/clinic-001-backups snapshots | tail -1

# Expected: New snapshot with today's date

# 5. Update baseline (prevent recurrence)
# Edit: baseline/clients/clinic-001.yaml
# Add firewall rule documentation

# 6. Generate evidence bundle
/opt/msp/scripts/generate-evidence-bundle.sh \
  --incident-id INC-20251031-0042 \
  --resolution manual \
  --operator "$(whoami)"
```

**Close Incident:**

Update incident status:
```
Status: Resolved
Resolution: Manual
MTTR: 3.2 hours
Root Cause: Client firewall blocking S3 access
Resolution Details: Client IT updated firewall rules, manual backup successful
Evidence Bundle: EB-20251031-0044 (manual remediation)
Follow-up: Baseline updated with firewall rule documentation
```

**Verification:** Incident resolved, backup successful, evidence documented

---

#### 3.4 Post-Incident Follow-Up

**Required Actions:**

- [ ] Update runbook if edge case discovered
- [ ] Document lessons learned in incident post-mortem
- [ ] Update baseline to prevent recurrence
- [ ] Notify client of resolution
- [ ] Schedule follow-up check (24-48 hours)

**Client Notification (Resolution):**

```
To: Bob Johnson <bob@clinic001.com>
Cc: Dr. Jane Smith <jane@clinic001.com>
Subject: [MSP Compliance] Backup Failure - RESOLVED

Incident ID: INC-20251031-0042
Status: Resolved
Resolution Time: 3.2 hours (within SLA)

Summary:
The backup failure affecting srv-primary has been successfully resolved.
Backups are now completing normally.

Resolution Details:
- Firewall rules updated to allow AWS S3 access
- Manual backup completed successfully
- Backup checksum verified
- Automated backups will resume on schedule (nightly at 2:00 AM)

Follow-Up Actions:
1. We have updated your baseline configuration to document the required
   firewall rules to prevent this issue from recurring.
2. We will monitor backup completion for the next 7 days to ensure
   stability.
3. This incident will be included in your monthly compliance packet.

Evidence:
A signed evidence bundle documenting the incident and resolution has
been generated and stored in your compliance archive.

Next Backup Scheduled: 2025-11-01 02:00:00 UTC

Thank you for your prompt assistance in resolving this issue.

Best regards,
MSP Operations Team
```

**Verification:** Client notified, follow-up scheduled

---

### Phase 4: Post-Incident Review (PIR)

#### 4.1 When PIR Is Required

**Mandatory PIR:**
- Any critical incident
- SLA breach
- Security incident
- Repeated incidents (same issue >3 times in 30 days)
- Client complaint
- Automated remediation failure requiring >2 manual interventions

**Optional PIR:**
- High-severity incidents (recommended)
- Interesting edge cases (learning opportunity)
- New incident types (improve detection)

---

#### 4.2 PIR Template

```markdown
# Post-Incident Review: INC-20251031-0042

**Date:** 2025-11-01
**Facilitator:** Operations Manager
**Attendees:** Operations Engineer, Security Officer, Compliance Officer

## Incident Summary

- **Client:** clinic-001 (Anytown Family Medicine)
- **Incident Type:** Backup failure
- **Severity:** High
- **Detection:** 2025-10-31 14:32 UTC (automated)
- **Resolution:** 2025-10-31 17:52 UTC (manual)
- **MTTR:** 3.2 hours (within 4-hour SLA)
- **SLA Met:** Yes

## Timeline

| Time (UTC) | Event |
|-----------|-------|
| 14:32 | Automated detection: backup >36 hours old |
| 14:32 | MCP planner selects RB-BACKUP-001 |
| 14:37 | Auto-fix attempt 1 fails (step 4: connection timeout) |
| 14:45 | Auto-fix attempt 2 fails (same error) |
| 14:45 | Incident escalated to operations engineer |
| 15:15 | Investigation begins |
| 15:45 | Root cause identified (firewall blocking S3) |
| 16:00 | Client IT contacted by email and phone |
| 16:30 | Client IT updates firewall rules |
| 17:30 | Manual backup triggered and successful |
| 17:45 | Baseline updated with firewall documentation |
| 17:52 | Incident closed, client notified |

## What Went Well

1. ✅ Automated detection within minutes of threshold breach
2. ✅ Evidence bundles captured all auto-fix attempts
3. ✅ Clear escalation path to operations engineer
4. ✅ Root cause identified quickly (<30 minutes)
5. ✅ Client responsive and cooperative
6. ✅ SLA met despite manual intervention required

## What Went Wrong

1. ❌ Auto-fix did not detect network connectivity issue
2. ❌ Runbook retried same failed action (should have diagnosed first)
3. ❌ Baseline did not document required firewall rules
4. ❌ No proactive monitoring of firewall changes

## Root Cause

**Primary:** Client firewall configuration change blocked outbound HTTPS
to AWS S3 IP ranges, preventing backup service from connecting to repository.

**Contributing Factors:**
- Baseline did not document network requirements
- No automated detection of firewall rule changes
- Runbook lacks network connectivity diagnostic step

## Action Items

| Action | Owner | Due Date | Status |
|--------|-------|----------|--------|
| Update RB-BACKUP-001 with network diagnostic step | Ops Engineer | 2025-11-07 | Open |
| Add firewall rule requirements to baseline template | Ops Manager | 2025-11-07 | Open |
| Implement firewall change detection monitoring | Security Officer | 2025-11-15 | Open |
| Document network requirements for all clients | Ops Team | 2025-11-30 | Open |

## Lessons Learned

1. **Runbooks should diagnose before acting:** Add connectivity checks
   before attempting service restarts.

2. **Baseline should document dependencies:** Firewall rules, network
   requirements, external service dependencies should all be documented
   in baseline configuration.

3. **Proactive change detection:** Monitor client infrastructure changes
   (firewall rules, network configs) and alert on potentially disruptive
   changes.

## Follow-Up

- Review all runbooks for similar diagnostic gaps
- Audit all client baselines for network dependency documentation
- Consider adding "pre-flight checks" to all runbooks
```

**Verification:** PIR completed within 7 days of incident resolution

---

## Security Incident Procedures

### When to Classify as Security Incident

**Immediate Security Incident:**
- Unauthorized access detected (failed root login, privilege escalation)
- Malware/ransomware detected
- Data exfiltration suspected
- Encryption disabled or compromised
- Baseline tampering detected
- Suspicious network activity (port scans, C2 traffic)

**Escalate to Security Officer Immediately**

---

### Security Incident Response (High-Level)

**See EMERG-002: Data Breach Response for full procedures**

1. **Contain:** Isolate affected systems (network segmentation, disable accounts)
2. **Preserve Evidence:** Snapshot VMs, copy logs, preserve memory dumps
3. **Notify:** Security Officer, client, potentially law enforcement
4. **Investigate:** Forensic analysis, scope determination
5. **Remediate:** Patch vulnerabilities, rotate credentials, restore from clean backups
6. **Report:** HIPAA breach notification if PHI involved (HHS within 60 days)

**Critical:** Do NOT destroy evidence. Preserve all logs, snapshots, and system state.

---

## Emergency Contacts

| Role | Primary Contact | Backup | Phone | Email |
|------|----------------|--------|-------|-------|
| **Operations Engineer** | [Name] | [Name] | [Phone] | ops@msp.com |
| **Operations Manager** | [Name] | [Name] | [Phone] | ops-manager@msp.com |
| **Security Officer** | [Name] | [Name] | [Phone] | security@msp.com |
| **On-Call Engineer** | [Name] | [Name] | [Phone] | oncall@msp.com |

### Escalation Matrix

| Incident Type | Severity | Primary Contact | Escalation Time |
|--------------|----------|----------------|-----------------|
| Backup failure | High | Ops Engineer | 2 hours |
| Service outage | Critical | On-Call → Ops Manager | 15 minutes |
| Security incident | Critical | Security Officer | Immediate |
| SLA breach | Any | Ops Manager | Immediate |
| Client complaint | Any | Ops Manager | 1 hour |

---

## Related Documents

- **SOP-001:** Daily Operations
- **SOP-003:** Disaster Recovery
- **SOP-004:** Client Escalation
- **SOP-010:** Client Onboarding
- **SOP-012:** Baseline Management
- **SOP-013:** Evidence Bundle Verification
- **SOP-014:** Runbook Management
- **OP-001:** MCP Server Operations
- **OP-002:** Evidence Pipeline Operations
- **EMERG-001:** Service Outage Response
- **EMERG-002:** Data Breach Response
- **EMERG-003:** Key Compromise Response

---

## Revision History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2025-10-31 | Initial version | Operations Team |

---

## Training & Acknowledgment

**I have read and understand SOP-002: Incident Response**

Operator Name: _________________________
Signature: _________________________
Date: _________________________

Manager Approval: _________________________
Date: _________________________

---

**Document Status:** ✅ Active
**Next Review:** 2025-11-30
**Owner:** Operations Manager
**Classification:** Internal Use Only
