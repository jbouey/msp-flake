# SOP-003: Disaster Recovery and Business Continuity

**Version:** 1.0
**Last Updated:** 2025-10-31
**Owner:** Security Officer
**Review Cycle:** Quarterly
**Required Reading:** All Operations Engineers, Security Officer

---

## Purpose

This Standard Operating Procedure defines disaster recovery (DR) and business continuity (BC) procedures for the MSP HIPAA Compliance Platform. This SOP ensures:

- Rapid recovery from catastrophic infrastructure failures
- Minimal service disruption to clients during disasters
- Data integrity and availability during recovery
- Compliance with HIPAA contingency plan requirements

**HIPAA Controls:**
- §164.308(a)(7)(i) - Contingency plan (Required)
- §164.308(a)(7)(ii)(A) - Data backup plan
- §164.308(a)(7)(ii)(B) - Disaster recovery plan
- §164.308(a)(7)(ii)(C) - Emergency mode operation plan
- §164.310(a)(2)(i) - Facility security plan (Contingency operations)

---

## Scope

### In Scope
- MSP central infrastructure (MCP server, event queue, evidence pipeline)
- Client management nodes (NixOS flake deployments)
- Evidence storage (WORM S3 buckets, local caches)
- Configuration repositories (Git, Terraform state)
- Cryptographic keys and secrets

### Out of Scope
- Client application infrastructure (EHR systems, databases) - client responsibility
- Client workstations and end-user devices - out of service scope
- Internet backbone outages - escalate to ISP
- AWS global outages - follow AWS Service Health Dashboard

---

## Roles and Responsibilities

| Role | Responsibilities |
|------|-----------------|
| **Security Officer** | DR plan owner, declares disaster, authorizes recovery procedures |
| **Operations Manager** | Coordinates recovery activities, client communication |
| **Operations Engineer** | Executes recovery procedures, validates restored services |
| **On-Call Engineer** | First responder to outages, initial assessment |
| **Infrastructure Lead** | Terraform/cloud infrastructure recovery, architecture decisions |

---

## Disaster Classification

### Severity Levels

| Level | Definition | Examples | RTO | RPO |
|-------|-----------|----------|-----|-----|
| **P0 - Total Outage** | Complete platform unavailable, all clients affected | AWS region failure, complete data center outage | 4 hours | 1 hour |
| **P1 - Major Outage** | Core service degraded, >25% clients affected | MCP server failure, event queue down | 2 hours | 30 minutes |
| **P2 - Partial Outage** | Single component failure, <25% clients affected | Evidence bundler failure, single client node down | 8 hours | 15 minutes |
| **P3 - Degraded Service** | Non-critical service impaired, no client impact | Dashboard slow, reporting delayed | 24 hours | N/A |

**RTO (Recovery Time Objective):** Maximum acceptable downtime
**RPO (Recovery Point Objective):** Maximum acceptable data loss

---

## Prerequisites

Before executing disaster recovery procedures, ensure you have:

- [ ] Access to disaster recovery runbook (this document)
- [ ] AWS root account credentials (secure vault)
- [ ] Terraform state backup (S3 backend)
- [ ] Git repository access (GitHub credentials)
- [ ] Cryptographic key backups (offline vault)
- [ ] Client contact list (for notifications)
- [ ] PagerDuty/alerting system access
- [ ] Disaster recovery test results (last quarter)

**Required Access:**
- AWS console (root or admin role)
- GitHub organization admin
- Cloud provider admin (if multi-cloud)
- Vault admin (for secrets recovery)

---

## Disaster Recovery Procedures

### Phase 1: Detection and Assessment (0-15 minutes)

#### 1.1 Initial Detection

**Automated Alerts:**
- PagerDuty: "MSP Platform - Total Outage"
- CloudWatch Alarms: "MCP Server Unreachable"
- Pingdom: "Compliance Dashboard Down"
- Client complaints: Multiple concurrent reports

**Manual Detection:**
- Operations engineer notices platform unresponsive
- Daily checklist fails across multiple clients
- Evidence bundles not generating

---

#### 1.2 Severity Assessment

**On-Call Engineer Performs:**

```bash
# Check MCP server
ping mcp-server.msp.internal
ssh mcp-server.msp.internal  # Timeout?

# Check compliance dashboard
curl -I https://compliance.msp.internal  # Response?

# Check event queue
redis-cli -h queue.msp.internal PING  # PONG?

# Check AWS infrastructure
aws ec2 describe-instances --region us-east-1 --profile msp-ops  # Error?

# Check AWS Service Health
aws health describe-events --region us-east-1 --profile msp-ops
```

**Assessment Questions:**

1. **How many clients affected?**
   - All clients → P0
   - >25% clients → P1
   - <25% clients → P2

2. **What services are down?**
   - MCP server → P1 (automated remediation stopped)
   - Event queue → P1 (incident detection stopped)
   - Evidence bundler → P2 (evidence delayed, not lost)
   - Dashboard → P3 (visibility issue only)

3. **What is the root cause?**
   - AWS region failure → P0 (requires regional failover)
   - Single server failure → P1 (restore from backup)
   - Network partition → P2 (wait or route around)
   - Configuration error → P2 (rollback deployment)

**Escalation Decision:**

```
IF severity >= P1:
  - Page Security Officer immediately
  - Notify Operations Manager
  - Activate DR team
ELSE:
  - Follow normal incident response (SOP-002)
```

---

#### 1.3 Declare Disaster

**Security Officer (or designee) declares disaster:**

```
Subject: [DISASTER DECLARED] MSP Platform P0 Outage

Severity: P0 - Total Outage
Affected: All clients
Root Cause: [AWS us-east-1 region failure / MCP server total failure / etc.]
Estimated Impact: [X] hours
RTO Target: 4 hours
RPO Target: 1 hour

DR Team Activated:
- Security Officer: [Name]
- Operations Manager: [Name]
- Operations Engineers: [Names]
- Infrastructure Lead: [Name]

Recovery Plan: [Brief description]

Client Notification: Sending within 15 minutes
Status Updates: Every 30 minutes

[Security Officer Name]
[Timestamp UTC]
```

**Client Notification Template:**

```
Subject: [URGENT] MSP Compliance Platform Service Disruption

Dear [Client Name],

We are experiencing a service disruption affecting our HIPAA compliance
monitoring platform. We have activated our disaster recovery procedures
and are working to restore services as quickly as possible.

Impact:
- Automated compliance monitoring temporarily unavailable
- Evidence bundle generation delayed
- Compliance dashboard inaccessible

Your Action Required:
- No immediate action required
- Continue normal operations
- Manual backups recommended until service restored

What We're Doing:
- Disaster recovery team activated
- Restoring services from backup infrastructure
- Estimated restoration time: [X] hours

What This Means for Compliance:
- Your systems remain compliant (baseline enforced locally)
- Evidence bundles will backfill once service restored
- No data loss expected
- This incident will be documented in your next compliance packet

Status Updates:
We will provide updates every 30 minutes at:
https://status.msp.com

Contact:
Emergency hotline: 555-MSP-HELP (24/7)
Email: ops@msp.com

We apologize for this disruption and appreciate your patience.

MSP Operations Team
```

---

### Phase 2: Immediate Containment (15-30 minutes)

#### 2.1 Stop Automated Processes

**Prevent cascading failures and data corruption:**

```bash
# If MCP server partially operational, stop services
ssh mcp-server.msp.internal
systemctl stop mcp-server
systemctl stop evidence-bundler
systemctl stop mcp-planner

# If event queue operational, pause consumption
redis-cli -h queue.msp.internal
> RENAME incidents:stream incidents:stream:paused

# If Terraform automation running, cancel
# (Check GitHub Actions, CI/CD pipelines)
gh workflow list --repo yourorg/msp-platform
gh run cancel <run-id>  # Cancel any in-progress deployments
```

**Rationale:** Prevent incomplete operations from corrupting state

---

#### 2.2 Preserve Evidence

**Snapshot critical data before recovery attempts:**

```bash
# Take EBS volume snapshots (if EC2-based)
aws ec2 create-snapshot \
  --volume-id vol-mcp-server \
  --description "DR Snapshot - $(date -u +%Y-%m-%d-%H%M%S)" \
  --tag-specifications 'ResourceType=snapshot,Tags=[{Key=Disaster,Value=P0-20251031}]' \
  --profile msp-ops

# Export Redis data (if event queue salvageable)
redis-cli -h queue.msp.internal --rdb /tmp/redis-backup-$(date +%s).rdb

# Backup Terraform state
aws s3 cp s3://msp-terraform-state/prod/terraform.tfstate \
  /tmp/terraform-state-backup-$(date +%s).tfstate \
  --profile msp-ops

# Archive Git commit SHAs
git log --all --oneline --no-decorate > /tmp/git-history-$(date +%s).txt
```

**Verification:** Snapshots and backups complete before proceeding

---

#### 2.3 Assess Data Integrity

**Check WORM storage integrity:**

```bash
# Verify WORM bucket accessible
aws s3 ls s3://msp-compliance-worm/ --profile msp-ops

# Count evidence bundles (should match expected count)
aws s3 ls s3://msp-compliance-worm/ --recursive --profile msp-ops | wc -l

# Check for corruption (sample 10 random bundles)
for i in {1..10}; do
  BUNDLE=$(aws s3 ls s3://msp-compliance-worm/ --recursive --profile msp-ops | shuf -n 1 | awk '{print $4}')
  aws s3 cp s3://msp-compliance-worm/$BUNDLE /tmp/test-bundle.json --profile msp-ops

  # Verify signature
  aws s3 cp s3://msp-compliance-worm/${BUNDLE}.sig /tmp/test-bundle.json.sig --profile msp-ops
  cosign verify-blob \
    --key /backup/signing-keys/public-key.pem \
    --signature /tmp/test-bundle.json.sig \
    /tmp/test-bundle.json

  echo "✅ Bundle verified: $BUNDLE"
done
```

**Data Loss Assessment:**

```bash
# Check last successful evidence bundle timestamp
LAST_BUNDLE=$(aws s3 ls s3://msp-compliance-worm/ --recursive --profile msp-ops | tail -1 | awk '{print $1, $2}')
echo "Last evidence bundle: $LAST_BUNDLE"

# Calculate data loss window
OUTAGE_START="2025-10-31 14:32:00"
DATA_LOSS_HOURS=$(( ($(date -d "$OUTAGE_START" +%s) - $(date -d "$LAST_BUNDLE" +%s)) / 3600 ))
echo "Potential data loss window: $DATA_LOSS_HOURS hours"

# Check if within RPO (1 hour for P0)
if [ $DATA_LOSS_HOURS -le 1 ]; then
  echo "✅ Within RPO"
else
  echo "⚠️  RPO EXCEEDED - Escalate to Security Officer"
fi
```

---

### Phase 3: Recovery Execution (30 minutes - 4 hours)

**Recovery strategy depends on failure type:**

#### 3.1 Scenario A: AWS Region Failure (Multi-Region Failover)

**Prerequisites:**
- DR region pre-configured (e.g., us-west-2)
- Terraform code supports multi-region deployment
- DNS managed by Route 53 with health checks

**Recovery Steps:**

```bash
# 1. Switch to DR region
export AWS_REGION=us-west-2
export TF_VAR_region=us-west-2

# 2. Deploy infrastructure to DR region
cd terraform/disaster-recovery/
terraform init -backend-config="key=prod-dr/terraform.tfstate"
terraform plan
terraform apply -auto-approve

# Expected resources:
# - MCP server (EC2 or ECS)
# - Event queue (Redis/ElastiCache)
# - Evidence bundler service
# - VPN gateway
# - Security groups

# 3. Restore data from backups
# Copy latest Redis snapshot to DR region
aws s3 cp s3://msp-backups/redis/latest.rdb \
  s3://msp-backups-us-west-2/redis/ \
  --source-region us-east-1 \
  --region us-west-2 \
  --profile msp-ops

# Restore Redis from snapshot
aws elasticache restore-cache-cluster-from-snapshot \
  --cache-cluster-id msp-queue-dr \
  --snapshot-name redis-backup-20251031 \
  --region us-west-2 \
  --profile msp-ops

# 4. Update DNS (Route 53 failover)
# If using health check-based failover, this happens automatically
# Otherwise, manually update DNS:
aws route53 change-resource-record-sets \
  --hosted-zone-id Z1234567890ABC \
  --change-batch file://dns-failover.json \
  --profile msp-ops

# dns-failover.json:
# {
#   "Changes": [{
#     "Action": "UPSERT",
#     "ResourceRecordSet": {
#       "Name": "mcp-server.msp.internal",
#       "Type": "A",
#       "TTL": 60,
#       "ResourceRecords": [{"Value": "<DR-REGION-IP>"}]
#     }
#   }]
# }

# 5. Verify services in DR region
curl https://mcp-server.msp.internal/health
# Expected: {"status": "healthy", "region": "us-west-2"}

# 6. Resume event processing
ssh mcp-server.msp.internal
systemctl start mcp-server
systemctl start evidence-bundler
systemctl start mcp-planner

# 7. Verify client connectivity
/opt/msp/scripts/test-client-connectivity.sh --all-clients
```

**Verification:**
- [ ] All services running in DR region
- [ ] DNS resolves to DR region
- [ ] Clients reconnecting automatically
- [ ] Evidence bundles generating
- [ ] Dashboard accessible

**RTO:** 2-4 hours (includes Terraform deployment, DNS propagation)

---

#### 3.2 Scenario B: MCP Server Failure (Single Server Recovery)

**Root Cause:** EC2 instance failure, corrupted OS, hardware failure

**Recovery Steps:**

```bash
# 1. Terminate failed instance (if still running)
INSTANCE_ID=$(aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=msp-mcp-server" \
  --query 'Reservations[0].Instances[0].InstanceId' \
  --output text \
  --profile msp-ops)

aws ec2 terminate-instances --instance-ids $INSTANCE_ID --profile msp-ops

# 2. Deploy new MCP server from Terraform
cd terraform/modules/mcp-server/
terraform taint aws_instance.mcp_server  # Force recreation
terraform apply -auto-approve

# 3. Restore configuration from Git
# (Terraform deploys latest flake automatically via cloud-init)
# Verify:
ssh mcp-server.msp.internal
nix flake metadata github:yourorg/msp-platform

# 4. Restore secrets from Vault
# (Terraform provisions Vault integration automatically)
# Verify:
cat /etc/msp/evidence-bundler.conf | grep -i "api_key"
# Should show: api_key: {{vault:msp/prod/api_key}}

vault kv get secret/msp/prod/api_key
# Should return: key=abc123...

# 5. Restore Redis queue state (if needed)
# Queue state is typically ephemeral; incidents will retry
# If backlog exists in S3:
aws s3 cp s3://msp-backups/redis/incidents-stream-backup.json \
  /tmp/incidents-backup.json \
  --profile msp-ops

redis-cli -h queue.msp.internal
> XADD incidents:stream * $(cat /tmp/incidents-backup.json)

# 6. Restart services
systemctl restart mcp-server
systemctl restart evidence-bundler

# 7. Verify health
curl http://localhost:8080/health
journalctl -u mcp-server -n 50 --no-pager
```

**Verification:**
- [ ] MCP server responding to health checks
- [ ] Processing incident queue
- [ ] Evidence bundles generating
- [ ] All clients reconnected

**RTO:** 30-60 minutes (Terraform deployment + verification)

---

#### 3.3 Scenario C: Evidence Storage Corruption

**Root Cause:** S3 bucket deletion, Object Lock misconfiguration, account compromise

**⚠️ CRITICAL:** Object Lock in COMPLIANCE mode prevents deletion. This scenario is extremely unlikely.

**Recovery Steps:**

```bash
# 1. Verify bucket status
aws s3api head-bucket --bucket msp-compliance-worm --profile msp-ops

# If bucket deleted (ERROR: 404 NoSuchBucket):
# Contact AWS Support immediately - COMPLIANCE mode prevents deletion
# This indicates account compromise

# 2. If Object Lock disabled (configuration drift):
# Re-enable Object Lock
aws s3api put-object-lock-configuration \
  --bucket msp-compliance-worm \
  --object-lock-configuration '{
    "ObjectLockEnabled": "Enabled",
    "Rule": {
      "DefaultRetention": {
        "Mode": "COMPLIANCE",
        "Days": 90
      }
    }
  }' \
  --profile msp-ops

# 3. Verify existing objects protected
aws s3api head-object \
  --bucket msp-compliance-worm \
  --key clinic-001/2025/10/EB-20251031-0042.json \
  --profile msp-ops | jq '.ObjectLockMode, .ObjectLockRetainUntilDate'

# Expected:
# "COMPLIANCE"
# "2026-01-29T00:00:00Z"

# 4. If data loss occurred (objects deleted before COMPLIANCE enabled):
# Restore from cross-region replication (if configured)
aws s3 sync s3://msp-compliance-worm-replica/ s3://msp-compliance-worm/ \
  --source-region eu-west-1 \
  --region us-east-1 \
  --profile msp-ops

# 5. If no replica, regenerate evidence from MCP logs
# (Evidence can be reconstructed from incident logs + runbook execution logs)
/opt/msp/scripts/regenerate-evidence-from-logs.sh \
  --start-date 2025-10-01 \
  --end-date 2025-10-31 \
  --client-id clinic-001
```

**⚠️ Security Incident:** If bucket deletion or Object Lock disabled → **IMMEDIATE escalation to EMERG-002: Data Breach Response**

**RTO:** 4-8 hours (cross-region restore) or 1-2 days (regeneration from logs)

---

#### 3.4 Scenario D: Configuration Repository Loss (Git/Terraform)

**Root Cause:** GitHub repository deleted, Git history corrupted, Terraform state lost

**Recovery Steps:**

```bash
# 1. Restore Git repository from backup
# Daily backups to separate storage (not GitHub)
aws s3 cp s3://msp-backups/git/msp-platform-$(date +%Y-%m-%d).bundle \
  /tmp/repo-restore.bundle \
  --profile msp-ops

git clone /tmp/repo-restore.bundle msp-platform-restored
cd msp-platform-restored

# Push to new GitHub repo (or restored repo)
git remote set-url origin git@github.com:yourorg/msp-platform.git
git push --all
git push --tags

# 2. Restore Terraform state
aws s3 cp s3://msp-terraform-state/prod/terraform.tfstate \
  terraform.tfstate \
  --profile msp-ops

# Verify state
terraform plan
# Expected: No changes (infrastructure matches state)

# If state lost entirely, import existing infrastructure:
terraform import aws_instance.mcp_server i-1234567890abcdef
terraform import aws_s3_bucket.worm_storage msp-compliance-worm
# ... (import all resources)

# 3. Update client flake references
# If using GitHub flake URLs, update all client nodes:
/opt/msp/scripts/update-all-clients.sh \
  --flake-url github:yourorg/msp-platform-restored
```

**Verification:**
- [ ] Git repository accessible
- [ ] All commits and tags present
- [ ] Terraform state valid
- [ ] Clients pulling from correct flake URL

**RTO:** 2-4 hours (depending on infrastructure import complexity)

---

### Phase 4: Verification and Testing (Post-Recovery)

#### 4.1 Service Health Verification

**Complete Checklist:**

```bash
# MCP Server
curl http://mcp-server.msp.internal/health
# Expected: {"status": "healthy", "uptime_seconds": <value>}

systemctl status mcp-server
systemctl status evidence-bundler
# Expected: active (running)

# Event Queue
redis-cli -h queue.msp.internal PING
redis-cli -h queue.msp.internal INFO replication
# Expected: PONG, role:master

# WORM Storage
aws s3 ls s3://msp-compliance-worm/ --profile msp-ops
# Expected: Bucket accessible, objects listed

# Evidence Generation
journalctl -u evidence-bundler -n 10 --no-pager
# Expected: Recent evidence bundles generated

# Dashboard
curl -I https://compliance.msp.internal
# Expected: HTTP 200 OK
```

---

#### 4.2 End-to-End Functional Testing

**Synthetic Incident Test:**

```bash
# Trigger test incident on test client
/opt/msp/scripts/test-incident.sh \
  --client-id test-client-001 \
  --type backup_failure

# Monitor incident processing
journalctl -u mcp-server -f

# Expected flow:
# 1. Incident detected (log watcher → event queue)
# 2. Runbook selected (MCP planner)
# 3. Runbook executed (MCP executor)
# 4. Evidence bundle generated
# 5. Evidence signed and uploaded to WORM

# Verify evidence bundle
BUNDLE_ID=$(journalctl -u evidence-bundler -n 20 --no-pager | grep "Generated evidence bundle" | tail -1 | awk '{print $NF}')

aws s3 ls s3://msp-compliance-worm/test-client-001/$(date +%Y/%m)/${BUNDLE_ID}.json \
  --profile msp-ops
# Expected: Bundle exists

# Verify signature
aws s3 cp s3://msp-compliance-worm/test-client-001/$(date +%Y/%m)/${BUNDLE_ID}.json \
  /tmp/test-bundle.json \
  --profile msp-ops

aws s3 cp s3://msp-compliance-worm/test-client-001/$(date +%Y/%m)/${BUNDLE_ID}.json.sig \
  /tmp/test-bundle.json.sig \
  --profile msp-ops

cosign verify-blob \
  --key /etc/msp/signing-keys/public-key.pem \
  --signature /tmp/test-bundle.json.sig \
  /tmp/test-bundle.json
# Expected: Verified OK
```

---

#### 4.3 Client Connectivity Testing

**Verify All Clients Reconnected:**

```bash
# Test sample of clients
for client in $(cat /etc/msp/clients.txt | shuf -n 10); do
  echo "Testing client: $client"

  # Ping client management node
  ping -c 3 mgmt.${client}.msp.internal

  # SSH and check watcher service
  ssh root@mgmt.${client}.msp.internal "systemctl status msp-watcher"

  # Check recent logs forwarded
  redis-cli -h queue.msp.internal XLEN incidents:${client}

  echo "---"
done
```

---

#### 4.4 Performance Baseline Verification

**Ensure no performance degradation:**

```bash
# Check MCP server response time
for i in {1..10}; do
  curl -o /dev/null -s -w "%{time_total}\n" http://mcp-server.msp.internal/health
done | awk '{sum+=$1; count+=1} END {print "Average:", sum/count, "seconds"}'
# Expected: <0.1 seconds

# Check incident processing throughput
# (Number of incidents processed per minute)
redis-cli -h queue.msp.internal INFO stats | grep instantaneous_ops_per_sec
# Expected: Similar to pre-disaster baseline

# Check dashboard load time
curl -o /dev/null -s -w "%{time_total}\n" https://compliance.msp.internal
# Expected: <2 seconds
```

---

### Phase 5: Post-Recovery Actions

#### 5.1 Client Notification (Recovery Complete)

```
Subject: [RESOLVED] MSP Compliance Platform Service Restored

Dear [Client Name],

We are pleased to inform you that our MSP Compliance Platform has been
fully restored and is operating normally.

Outage Summary:
- Start: 2025-10-31 14:32 UTC
- End: 2025-10-31 17:45 UTC
- Duration: 3 hours 13 minutes
- Root Cause: [Brief description]

Service Status:
✅ Automated compliance monitoring restored
✅ Evidence bundle generation resumed
✅ Compliance dashboard accessible
✅ All backlogged incidents processed

Data Integrity:
✅ No data loss occurred
✅ All evidence bundles verified and intact
✅ Compliance status unaffected

Evidence Backfill:
We have successfully backfilled all evidence bundles for the period
during the outage. Your compliance record remains complete and continuous.

What Happens Next:
1. This incident will be documented in your monthly compliance packet
2. We will conduct a post-incident review to prevent recurrence
3. You will receive a detailed incident report within 7 days

No Action Required:
Your systems have automatically reconnected and are operating normally.
No manual intervention is needed on your end.

We apologize for the disruption and appreciate your patience during
this incident. If you have any questions or concerns, please contact
our operations team.

Contact:
Phone: 555-MSP-HELP (24/7)
Email: ops@msp.com

Thank you,
MSP Operations Team
```

---

#### 5.2 Post-Incident Review (Required)

**Conduct PIR within 7 days of recovery:**

```markdown
# Disaster Recovery Post-Incident Review

**Date:** 2025-11-07
**Incident:** P0 Total Outage - 2025-10-31
**Facilitator:** Security Officer
**Attendees:** Operations Manager, Infrastructure Lead, Operations Engineers

## Incident Summary

- **Disaster Type:** [AWS region failure / MCP server failure / etc.]
- **Severity:** P0 - Total Outage
- **Duration:** 3 hours 13 minutes
- **Clients Affected:** All (47 clients)
- **RTO Target:** 4 hours
- **RTO Actual:** 3.2 hours ✅ Met
- **RPO Target:** 1 hour
- **RPO Actual:** 0 hours ✅ Met (no data loss)

## Timeline

| Time (UTC) | Event |
|-----------|-------|
| 14:32 | Outage detected (automated alerts) |
| 14:45 | Disaster declared (P0) |
| 14:50 | Client notifications sent |
| 15:00 | DR team assembled |
| 15:15 | Recovery initiated (DR region deployment) |
| 16:30 | DR infrastructure online |
| 17:00 | Services verified, clients reconnecting |
| 17:30 | Full service restoration |
| 17:45 | Recovery complete, clients notified |

## What Went Well

1. ✅ Automated detection within minutes
2. ✅ DR team activated quickly (<15 min)
3. ✅ Client notifications timely and clear
4. ✅ No data loss (WORM storage intact)
5. ✅ RTO/RPO targets met
6. ✅ Terraform automation enabled rapid recovery
7. ✅ Evidence bundles backfilled successfully

## What Went Wrong

1. ❌ Initial assessment took longer than expected (15 min vs 5 min target)
2. ❌ DNS propagation delay (15 min) extended RTO
3. ❌ Client reconnection manual for 3 clients (automation failed)
4. ❌ Dashboard performance degraded in DR region initially

## Root Cause

**Primary:** [Detailed root cause description]

**Contributing Factors:**
- [Factor 1]
- [Factor 2]

## Action Items

| Action | Owner | Due Date | Status |
|--------|-------|----------|--------|
| Reduce DNS TTL to 60 seconds for faster failover | Infrastructure Lead | 2025-11-14 | Open |
| Automate client reconnection retry logic | Ops Engineer | 2025-11-21 | Open |
| Pre-warm DR region to match prod performance | Infrastructure Lead | 2025-11-30 | Open |
| Update DR runbook with lessons learned | Security Officer | 2025-11-07 | Complete |
| Conduct DR test in 90 days | Operations Manager | 2026-01-31 | Scheduled |

## Recommendations

1. **Multi-Region Active-Active:** Consider active-active deployment to eliminate failover delay
2. **Chaos Engineering:** Implement regular failure injection testing
3. **Client Resilience:** Improve client node reconnection logic
4. **Monitoring:** Add pre-emptive alerts for infrastructure health
```

---

#### 5.3 Update DR Documentation

```bash
# Update DR runbook with lessons learned
git clone git@github.com:yourorg/msp-platform.git
cd msp-platform/docs/sop/

# Edit SOP-003
vi SOP-003_DISASTER_RECOVERY.md

# Add "Lessons Learned" section
# Document any new procedures discovered during recovery

# Commit changes
git add SOP-003_DISASTER_RECOVERY.md
git commit -m "Update DR runbook with 2025-10-31 incident lessons"
git push
```

---

## Disaster Recovery Testing

### Quarterly DR Test Schedule

**Required by HIPAA §164.308(a)(7)(ii)(D) - Testing and revision procedures**

| Quarter | Test Type | Scope | Duration |
|---------|-----------|-------|----------|
| Q1 | Tabletop Exercise | Full team walkthrough | 2 hours |
| Q2 | Component Failover Test | MCP server recovery | 4 hours |
| Q3 | Partial DR Activation | DR region deployment (no failover) | 6 hours |
| Q4 | Full DR Test | Complete regional failover | 8 hours |

---

### DR Test Procedure

```bash
# Announce test to team (no client impact)
# Schedule during low-traffic period (Sunday 2 AM UTC)

# 1. Pre-test snapshot
aws ec2 create-snapshot \
  --volume-id vol-mcp-server \
  --description "DR Test Snapshot - $(date)" \
  --tag-specifications 'ResourceType=snapshot,Tags=[{Key=Test,Value=DR-Q4-2025}]' \
  --profile msp-ops

# 2. Execute DR procedure (Scenario B: MCP Server Failure)
# (Follow Section 3.2)

# 3. Verify recovery
# (Follow Section 4.1-4.4)

# 4. Measure metrics
echo "RTO: [time from detection to recovery]"
echo "RPO: [data loss in minutes]"
echo "Issues encountered: [list]"

# 5. Rollback to production
terraform destroy -target=aws_instance.mcp_server_dr
# (DR instance terminated, prod instance untouched)

# 6. Document results
cat > dr-test-results-$(date +%Y-Q%q).md <<EOF
# DR Test Results - Q4 2025

**Date:** $(date)
**Test Type:** Component Failover Test
**Scope:** MCP Server Recovery

## Metrics
- RTO Target: 2 hours
- RTO Actual: [X] hours
- RPO Target: 30 minutes
- RPO Actual: [X] minutes

## Issues
1. [Issue 1]
2. [Issue 2]

## Action Items
1. [Action 1]
2. [Action 2]

## Pass/Fail
[PASS/FAIL] - [Justification]
EOF
```

---

## Emergency Contacts

| Role | Primary Contact | Backup | Phone | Email |
|------|----------------|--------|-------|-------|
| **Security Officer** | [Name] | [Name] | [Phone] | security@msp.com |
| **Operations Manager** | [Name] | [Name] | [Phone] | ops@msp.com |
| **Infrastructure Lead** | [Name] | [Name] | [Phone] | infra@msp.com |
| **On-Call Engineer** | [Name] | [Name] | [Phone] | oncall@msp.com |

### External Contacts

| Service | Contact | Purpose |
|---------|---------|---------|
| **AWS Support** | 1-800-AWS-HELP | Infrastructure issues, account recovery |
| **GitHub Support** | support@github.com | Repository recovery |
| **HashiCorp Vault Support** | [Contact] | Secrets recovery |

---

## Related Documents

- **SOP-001:** Daily Operations
- **SOP-002:** Incident Response
- **SOP-004:** Client Escalation
- **OP-001:** MCP Server Operations
- **OP-002:** Evidence Pipeline Operations
- **OP-003:** WORM Storage Management
- **EMERG-001:** Service Outage Response
- **EMERG-002:** Data Breach Response

---

## Revision History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2025-10-31 | Initial version | Security Team |

---

## Training & Acknowledgment

**I have read and understand SOP-003: Disaster Recovery and Business Continuity**

Operator Name: _________________________
Signature: _________________________
Date: _________________________

Manager Approval: _________________________
Date: _________________________

---

**Document Status:** ✅ Active
**Next Review:** 2026-01-31 (Quarterly)
**Owner:** Security Officer
**Classification:** Internal Use Only
