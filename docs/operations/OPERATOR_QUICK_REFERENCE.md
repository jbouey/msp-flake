# Operator Quick Reference Card

**Print this page and keep near your workstation**

---

## Emergency Contacts

| Role | Name | Phone | Email |
|------|------|-------|-------|
| Lead Operator | _____________ | _____________ | _____________ |
| Backup Operator | _____________ | _____________ | _____________ |
| Security Lead | _____________ | _____________ | _____________ |
| On-Call Escalation | _____________ | _____________ | _____________ |

---

## Critical File Locations

| Item | Path |
|------|------|
| Evidence bundles | `/var/lib/msp/evidence/` |
| Signing keys | `/etc/msp/signing-keys/` |
| Secrets | `/etc/msp/secrets/` (SOPS-encrypted) |
| Schemas | `/opt/msp/evidence/schema/` |
| Client configs | `/var/lib/msp/clients/{client-id}/` |
| Logs | `/var/log/msp/` |

---

## Daily Health Check (5 minutes)

```bash
# 1. Check service status
sudo systemctl status mcp-executor

# 2. Check disk space (alert if >80%)
df -h /var/lib/msp/evidence

# 3. Check recent errors
journalctl -u mcp-executor --since "24 hours ago" | grep -i error | wc -l

# 4. Count yesterday's evidence bundles
ls /var/lib/msp/evidence/EB-$(date -d yesterday +%Y%m%d)-*.json | wc -l

# 5. Check WORM upload status (Day 3+ implementation)
aws s3 ls s3://msp-compliance-worm/evidence/$(date -d yesterday +%Y%m%d)/ | wc -l
```

**Expected Results:**
- Service: `active (running)`
- Disk: `<80%`
- Errors: `0-5` (review if >5)
- Bundles: `varies by client load`
- WORM uploads: `should match local bundle count`

---

## Common Tasks

### Verify an Evidence Bundle

```bash
BUNDLE_ID="EB-20251101-0042"

cosign verify-blob \
  --key /etc/msp/signing-keys/private-key.pub \
  --bundle /var/lib/msp/evidence/${BUNDLE_ID}.json.bundle \
  /var/lib/msp/evidence/${BUNDLE_ID}.json
```

**Expected output:** `Verified OK`

### View Bundle Contents

```bash
BUNDLE_ID="EB-20251101-0042"

cat /var/lib/msp/evidence/${BUNDLE_ID}.json | jq '
{
  bundle_id: .bundle_id,
  incident: .incident.incident_id,
  event_type: .incident.event_type,
  severity: .incident.severity,
  mttr: .execution.mttr_seconds,
  sla_met: .execution.sla_met,
  resolution: .outputs.resolution_status,
  hipaa_controls: .incident.hipaa_controls
}
'
```

### Find Bundles by Date

```bash
# Today
ls /var/lib/msp/evidence/EB-$(date +%Y%m%d)-*.json

# Specific date
ls /var/lib/msp/evidence/EB-20251015-*.json

# Date range (last 7 days)
find /var/lib/msp/evidence -name "EB-*.json" -mtime -7
```

### Find Failed Incidents

```bash
# Failed resolutions
grep -l '"resolution_status": "failed"' /var/lib/msp/evidence/EB-*.json

# Missed SLAs
grep -l '"sla_met": false' /var/lib/msp/evidence/EB-*.json

# Both
grep -l '"sla_met": false' /var/lib/msp/evidence/EB-*.json | \
  xargs grep -l '"resolution_status": "failed"'
```

### Test Configuration

```bash
cd /opt/msp/mcp-server/evidence
python3 config.py
```

**Expected output:** `✅ Configuration valid`

### Test Evidence Pipeline

```bash
cd /opt/msp/mcp-server/evidence
python3 pipeline.py
```

**Expected output:** `✅ INTEGRATION TEST PASSED`

### Decrypt a Secret

```bash
# View COSIGN password
sops -d /etc/msp/secrets/cosign-password.enc

# View client API key
sops -d /etc/msp/secrets/{client-id}-api-key.enc
```

---

## Troubleshooting

### Service Won't Start

```bash
# Check logs
journalctl -u mcp-executor -n 100 --no-pager

# Check configuration
python3 /opt/msp/mcp-server/evidence/config.py

# Check signing key permissions
ls -la /etc/msp/signing-keys/

# Test COSIGN password
export COSIGN_PASSWORD=$(sops -d /etc/msp/secrets/cosign-password.enc)
cosign verify-blob \
  --key /etc/msp/signing-keys/private-key.pub \
  --bundle /var/lib/msp/evidence/EB-*.json.bundle \
  /var/lib/msp/evidence/EB-*.json | head -1
```

### Signature Verification Fails

**Possible causes:**
1. Bundle was tampered with (CRITICAL - investigate)
2. Wrong public key used
3. Bundle and signature from different generations

```bash
# Check bundle hash
cat /var/lib/msp/evidence/{bundle}.json | jq -r '.evidence_bundle_hash'

# Check which key signed it
# (Compare bundle timestamp to key rotation dates)
stat -c %Y /etc/msp/signing-keys/private-key.key

# Try with archived key if after rotation
cosign verify-blob \
  --key /etc/msp/signing-keys/archive/private-key-2024-2025.pub \
  --bundle {bundle}.json.bundle \
  {bundle}.json
```

### Disk Full

```bash
# Check usage
df -h /var/lib/msp/evidence

# Find large bundles
find /var/lib/msp/evidence -size +10M

# Check WORM upload status
aws s3 ls s3://msp-compliance-worm/evidence/ --recursive | wc -l
ls /var/lib/msp/evidence/EB-*.json | wc -l
# These should match (after Day 3 implementation)

# Safe cleanup (only if WORM upload confirmed)
# NEVER delete bundles without WORM backup
find /var/lib/msp/evidence -name "EB-*.json" -mtime +90 -ls
# Review list carefully before deleting
```

### WORM Upload Failing (Day 3+ implementation)

```bash
# Check AWS credentials
aws sts get-caller-identity

# Test bucket access
aws s3 ls s3://msp-compliance-worm/

# Check uploader logs
journalctl -u mcp-uploader --since "1 hour ago"

# Manual upload test
aws s3 cp /var/lib/msp/evidence/EB-20251101-0042.json \
          s3://msp-compliance-worm/evidence/
```

---

## Key Rotation Reminder

**Check key age:**
```bash
key_created=$(stat -c %Y /etc/msp/signing-keys/private-key.key)
now=$(date +%s)
age_days=$(( ($now - $key_created) / 86400 ))
echo "Signing key age: $age_days days"
```

**Rotation schedule:**
- Age 330 days: Start planning rotation
- Age 350 days: Schedule maintenance window
- Age 365 days: MUST rotate

**Procedure:** See MANUAL_OPERATIONS_CHECKLIST.md → Annual Operations

---

## Monthly Checklist

- [ ] First Friday: Generate compliance packets (all clients)
- [ ] Second Monday: Review failed incidents from last month
- [ ] Third Friday: Backup restore test (5 random bundles)
- [ ] Fourth Monday: Client statistics review

---

## Red Flags (Investigate Immediately)

⚠️ **Service down >15 minutes**
```bash
sudo systemctl status mcp-executor
```

⚠️ **No evidence bundles in 24 hours**
```bash
find /var/lib/msp/evidence -name "EB-$(date +%Y%m%d)-*.json" | wc -l
```

⚠️ **Disk >90% full**
```bash
df -h /var/lib/msp/evidence
```

⚠️ **Signature verification failures**
```bash
# Test 10 recent bundles
for bundle in $(ls /var/lib/msp/evidence/EB-*.json | tail -10); do
  cosign verify-blob \
    --key /etc/msp/signing-keys/private-key.pub \
    --bundle ${bundle}.bundle \
    $bundle >/dev/null 2>&1 || echo "FAILED: $bundle"
done
```

⚠️ **WORM upload lag >24 hours**
```bash
# Compare local vs WORM counts
local_count=$(ls /var/lib/msp/evidence/EB-*.json | wc -l)
worm_count=$(aws s3 ls s3://msp-compliance-worm/evidence/ --recursive | grep ".json$" | wc -l)
echo "Local: $local_count | WORM: $worm_count | Lag: $(($local_count - $worm_count))"
```

---

## Client Incident Response

**When client calls about incident:**

1. Get incident details
   - What happened?
   - When did it occur?
   - What system?

2. Find related evidence bundle
   ```bash
   # Search by date and hostname
   grep -l '"hostname": "srv-primary"' /var/lib/msp/evidence/EB-20251101-*.json
   ```

3. Review bundle
   ```bash
   cat /var/lib/msp/evidence/{bundle}.json | jq
   ```

4. Explain remediation
   - Show actions taken (`.actions_taken[]`)
   - Show resolution status (`.outputs.resolution_status`)
   - Show MTTR (`.execution.mttr_seconds`)

5. If failed, explain why
   ```bash
   cat /var/lib/msp/evidence/{bundle}.json | jq '.actions_taken[] | select(.result == "failed")'
   ```

---

## Useful One-Liners

```bash
# List all clients
ls /var/lib/msp/clients/

# Count bundles per client
for client in $(ls /var/lib/msp/clients/); do
  count=$(grep -l "\"client_id\": \"$client\"" /var/lib/msp/evidence/EB-*.json | wc -l)
  echo "$client: $count bundles"
done

# Average MTTR last 7 days
grep -h "mttr_seconds" /var/lib/msp/evidence/EB-$(date -d "7 days ago" +%Y%m%d)-*.json | \
  awk '{sum+=$2; count++} END {print "Avg MTTR: " sum/count " seconds"}'

# SLA compliance rate
total=$(ls /var/lib/msp/evidence/EB-*.json | wc -l)
met=$(grep -l '"sla_met": true' /var/lib/msp/evidence/EB-*.json | wc -l)
echo "SLA Compliance: $(echo "scale=2; $met/$total*100" | bc)%"

# Most common incident types
grep -h '"event_type"' /var/lib/msp/evidence/EB-*.json | \
  sort | uniq -c | sort -rn | head -10
```

---

## Escalation Criteria

**Escalate to Lead Operator if:**
- Service down >30 minutes
- Disk >95% full
- >10 signature verification failures
- WORM upload failing >4 hours
- Suspected security incident

**Escalate to Security Lead if:**
- Unauthorized access detected
- Private key compromise suspected
- Evidence bundle tampering detected
- Anomalous bundle creation (off-hours, unusual volume)

**Escalate to Client if:**
- SLA missed on critical incident
- Manual remediation required
- Service affecting multiple systems

---

## Password Locations (SOPS-Encrypted)

| Secret | Path |
|--------|------|
| COSIGN password | `/etc/msp/secrets/cosign-password.enc` |
| Client API keys | `/etc/msp/secrets/{client-id}-api-key.enc` |
| AWS credentials | `/etc/msp/secrets/aws-credentials.enc` |
| SOPS master key | `~/.config/sops/age/keys.txt` |

**To decrypt:** `sops -d /path/to/secret.enc`

---

## Audit Support Quick Commands

```bash
# Auditor wants to verify bundle
cosign verify-blob \
  --key /var/www/compliance/public-signing-key.pub \
  --bundle /var/lib/msp/evidence/{bundle}.json.bundle \
  /var/lib/msp/evidence/{bundle}.json

# Auditor wants bundle details
cat /var/lib/msp/evidence/{bundle}.json | jq

# Auditor wants specific incident
grep -l "{incident-id}" /var/lib/msp/evidence/EB-*.json

# Auditor wants HIPAA controls for incident
cat /var/lib/msp/evidence/{bundle}.json | jq -r '.incident.hipaa_controls[]'

# Auditor wants monthly summary
python3 /opt/msp/scripts/generate-monthly-summary.py --month 2025-10
```

---

**Print Date:** _______________
**Operator Name:** _______________
**Last Updated:** November 1, 2025
