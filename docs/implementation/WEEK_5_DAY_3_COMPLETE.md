# Week 5 Day 3: WORM Storage Implementation (COMPLETE)

**Date:** November 1, 2025
**Status:** ✅ Complete
**Testing:** Manual setup required (AWS credentials)

---

## Objective

Implement AWS S3 with Object Lock for Write-Once-Read-Many (WORM) storage of evidence bundles, ensuring tamper-evident archival that meets HIPAA §164.310(d)(2)(iv) requirements.

---

## What Was Implemented

### 1. Evidence Uploader Service (`uploader.py`)

**File:** `mcp-server/evidence/uploader.py` (442 lines)

**Features:**
- Upload evidence bundles to S3 with Object Lock in COMPLIANCE mode
- Automatic retry logic for network failures (3 attempts, 5-second delay)
- SHA256 verification after upload
- Object Lock validation to confirm COMPLIANCE mode applied
- 90-day minimum retention period (HIPAA requirement)
- Date-based S3 key structure: `evidence/{client_id}/{year}/{month}/{bundle_id}.json`
- Download and list operations for bundle retrieval
- Metadata attached to each S3 object (upload timestamp, retention period, original path)

**Key Implementation:**
```python
class EvidenceUploader:
    def upload_bundle(
        self,
        bundle_path: Path,
        signature_path: Path,
        client_id: str
    ) -> Tuple[str, str]:
        # Generate S3 keys with date-based prefix
        # Format: evidence/{client_id}/{year}/{month}/{bundle_id}.json

        # Upload with Object Lock retention
        self.s3_client.put_object(
            Bucket=self.bucket_name,
            Key=s3_key,
            Body=file_path.read_bytes(),
            ObjectLockMode='COMPLIANCE',  # Cannot be removed by anyone
            ObjectLockRetainUntilDate=retention_until,
            ...
        )

        # Verify upload (SHA256 checksum)
        self._verify_upload(local_path, s3_key)

        # Verify Object Lock is applied
        self._verify_object_lock(s3_key)

        return bundle_uri, signature_uri
```

**HIPAA Controls Addressed:**
- §164.310(d)(2)(iv) - Data Backup and Storage
- §164.312(c)(1) - Integrity Controls

### 2. Terraform WORM Storage Module

**Directory:** `terraform/modules/worm-storage/`

**Files:**
- `main.tf` (244 lines) - Infrastructure as Code for WORM-compliant S3 bucket
- `README.md` (comprehensive setup and usage guide)

**Terraform Resources Created:**

1. **S3 Bucket with Object Lock**
   - `object_lock_enabled = true` (must be set at creation, cannot be added later)
   - Globally unique bucket name
   - Tagged for compliance tracking

2. **Public Access Block**
   - All public access blocked (HIPAA requirement)
   - `block_public_acls = true`
   - `block_public_policy = true`
   - `ignore_public_acls = true`
   - `restrict_public_buckets = true`

3. **Versioning**
   - Required for Object Lock
   - Provides additional protection against accidental deletion

4. **Server-Side Encryption**
   - AES-256 encryption at rest
   - Enabled by default for all objects

5. **Object Lock Configuration**
   - Default retention mode: **COMPLIANCE**
   - Default retention period: **90 days** (configurable)
   - COMPLIANCE mode cannot be overridden by anyone (including AWS root account)

6. **Lifecycle Policy**
   - Transition to Glacier storage class after 30 days (cost optimization)
   - Expire objects after retention period + 30-day grace period
   - Applied to `evidence/` prefix only

7. **Bucket Policy**
   - Enforce SSL/TLS for all requests
   - Deny all non-HTTPS traffic

8. **IAM Policy for Uploader Service**
   - Least-privilege policy
   - Allows: PutObject, PutObjectRetention, GetObject, GetObjectRetention, ListBucket
   - Scoped to `evidence/*` prefix only
   - Separate from other S3 access

**Terraform Variables:**
```hcl
variable "bucket_name" {
  # Must be globally unique, DNS-compatible
  validation: 3-63 characters, lowercase, no underscores
}

variable "retention_days" {
  # HIPAA recommends 90+ days
  default = 90
  validation: >= 90
}

variable "lifecycle_transition_days" {
  # Days before moving to Glacier
  default = 30
}
```

**Terraform Outputs:**
- `bucket_name` - Name of created bucket
- `bucket_arn` - ARN for IAM policies
- `bucket_region` - AWS region
- `uploader_policy_arn` - IAM policy for attaching to service credentials
- `object_lock_enabled` - Confirmation that Object Lock is enabled
- `retention_days` - Configured retention period

### 3. Evidence Pipeline Integration

**File:** `mcp-server/evidence/pipeline.py` (updated)

**Changes:**
1. **Optional Uploader Import**
   - Graceful fallback if uploader not available
   - Logs warning if WORM storage disabled

2. **Pipeline Constructor Enhancement**
   - New parameter: `enable_worm` (defaults to checking `MSP_WORM_BUCKET` env var)
   - Automatically initializes uploader if bucket configured
   - Validates bucket has Object Lock enabled on initialization

3. **Upload Step in process_incident**
   - Step 6 (after signing and verification)
   - Uploads bundle and signature to S3
   - Updates storage locations in evidence metadata
   - Non-fatal errors: logs warning but doesn't fail pipeline (evidence still stored locally)

**Configuration via Environment Variables:**
```bash
# Enable WORM storage
export MSP_WORM_BUCKET=msp-compliance-evidence-prod
export AWS_REGION=us-east-1

# AWS credentials (use IAM role in production)
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
```

---

## Manual Setup Required

Since this involves AWS infrastructure, manual setup is required before testing:

### Step 1: Deploy S3 Bucket with Terraform

```bash
cd /Users/dad/Documents/Msp_Flakes/terraform/modules/worm-storage

# Initialize Terraform
terraform init

# Plan (review what will be created)
terraform plan -var="bucket_name=msp-test-worm-$(date +%s)"

# Apply (create resources)
terraform apply -var="bucket_name=msp-test-worm-$(date +%s)"

# Save outputs
BUCKET_NAME=$(terraform output -raw bucket_name)
BUCKET_REGION=$(terraform output -raw bucket_region)
POLICY_ARN=$(terraform output -raw uploader_policy_arn)
```

### Step 2: Create IAM User for Uploader

```bash
# Create IAM user
aws iam create-user --user-name mcp-evidence-uploader-test

# Attach policy
aws iam attach-user-policy \
  --user-name mcp-evidence-uploader-test \
  --policy-arn $POLICY_ARN

# Create access keys
aws iam create-access-key --user-name mcp-evidence-uploader-test > /tmp/aws-credentials.json

# Extract credentials
AWS_ACCESS_KEY_ID=$(cat /tmp/aws-credentials.json | jq -r '.AccessKey.AccessKeyId')
AWS_SECRET_ACCESS_KEY=$(cat /tmp/aws-credentials.json | jq -r '.AccessKey.SecretAccessKey')
```

### Step 3: Configure Environment

```bash
# Set environment variables
export MSP_WORM_BUCKET=$BUCKET_NAME
export AWS_REGION=$BUCKET_REGION
export AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID
export AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY

# Verify configuration
aws sts get-caller-identity
aws s3api get-object-lock-configuration --bucket $MSP_WORM_BUCKET
```

### Step 4: Test Upload

```bash
cd /Users/dad/Documents/Msp_Flakes/mcp-server/evidence

# Test uploader directly
python3 uploader.py

# Test integrated pipeline with WORM storage
python3 pipeline.py
```

---

## Testing Without AWS Setup

If you don't want to set up AWS resources yet, the evidence pipeline still works:

```bash
cd /Users/dad/Documents/Msp_Flakes/mcp-server/evidence

# Run without WORM storage (local only)
unset MSP_WORM_BUCKET
python3 pipeline.py

# Output will show:
# "Evidence uploader not available - WORM storage disabled"
# Evidence bundles are still created, signed, and stored locally
```

---

## How It Works

### Upload Flow

1. **Local Evidence Generation**
   - Bundler creates evidence bundle JSON
   - Signer creates cryptographic signature with cosign
   - Both files stored locally in `~/msp-production/evidence/`

2. **WORM Storage Upload** (if enabled)
   - Pipeline checks if `self.uploader` is initialized
   - Uploader reads bundle and signature from local disk
   - Generates S3 key with date-based prefix: `evidence/{client_id}/{year}/{month}/{bundle_id}.json`
   - Uploads bundle to S3 with:
     - `ObjectLockMode='COMPLIANCE'`
     - `ObjectLockRetainUntilDate` = current_time + 90 days
     - AES-256 encryption
     - Metadata (upload timestamp, retention period, original path)
   - Uploads signature to S3 with same settings
   - Verifies upload (SHA256 checksum comparison)
   - Verifies Object Lock is applied (COMPLIANCE mode, retention period)

3. **Verification**
   - Downloads object from S3
   - Compares SHA256 hash with local file
   - Checks Object Lock retention metadata

4. **Post-Upload Lifecycle**
   - Day 0-30: Stored in S3 Standard storage class
   - Day 30+: Automatically transitioned to Glacier (cheaper storage)
   - Day 90 (retention expires): Object can be deleted (but not before)
   - Day 120: Object automatically expired (retention + 30-day grace period)

### Object Lock in COMPLIANCE Mode

**What COMPLIANCE Mode Means:**
- Once object is uploaded with COMPLIANCE retention, it **cannot be deleted** by anyone until retention expires
- Cannot be overridden by:
  - IAM users (even with full S3 permissions)
  - IAM roles
  - AWS account root user
  - AWS Support
- Cannot be shortened (retention period is minimum)
- Provides cryptographic proof of retention compliance

**Difference from GOVERNANCE Mode:**
- GOVERNANCE mode: Can be overridden by users with special permission (`s3:BypassGovernanceRetention`)
- COMPLIANCE mode: Cannot be overridden by anyone, no exceptions
- HIPAA auditors prefer COMPLIANCE mode for evidence retention

### S3 Key Structure

Evidence bundles are organized by date for efficient retrieval:

```
s3://msp-compliance-evidence-prod/
└── evidence/
    └── clinic-001/              # Client ID
        ├── 2025/                # Year
        │   ├── 10/              # Month
        │   │   ├── EB-20251015-0001.json
        │   │   ├── EB-20251015-0001.json.bundle
        │   │   ├── EB-20251015-0002.json
        │   │   └── EB-20251015-0002.json.bundle
        │   └── 11/
        │       ├── EB-20251101-0001.json
        │       └── EB-20251101-0001.json.bundle
        └── clinic-002/
            └── 2025/
                └── 10/
                    └── ...
```

**Benefits:**
- Easy date-range queries (list all bundles for October 2025)
- Client isolation (separate prefixes per client)
- Efficient S3 ListObjects operations
- Clear organization for auditors

---

## Cost Estimate

**Storage Costs (per GB/month):**
- S3 Standard (first 30 days): $0.023/GB
- S3 Glacier (after 30 days): $0.004/GB

**Example: 10 Clients, 10 GB Evidence per Month**
- Month 1: 10 GB × $0.023 = $0.23
- Month 2: 10 GB (new) × $0.023 + 10 GB (old) × $0.004 = $0.23 + $0.04 = $0.27
- Month 3: 10 GB (new) × $0.023 + 20 GB (old) × $0.004 = $0.23 + $0.08 = $0.31
- **Steady state (after 90 days)**: ~$0.93/month

**Additional Costs:**
- API Requests: PUT ($0.005 per 1,000 requests), GET ($0.0004 per 1,000 requests)
- Data Transfer: OUT to internet ($0.09/GB), within AWS (free)

**Total Estimated Cost for 10 Clients:**
- Storage: $0.93/month
- API: ~$0.05/month (uploading ~1,000 bundles)
- **Total: ~$1.00/month** (negligible compared to service revenue)

---

## HIPAA Compliance Documentation

### Controls Satisfied

| HIPAA Control | Requirement | Implementation |
|---------------|-------------|----------------|
| §164.310(d)(2)(iv) | Data Backup and Storage | S3 with 99.999999999% durability, 90-day retention |
| §164.312(c)(1) | Integrity Controls | Object Lock prevents tampering, cosign signatures |
| §164.312(a)(2)(iv) | Encryption and Decryption | AES-256 server-side encryption, TLS in transit |

### Auditor Evidence

When auditor requests proof of backup retention:

1. **Show Terraform Configuration**
   - `terraform/modules/worm-storage/main.tf`
   - Proves Object Lock in COMPLIANCE mode
   - Shows 90-day retention policy

2. **Show S3 Bucket Configuration**
   ```bash
   aws s3api get-object-lock-configuration --bucket $BUCKET_NAME
   ```
   Output confirms Object Lock enabled with COMPLIANCE mode

3. **Show Example Evidence Bundle**
   ```bash
   aws s3api get-object-retention --bucket $BUCKET_NAME --key evidence/clinic-001/2025/11/EB-20251101-0001.json
   ```
   Output shows retention expiration date

4. **Show IAM Policy**
   - `terraform/modules/worm-storage/main.tf` (aws_iam_policy resource)
   - Proves least-privilege access

5. **Show Upload Logs**
   - Evidence bundle metadata shows upload timestamp
   - CloudTrail logs (if enabled) show all S3 API calls

---

## Troubleshooting

### Error: "Bucket does not have Object Lock enabled"

**Cause:** Bucket was created without Object Lock, or trying to use existing bucket

**Solution:** Object Lock must be enabled at bucket creation. Destroy and recreate:
```bash
terraform destroy
terraform apply
```

### Error: "Access Denied" when uploading

**Cause:** IAM credentials don't have required permissions

**Solution:** Verify IAM policy is attached:
```bash
aws iam list-attached-user-policies --user-name mcp-evidence-uploader-test
aws iam get-policy-version --policy-arn $POLICY_ARN --version-id v1
```

### Error: "Upload verification failed: checksums don't match"

**Cause:** Network corruption during upload

**Solution:** Uploader automatically retries (max 3 attempts). If persistent, check network connection.

### Error: "Cannot delete object" (after retention expires)

**Cause:** This is expected behavior during retention period

**Solution:** Wait until retention expires. Check retention date:
```bash
aws s3api get-object-retention --bucket $BUCKET_NAME --key $KEY
```

### Warning: "WORM storage upload failed (non-fatal)"

**Cause:** S3 upload failed but local evidence is preserved

**Solution:** Evidence is still valid (stored locally and signed). Investigate S3 connectivity:
```bash
aws s3 ls s3://$MSP_WORM_BUCKET/
```

---

## Security Considerations

1. **Credentials Management**
   - DO NOT commit AWS credentials to Git
   - Use IAM roles in production (EC2 instance profile or ECS task role)
   - Rotate access keys regularly (90 days)
   - Store credentials in SOPS/Vault for production

2. **Least Privilege**
   - Uploader IAM policy only allows operations on `evidence/*` prefix
   - Cannot delete or modify objects (even without Object Lock)
   - Cannot access other buckets

3. **Audit Trail**
   - Enable CloudTrail for all S3 operations
   - Log to separate audit bucket (also with Object Lock)
   - Alert on suspicious operations (bulk deletions, policy changes)

4. **Encryption**
   - Server-side encryption (SSE-S3) enabled by default
   - For additional security, use SSE-KMS with customer-managed keys
   - TLS required for all uploads/downloads (enforced by bucket policy)

5. **Multi-Region Replication**
   - Consider S3 Cross-Region Replication for disaster recovery
   - Replicate to second AWS region
   - Apply same Object Lock settings to replica

---

## Production Deployment Checklist

When deploying WORM storage to production:

- [ ] Create production S3 bucket with unique name
- [ ] Apply Terraform configuration
- [ ] Create IAM role (not IAM user) for uploader service
- [ ] Attach uploader policy to IAM role
- [ ] Configure EC2 instance profile or ECS task role
- [ ] Set `MSP_WORM_BUCKET` environment variable on all MCP servers
- [ ] Set `AWS_REGION` environment variable
- [ ] Test upload with sample bundle
- [ ] Verify Object Lock is applied
- [ ] Enable CloudTrail logging for S3 operations
- [ ] Set up S3 event notifications (optional - for upload monitoring)
- [ ] Document bucket name and region in compliance documentation
- [ ] Update BAA to include AWS S3 as sub-processor
- [ ] Test restore procedure (download bundle, verify signature)
- [ ] Set up automated retention expiration alerts (optional)

---

## Next Steps (Week 5 Day 4-5)

### Day 4: MCP Executor Integration
- Create `mcp-server/executor.py` - Runbook execution service
- Wire evidence pipeline into actual incident response
- Pass real incident data (not mock data) to bundler
- Test with all 6 core runbooks

### Day 5: End-to-End Testing
- Simulate real incidents
- Verify evidence generation for each runbook
- Confirm WORM storage uploads
- Performance testing (can handle burst of incidents)
- Documentation updates

---

## Metrics

- **Lines of Code:** 686 (uploader: 442, terraform: 244)
- **Terraform Resources:** 8 (bucket, public access block, versioning, encryption, object lock, lifecycle, bucket policy, IAM policy)
- **Environment Variables:** 4 (MSP_WORM_BUCKET, AWS_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)
- **HIPAA Controls:** 3 (§164.310(d)(2)(iv), §164.312(c)(1), §164.312(a)(2)(iv))
- **Cost per Client:** ~$0.10/month (assuming 1 GB evidence per month)

---

## Files Created

**Evidence Uploader:**
- `mcp-server/evidence/uploader.py` (442 lines)

**Terraform Module:**
- `terraform/modules/worm-storage/main.tf` (244 lines)
- `terraform/modules/worm-storage/README.md` (comprehensive documentation)

**Updated Files:**
- `mcp-server/evidence/pipeline.py` (added WORM storage integration)

---

**Day 3 Status:** ✅ Complete
**Manual Setup Required:** Yes (AWS infrastructure deployment)
**Ready for Day 4:** Yes (evidence pipeline with optional WORM storage)

---

## Quick Start (Without AWS Setup)

If you want to test the evidence pipeline without setting up AWS:

```bash
cd /Users/dad/Documents/Msp_Flakes/mcp-server/evidence

# Ensure WORM storage is disabled
unset MSP_WORM_BUCKET

# Run pipeline test
python3 pipeline.py

# Output:
# WARNING: Evidence uploader not available - WORM storage disabled
# ✅ Evidence pipeline complete
# Bundle: ~/msp-production/evidence/EB-20251101-NNNN.json
# Signature: ~/msp-production/evidence/EB-20251101-NNNN.json.bundle
```

Evidence bundles are still created, validated, signed, and stored locally. WORM storage is optional enhancement for production deployments.
