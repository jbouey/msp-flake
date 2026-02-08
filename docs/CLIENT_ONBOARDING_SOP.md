# Client Onboarding: Standard Operating Procedure

**Document Type:** Standard Operating Procedure (SOP)
**Version:** 2.0
**Last Updated:** 2025-12-31
**Owner:** MSP Operations Team
**Review Cycle:** Quarterly

> **New in v2.0:** Central Command dashboard integration, Sites management, appliance phone-home system, and HTTPS endpoints at osiriscare.net.

---

## Table of Contents

1. [Overview](#overview)
2. [Central Command Dashboard](#central-command-dashboard)
3. [Onboarding Timeline](#onboarding-timeline)
4. [Pre-Onboarding Phase](#pre-onboarding-phase)
5. [Technical Deployment Phase](#technical-deployment-phase)
6. [Validation & Testing Phase](#validation--testing-phase)
7. [Client Handoff Phase](#client-handoff-phase)
8. [Post-Go-Live Support](#post-go-live-support)
9. [Troubleshooting Guide](#troubleshooting-guide)
10. [Checklists](#checklists)

---

## Overview

### Purpose

This SOP defines the complete process for onboarding new clients to the MSP HIPAA Compliance Platform, from initial assessment through go-live and ongoing support.

### Scope

**Applies to:**
- New healthcare clients (1-50 provider practices)
- Infrastructure-only monitoring (servers, network devices)
- HIPAA Security Rule compliance automation

**Does NOT apply to:**
- End-user device management (workstations, laptops)
- SaaS application support
- Clinical system implementation

### Goals

- **Speed:** Complete technical deployment in 3 hours
- **Quality:** Zero compliance gaps on day one
- **Evidence:** Auditor-ready documentation from first day
- **Validation:** 6-week validation period with continuous monitoring

### Success Criteria

- [ ] All infrastructure discovered and classified
- [ ] Baseline configuration applied and attested
- [ ] Evidence bundles generating nightly
- [ ] First monthly compliance packet delivered
- [ ] Client staff trained on dashboard
- [ ] Zero critical incidents unresolved

---

## Central Command Dashboard

### Production URLs

| Service | URL | Purpose |
|---------|-----|---------|
| Dashboard | https://dashboard.osiriscare.net | Central Command UI |
| API | https://api.osiriscare.net | REST API & phone-home |
| Alternate | https://msp.osiriscare.net | Dashboard alias |

### Sites Management

The **Sites** page (`/sites`) is the primary interface for managing client onboarding:

**Creating a New Site:**
1. Navigate to https://dashboard.osiriscare.net/sites
2. Click **"+ New Site"**
3. Enter clinic name, contact info, and tier
4. System generates unique `site_id` (e.g., `acme-dental-a1b2c3`)
5. Site appears in pipeline at "Pending" stage

**Site Status Indicators:**
- 🟢 **Online**: Appliance checked in within 5 minutes
- 🟡 **Stale**: Last checkin 5-15 minutes ago
- 🔴 **Offline**: Last checkin > 15 minutes ago
- ⚪ **Pending**: Appliance not yet connected

**Site Detail Page (`/sites/{site_id}`):**
- Contact information
- Connected appliances with live status
- Stored credentials (encrypted)
- Onboarding progress timeline
- Blockers and notes

### Appliance Phone-Home System

Each deployed appliance phones home every 60 seconds:

```bash
# Appliance calls this endpoint automatically
POST https://api.osiriscare.net/api/appliances/checkin
{
  "site_id": "clinic-name-abc123",
  "mac_address": "aa:bb:cc:dd:ee:ff",
  "hostname": "msp-appliance-01",
  "ip_addresses": ["192.168.1.100"],
  "agent_version": "1.0.0",
  "nixos_version": "24.05",
  "uptime_seconds": 86400
}
```

**First Checkin:**
- Automatically registers appliance
- Updates site stage to "Connectivity"
- Site appears as "Online" in dashboard

**Ongoing Checkins:**
- Updates `last_checkin` timestamp
- Maintains "Online" status
- Tracks uptime and version info

### Credential Storage

Store client credentials securely for appliance use:

1. Navigate to Site Detail page
2. Click **"+ Add Credential"**
3. Select type (Router, Active Directory, EHR, Backup, Other)
4. Enter name, host, username, password
5. Credentials are encrypted with Fernet before storage

**Credential Types:**
- `router` - Network device access
- `active_directory` - Windows domain credentials
- `ehr` - EHR system access
- `backup` - Backup service credentials
- `other` - Custom credentials

### Onboarding Pipeline Stages

Sites progress through 12 stages:

```
Lead → Discovery → Proposal → Contract → Intake → Credentials →
Shipped → Received → Connectivity → Scanning → Baseline → Active
```

| Stage | Trigger | Dashboard Location |
|-------|---------|-------------------|
| Lead | n8n webhook or manual | Sites list |
| Connectivity | First appliance checkin | Auto-updated |
| Scanning | Discovery scan complete | Auto-updated |
| Baseline | Baseline applied | Auto-updated |
| Active | Validation complete | Manual promotion |

### API Quick Reference

```bash
# Health check
curl https://api.osiriscare.net/health

# List all sites
curl https://api.osiriscare.net/api/sites

# Create site
curl -X POST https://api.osiriscare.net/api/sites \
  -H "Content-Type: application/json" \
  -d '{"clinic_name": "Test Clinic", "tier": "mid"}'

# Get site details
curl https://api.osiriscare.net/api/sites/{site_id}

# Simulate appliance checkin
curl -X POST https://api.osiriscare.net/api/appliances/checkin \
  -H "Content-Type: application/json" \
  -d '{"site_id": "{site_id}", "mac_address": "aa:bb:cc:dd:ee:ff"}'
```

---

## Onboarding Timeline

```
┌─────────────────────────────────────────────────────────────────────┐
│                     CLIENT ONBOARDING TIMELINE                       │
└─────────────────────────────────────────────────────────────────────┘

WEEK -1: Pre-Onboarding
├── Day -7: Initial discovery call
├── Day -5: Technical assessment
├── Day -3: Contract & BAA signing
└── Day -1: Pre-deployment checklist

WEEK 0: Technical Deployment (3-hour window)
├── Hour 0: Environment setup
├── Hour 1: Infrastructure deployment
├── Hour 2: Validation & testing
└── Hour 3: Client handoff

WEEKS 1-6: Validation Period
├── Week 1: Daily monitoring & tuning
├── Week 2: First compliance packet review
├── Week 3-4: Incident response validation
├── Week 5: Security hardening verification
└── Week 6: Final audit & go-live

ONGOING: Steady-State Operations
├── Daily: Automated monitoring & remediation
├── Weekly: Executive postcard
├── Monthly: Compliance packet delivery
└── Quarterly: Baseline review
```

### Time Estimates

| Phase | Duration | Engineer Time | Client Time |
|-------|----------|---------------|-------------|
| Pre-Onboarding | 1 week | 4 hours | 2 hours |
| Technical Deployment | 3 hours | 3 hours | 1 hour |
| Validation | 6 weeks | 2 hours/week | 30 min/week |
| Steady-State | Ongoing | 1 hour/month | 15 min/month |

**Total Setup Investment:**
- Engineer: ~16 hours over 7 weeks
- Client: ~5 hours over 7 weeks

---

## Pre-Onboarding Phase

### Objective

Gather requirements, assess infrastructure, and prepare for deployment.

### Timeline

7 days before deployment

### Step 1: Initial Discovery Call (Day -7)

**Duration:** 60 minutes
**Participants:** Sales/Technical Lead, Client IT Manager/Decision Maker

**Agenda:**

1. **Business Context (10 min)**
   - Practice size (providers, staff)
   - Locations (single site, multi-site)
   - Current compliance posture
   - Pain points with existing systems

2. **Technical Overview (15 min)**
   - EHR system (vendor, version, hosting)
   - Server infrastructure (on-prem, cloud, hybrid)
   - Network topology (VLANs, firewall, VPN)
   - Existing monitoring tools
   - Backup systems

3. **Service Scope Alignment (15 min)**
   - What we monitor (servers, network devices)
   - What we don't monitor (workstations, SaaS, printers)
   - Guided remediation examples (L1/L2/L3 tiers)
   - Evidence generation process
   - HIPAA compliance coverage

4. **Questions & Next Steps (20 min)**
   - Client concerns or requirements
   - Schedule technical assessment
   - Contract timeline
   - Set deployment date

**Deliverables:**

- [ ] Discovery call notes documented
- [ ] Client profile created in CRM
- [ ] Technical assessment scheduled

**Template:** `templates/discovery-call-notes.md`

---

### Step 2: Technical Assessment (Day -5)

**Duration:** 90 minutes
**Participants:** Technical Lead, Client IT Staff

**Preparation (Client):**

Client must provide:
- [ ] Network diagram (if available)
- [ ] Server inventory list
- [ ] Firewall configuration export
- [ ] Current backup schedule
- [ ] VPN access for remote assessment (if applicable)

**Assessment Activities:**

1. **Infrastructure Discovery (30 min)**
   ```bash
   # Run network discovery scan
   # Document:
   # - Server count and types (Windows/Linux)
   # - Network devices (firewalls, switches, routers)
   # - Storage systems
   # - Backup infrastructure
   # - EHR server details (version, IP, OS)
   ```

2. **Access & Permissions Review (20 min)**
   - SSH/RDP access method
   - Administrative accounts
   - Certificate authority (if exists)
   - Current authentication methods
   - Break-glass procedures

3. **Compliance Gap Analysis (20 min)**
   - Current HIPAA posture
   - Recent audit findings (if any)
   - Known vulnerabilities
   - Patching cadence
   - Backup testing frequency

4. **Deployment Planning (20 min)**
   - Identify management server/VM for agent
   - Network access requirements
   - Firewall rule changes needed
   - Maintenance window scheduling
   - Rollback plan

**Deliverables:**

- [ ] Technical assessment report
- [ ] Infrastructure inventory spreadsheet
- [ ] Gap analysis with priorities
- [ ] Deployment plan with timeline
- [ ] Risk assessment (if any)

**Template:** `templates/technical-assessment-report.md`

---

### Step 3: Contract & BAA Signing (Day -3)

**Duration:** 1 hour (client review time may vary)

**Documents Required:**

1. **Master Services Agreement (MSA)**
   - Service scope
   - Pricing (tiered by size)
   - SLA commitments
   - Termination clauses
   - Code escrow clause

2. **Business Associate Agreement (BAA)**
   - Metadata-only processing scope
   - Sub-processor list (AWS, OpenAI, etc.)
   - Incident notification procedures
   - Audit rights
   - Data retention (7 years)

3. **Service Level Agreement (SLA)**
   - Uptime target: 99.9%
   - Critical incident MTTR: <4 hours
   - Evidence bundle delivery: daily
   - Compliance packet delivery: monthly
   - Support response times

**Client Actions:**

- [ ] Review contracts with legal counsel
- [ ] Sign MSA
- [ ] Sign BAA
- [ ] Provide billing information
- [ ] Designate technical contacts

**MSP Actions:**

- [ ] Execute contracts
- [ ] Create client account in billing system
- [ ] Provision infrastructure (Terraform workspace)
- [ ] Generate SSH keys and certificates
- [ ] Create initial baseline configuration

**Templates:**
- `templates/master-services-agreement.docx`
- `templates/business-associate-agreement.docx`
- `templates/service-level-agreement.docx`

---

### Step 4: Pre-Deployment Checklist (Day -1)

**Duration:** 30 minutes

**MSP Preparation:**

- [ ] Client workspace created in Terraform
- [ ] Client-specific baseline config (`baseline/exceptions/clinic-{id}.yaml`)
- [ ] Signing keys generated and stored securely
- [ ] S3 WORM bucket created with Object Lock
- [ ] Event queue stream configured (`tenant:{client-id}:*`)
- [ ] MCP server whitelisted client ID
- [ ] Monitoring dashboards templated
- [ ] Evidence storage path created (`/var/lib/msp/evidence/{client-id}/`)

- [ ] Client contacts confirmed:
  - Primary technical contact (phone, email)
  - Secondary/escalation contact
  - Executive sponsor (for compliance packets)

- [ ] Deployment window confirmed:
  - Date and time
  - Duration (3 hours)
  - Rollback deadline

- [ ] Access credentials ready:
  - VPN access tested
  - SSH keys distributed
  - Firewall rules pre-approved

**Client Preparation:**

- [ ] Maintenance window scheduled and communicated
- [ ] IT staff available during deployment
- [ ] Backup taken before deployment
- [ ] Change advisory published internally
- [ ] Emergency contact list shared with MSP

**Go/No-Go Decision:**

Review checklist with client technical lead:
- All prerequisites met? → GO
- Any blockers or concerns? → Document and resolve
- Rollback plan understood? → GO
- Client comfortable proceeding? → GO

**If NO-GO:** Reschedule deployment and address gaps.

---

## Technical Deployment Phase

### Objective

Deploy monitoring infrastructure, apply baseline configuration, and validate functionality.

### Timeline

3-hour deployment window (typically off-hours)

### Prerequisites

- [ ] Pre-deployment checklist 100% complete
- [ ] VPN/remote access tested
- [ ] Client IT staff standing by
- [ ] Backup verified and accessible

---

### Hour 1: Infrastructure Deployment

#### Step 1.1: Deploy Management Node (15 min)

**Option A: On-Premises VM**
```bash
# SSH to client hypervisor
ssh admin@vcenter.clinic-001.local

# Deploy NixOS VM from template
# Specs: 2 vCPU, 4GB RAM, 100GB disk
# Network: Management VLAN

# Wait for VM to boot
# Configure network (static IP recommended)
```

**Option B: Cloud-Hosted Agent**
```bash
# Deploy via Terraform
cd terraform/clients/clinic-001
terraform init
terraform plan -out=deployment.plan
terraform apply deployment.plan

# Output will show:
# - Management VM IP address
# - Initial SSH access command
# - MCP connection string
```

**Validation:**
- [ ] VM accessible via SSH
- [ ] Network connectivity verified
- [ ] Internet access confirmed (for NixOS packages)
- [ ] Can reach MCP server (telnet mcp.msp.internal 443)

---

#### Step 1.2: Bootstrap Baseline Configuration (20 min)

```bash
# SSH to management node
ssh root@mgmt.clinic-001.local

# Clone client flake
git clone https://github.com/yourorg/msp-client-flake.git /etc/nixos/

# Customize for client
cd /etc/nixos
cp flake.template.nix flake.nix

# Edit flake.nix
CLIENT_ID="clinic-001"
MCP_SERVER="https://mcp.msp.internal"
EVENT_QUEUE="redis://queue.msp.internal:6379"

# Apply baseline
nixos-rebuild switch --flake .#clinic-001

# This will:
# - Enforce SSH hardening (disable passwords)
# - Configure LUKS encryption (if applicable)
# - Set up audit logging (auditd + journald)
# - Deploy MCP watcher agent
# - Configure log forwarding
# - Apply firewall rules

# Expected output:
# building Nix derivation...
# activating the configuration...
# setting up /etc...
# starting/stopping services...
# success

# Verify baseline applied
nix flake metadata --json | jq .locked.narHash
# Should match baseline hash
```

**Validation:**
- [ ] NixOS rebuild succeeded
- [ ] Baseline hash matches expected value
- [ ] SSH password auth disabled
- [ ] Audit logging active (`systemctl status auditd`)
- [ ] MCP watcher service running (`systemctl status msp-watcher`)

---

#### Step 1.3: Network Discovery (15 min)

```bash
# Run automated discovery
/opt/msp/scripts/discover-infrastructure.sh --client-id clinic-001

# This will:
# 1. Scan network subnets (provided by client)
# 2. Identify servers, network devices, workstations
# 3. Classify by device type and tier
# 4. Generate enrollment recommendations

# Review discovered devices
cat /var/lib/msp/discovery/clinic-001-devices.json | jq .

# Example output:
# {
#   "discovered": 47,
#   "tier_1": 12,  # Servers, network gear
#   "tier_2": 8,   # Databases, apps
#   "tier_3": 3,   # Medical devices
#   "excluded": 24 # Workstations, printers
# }
```

**Validation:**
- [ ] Discovery scan completed without errors
- [ ] Device count matches client's expectations
- [ ] Critical servers identified (EHR, database, backup)
- [ ] Network devices identified (firewall, switches)
- [ ] Exclusions appropriate (workstations, printers)

---

#### Step 1.4: Deploy Monitoring Agents (10 min)

```bash
# For each Tier 1 server, deploy monitoring agent

# Linux servers (SSH-based deployment)
for server in $(cat tier1-linux-servers.txt); do
  ssh root@$server 'bash -s' < /opt/msp/scripts/install-agent.sh
done

# Windows servers (WinRM-based deployment)
for server in $(cat tier1-windows-servers.txt); do
  ansible-playbook -i $server, /opt/msp/playbooks/deploy-windows-agent.yml
done

# Network devices (configure syslog forwarding)
for device in $(cat tier1-network-devices.txt); do
  # Configure device to send syslog to management node
  # (Manual or via Ansible, depending on device type)
done
```

**Validation:**
- [ ] All Tier 1 servers have agents deployed
- [ ] Agent health checks passing
- [ ] Logs flowing to central queue
- [ ] Agent appears in dashboard

---

### Hour 2: Configuration & Integration

#### Step 2.1: Configure Client-Specific Baseline (15 min)

```bash
# Edit client-specific exceptions (if needed)
vim /etc/nixos/baseline/exceptions/clinic-001.yaml

# Example exception:
# exceptions:
#   - id: "EXC-001-SSH-PORT"
#     rule_id: "ssh_hardening.port"
#     baseline_value: 22
#     override_value: 2222
#     justification: "Legacy firewall rules"
#     approved_by: "CISO"
#     expires: "2026-01-15"

# Apply updated baseline
nixos-rebuild switch --flake .#clinic-001

# Verify no drift
systemctl status baseline-drift-detector
```

**Validation:**
- [ ] Exceptions documented and approved
- [ ] Baseline applied successfully
- [ ] Drift detector shows clean status

---

#### Step 2.2: Configure Runbook Permissions (10 min)

```bash
# Configure which runbooks this client is authorized to use
cat > /etc/msp/client-runbooks.yaml <<EOF
client_id: clinic-001
authorized_runbooks:
  - RB-BACKUP-001  # Backup failure remediation
  - RB-CERT-001    # Certificate renewal
  - RB-DISK-001    # Disk cleanup
  - RB-SERVICE-001 # Service restart
  - RB-CPU-001     # CPU spike investigation
  - RB-RESTORE-001 # Backup restore testing

rate_limits:
  max_executions_per_hour: 10
  cooldown_minutes: 5

escalation:
  email: it@clinic-001.com
  pagerduty_key: XXXXX
EOF

# Sync to MCP server
curl -X POST https://mcp.msp.internal/api/clients/clinic-001/runbooks \
  -H "Authorization: Bearer $MSP_API_KEY" \
  -d @/etc/msp/client-runbooks.yaml
```

**Validation:**
- [ ] Runbook permissions synced to MCP server
- [ ] Client ID whitelisted in MCP guardrails
- [ ] Rate limits configured

---

#### Step 2.3: Configure Evidence Pipeline (15 min)

```bash
# Set up evidence collection
mkdir -p /var/lib/msp/evidence/clinic-001/{bundles,signatures,artifacts}

# Configure evidence bundler
cat > /etc/msp/evidence-config.yaml <<EOF
client_id: clinic-001
output_path: /var/lib/msp/evidence/clinic-001/bundles
signing_key: /etc/msp/keys/evidence-signing-key.pem
worm_bucket: s3://msp-compliance-worm-clinic-001
retention_days: 2555  # 7 years
EOF

# Test evidence generation
/opt/msp/scripts/test-evidence-bundle.sh --client-id clinic-001

# Should create test bundle and upload to WORM storage
# Output:
# ✓ Bundle created: EB-20251031-0001.json
# ✓ Bundle signed: EB-20251031-0001.json.sig
# ✓ Uploaded to: s3://msp-compliance-worm-clinic-001/2025/10/
# ✓ Signature verified
```

**Validation:**
- [ ] Test evidence bundle created
- [ ] Bundle signed with cosign
- [ ] Uploaded to WORM storage
- [ ] Signature verification passes
- [ ] S3 Object Lock confirmed active

---

#### Step 2.4: Configure Dashboards & Reporting (20 min)

```bash
# Generate client-specific Grafana dashboard
/opt/msp/scripts/generate-dashboard.sh --client-id clinic-001

# This creates:
# - Real-time compliance dashboard
# - Evidence bundle viewer
# - Incident timeline
# - Runbook execution history

# Set up monthly compliance packet
cat > /etc/msp/reporting-schedule.yaml <<EOF
client_id: clinic-001
packets:
  monthly:
    enabled: true
    schedule: "0 6 1 * *"  # 6 AM on 1st of month
    recipients:
      - admin@clinic-001.com
      - compliance@clinic-001.com
  weekly_postcard:
    enabled: true
    schedule: "0 8 * * 1"  # Monday 8 AM
    recipients:
      - ceo@clinic-001.com
EOF

# Test packet generation
/opt/msp/scripts/generate-compliance-packet.sh \
  --client-id clinic-001 \
  --month $(date +%m) \
  --year $(date +%Y) \
  --output /tmp/test-packet.pdf

# Review generated PDF
```

**Validation:**
- [ ] Dashboard accessible at https://dashboard.msp.internal/clinic-001
- [ ] Test compliance packet generated
- [ ] Packet includes all required sections
- [ ] PDF is auditor-readable

---

### Hour 3: Validation & Client Handoff

#### Step 3.1: Synthetic Incident Testing (20 min)

**Test all core runbooks with synthetic incidents:**

```bash
# Test 1: Backup Failure
/opt/msp/scripts/test-incident.sh \
  --client-id clinic-001 \
  --type backup_failure \
  --severity high

# Expected flow:
# 1. Incident published to queue
# 2. MCP planner selects RB-BACKUP-001
# 3. Runbook executes steps
# 4. Evidence bundle generated
# 5. Issue resolved or escalated

# Verify:
# - Runbook executed successfully
# - Evidence bundle created
# - Dashboard updated
# - Email notification sent (if escalated)

# Test 2: Certificate Expiry
/opt/msp/scripts/test-incident.sh \
  --client-id clinic-001 \
  --type cert_expiry \
  --severity medium

# Test 3: Disk Full
/opt/msp/scripts/test-incident.sh \
  --client-id clinic-001 \
  --type disk_full \
  --severity high

# Test 4: Service Crash
/opt/msp/scripts/test-incident.sh \
  --client-id clinic-001 \
  --type service_crash \
  --severity critical

# Review test results
cat /var/lib/msp/test-results/clinic-001-$(date +%Y%m%d).json
```

**Validation:**
- [ ] All 4 synthetic incidents resolved automatically
- [ ] Evidence bundles generated for each
- [ ] Dashboard reflects incident history
- [ ] Escalation emails received (if applicable)
- [ ] MTTR within SLA targets

---

#### Step 3.2: Backup & Restore Testing (15 min)

```bash
# Verify backup monitoring active
systemctl status restic-backup.service

# Check last backup status
restic snapshots --repo $BACKUP_REPO | tail -5

# Trigger test restore (RB-RESTORE-001)
/opt/msp/runbooks/execute.sh \
  --runbook RB-RESTORE-001 \
  --client-id clinic-001

# This will:
# 1. Select random backup from last 7 days
# 2. Create scratch VM
# 3. Restore sample files
# 4. Verify checksums
# 5. Cleanup scratch VM
# 6. Generate evidence bundle

# Verify restore successful
cat /var/lib/msp/evidence/clinic-001/bundles/latest/restore-test-results.json
```

**Validation:**
- [ ] Backup job running on schedule
- [ ] Restore test completed successfully
- [ ] Checksums verified
- [ ] Evidence bundle created
- [ ] Results documented

---

#### Step 3.3: Client Walkthrough (15 min)

**Live demo with client IT staff:**

1. **Dashboard Navigation (5 min)**
   - Log in to https://dashboard.msp.internal/clinic-001
   - Show compliance posture overview
   - Explain control status tiles
   - Review incident timeline
   - Show evidence bundle viewer

2. **Incident Response Demo (5 min)**
   - Trigger live test incident
   - Show real-time detection
   - Explain automated remediation
   - Show evidence generation
   - Review closed-loop verification

3. **Compliance Packet Review (5 min)**
   - Open sample monthly packet PDF
   - Walk through each section
   - Explain how to use for audits
   - Show evidence bundle verification

**Client Actions:**
- [ ] Client staff able to log in to dashboard
- [ ] Client understands incident workflow
- [ ] Client comfortable with compliance packet
- [ ] Client knows escalation procedures
- [ ] Client questions answered

---

#### Step 3.4: Go-Live Sign-Off (10 min)

**Final Checklist:**

- [ ] All infrastructure discovered and monitored
- [ ] Baseline configuration applied and attested
- [ ] Synthetic incident tests passed
- [ ] Backup monitoring active
- [ ] Evidence pipeline functional
- [ ] Dashboard accessible
- [ ] Client staff trained
- [ ] Contact information verified
- [ ] Escalation procedures tested

**Client Acknowledgment:**

Client IT lead signs off on deployment:

```
Client: Clinic ABC
Deployment Date: 2025-10-31
Deployment Engineer: [Name]
Client Technical Lead: [Name]

Deployment Status: ✅ SUCCESSFUL

Client Sign-Off:
Signature: _________________________
Date: _________________________
```

**Post-Deployment Communications:**

- [ ] Welcome email sent to client contacts
- [ ] Dashboard credentials delivered securely
- [ ] Support contact information provided
- [ ] First monthly packet scheduled
- [ ] 30-day check-in scheduled

---

## Validation & Testing Phase

### Objective

Monitor system for 6 weeks to ensure stability and tune configurations.

### Timeline

6 weeks post-deployment

---

### Week 1: Daily Monitoring & Tuning

**Goals:**
- Catch any missed configuration issues
- Tune alert thresholds
- Validate incident detection

**Daily Activities:**

1. **Morning Review (15 min)**
   ```bash
   # Check overnight incidents
   /opt/msp/scripts/daily-review.sh --client-id clinic-001

   # Review:
   # - Incidents detected
   # - Auto-remediations
   # - Escalations
   # - Evidence bundles
   ```

2. **Threshold Tuning**
   - Adjust disk usage alerts (if too noisy)
   - Tune CPU spike detection
   - Refine log filters
   - Update baseline exceptions if needed

3. **Client Check-In (Friday)**
   - 15-minute call with client IT
   - Review week's incidents
   - Address any concerns
   - Gather feedback

**Deliverables:**
- [ ] Daily incident reports
- [ ] Configuration adjustments documented
- [ ] Week 1 summary report delivered to client

---

### Week 2: First Compliance Packet Review

**Goals:**
- Validate compliance packet generation
- Review with client
- Gather feedback

**Activities:**

1. **Generate Month-0 Packet**
   ```bash
   # Generate partial-month packet (Week 2)
   /opt/msp/scripts/generate-compliance-packet.sh \
     --client-id clinic-001 \
     --partial-month \
     --output /tmp/clinic-001-month0.pdf
   ```

2. **Client Review Meeting (60 min)**
   - Walk through packet sections
   - Explain evidence bundles
   - Show verification process
   - Discuss any gaps or concerns

3. **Refinements**
   - Adjust packet formatting if needed
   - Add client-requested sections
   - Clarify HIPAA control mappings

**Deliverables:**
- [ ] Month-0 compliance packet
- [ ] Client feedback incorporated
- [ ] Packet template finalized

---

### Week 3-4: Incident Response Validation

**Goals:**
- Validate remediation effectiveness (L1/L2 success rates, L3 escalation process)
- Test escalation procedures
- Measure MTTR

**Activities:**

1. **Incident Analysis**
   - Review all incidents from Weeks 1-4
   - Calculate success rate (auto-resolved vs. escalated)
   - Measure MTTR by severity
   - Identify patterns or recurring issues

2. **Escalation Test**
   - Simulate critical incident requiring human intervention
   - Verify escalation email/PagerDuty triggers
   - Validate client receives notification
   - Confirm escalation runbook followed

3. **Runbook Optimization**
   - Refine runbooks based on real incidents
   - Add new runbooks if gaps identified
   - Update evidence requirements

**Deliverables:**
- [ ] Incident analysis report
- [ ] MTTR metrics
- [ ] Runbook updates (if any)
- [ ] Escalation test results

---

### Week 5: Security Hardening Verification

**Goals:**
- Verify all security controls active
- Conduct penetration testing
- Review audit logs

**Activities:**

1. **Control Verification**
   ```bash
   # Run automated control checks
   /opt/msp/scripts/verify-controls.sh --client-id clinic-001

   # Checks:
   # - SSH password auth disabled
   # - LUKS encryption active
   # - Audit logging comprehensive
   # - Firewall rules correct
   # - Certificates valid
   # - Time sync accurate
   ```

2. **Penetration Testing (optional)**
   - SSH brute force attempt (should fail)
   - Unauthorized access attempt (should be logged)
   - Evidence tampering attempt (should fail due to WORM)

3. **Audit Log Review**
   - Review 30 days of audit logs
   - Verify all access logged
   - Check for anomalies
   - Validate log forwarding

**Deliverables:**
- [ ] Security control verification report
- [ ] Penetration test results (if performed)
- [ ] Audit log analysis
- [ ] Remediation plan for any gaps

---

### Week 6: Final Audit & Go-Live

**Goals:**
- Comprehensive system audit
- Final tuning
- Official go-live sign-off

**Activities:**

1. **Comprehensive Audit**
   - Review all 6 weeks of incidents
   - Verify 100% evidence coverage
   - Check compliance packet quality
   - Validate backup/restore testing
   - Confirm baseline configuration is applied and attested

2. **Client Final Review**
   - Present 6-week summary
   - Show metrics and trends
   - Review first full monthly packet
   - Discuss steady-state expectations
   - Answer final questions

3. **Go-Live Approval**
   - Client signs off on validation period
   - Transition to steady-state support
   - Schedule quarterly baseline review

**Deliverables:**
- [ ] 6-week validation summary report
- [ ] First full monthly compliance packet
- [ ] Go-live approval from client
- [ ] Steady-state support SLA activated

---

## Client Handoff Phase

### Objective

Ensure client understands system and can operate independently for routine tasks.

### Training Materials

#### 1. Dashboard User Guide

**Delivered:** PDF + live training session (30 min)

**Topics:**
- Logging in securely
- Dashboard navigation
- Understanding compliance tiles
- Reviewing incident history
- Viewing evidence bundles
- Interpreting compliance packets

**Hands-On:**
- Client logs in and navigates dashboard
- Client reviews sample incident
- Client downloads evidence bundle

---

#### 2. Incident Response Guide

**Delivered:** Quick reference card (laminated)

**Content:**
```
┌─────────────────────────────────────────────────────────┐
│         INCIDENT RESPONSE QUICK REFERENCE               │
└─────────────────────────────────────────────────────────┘

L1/L2 REMEDIATION (Operator-Authorized, Reviewed Daily):
• Backup failures → Remediation attempted, verified
• Service crashes → Restart attempted, verified
• Disk full → Cleanup attempted, verified
• Certificate expiry → Renewal attempted, verified

ESCALATED (Your Action Needed):
• Email: incident-{id}@alerts.msp.com
• Subject: [CRITICAL] Client-001: {Description}
• Response: Reply with "ACK" to acknowledge
• SLA: Response within 1 hour

EMERGENCY CONTACT:
• Phone: 1-800-MSP-HELP
• Email: emergency@msp.com
• Available: 24/7/365

HELPFUL LINKS:
• Dashboard: https://dashboard.msp.internal/clinic-001
• Knowledge Base: https://kb.msp.internal
• Compliance Packets: https://evidence.msp.internal/clinic-001
```

---

#### 3. Monthly Packet Usage Guide

**Delivered:** PDF guide (10 pages)

**Topics:**
- What's in the monthly packet
- How to read control status
- Understanding evidence bundles
- Preparing for audits
- Sharing with auditors
- Verifying signatures

**Appendix:**
- Sample auditor questions and responses
- Evidence verification commands
- Compliance control mapping

---

#### 4. Escalation Procedures

**Delivered:** Process diagram + contact list

**When to Escalate:**
- Auto-remediation failed
- Critical system down >15 minutes
- Security incident suspected
- Audit request received
- New compliance requirement
- System behavior concerns

**How to Escalate:**
1. Email emergency@msp.com
2. Include: Client ID, description, urgency
3. Expect ACK within 15 minutes
4. Resolution within SLA (1-4 hours based on severity)

**Escalation Tiers:**
- Tier 1: Automated response (no escalation)
- Tier 2: Email notification (informational)
- Tier 3: Email + ticket (action required)
- Tier 4: Email + ticket + phone call (critical)

---

## Post-Go-Live Support

### Steady-State Operations

**MSP Responsibilities:**

**Daily:**
- [ ] Monitor incident queue
- [ ] Review L1/L2 remediation success rate
- [ ] Check evidence bundle generation
- [ ] Verify backup completions

**Weekly:**
- [ ] Generate and send executive postcard
- [ ] Review incident trends
- [ ] Check system health metrics
- [ ] Validate compliance posture

**Monthly:**
- [ ] Generate and deliver compliance packet
- [ ] Review monthly SLA metrics with client
- [ ] Plan any baseline updates
- [ ] Schedule quarterly review

**Quarterly:**
- [ ] Conduct baseline review
- [ ] Update HIPAA control mappings
- [ ] Review exceptions (renew or expire)
- [ ] Client satisfaction survey
- [ ] Roadmap planning

**Annual:**
- [ ] Comprehensive security audit
- [ ] BAA renewal
- [ ] Pricing review
- [ ] Roadmap review

---

**Client Responsibilities:**

**Daily:**
- None (system is automated)

**Weekly:**
- [ ] Review executive postcard (5 min)

**Monthly:**
- [ ] Review compliance packet (15 min)
- [ ] Acknowledge receipt

**Quarterly:**
- [ ] Participate in baseline review (30 min)
- [ ] Provide feedback

**Annual:**
- [ ] Participate in security audit
- [ ] Renew contracts

---

## Troubleshooting Guide

### Common Issues & Resolution

#### Issue 1: Agent Not Reporting

**Symptoms:**
- Dashboard shows "Agent Offline"
- No logs flowing from server

**Diagnosis:**
```bash
# Check agent service status
ssh root@server systemctl status msp-watcher

# Check network connectivity
ssh root@server curl -I https://mcp.msp.internal

# Check event queue connectivity
ssh root@server telnet queue.msp.internal 6379
```

**Resolution:**
```bash
# Restart agent
ssh root@server systemctl restart msp-watcher

# Check logs
ssh root@server journalctl -u msp-watcher -n 100

# If still failing, redeploy agent
/opt/msp/scripts/redeploy-agent.sh --server server.clinic-001.local
```

---

#### Issue 2: Evidence Bundle Not Generating

**Symptoms:**
- Incidents resolved but no evidence bundle
- Dashboard shows "Evidence Missing"

**Diagnosis:**
```bash
# Check evidence bundler service
systemctl status evidence-bundler

# Check for errors in logs
journalctl -u evidence-bundler -n 100

# Verify WORM storage accessible
aws s3 ls s3://msp-compliance-worm-clinic-001/
```

**Resolution:**
```bash
# Manually trigger evidence generation
/opt/msp/scripts/generate-evidence-bundle.sh \
  --incident-id INC-20251031-0001 \
  --client-id clinic-001

# Check signing key
cosign verify-blob --key /etc/msp/keys/evidence-signing-key.pub \
  --signature test.sig test.json

# If WORM storage issue, verify IAM permissions
```

---

#### Issue 3: Baseline Drift Detected

**Symptoms:**
- Alert: "Configuration drift detected"
- Baseline hash mismatch

**Diagnosis:**
```bash
# Check current flake hash
ssh root@mgmt.clinic-001.local nix flake metadata --json | jq .locked.narHash

# Compare to expected baseline
cat /etc/msp/baselines/clinic-001.yaml | grep flake_hash

# Identify what changed
ssh root@mgmt.clinic-001.local nixos-rebuild dry-run
```

**Resolution:**
```bash
# If drift is authorized, update baseline exception
vim /etc/nixos/baseline/exceptions/clinic-001.yaml

# If drift is unauthorized, reapply baseline
ssh root@mgmt.clinic-001.local nixos-rebuild switch --flake .#clinic-001

# Verify drift resolved
systemctl status baseline-drift-detector
```

---

## Checklists

### Pre-Onboarding Checklist

- [ ] Discovery call completed and documented
- [ ] Technical assessment completed
- [ ] Infrastructure inventory created
- [ ] Gap analysis performed
- [ ] Deployment plan approved
- [ ] Contracts signed (MSA + BAA + SLA)
- [ ] Billing information collected
- [ ] Client contacts designated
- [ ] Terraform workspace created
- [ ] Client-specific baseline created
- [ ] Deployment window scheduled

---

### Deployment Day Checklist

**Pre-Deployment:**
- [ ] Pre-deployment checklist 100% complete
- [ ] VPN/remote access tested
- [ ] Client IT staff available
- [ ] Backup verified
- [ ] Go/No-Go decision: GO

**Hour 1: Infrastructure**
- [ ] Management node deployed
- [ ] Baseline configuration applied
- [ ] Network discovery completed
- [ ] Monitoring agents deployed

**Hour 2: Configuration**
- [ ] Client-specific baseline configured
- [ ] Runbook permissions set
- [ ] Evidence pipeline configured
- [ ] Dashboards and reporting set up

**Hour 3: Validation**
- [ ] Synthetic incident tests passed
- [ ] Backup/restore test passed
- [ ] Client walkthrough completed
- [ ] Go-live sign-off obtained

**Post-Deployment:**
- [ ] Welcome email sent
- [ ] Dashboard credentials delivered
- [ ] Support contacts provided
- [ ] First packet scheduled
- [ ] 30-day check-in scheduled

---

### Validation Period Checklist

**Week 1:**
- [ ] Daily monitoring completed
- [ ] Thresholds tuned
- [ ] Client check-in completed
- [ ] Week 1 summary delivered

**Week 2:**
- [ ] First compliance packet generated
- [ ] Client review meeting held
- [ ] Feedback incorporated

**Week 3-4:**
- [ ] Incident analysis completed
- [ ] Escalation test passed
- [ ] Runbooks optimized

**Week 5:**
- [ ] Security controls verified
- [ ] Audit logs reviewed
- [ ] Gaps remediated

**Week 6:**
- [ ] Comprehensive audit completed
- [ ] Final client review held
- [ ] Go-live approval obtained
- [ ] Steady-state support activated

---

### Monthly Operations Checklist

**First Week:**
- [ ] Generate monthly compliance packet
- [ ] Deliver to client
- [ ] Client acknowledges receipt

**Second Week:**
- [ ] Review incident trends
- [ ] Check SLA metrics
- [ ] Plan any needed changes

**Third Week:**
- [ ] Review backup success rate
- [ ] Verify evidence bundle generation
- [ ] Check baseline compliance

**Fourth Week:**
- [ ] Generate executive summary
- [ ] Schedule next month's review
- [ ] Document lessons learned

---

## Appendix

### Document Templates

All templates available in `templates/` directory:

- `discovery-call-notes.md`
- `technical-assessment-report.md`
- `deployment-plan.md`
- `go-live-signoff.docx`
- `weekly-status-report.md`
- `monthly-compliance-packet.md`

### Scripts Reference

All scripts available in `/opt/msp/scripts/`:

- `discover-infrastructure.sh` - Network discovery
- `install-agent.sh` - Agent deployment
- `test-incident.sh` - Synthetic incident testing
- `generate-evidence-bundle.sh` - Manual evidence generation
- `generate-compliance-packet.sh` - Compliance packet generation
- `verify-controls.sh` - Security control verification
- `daily-review.sh` - Daily monitoring summary

### Contact Information

**MSP Support:**
- Emergency: 1-800-MSP-HELP (24/7)
- Email: support@msp.com
- Portal: https://support.msp.com

**Technical Escalation:**
- Email: engineering@msp.com
- Phone: 1-800-MSP-TECH (business hours)

**Compliance Questions:**
- Email: compliance@msp.com
- Phone: 1-800-MSP-COMP (business hours)

---

**End of SOP**

**Document Version:** 2.0
**Last Updated:** 2025-12-31
**Next Review:** 2026-03-31
**Owner:** MSP Operations Team

**Change Log:**
- 2025-12-31: v2.0 - Added Central Command dashboard section, Sites management, appliance phone-home system, HTTPS endpoints at osiriscare.net
- 2025-10-31: v1.0 - Initial version created
