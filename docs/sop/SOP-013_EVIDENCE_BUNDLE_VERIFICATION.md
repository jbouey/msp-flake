# SOP-013: Evidence Bundle Verification

**Version:** 1.0
**Last Updated:** 2025-10-31
**Owner:** Security Officer
**Review Cycle:** Monthly

---

## What This Is

Evidence bundles are the proof that your compliance automation actually happened. This SOP covers how to verify that evidence bundles are valid, complete, and haven't been tampered with.

**You'll use this when:**
- Daily operations checklist (spot-check 3-5 bundles)
- Client audit preparation (verify entire period)
- Incident investigation (verify specific bundle)
- Troubleshooting evidence pipeline issues
- Responding to "prove this actually happened" questions

---

## Quick Verification (30 seconds)

**Just need to verify one bundle is legit:**

```bash
# Pick any bundle
BUNDLE="/var/lib/msp/evidence/EB-20251031-0042.json"

# Verify signature
cosign verify-blob \
  --key /etc/msp/signing-keys/public-key.pem \
  --signature ${BUNDLE}.sig \
  $BUNDLE

# Output:
# Verified OK                    ← Good
# Error: failed to verify        ← Bad, see troubleshooting
```

If you see "Verified OK", the bundle is authentic and hasn't been tampered with. Done.

---

## Full Verification (5 minutes)

**Need to verify bundle is valid for compliance:**

```bash
BUNDLE="/var/lib/msp/evidence/EB-20251031-0042.json"

# 1. Check signature (proves authenticity)
echo "1. Verifying signature..."
cosign verify-blob \
  --key /etc/msp/signing-keys/public-key.pem \
  --signature ${BUNDLE}.sig \
  $BUNDLE

# 2. Validate JSON schema (proves format correct)
echo "2. Validating schema..."
jsonschema -i $BUNDLE \
  /opt/msp/evidence/schema/evidence-bundle-v1.schema.json

# 3. Check bundle hash (proves internal consistency)
echo "3. Checking bundle hash..."
CLAIMED_HASH=$(jq -r '.evidence_bundle_hash' $BUNDLE)
COMPUTED_HASH=$(jq 'del(.evidence_bundle_hash, .signatures, .storage_locations)' $BUNDLE | sha256sum | awk '{print "sha256:" $1}')

if [ "$CLAIMED_HASH" == "$COMPUTED_HASH" ]; then
  echo "✅ Hash verified"
else
  echo "❌ Hash mismatch - bundle may be corrupted"
fi

# 4. Check WORM storage (proves it was uploaded)
echo "4. Checking WORM storage..."
CLIENT_ID=$(jq -r '.client_id' $BUNDLE)
BUNDLE_ID=$(basename $BUNDLE .json)
YEAR=$(echo $BUNDLE_ID | cut -d'-' -f2 | cut -c1-4)
MONTH=$(echo $BUNDLE_ID | cut -d'-' -f2 | cut -c5-6)

aws s3api head-object \
  --bucket msp-compliance-worm \
  --key ${CLIENT_ID}/${YEAR}/${MONTH}/${BUNDLE_ID}.json \
  --profile msp-ops | jq '.ObjectLockMode, .ObjectLockRetainUntilDate'

# Expected output:
# "COMPLIANCE"
# "2026-01-29T00:00:00Z"

echo "✅ Full verification complete"
```

**What each step proves:**

1. **Signature:** Bundle hasn't been modified since creation (cryptographic proof)
2. **Schema:** Bundle contains all required fields (completeness check)
3. **Hash:** Bundle internal structure is consistent (corruption check)
4. **WORM:** Bundle is immutable and stored safely (tamper-evidence)

---

## Understanding Evidence Bundle Structure

**Every bundle has these sections:**

```json
{
  "bundle_id": "EB-20251031-0042",           // Unique ID
  "bundle_version": "1.0",                   // Schema version
  "client_id": "clinic-001",                 // Which client
  "generated_at": "2025-10-31T14:32:01Z",   // When created

  "incident": {                              // What triggered this
    "incident_id": "INC-20251031-0042",
    "event_type": "backup_failure",
    "severity": "high",
    "detected_at": "2025-10-31T14:32:01Z",
    "hipaa_controls": ["164.308(a)(7)(ii)(A)"]
  },

  "runbook": {                               // What we did about it
    "runbook_id": "RB-BACKUP-001",
    "runbook_version": "1.0",
    "runbook_hash": "sha256:abc123...",     // Proves which runbook ran
    "steps_executed": 4
  },

  "execution": {                             // How it went
    "timestamp_start": "2025-10-31T14:32:01Z",
    "timestamp_end": "2025-10-31T14:37:23Z",
    "operator": "service:mcp-executor",
    "mttr_seconds": 322,
    "sla_met": true
  },

  "actions_taken": [                         // Detailed step-by-step
    {
      "step": 1,
      "action": "check_backup_logs",
      "script_hash": "sha256:def456...",    // Proves what script ran
      "result": "failed",
      "exit_code": 0,
      "timestamp": "2025-10-31T14:32:15Z"
    },
    // ... more steps
  ],

  "outputs": {                               // Final result
    "resolution_status": "success",
    "backup_completion_hash": "sha256:ghi789..."
  },

  "evidence_bundle_hash": "sha256:jkl012...", // Self-integrity check
  "signatures": {},                           // Added by signer
  "storage_locations": [...]                  // Where it's stored
}
```

**Key fields for verification:**

- **hipaa_controls**: Which HIPAA rules this satisfies (auditors look for this)
- **script_hash**: Proves exactly which code ran (not just "we ran a backup")
- **mttr_seconds**: Proves how fast we fixed it (SLA compliance)
- **timestamp_start/end**: Proves when it happened (can't be backdated if signed)

---

## Common Verification Scenarios

### Scenario 1: Daily Spot Check

**What:** Verify 3-5 random bundles to catch pipeline issues early

**How:**

```bash
# Get 5 random bundles from yesterday
YESTERDAY=$(date -d "yesterday" +%Y-%m-%d)

find /var/lib/msp/evidence/ -name "EB-${YESTERDAY//-/}*.json" | shuf -n 5 | while read bundle; do
  echo "Checking: $(basename $bundle)"

  # Quick signature check only
  cosign verify-blob \
    --key /etc/msp/signing-keys/public-key.pem \
    --signature ${bundle}.sig \
    $bundle > /dev/null 2>&1

  if [ $? -eq 0 ]; then
    echo "  ✅ Valid"
  else
    echo "  ❌ INVALID - investigate"
  fi
done
```

**If any fail:** Investigate immediately (see Troubleshooting section)

---

### Scenario 2: Verify Entire Client Period (Audit Prep)

**What:** Verify all bundles for a client over audit period (e.g., 6 months)

**How:**

```bash
CLIENT_ID="clinic-001"
START_DATE="2025-04-01"
END_DATE="2025-10-31"

# Use the verification script
/opt/msp/scripts/verify-client-period.sh \
  --client-id $CLIENT_ID \
  --start-date $START_DATE \
  --end-date $END_DATE

# This checks:
# 1. All expected bundles exist (no gaps)
# 2. All signatures valid
# 3. All schemas validate
# 4. All bundles in WORM storage

# Output:
# Verifying clinic-001 from 2025-04-01 to 2025-10-31
#
# Expected bundles: 214 (1 per day)
# Found bundles: 214
# Missing bundles: 0
#
# Signature verification: 214/214 passed ✅
# Schema validation: 214/214 passed ✅
# WORM storage check: 214/214 present ✅
#
# Verification complete: PASS
```

**If gaps found:**

```bash
# Find missing dates
/opt/msp/scripts/find-missing-evidence.sh \
  --client-id $CLIENT_ID \
  --start-date $START_DATE \
  --end-date $END_DATE

# Output shows:
# Missing evidence for:
# - 2025-06-15 (1 day)
# - 2025-07-22 (1 day)

# Check why they're missing
journalctl -u evidence-bundler --since "2025-06-15 00:00:00" --until "2025-06-16 00:00:00" | grep -i error

# Common reasons:
# - Service outage (check SOP-003: Disaster Recovery)
# - S3 upload failure (check network/permissions)
# - No incidents that day (legitimate gap)
```

---

### Scenario 3: Verify Specific Incident

**What:** Client asks "prove you fixed that backup issue on June 15"

**How:**

```bash
# Find bundles for that client and date
CLIENT_ID="clinic-001"
DATE="2025-06-15"

# List bundles from that day
aws s3 ls s3://msp-compliance-worm/${CLIENT_ID}/2025/06/ --profile msp-ops | grep ${DATE//-/}

# Download specific bundle
BUNDLE_ID="EB-20250615-0023"
aws s3 cp s3://msp-compliance-worm/${CLIENT_ID}/2025/06/${BUNDLE_ID}.json \
  /tmp/${BUNDLE_ID}.json --profile msp-ops

aws s3 cp s3://msp-compliance-worm/${CLIENT_ID}/2025/06/${BUNDLE_ID}.json.sig \
  /tmp/${BUNDLE_ID}.json.sig --profile msp-ops

# Verify signature
cosign verify-blob \
  --key /etc/msp/signing-keys/public-key-2025.pem \
  --signature /tmp/${BUNDLE_ID}.json.sig \
  /tmp/${BUNDLE_ID}.json

# Show client the proof
jq '{
  incident: .incident.event_type,
  detected: .incident.detected_at,
  resolved: .execution.timestamp_end,
  resolution_time_minutes: (.execution.mttr_seconds / 60 | floor),
  what_we_did: .actions_taken[].action,
  final_status: .outputs.resolution_status,
  hipaa_controls: .hipaa_controls
}' /tmp/${BUNDLE_ID}.json

# Output:
# {
#   "incident": "backup_failure",
#   "detected": "2025-06-15T14:32:01Z",
#   "resolved": "2025-06-15T14:37:23Z",
#   "resolution_time_minutes": 5,
#   "what_we_did": [
#     "check_backup_logs",
#     "verify_disk_space",
#     "restart_backup_service",
#     "trigger_manual_backup"
#   ],
#   "final_status": "success",
#   "hipaa_controls": ["164.308(a)(7)(ii)(A)", "164.310(d)(2)(iv)"]
# }
```

**Send this to client with signature verification proof**

---

## Troubleshooting Verification Failures

### Signature Verification Failed

**Error:**
```
Error: failed to verify signature: invalid signature when validating ASN.1 encoded signature
```

**What this means:** Bundle was modified after signing, or signature file is wrong/corrupted

**Troubleshooting steps:**

```bash
BUNDLE="/var/lib/msp/evidence/EB-20251031-0042.json"

# 1. Check if signature file exists and has content
ls -lh ${BUNDLE}.sig
# Expected: ~512 bytes
# If 0 bytes or missing → signature wasn't created

# 2. Check if bundle was modified
stat $BUNDLE
# Look at "Modify" timestamp
# If modified after signature created → someone edited it

# 3. Try with historical key (if bundle is old)
cosign verify-blob \
  --key /etc/msp/signing-keys/historical/public-key-2024.pem \
  --signature ${BUNDLE}.sig \
  $BUNDLE

# If this works → you used wrong key (bundle signed with old key)

# 4. Check bundle JSON is valid
jq . $BUNDLE > /dev/null
# If error → bundle is corrupted (invalid JSON)

# 5. Re-download from WORM storage
CLIENT_ID=$(jq -r '.client_id' $BUNDLE 2>/dev/null || echo "unknown")
BUNDLE_ID=$(basename $BUNDLE .json)
YEAR=$(echo $BUNDLE_ID | cut -d'-' -f2 | cut -c1-4)
MONTH=$(echo $BUNDLE_ID | cut -d'-' -f2 | cut -c5-6)

aws s3 cp s3://msp-compliance-worm/${CLIENT_ID}/${YEAR}/${MONTH}/${BUNDLE_ID}.json \
  /tmp/${BUNDLE_ID}-clean.json --profile msp-ops

aws s3 cp s3://msp-compliance-worm/${CLIENT_ID}/${YEAR}/${MONTH}/${BUNDLE_ID}.json.sig \
  /tmp/${BUNDLE_ID}-clean.json.sig --profile msp-ops

cosign verify-blob \
  --key /etc/msp/signing-keys/public-key.pem \
  --signature /tmp/${BUNDLE_ID}-clean.json.sig \
  /tmp/${BUNDLE_ID}-clean.json

# If this works → local copy was corrupted, use WORM copy
```

**Resolution:**

- If bundle was modified → **SECURITY INCIDENT** - escalate to Security Officer
- If signature missing → Regenerate (if MCP logs still available)
- If wrong key → Use correct historical key
- If corrupted → Restore from WORM storage

---

### Schema Validation Failed

**Error:**
```
ValidationError: 'hipaa_controls' is a required property
```

**What this means:** Bundle is missing required fields or has wrong data types

**Troubleshooting:**

```bash
# Get detailed validation errors
jsonschema -i $BUNDLE \
  /opt/msp/evidence/schema/evidence-bundle-v1.schema.json \
  -o pretty

# Example output:
# {
#   "error": "ValidationError",
#   "message": "'hipaa_controls' is a required property",
#   "path": ["incident"]
# }

# This means: incident.hipaa_controls field is missing
```

**Common schema issues:**

| Error | Meaning | Fix |
|-------|---------|-----|
| `'X' is a required property` | Missing field | MCP executor bug - update code |
| `123 is not of type 'string'` | Wrong data type | MCP executor bug - fix type |
| `'unknown_type' is not one of [...]` | Invalid enum | Add to schema or fix MCP code |
| `Additional properties are not allowed` | Extra field | Remove field or update schema |

**Resolution:**

- If MCP executor generating bad bundles → Fix executor code (see OP-001)
- If schema is too strict → Update schema (requires approval)
- If one-time corruption → Regenerate bundle from logs

---

### Bundle Not in WORM Storage

**Error:**
```
An error occurred (404) when calling the HeadObject operation: Not Found
```

**What this means:** Bundle exists locally but wasn't uploaded to S3

**Troubleshooting:**

```bash
# Check upload logs
journalctl -u evidence-bundler -n 500 --no-pager | grep "EB-20251031-0042"

# Look for:
# [ERROR] Failed to upload EB-20251031-0042.json to S3: <reason>

# Common reasons:
# 1. Network timeout
# 2. Permission denied (IAM role issue)
# 3. Bucket not found
# 4. S3 outage

# Manual upload
CLIENT_ID=$(jq -r '.client_id' $BUNDLE)
BUNDLE_ID=$(basename $BUNDLE .json)
YEAR=$(echo $BUNDLE_ID | cut -d'-' -f2 | cut -c1-4)
MONTH=$(echo $BUNDLE_ID | cut -d'-' -f2 | cut -c5-6)

aws s3 cp $BUNDLE \
  s3://msp-compliance-worm/${CLIENT_ID}/${YEAR}/${MONTH}/ \
  --profile msp-ops

aws s3 cp ${BUNDLE}.sig \
  s3://msp-compliance-worm/${CLIENT_ID}/${YEAR}/${MONTH}/ \
  --profile msp-ops

# Verify Object Lock applied
aws s3api head-object \
  --bucket msp-compliance-worm \
  --key ${CLIENT_ID}/${YEAR}/${MONTH}/${BUNDLE_ID}.json \
  --profile msp-ops | jq '.ObjectLockMode'

# Expected: "COMPLIANCE"
```

**Resolution:**

- Network issue → Wait and retry
- Permission issue → Fix IAM role (see OP-002)
- Manual upload → Upload missing bundles
- Systematic issue → Fix evidence bundler service

---

### Hash Mismatch

**Error:**
```
Bundle hash mismatch
Expected: sha256:abc123...
Computed: sha256:def456...
```

**What this means:** Bundle internal structure doesn't match claimed hash

**This is serious - indicates corruption or tampering**

**Troubleshooting:**

```bash
# Check what changed
CLAIMED_HASH=$(jq -r '.evidence_bundle_hash' $BUNDLE)

# Recompute hash excluding signature fields
COMPUTED_HASH=$(jq 'del(.evidence_bundle_hash, .signatures, .storage_locations)' $BUNDLE | sha256sum | awk '{print "sha256:" $1}')

# If they don't match, something in the bundle changed

# Compare with WORM storage copy
BUNDLE_ID=$(basename $BUNDLE .json)
YEAR=$(echo $BUNDLE_ID | cut -d'-' -f2 | cut -c1-4)
MONTH=$(echo $BUNDLE_ID | cut -d'-' -f2 | cut -c5-6)
CLIENT_ID=$(jq -r '.client_id' $BUNDLE)

aws s3 cp s3://msp-compliance-worm/${CLIENT_ID}/${YEAR}/${MONTH}/${BUNDLE_ID}.json \
  /tmp/worm-${BUNDLE_ID}.json --profile msp-ops

# Diff the files
diff <(jq -S . $BUNDLE) <(jq -S . /tmp/worm-${BUNDLE_ID}.json)

# This shows exactly what changed
```

**Resolution:**

- Local copy corrupted → Use WORM copy
- WORM copy also mismatched → **MAJOR ISSUE** - escalate to Security Officer
- Hash field was never set → Bundle generated wrong (fix bundler)

---

## Batch Verification

**Need to verify many bundles quickly:**

```bash
# Verify all bundles from last week
find /var/lib/msp/evidence/ -name "EB-*.json" -mtime -7 | \
  parallel -j4 "cosign verify-blob \
    --key /etc/msp/signing-keys/public-key.pem \
    --signature {}.sig \
    {} > /dev/null 2>&1 && echo '✅ {}' || echo '❌ {}'"

# Uses GNU parallel to check 4 bundles at once
# Adjust -j flag based on CPU cores
```

**Check WORM storage for all clients:**

```bash
# List all clients
cat /etc/msp/clients.txt | while read client; do
  echo "Checking: $client"

  # Count bundles in WORM for last 30 days
  BUNDLE_COUNT=$(aws s3 ls s3://msp-compliance-worm/${client}/ \
    --recursive --profile msp-ops | \
    grep $(date +%Y/%m) | \
    grep ".json\"$" | wc -l)

  # Should be ~30 bundles (1 per day)
  if [ $BUNDLE_COUNT -lt 25 ]; then
    echo "  ⚠️  Only $BUNDLE_COUNT bundles (expected ~30)"
  else
    echo "  ✅ $BUNDLE_COUNT bundles"
  fi
done
```

---

## Verification Automation

**Daily automated verification (runs via cron):**

```bash
# /opt/msp/scripts/daily-evidence-verification.sh
#!/bin/bash

DATE=$(date +%Y-%m-%d)
REPORT_FILE="/var/log/msp/evidence-verification-${DATE}.log"

echo "Evidence Verification Report - $DATE" > $REPORT_FILE
echo "========================================" >> $REPORT_FILE

# 1. Verify yesterday's bundles
YESTERDAY=$(date -d "yesterday" +%Y-%m-%d)
echo "Checking bundles from: $YESTERDAY" >> $REPORT_FILE

TOTAL=0
VALID=0
INVALID=0

find /var/lib/msp/evidence/ -name "EB-${YESTERDAY//-/}*.json" | while read bundle; do
  TOTAL=$((TOTAL + 1))

  cosign verify-blob \
    --key /etc/msp/signing-keys/public-key.pem \
    --signature ${bundle}.sig \
    $bundle > /dev/null 2>&1

  if [ $? -eq 0 ]; then
    VALID=$((VALID + 1))
  else
    INVALID=$((INVALID + 1))
    echo "INVALID: $(basename $bundle)" >> $REPORT_FILE
  fi
done

echo "Total: $TOTAL" >> $REPORT_FILE
echo "Valid: $VALID" >> $REPORT_FILE
echo "Invalid: $INVALID" >> $REPORT_FILE

# 2. Alert if any invalid
if [ $INVALID -gt 0 ]; then
  echo "⚠️ ALERT: $INVALID invalid bundles detected" >> $REPORT_FILE

  # Send alert
  mail -s "[ALERT] Evidence Verification Failed" ops@msp.com < $REPORT_FILE
fi

# 3. Cleanup old verification reports (keep 90 days)
find /var/log/msp/ -name "evidence-verification-*.log" -mtime +90 -delete
```

**Install cron job:**

```bash
# Run daily at 6 AM
echo "0 6 * * * /opt/msp/scripts/daily-evidence-verification.sh" | crontab -
```

---

## Manual Verification for Auditors

**Give auditors this one-liner:**

```bash
# Download bundle and signature from WORM storage
CLIENT_ID="clinic-001"
BUNDLE_ID="EB-20250615-0023"
YEAR="2025"
MONTH="06"

aws s3 cp s3://msp-compliance-worm/${CLIENT_ID}/${YEAR}/${MONTH}/${BUNDLE_ID}.json .
aws s3 cp s3://msp-compliance-worm/${CLIENT_ID}/${YEAR}/${MONTH}/${BUNDLE_ID}.json.sig .

# Download public key
curl -o public-key.pem https://compliance.msp.com/public-keys/2025.pem

# Verify signature
cosign verify-blob \
  --key public-key.pem \
  --signature ${BUNDLE_ID}.json.sig \
  ${BUNDLE_ID}.json

# Output: Verified OK ✅
```

**If auditor doesn't have cosign:**

```bash
# Alternative: OpenSSL verification
openssl dgst -sha256 -verify public-key.pem \
  -signature ${BUNDLE_ID}.json.sig \
  ${BUNDLE_ID}.json

# Output: Verified OK
```

---

## Verification Metrics

**Track these metrics monthly:**

```bash
# Total bundles generated
TOTAL_BUNDLES=$(aws s3 ls s3://msp-compliance-worm/ --recursive | grep ".json\"$" | wc -l)

# Bundles with valid signatures
VALID_SIGS=$(find /var/lib/msp/evidence/ -name "*.json" -mtime -30 | \
  parallel -j4 "cosign verify-blob --key /etc/msp/signing-keys/public-key.pem \
    --signature {}.sig {} > /dev/null 2>&1 && echo 1" | wc -l)

# Verification success rate
SUCCESS_RATE=$(echo "scale=2; ($VALID_SIGS / $TOTAL_BUNDLES) * 100" | bc)

echo "Total bundles (30 days): $TOTAL_BUNDLES"
echo "Valid signatures: $VALID_SIGS"
echo "Success rate: ${SUCCESS_RATE}%"

# Target: >99.9% success rate
```

**Include in monthly compliance packets**

---

## Related Documents

- **SOP-002:** Incident Response
- **SOP-011:** Compliance Audit Support
- **OP-002:** Evidence Pipeline Operations
- **Evidence Schema:** /opt/msp/evidence/schema/evidence-bundle-v1.schema.json

---

## Revision History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2025-10-31 | Initial version | Security Team |

---

**Document Status:** ✅ Active
**Next Review:** 2025-11-30 (Monthly)
**Owner:** Security Officer
