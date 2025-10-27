# First Pilot Deployment Guide

## Overview

This guide walks through deploying the MSP automation platform for the first pilot client. It covers infrastructure deployment, service verification, and initial monitoring setup.

**Timeline:** 2-3 hours for initial deployment + 24 hours burn-in
**Prerequisites:** AWS account, Terraform, Git access, SSH keys configured

---

## Pre-Deployment Checklist

### AWS Account Setup

- [ ] AWS account created with appropriate permissions
- [ ] S3 bucket created for Terraform state: `msp-platform-terraform-state`
- [ ] IAM user/role with required permissions:
  - EC2 full access
  - VPC full access
  - ElastiCache full access
  - CloudWatch full access
  - Secrets Manager full access
  - IAM role creation

### Development Environment

- [ ] Terraform >= 1.5.0 installed
- [ ] AWS CLI configured with credentials
- [ ] Git repository cloned
- [ ] SSH keys generated for client access
- [ ] SSH CA key generated (optional but recommended)

### Client Information Gathered

- [ ] Client ID chosen (e.g., `clinic-001`)
- [ ] Client name documented
- [ ] Target subnets identified for discovery
- [ ] Network access confirmed (VPN or direct)
- [ ] Point of contact established

---

## Phase 1: Infrastructure Deployment (30 minutes)

### Step 1: Configure Variables

Create `terraform/examples/complete-deployment/terraform.tfvars`:

```hcl
aws_region = "us-east-1"
environment = "prod"

client_id   = "clinic-001"
client_name = "Sunset Medical Clinic"

vpc_cidr = "10.0.0.0/16"
```

### Step 2: Initialize Terraform

```bash
cd terraform/examples/complete-deployment

# Initialize Terraform
terraform init

# Review the plan
terraform plan -out=tfplan

# Review outputs
terraform show tfplan
```

**Expected Resources:**
- VPC with public/private subnets
- NAT gateway
- ElastiCache Redis cluster (2 nodes)
- EC2 instance (t3.small)
- Security groups
- IAM roles
- CloudWatch log groups
- Secrets Manager secrets

### Step 3: Apply Infrastructure

```bash
# Apply the plan
terraform apply tfplan

# This will take ~10-15 minutes
# ElastiCache cluster creation is the longest step
```

**Save outputs:**

```bash
# Save all outputs
terraform output > outputs.txt

# Save sensitive connection string
terraform output -raw event_queue_connection_string > .connection_string
chmod 600 .connection_string
```

### Step 4: Verify Infrastructure

```bash
# Check VPC
aws ec2 describe-vpcs --filters "Name=tag:Client,Values=Sunset Medical Clinic"

# Check EC2 instance
aws ec2 describe-instances --filters "Name=tag:Client,Values=Sunset Medical Clinic"

# Check ElastiCache
aws elasticache describe-replication-groups --replication-group-id msp-prod-clinic-001-redis

# Check client VM is running
aws ec2 describe-instance-status --instance-ids $(terraform output -raw client_vm_instance_id)
```

**Expected Status:**
- VPC: `available`
- EC2: `running`, status checks `2/2 passed`
- ElastiCache: `available`

---

## Phase 2: Service Verification (20 minutes)

### Step 5: Wait for Bootstrap Completion

The client VM runs cloud-init scripts that:
1. Install Nix package manager
2. Install MSP watcher service
3. Install network discovery service
4. Configure CloudWatch agent

**Monitor bootstrap progress:**

```bash
# Get instance ID
INSTANCE_ID=$(terraform output -raw client_vm_instance_id)

# Stream cloud-init logs
aws logs tail /aws/ec2/cloud-init/$INSTANCE_ID --follow

# Check for completion message
aws logs filter-log-events \
  --log-group-name /aws/ec2/cloud-init/$INSTANCE_ID \
  --filter-pattern "Bootstrap complete"
```

**Typical bootstrap time:** 5-10 minutes

### Step 6: SSH Access Verification

```bash
# Get SSH command
SSH_CMD=$(terraform output -raw ssh_command)

# Connect to instance
eval $SSH_CMD

# Once connected, verify services:
sudo systemctl status msp-watcher
sudo systemctl status msp-discovery
sudo systemctl status amazon-cloudwatch-agent

# Check logs
sudo journalctl -u msp-watcher -n 50
sudo journalctl -u msp-discovery -n 50

# Verify Nix installation
nix --version

# Exit
exit
```

**Expected:**
- All services: `active (running)`
- No error messages in logs
- Nix version: 2.18.x or newer

### Step 7: CloudWatch Verification

```bash
# Get log group name
LOG_GROUP=$(terraform output -raw client_vm_log_group_name)

# Check watcher logs
aws logs tail /msp/clinic-001/watcher --follow --since 1h

# Check discovery logs
aws logs tail /msp/clinic-001/discovery --follow --since 1h

# Check system logs
aws logs tail /msp/clinic-001/syslog --follow --since 1h
```

**Expected:**
- Logs flowing to CloudWatch
- Watcher connecting to MCP server
- Discovery starting network scans

### Step 8: Event Queue Verification

```bash
# Get connection details
QUEUE_ENDPOINT=$(terraform output -raw event_queue_endpoint)
QUEUE_PORT=$(terraform output event_queue_port)

# Test connectivity from client VM
eval $SSH_CMD

# Inside VM:
redis-cli -h $QUEUE_ENDPOINT -p 6379 ping
# Expected: PONG

# Check stream exists
redis-cli -h $QUEUE_ENDPOINT -p 6379 KEYS "tenant:clinic-001:*"

# Exit
exit
```

---

## Phase 3: Network Discovery Setup (30 minutes)

### Step 9: Configure Discovery Subnets

The example deployment includes default subnets. To customize:

```bash
# Edit main.tf to update subnets_to_discover
vim terraform/examples/complete-deployment/main.tf

# Find the client_vm module section and update:
subnets_to_discover = [
  "192.168.1.0/24",   # Main office
  "192.168.10.0/24",  # Servers
  "10.0.1.0/24"       # Medical devices
]

# Apply changes
terraform apply
```

### Step 10: Trigger Manual Discovery

```bash
# SSH to client VM
eval $SSH_CMD

# Trigger discovery manually
sudo msp-discovery --scan-now

# Monitor progress
sudo journalctl -u msp-discovery -f

# View results
sudo cat /var/lib/msp-discovery/discovery_latest.json | jq .
```

**Expected Output:**
```json
{
  "total_devices": 47,
  "by_type": {
    "linux_server": 8,
    "windows_server": 4,
    "network_infrastructure": 6,
    "windows_workstation": 12,
    "printer": 3
  },
  "by_tier": {
    "1": 18,
    "2": 16,
    "3": 0
  },
  "auto_enroll_count": 18,
  "manual_review_count": 16,
  "excluded_count": 12
}
```

### Step 11: Review Discovery Results

```bash
# Download discovery results
scp admin@$(terraform output -raw client_vm_private_ip):/var/lib/msp-discovery/discovery_latest.json ./

# Review with jq
cat discovery_latest.json | jq '.devices[] | select(.should_monitor == true)'

# Count by type
cat discovery_latest.json | jq '.devices | group_by(.device_type) | map({type: .[0].device_type, count: length})'
```

---

## Phase 4: Initial Monitoring Configuration (30 minutes)

### Step 12: Review Auto-Enrollment Queue

```bash
# SSH to client VM
eval $SSH_CMD

# View enrollment queue
sudo cat /var/lib/msp-discovery/enrollment_queue.json | jq .

# View enrollment results
sudo cat /var/lib/msp-discovery/enrollment_results_*.json | jq .
```

**Review:**
- Devices queued for auto-enrollment
- Devices requiring manual review
- Enrollment successes/failures

### Step 13: Manual Enrollment (if needed)

For devices that require manual approval:

```bash
# List devices needing approval
cat discovery_latest.json | jq '.devices[] | select(.enrollment_status == "manual_review_required")'

# Edit enrollment config to approve specific devices
sudo vim /etc/msp-discovery/config.yaml

# Add approved devices to whitelist
approved_devices:
  - ip: "192.168.10.50"
    reason: "Critical database server - manual approval"
  - ip: "10.0.1.45"
    reason: "PACS server - medical device"

# Trigger enrollment
sudo systemctl restart msp-discovery
```

### Step 14: Verify Device Registration

```bash
# Check MCP server for registered devices
curl -H "Authorization: Bearer $MCP_API_KEY" \
  https://mcp.your-msp.com/api/devices?client_id=clinic-001 | jq .

# Expected response:
{
  "client_id": "clinic-001",
  "total_devices": 18,
  "devices": [
    {
      "device_id": "clinic-001-192-168-1-10",
      "ip": "192.168.1.10",
      "hostname": "web-server-01",
      "device_type": "linux_server",
      "tier": 1,
      "monitoring_method": "agent",
      "status": "active"
    }
    // ... more devices
  ]
}
```

---

## Phase 5: Baseline Enforcement Verification (30 minutes)

### Step 15: Deploy Baseline Configuration

```bash
# The client VM should already have baseline applied via flake
# Verify baseline configuration

eval $SSH_CMD

# Check baseline version
cat /etc/nixos/configuration.nix | grep baseline

# Verify security settings
sudo cat /etc/nixos/baseline-v1.yaml

# Check applied modules
sudo nixos-option services.msp-encryption.enable
sudo nixos-option services.msp-ssh-hardening.enable
sudo nixos-option services.msp-secrets.enable
```

### Step 16: Test Baseline Enforcement

```bash
# Test SSH hardening
ssh-keyscan $(terraform output -raw client_vm_private_ip)
# Should show only strong algorithms (ed25519, ecdsa)

# Test encryption
lsblk -f
# Should show LUKS encrypted volumes

# Test time sync
timedatectl status
# Should show NTP synchronized: yes

# Test auditd
sudo systemctl status auditd
# Should be active and running
```

### Step 17: Generate First Evidence Bundle

```bash
# SSH to client VM
eval $SSH_CMD

# Generate evidence bundle
sudo msp-evidence-packager --generate

# View bundle
ls -lah /var/lib/msp-evidence/

# Download bundle
exit
scp admin@$(terraform output -raw client_vm_private_ip):/var/lib/msp-evidence/EB-*.zip ./

# Verify signature
cosign verify-blob --key /path/to/public-key --signature EB-*.sig EB-*.zip
```

---

## Phase 6: Testing & Burn-In (24 hours)

### Step 18: Synthetic Incident Testing

Create test incidents to verify automation:

```bash
# SSH to client VM
eval $SSH_CMD

# Test 1: Simulate backup failure
sudo touch /var/log/backup-failed.log
echo "$(date) ERROR: Backup job failed - disk full" | sudo tee -a /var/log/backup-failed.log

# Wait 2 minutes, check if incident detected
sudo journalctl -u msp-watcher -n 20 | grep -i backup

# Test 2: Simulate high CPU
sudo stress --cpu 8 --timeout 60s &

# Monitor remediation
sudo journalctl -u msp-watcher -f

# Test 3: Simulate service crash
sudo systemctl stop nginx
sleep 30
sudo systemctl status nginx
# Should auto-restart via watcher

# Test 4: Simulate cert expiry warning
sudo touch /var/log/cert-expiry-warning.log
echo "$(date) WARNING: Certificate expires in 25 days" | sudo tee -a /var/log/cert-expiry-warning.log
```

### Step 19: Monitor Automation Response

```bash
# Check MCP server for incident processing
curl -H "Authorization: Bearer $MCP_API_KEY" \
  https://mcp.your-msp.com/api/incidents?client_id=clinic-001&hours=1 | jq .

# Check runbook executions
curl -H "Authorization: Bearer $MCP_API_KEY" \
  https://mcp.your-msp.com/api/runbooks/executions?client_id=clinic-001 | jq .

# Verify evidence generation
ls -lah /var/lib/msp-evidence/ | grep EB-
```

### Step 20: 24-Hour Burn-In Monitoring

Set up monitoring for the first 24 hours:

```bash
# Create monitoring script
cat > monitor-pilot.sh <<'EOF'
#!/bin/bash
CLIENT_ID="clinic-001"
MCP_API_KEY="your-api-key"

while true; do
  echo "=== $(date) ==="

  # Check service status
  aws ec2 describe-instance-status --instance-ids $INSTANCE_ID --query 'InstanceStatuses[0].InstanceStatus.Status' --output text

  # Check recent incidents
  curl -s -H "Authorization: Bearer $MCP_API_KEY" \
    "https://mcp.your-msp.com/api/incidents?client_id=$CLIENT_ID&hours=1" | \
    jq '.incidents | length'

  # Check CloudWatch metrics
  aws cloudwatch get-metric-statistics \
    --namespace AWS/EC2 \
    --metric-name CPUUtilization \
    --dimensions Name=InstanceId,Value=$INSTANCE_ID \
    --start-time $(date -u -d '5 minutes ago' +%Y-%m-%dT%H:%M:%S) \
    --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
    --period 300 \
    --statistics Average \
    --query 'Datapoints[0].Average' \
    --output text

  echo ""
  sleep 300  # Check every 5 minutes
done
EOF

chmod +x monitor-pilot.sh

# Run in background
nohup ./monitor-pilot.sh > pilot-monitor.log 2>&1 &

# Tail logs
tail -f pilot-monitor.log
```

---

## Phase 7: Documentation & Handoff (1 hour)

### Step 21: Generate Deployment Report

```bash
# Create deployment report
cat > deployment-report-clinic-001.md <<EOF
# Deployment Report: Sunset Medical Clinic (clinic-001)

## Deployment Summary
- **Date:** $(date)
- **Environment:** Production
- **Region:** $(terraform output aws_region)
- **Duration:** X hours

## Infrastructure Deployed
- VPC ID: $(terraform output vpc_id)
- Client VM: $(terraform output client_vm_instance_id)
- Event Queue: $(terraform output event_queue_endpoint)

## Services Status
- MSP Watcher: Active
- Network Discovery: Active
- CloudWatch Agent: Active

## Discovery Results
- Total Devices: X
- Auto-Enrolled: X
- Manual Review: X
- Excluded: X

## Evidence Bundles
- Initial bundle generated: EB-YYYYMMDD-clinic-001.zip
- Signature verified: Yes

## Access Information
- SSH Command: $(terraform output ssh_command)
- CloudWatch Dashboard: $(terraform output cloudwatch_dashboard_url)

## Next Steps
1. Complete 24-hour burn-in monitoring
2. Schedule weekly review meeting
3. Generate first monthly compliance packet
4. Train client POC on dashboard access

## Support Contacts
- Technical: ops@your-msp.com
- Emergency: +1-XXX-XXX-XXXX
EOF

cat deployment-report-clinic-001.md
```

### Step 22: Client Access Setup

```bash
# Create client user account (read-only access)
aws iam create-user --user-name clinic-001-readonly

# Attach CloudWatch read-only policy
aws iam attach-user-policy \
  --user-name clinic-001-readonly \
  --policy-arn arn:aws:iam::aws:policy/CloudWatchReadOnlyAccess

# Generate console access
aws iam create-login-profile \
  --user-name clinic-001-readonly \
  --password $(openssl rand -base64 24)

# Generate access credentials for API
aws iam create-access-key --user-name clinic-001-readonly > clinic-001-credentials.json
chmod 600 clinic-001-credentials.json
```

### Step 23: Schedule First Review

```bash
# Add to calendar (example with Google Calendar CLI)
gcalcli add \
  --title "MSP Platform - Clinic-001 Weekly Review" \
  --when "$(date -d 'next monday 10:00' +'%Y-%m-%d %H:%M')" \
  --duration 30 \
  --reminder 60 \
  --description "Review incidents, compliance status, and optimization opportunities"
```

---

## Post-Deployment Checklist

### Immediate (Day 1)

- [ ] All infrastructure deployed successfully
- [ ] Services running and healthy
- [ ] Network discovery completed
- [ ] Auto-enrollment processed
- [ ] Synthetic incidents tested
- [ ] Evidence bundle generated
- [ ] Client access configured
- [ ] Deployment report created

### Short-term (Week 1)

- [ ] 24-hour burn-in completed without issues
- [ ] No repeated incidents (cooldown working)
- [ ] Discovery running on schedule
- [ ] Weekly evidence bundles generating
- [ ] Client POC trained on dashboard
- [ ] Support escalation path tested

### Medium-term (Month 1)

- [ ] First monthly compliance packet generated
- [ ] Baseline exceptions reviewed and approved
- [ ] Patch compliance verified
- [ ] Backup test-restore completed
- [ ] Cost optimization reviewed
- [ ] Client satisfaction survey

---

## Troubleshooting

### Issue: Bootstrap Script Fails

**Symptoms:**
- Services not starting
- Cloud-init logs show errors

**Solution:**
```bash
# SSH to instance
eval $SSH_CMD

# Check cloud-init status
sudo cloud-init status --long

# View full logs
sudo cat /var/log/cloud-init-output.log

# Manually run bootstrap
sudo /tmp/bootstrap-watcher.sh
```

### Issue: Discovery Not Finding Devices

**Symptoms:**
- Empty discovery results
- No devices in enrollment queue

**Solution:**
```bash
# Check network connectivity
ping -c 3 192.168.1.1

# Verify subnets are correct
sudo cat /etc/msp-discovery/config.yaml

# Check firewall rules
sudo iptables -L -n

# Run discovery with debug logging
sudo msp-discovery --scan-now --log-level debug
```

### Issue: Event Queue Connection Fails

**Symptoms:**
- Watcher unable to publish events
- Redis connection errors

**Solution:**
```bash
# Check security group allows traffic
aws ec2 describe-security-groups --group-ids $(terraform output event_queue_security_group_id)

# Test connectivity
telnet $(terraform output event_queue_endpoint) 6379

# Check auth token
aws secretsmanager get-secret-value --secret-id $(terraform output event_queue_auth_secret_arn)

# Verify watcher config
sudo cat /var/lib/msp-watcher/config.json
```

### Issue: High AWS Costs

**Symptoms:**
- Unexpected AWS bill
- High data transfer charges

**Solution:**
```bash
# Check data transfer
aws cloudwatch get-metric-statistics \
  --namespace AWS/EC2 \
  --metric-name NetworkOut \
  --dimensions Name=InstanceId,Value=$INSTANCE_ID \
  --start-time $(date -u -d '1 day ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 3600 \
  --statistics Sum

# Review ElastiCache usage
aws elasticache describe-cache-clusters --show-cache-node-info

# Consider downsizing if underutilized
# t3.small → t3.micro (saves ~$7.50/month per instance)
```

---

## Success Criteria

The pilot deployment is considered successful when:

1. ✅ All services running healthy for 24+ hours
2. ✅ Network discovery completed successfully
3. ✅ At least 10 devices auto-enrolled
4. ✅ Synthetic incidents detected and remediated
5. ✅ Evidence bundles generating automatically
6. ✅ No critical errors in logs
7. ✅ Client POC can access dashboard
8. ✅ Costs within expected range ($30-50/month)

---

## Next Steps After Pilot

1. **Week 2-4: Optimization**
   - Fine-tune discovery schedules
   - Adjust enrollment thresholds
   - Optimize alerting rules

2. **Month 2: Expansion**
   - Add additional clients
   - Implement custom runbooks
   - Enhance compliance reporting

3. **Month 3: Productionization**
   - Multi-region deployment
   - Advanced monitoring
   - Client self-service portal

---

## Support Resources

- **Documentation:** `/docs` directory in repository
- **Runbooks:** `/runbooks` directory
- **Terraform Modules:** `/terraform/modules`
- **Slack Channel:** #msp-platform-support
- **On-Call:** PagerDuty integration

**Emergency Contact:** ops@your-msp.com | +1-XXX-XXX-XXXX
