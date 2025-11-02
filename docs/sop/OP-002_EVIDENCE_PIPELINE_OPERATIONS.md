# OP-002: Evidence Pipeline Operations

**Version:** 1.0
**Last Updated:** 2025-10-31
**Owner:** Security Officer
**Review Cycle:** Quarterly
**Document Type:** Operator Manual (Technical Reference)

---

## Purpose

This Operator Manual provides detailed technical procedures for managing the evidence generation pipeline that underpins HIPAA compliance monitoring. This includes:

- Evidence bundler service operation and troubleshooting
- Cryptographic signing infrastructure (cosign)
- WORM storage management (S3 Object Lock)
- Evidence verification and auditor handoff procedures
- Disaster recovery for evidence archives

**Audience:** Operations Engineers, Security Officers

**HIPAA Controls:**
- §164.312(b) - Audit controls
- §164.316(b)(1)(i) - Documentation (time-limited retention)
- §164.316(b)(2)(i) - Availability for inspection

---

## System Architecture

### Evidence Pipeline Components

```
┌─────────────────┐
│  MCP Executor   │ (Generates evidence during runbook execution)
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────────────────┐
│           Evidence Bundler Service              │
│                                                 │
│  - Collects artifacts from incidents           │
│  - Validates JSON schema                       │
│  - Generates bundle ID (EB-YYYYMMDD-NNNN)     │
│  - Writes to local storage                     │
└────────┬────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────┐
│           Cryptographic Signer                  │
│                                                 │
│  - Signs bundle with cosign private key        │
│  - Generates detached signature (.sig file)   │
│  - Verifies signature integrity                │
└────────┬────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────┐
│           WORM Storage Uploader                 │
│                                                 │
│  - Uploads bundle to S3 with Object Lock       │
│  - Uploads signature alongside bundle          │
│  - Verifies upload integrity                   │
│  - Sets 90-day retention policy                │
└────────┬────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────┐
│        S3 Bucket (COMPLIANCE Mode)              │
│                                                 │
│  - Object Lock enabled (COMPLIANCE)            │
│  - 90-day retention period                     │
│  - Versioning enabled                          │
│  - Encryption at rest (AES-256-GCM)           │
│  - Access logging enabled                      │
└─────────────────────────────────────────────────┘
```

### File Locations

| Component | Path | Ownership | Permissions |
|-----------|------|-----------|-------------|
| Evidence bundler service | `/opt/msp/evidence/bundler.py` | root:msp | 755 |
| Bundler config | `/etc/msp/evidence-bundler.conf` | root:msp | 640 |
| Local evidence storage | `/var/lib/msp/evidence/` | msp:msp | 750 |
| Signing keys (private) | `/etc/msp/signing-keys/private-key.pem` | root:msp | 400 |
| Signing keys (public) | `/etc/msp/signing-keys/public-key.pem` | msp:msp | 644 |
| Service logs | `/var/log/msp/evidence-bundler.log` | msp:msp | 644 |

---

## Prerequisites

Before operating the evidence pipeline, ensure:

- [ ] Access to MCP server (`ssh mcp-server.msp.internal`)
- [ ] Evidence bundler service running (`systemctl status evidence-bundler`)
- [ ] Signing keys installed (`/etc/msp/signing-keys/`)
- [ ] S3 bucket accessible (`aws s3 ls s3://msp-compliance-worm/`)
- [ ] cosign installed (`cosign version`)
- [ ] AWS CLI configured (`aws configure list --profile msp-ops`)

**Required Permissions:**
- `sudo` access on MCP server
- AWS IAM role: `msp-evidence-uploader` (S3 write-only)
- Key management access (for key rotation)

---

## Operating Procedures

### 1. Service Health Check

#### 1.1 Check Evidence Bundler Service Status

```bash
ssh mcp-server.msp.internal

# Check service status
systemctl status evidence-bundler

# Expected output:
# ● evidence-bundler.service - MSP Evidence Bundler
#      Loaded: loaded (/etc/systemd/system/evidence-bundler.service; enabled)
#      Active: active (running) since Thu 2025-10-31 00:00:01 UTC; 1d 2h ago
#    Main PID: 1234 (python3)
#       Tasks: 3 (limit: 4915)
#      Memory: 45.2M
#         CPU: 2min 34s
#      CGroup: /system.slice/evidence-bundler.service
#              └─1234 /usr/bin/python3 /opt/msp/evidence/bundler.py
```

**Healthy Status:**
- Active: `active (running)`
- Uptime: >1 day (should not restart frequently)
- Memory: <100M (typical: 40-60M)
- CPU: Low usage (evidence bundler is event-driven)

**Unhealthy Indicators:**
- Status: `failed` or `activating` → Service crashed or failing to start
- Recent restarts → Check logs for errors
- High memory (>200M) → Possible memory leak
- High CPU (>10%) → Possible infinite loop

---

#### 1.2 Check Recent Evidence Generation

```bash
# Check logs for recent activity
journalctl -u evidence-bundler -n 50 --no-pager

# Expected: Evidence bundles generated in last 24 hours
# Look for:
# [INFO] Generated evidence bundle: EB-20251031-0042 (client: clinic-001)
# [INFO] Signed bundle: EB-20251031-0042.json
# [INFO] Uploaded to WORM: s3://msp-compliance-worm/clinic-001/2025/10/EB-20251031-0042.json
```

**Healthy Logs:**
- Regular bundle generation (multiple per hour typical)
- No ERROR or CRITICAL messages
- Successful uploads to WORM storage

**Error Patterns:**
```bash
# Check for errors in last 100 lines
journalctl -u evidence-bundler -n 100 --no-pager | grep -i "error\|critical\|failed"

# Common errors:
# - "Failed to sign bundle" → Signing key issue (see Section 3)
# - "S3 upload failed" → Network or permissions issue (see Section 4)
# - "Schema validation failed" → MCP executor generating invalid JSON (see Section 5)
```

---

#### 1.3 Verify Local Evidence Storage

```bash
# Check local evidence directory
ls -lh /var/lib/msp/evidence/ | tail -20

# Expected: Recent .json and .sig files
# -rw-r--r-- 1 msp msp  12K Oct 31 14:32 EB-20251031-0042.json
# -rw-r--r-- 1 msp msp 512B Oct 31 14:32 EB-20251031-0042.json.sig

# Check disk usage
df -h /var/lib/msp/evidence/

# Expected: <10% full (evidence is uploaded to S3 then purged after 7 days)
```

**Disk Space Issues:**
If `/var/lib/msp/evidence/` exceeds 80% full:

```bash
# Check for stale evidence (not uploaded)
find /var/lib/msp/evidence/ -type f -mtime +7 -name "*.json"

# If old files exist, investigate upload failures
journalctl -u evidence-bundler -n 500 --no-pager | grep "upload failed"

# Manual cleanup (ONLY after verifying uploads)
find /var/lib/msp/evidence/ -type f -mtime +7 -delete
```

---

### 2. Evidence Bundle Verification

#### 2.1 Verify Bundle Integrity (Local)

```bash
# Select a recent bundle
BUNDLE_ID="EB-20251031-0042"

# Verify signature
cosign verify-blob \
  --key /etc/msp/signing-keys/public-key.pem \
  --signature /var/lib/msp/evidence/${BUNDLE_ID}.json.sig \
  /var/lib/msp/evidence/${BUNDLE_ID}.json

# Expected output:
# Verified OK
```

**Signature Verification Failures:**

If signature verification fails:
```
Error: failed to verify signature
```

**Troubleshooting:**
1. Check signature file exists and is non-empty:
   ```bash
   ls -lh /var/lib/msp/evidence/${BUNDLE_ID}.json.sig
   # Should be ~512 bytes
   ```

2. Verify public key matches private key:
   ```bash
   # Extract public key from private key
   openssl ec -in /etc/msp/signing-keys/private-key.pem -pubout

   # Compare with public-key.pem
   diff <(openssl ec -in /etc/msp/signing-keys/private-key.pem -pubout) \
        /etc/msp/signing-keys/public-key.pem
   ```

3. Check bundle file integrity:
   ```bash
   # Verify JSON is valid
   jq . /var/lib/msp/evidence/${BUNDLE_ID}.json > /dev/null

   # If jq fails, bundle is corrupted
   ```

4. If bundle or signature corrupted, regenerate:
   ```bash
   # Trigger manual evidence regeneration (if incident data still available)
   /opt/msp/scripts/regenerate-evidence.sh --incident-id INC-20251031-0042
   ```

---

#### 2.2 Verify WORM Storage Upload

```bash
# Check if bundle exists in S3
CLIENT_ID="clinic-001"
BUNDLE_DATE="2025-10-31"

aws s3 ls s3://msp-compliance-worm/${CLIENT_ID}/2025/10/${BUNDLE_ID}.json \
  --profile msp-ops

# Expected output:
# 2025-10-31 14:32:45      12345 EB-20251031-0042.json

# Check signature file
aws s3 ls s3://msp-compliance-worm/${CLIENT_ID}/2025/10/${BUNDLE_ID}.json.sig \
  --profile msp-ops

# Expected output:
# 2025-10-31 14:32:46        512 EB-20251031-0042.json.sig
```

**Missing Bundles in S3:**

If bundle is local but not in S3:

```bash
# Check uploader logs
journalctl -u evidence-bundler -n 200 --no-pager | grep ${BUNDLE_ID}

# Look for upload failure reasons:
# - Network timeout
# - Permission denied
# - Bucket not found
# - Object Lock configuration error

# Manual upload (if upload failed)
aws s3 cp /var/lib/msp/evidence/${BUNDLE_ID}.json \
  s3://msp-compliance-worm/${CLIENT_ID}/2025/10/ \
  --profile msp-ops

aws s3 cp /var/lib/msp/evidence/${BUNDLE_ID}.json.sig \
  s3://msp-compliance-worm/${CLIENT_ID}/2025/10/ \
  --profile msp-ops

# Verify Object Lock applied
aws s3api head-object \
  --bucket msp-compliance-worm \
  --key ${CLIENT_ID}/2025/10/${BUNDLE_ID}.json \
  --profile msp-ops | jq '.ObjectLockMode, .ObjectLockRetainUntilDate'

# Expected:
# "COMPLIANCE"
# "2026-01-29T00:00:00Z"  (90 days from upload)
```

---

#### 2.3 Verify Bundle Schema Compliance

```bash
# Download JSON schema
SCHEMA_PATH="/opt/msp/evidence/schema/evidence-bundle-v1.schema.json"

# Validate bundle against schema
jsonschema -i /var/lib/msp/evidence/${BUNDLE_ID}.json ${SCHEMA_PATH}

# Expected output: (no output = validation passed)
```

**Schema Validation Failures:**

If bundle fails schema validation:

```bash
# Get detailed validation errors
jsonschema -i /var/lib/msp/evidence/${BUNDLE_ID}.json ${SCHEMA_PATH} -o pretty

# Common validation errors:
# - Missing required field (e.g., "hipaa_controls")
# - Invalid data type (e.g., string instead of integer)
# - Invalid enum value (e.g., unknown incident type)

# Example error:
# {
#   "error": "ValidationError",
#   "message": "'hipaa_controls' is a required property",
#   "path": ["incident"]
# }
```

**Resolution:**
- If MCP executor is generating invalid bundles, fix executor code
- Update schema if validation is too strict
- Regenerate bundle if one-time corruption

---

### 3. Cryptographic Key Management

#### 3.1 Key Rotation Schedule

**Signing keys should be rotated:**
- Every 365 days (annually)
- Immediately if compromise suspected
- When cryptographic standards change

**Current Key Information:**
```bash
# View key creation date
stat /etc/msp/signing-keys/private-key.pem | grep "Birth"

# View key algorithm
openssl ec -in /etc/msp/signing-keys/private-key.pem -text -noout | head -1

# Expected: EC PRIVATE KEY (P-256 curve or stronger)
```

---

#### 3.2 Generate New Signing Key Pair

**⚠️ CRITICAL: Do NOT delete old keys until all old bundles verified**

```bash
# Generate new key pair
cd /etc/msp/signing-keys/

# Generate new private key (P-256 elliptic curve)
openssl ecparam -genkey -name prime256v1 -out private-key-2026.pem

# Extract public key
openssl ec -in private-key-2026.pem -pubout -out public-key-2026.pem

# Set permissions
chmod 400 private-key-2026.pem
chmod 644 public-key-2026.pem
chown root:msp private-key-2026.pem public-key-2026.pem

# Verify key pair
cosign verify-blob \
  --key public-key-2026.pem \
  --signature <(echo "test" | cosign sign-blob --key private-key-2026.pem /dev/stdin) \
  <(echo "test")

# Expected: Verified OK
```

---

#### 3.3 Deploy New Keys

```bash
# Backup old keys
cp private-key.pem private-key-2025.pem.backup
cp public-key.pem public-key-2025.pem.backup

# Deploy new keys
ln -sf private-key-2026.pem private-key.pem
ln -sf public-key-2026.pem public-key.pem

# Restart evidence bundler to pick up new key
systemctl restart evidence-bundler

# Verify service restarted successfully
systemctl status evidence-bundler

# Verify new bundles use new key
# (Wait for next bundle generation, then verify signature with new public key)
```

---

#### 3.4 Maintain Historical Public Keys

**⚠️ CRITICAL: Keep all historical public keys for auditor verification**

```bash
# Create public key archive
mkdir -p /etc/msp/signing-keys/historical/

# Archive old public key with date range
cp public-key-2025.pem.backup \
   /etc/msp/signing-keys/historical/public-key-2025-01-01-to-2025-12-31.pem

# Document key rotation in changelog
cat >> /etc/msp/signing-keys/KEY_ROTATION_LOG.txt <<EOF
Date: 2025-10-31
Action: Key rotation
Old Key: public-key-2025.pem (SHA256: $(sha256sum public-key-2025.pem.backup | awk '{print $1}'))
New Key: public-key-2026.pem (SHA256: $(sha256sum public-key-2026.pem | awk '{print $1}'))
Reason: Annual rotation
Operator: $(whoami)
EOF

# Distribute new public key to clients (for auditor verification)
/opt/msp/scripts/distribute-public-key.sh --key public-key-2026.pem
```

---

### 4. WORM Storage Management

#### 4.1 Verify S3 Bucket Configuration

```bash
# Check Object Lock configuration
aws s3api get-object-lock-configuration \
  --bucket msp-compliance-worm \
  --profile msp-ops

# Expected output:
# {
#     "ObjectLockConfiguration": {
#         "ObjectLockEnabled": "Enabled",
#         "Rule": {
#             "DefaultRetention": {
#                 "Mode": "COMPLIANCE",
#                 "Days": 90
#             }
#         }
#     }
# }
```

**Critical Configuration:**
- `ObjectLockEnabled: Enabled` → Cannot disable without AWS support
- `Mode: COMPLIANCE` → Cannot be deleted by anyone (including root) until retention expires
- `Days: 90` → HIPAA minimum is 6 years, but we archive to long-term after 90 days

---

#### 4.2 Verify Bucket Encryption

```bash
# Check encryption configuration
aws s3api get-bucket-encryption \
  --bucket msp-compliance-worm \
  --profile msp-ops

# Expected output:
# {
#     "ServerSideEncryptionConfiguration": {
#         "Rules": [
#             {
#                 "ApplyServerSideEncryptionByDefault": {
#                     "SSEAlgorithm": "AES256"
#                 },
#                 "BucketKeyEnabled": true
#             }
#         ]
#     }
# }
```

---

#### 4.3 Monitor Bucket Access Logs

```bash
# S3 access logs are written to separate bucket
aws s3 ls s3://msp-compliance-worm-logs/ \
  --recursive \
  --profile msp-ops | tail -20

# Download recent logs
aws s3 cp s3://msp-compliance-worm-logs/$(date +%Y-%m-%d)-access.log \
  /tmp/s3-access.log \
  --profile msp-ops

# Check for unauthorized access attempts
grep -i "40[13]\|50[0-9]" /tmp/s3-access.log

# Expected: No 403 (Forbidden) or 5xx errors from known IP addresses
```

**Suspicious Activity:**
- 403 errors from unknown IPs → Potential unauthorized access attempts
- 404 errors for existing objects → Potential enumeration attack
- High volume of requests from single IP → Potential DoS or data exfiltration

**Action:** Escalate to Security Officer immediately

---

#### 4.4 Storage Cost Monitoring

```bash
# Check bucket size and object count
aws s3 ls s3://msp-compliance-worm/ \
  --recursive \
  --summarize \
  --profile msp-ops

# Expected output (example):
# Total Objects: 4500
# Total Size: 125.3 GiB

# Calculate monthly cost
# S3 Standard: $0.023 per GB/month
# 125.3 GB × $0.023 = ~$2.88/month
```

**Cost Optimization:**

After 90-day retention period, transition to cheaper storage:

```bash
# Configure lifecycle policy (already applied in Terraform)
aws s3api get-bucket-lifecycle-configuration \
  --bucket msp-compliance-worm \
  --profile msp-ops

# Expected: Transition to Glacier Deep Archive after 90 days
# Glacier Deep Archive: $0.00099 per GB/month (96% cheaper)
```

---

### 5. Troubleshooting Common Issues

#### 5.1 Evidence Bundler Service Crashes

**Symptom:**
```bash
systemctl status evidence-bundler
# Status: failed
```

**Investigation:**

```bash
# Check crash logs
journalctl -u evidence-bundler -n 100 --no-pager

# Common crash causes:
# 1. Signing key missing or permissions wrong
# 2. Disk full (/var/lib/msp/evidence/)
# 3. Python dependencies missing
# 4. Configuration file syntax error

# Check signing keys
ls -lh /etc/msp/signing-keys/
# Verify private-key.pem exists and has 400 permissions

# Check disk space
df -h /var/lib/msp/evidence/

# Check Python dependencies
/usr/bin/python3 -c "import cosign, boto3, jsonschema"
# Should have no output (no import errors)

# Check configuration syntax
python3 -c "import yaml; yaml.safe_load(open('/etc/msp/evidence-bundler.conf'))"
```

**Resolution:**

```bash
# Fix signing key permissions
chmod 400 /etc/msp/signing-keys/private-key.pem
chown root:msp /etc/msp/signing-keys/private-key.pem

# Clean up disk space
find /var/lib/msp/evidence/ -type f -mtime +7 -delete

# Reinstall dependencies
pip3 install --upgrade cosign boto3 jsonschema

# Restart service
systemctl restart evidence-bundler
systemctl status evidence-bundler
```

---

#### 5.2 S3 Upload Failures

**Symptom:**
```bash
journalctl -u evidence-bundler -n 50 --no-pager | grep "upload failed"
# [ERROR] Failed to upload EB-20251031-0042.json to S3: Access Denied
```

**Investigation:**

```bash
# Test S3 access
aws s3 ls s3://msp-compliance-worm/ --profile msp-ops

# If "Access Denied":
# 1. Check IAM role attached to MCP server
aws sts get-caller-identity --profile msp-ops

# Expected: Role: msp-evidence-uploader

# 2. Verify IAM policy allows PutObject
aws iam get-role-policy \
  --role-name msp-evidence-uploader \
  --policy-name S3EvidenceUpload

# 3. Check S3 bucket policy
aws s3api get-bucket-policy \
  --bucket msp-compliance-worm \
  --profile msp-ops
```

**Resolution:**

If IAM role missing or permissions insufficient, fix via Terraform:

```bash
cd terraform/modules/evidence-pipeline/
terraform plan
terraform apply
```

Manual upload of failed bundles:

```bash
# Find bundles not in S3
comm -23 \
  <(ls /var/lib/msp/evidence/*.json | sort) \
  <(aws s3 ls s3://msp-compliance-worm/ --recursive --profile msp-ops | awk '{print $4}' | sort)

# Manual upload each missing bundle
for bundle in $(cat missing-bundles.txt); do
  CLIENT_ID=$(jq -r '.client_id' "$bundle")
  BUNDLE_ID=$(basename "$bundle" .json)
  YEAR=$(echo $BUNDLE_ID | cut -d'-' -f2 | cut -c1-4)
  MONTH=$(echo $BUNDLE_ID | cut -d'-' -f2 | cut -c5-6)

  aws s3 cp "$bundle" \
    "s3://msp-compliance-worm/${CLIENT_ID}/${YEAR}/${MONTH}/" \
    --profile msp-ops

  aws s3 cp "${bundle}.sig" \
    "s3://msp-compliance-worm/${CLIENT_ID}/${YEAR}/${MONTH}/" \
    --profile msp-ops
done
```

---

#### 5.3 Schema Validation Failures

**Symptom:**
```bash
journalctl -u evidence-bundler -n 50 --no-pager | grep "schema"
# [ERROR] Evidence bundle EB-20251031-0042 failed schema validation
```

**Investigation:**

```bash
# Get validation error details
journalctl -u evidence-bundler -n 200 --no-pager | grep -A 10 "EB-20251031-0042"

# Expected error format:
# [ERROR] Schema validation failed: 'hipaa_controls' is a required property at path: incident

# Manually validate bundle
BUNDLE_ID="EB-20251031-0042"
jsonschema -i /var/lib/msp/evidence/${BUNDLE_ID}.json \
           /opt/msp/evidence/schema/evidence-bundle-v1.schema.json \
           -o pretty
```

**Common Schema Violations:**

1. **Missing Required Field:**
   ```json
   {
     "error": "'hipaa_controls' is a required property",
     "path": ["incident"]
   }
   ```
   **Fix:** MCP executor must include HIPAA controls in incident data

2. **Invalid Data Type:**
   ```json
   {
     "error": "123 is not of type 'string'",
     "path": ["incident", "client_id"]
   }
   ```
   **Fix:** MCP executor passing wrong data type

3. **Invalid Enum Value:**
   ```json
   {
     "error": "'unknown_type' is not one of ['backup_failure', 'cert_expiry', ...]",
     "path": ["incident", "event_type"]
   }
   ```
   **Fix:** Add new event type to schema or fix MCP executor

**Resolution:**

Fix MCP executor to generate valid bundles (see OP-001: MCP Server Operations)

For one-time issues, manually correct bundle:

```bash
# Edit bundle JSON (fix validation error)
vi /var/lib/msp/evidence/${BUNDLE_ID}.json

# Re-sign bundle
cosign sign-blob \
  --key /etc/msp/signing-keys/private-key.pem \
  --output-signature /var/lib/msp/evidence/${BUNDLE_ID}.json.sig \
  /var/lib/msp/evidence/${BUNDLE_ID}.json

# Re-upload to S3
# (see Section 5.2)
```

---

### 6. Auditor Handoff Procedures

#### 6.1 Prepare Evidence Bundle for Auditor

When auditor requests evidence for specific incident or time period:

```bash
# Example: Auditor requests all October 2025 bundles for clinic-001

CLIENT_ID="clinic-001"
MONTH="2025-10"

# Download all bundles for month
aws s3 sync \
  s3://msp-compliance-worm/${CLIENT_ID}/2025/10/ \
  /tmp/auditor-evidence/${CLIENT_ID}/${MONTH}/ \
  --profile msp-ops

# Create auditor package
cd /tmp/auditor-evidence/${CLIENT_ID}/${MONTH}/

# Generate SHA-256 checksums
find . -type f -name "*.json" -exec sha256sum {} \; > CHECKSUMS.txt

# Create README
cat > README.txt <<EOF
Evidence Bundle Archive - ${CLIENT_ID}
Period: ${MONTH}
Generated: $(date -u +"%Y-%m-%d %H:%M:%S UTC")

Contents:
- Evidence bundles (.json files): Incident details, remediation actions, timestamps
- Cryptographic signatures (.sig files): Cosign detached signatures
- CHECKSUMS.txt: SHA-256 checksums of all bundles

Verification Instructions:
1. Verify checksums:
   sha256sum -c CHECKSUMS.txt

2. Verify signatures (requires public key):
   cosign verify-blob \
     --key public-key.pem \
     --signature EB-YYYYMMDD-NNNN.json.sig \
     EB-YYYYMMDD-NNNN.json

3. Validate JSON schema:
   jsonschema -i EB-YYYYMMDD-NNNN.json evidence-bundle-v1.schema.json

Public Key: See attached public-key.pem
JSON Schema: See attached evidence-bundle-v1.schema.json

Contact: compliance@msp.com
EOF

# Copy public key and schema
cp /etc/msp/signing-keys/public-key.pem .
cp /opt/msp/evidence/schema/evidence-bundle-v1.schema.json .

# Create ZIP archive
cd /tmp/auditor-evidence/
zip -r ${CLIENT_ID}-${MONTH}-evidence.zip ${CLIENT_ID}/${MONTH}/

# Generate ZIP checksum
sha256sum ${CLIENT_ID}-${MONTH}-evidence.zip > ${CLIENT_ID}-${MONTH}-evidence.zip.sha256

# Transfer to auditor (SFTP, secure email, client portal)
```

---

#### 6.2 Auditor Verification Script

Provide this script to auditors for self-service verification:

```bash
#!/bin/bash
# verify-evidence.sh
# Usage: ./verify-evidence.sh <bundle-file.json>

set -e

BUNDLE_FILE="$1"
SIG_FILE="${BUNDLE_FILE}.sig"
PUBLIC_KEY="public-key.pem"
SCHEMA="evidence-bundle-v1.schema.json"

if [ ! -f "$BUNDLE_FILE" ]; then
  echo "Error: Bundle file not found: $BUNDLE_FILE"
  exit 1
fi

echo "Verifying evidence bundle: $BUNDLE_FILE"
echo "----------------------------------------"

# 1. Verify signature
echo "1. Verifying cryptographic signature..."
if cosign verify-blob --key "$PUBLIC_KEY" --signature "$SIG_FILE" "$BUNDLE_FILE" > /dev/null 2>&1; then
  echo "   ✅ Signature valid"
else
  echo "   ❌ Signature verification FAILED"
  exit 1
fi

# 2. Validate JSON schema
echo "2. Validating JSON schema..."
if jsonschema -i "$BUNDLE_FILE" "$SCHEMA" > /dev/null 2>&1; then
  echo "   ✅ Schema valid"
else
  echo "   ❌ Schema validation FAILED"
  exit 1
fi

# 3. Check bundle integrity
echo "3. Checking bundle integrity..."
BUNDLE_HASH=$(jq -r '.evidence_bundle_hash' "$BUNDLE_FILE")
COMPUTED_HASH=$(jq 'del(.evidence_bundle_hash, .signatures, .storage_locations)' "$BUNDLE_FILE" | sha256sum | awk '{print "sha256:" $1}')

if [ "$BUNDLE_HASH" == "$COMPUTED_HASH" ]; then
  echo "   ✅ Bundle hash verified"
else
  echo "   ⚠️  Bundle hash mismatch (expected: $BUNDLE_HASH, computed: $COMPUTED_HASH)"
fi

# 4. Display bundle summary
echo "4. Bundle summary:"
echo "   Client ID:     $(jq -r '.client_id' "$BUNDLE_FILE")"
echo "   Incident ID:   $(jq -r '.incident_id' "$BUNDLE_FILE")"
echo "   Runbook ID:    $(jq -r '.runbook_id' "$BUNDLE_FILE")"
echo "   Timestamp:     $(jq -r '.timestamp_start' "$BUNDLE_FILE")"
echo "   HIPAA Controls: $(jq -r '.hipaa_controls | join(", ")' "$BUNDLE_FILE")"
echo "   Resolution:    $(jq -r '.outputs.resolution_status' "$BUNDLE_FILE")"
echo "   MTTR:          $(jq -r '.mttr_seconds' "$BUNDLE_FILE")s"

echo "----------------------------------------"
echo "✅ Evidence bundle verification complete"
```

---

## Emergency Procedures

### Evidence Pipeline Disaster Recovery

**See SOP-003: Disaster Recovery for full procedures**

**Quick Recovery Steps:**

1. **Restore WORM Storage Access:**
   - Evidence bundles are immutable in S3
   - No "restore" needed - just verify bucket accessibility
   - If bucket deleted: Contact AWS support (Object Lock prevents deletion)

2. **Restore Evidence Bundler Service:**
   ```bash
   # Redeploy from Terraform
   cd terraform/modules/evidence-pipeline/
   terraform apply

   # Or manual service restoration
   systemctl start evidence-bundler
   ```

3. **Restore Signing Keys:**
   - Keys should be backed up in secure vault (HashiCorp Vault, AWS Secrets Manager)
   - If keys lost: Generate new keys, document rotation in KEY_ROTATION_LOG.txt
   - Old bundles remain verifiable with historical public keys

4. **Restore Local Evidence Storage:**
   - Local evidence is temporary (7-day retention)
   - If lost: Re-download from S3 if needed
   - All critical evidence is in WORM storage

---

## Related Documents

- **SOP-001:** Daily Operations
- **SOP-002:** Incident Response
- **SOP-003:** Disaster Recovery
- **SOP-013:** Evidence Bundle Verification
- **OP-001:** MCP Server Operations
- **OP-003:** WORM Storage Management
- **OP-005:** Cryptographic Key Management

---

## Revision History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2025-10-31 | Initial version | Security Team |

---

**Document Status:** ✅ Active
**Next Review:** 2026-01-31 (Quarterly)
**Owner:** Security Officer
**Classification:** Internal Use Only
