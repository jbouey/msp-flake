# WORM Storage Module

AWS S3 bucket with Object Lock in COMPLIANCE mode for immutable evidence storage.

## Features

- **Object Lock in COMPLIANCE mode**: Cannot be deleted or modified by anyone (including AWS root) until retention expires
- **90-day minimum retention**: HIPAA-compliant evidence retention period
- **Server-side encryption**: AES-256 encryption at rest
- **Public access blocked**: No public read/write access possible
- **Versioning enabled**: Required for Object Lock, provides additional protection
- **Lifecycle policies**: Automatic transition to Glacier after 30 days for cost optimization
- **TLS enforcement**: Denies all non-SSL/TLS requests
- **IAM policy for uploader**: Least-privilege policy for evidence upload service

## HIPAA Controls

| Control | Description | Implementation |
|---------|-------------|----------------|
| §164.310(d)(2)(iv) | Data Backup and Storage | S3 with 99.999999999% durability |
| §164.312(c)(1) | Integrity Controls | Object Lock prevents tampering |
| §164.312(a)(2)(iv) | Encryption and Decryption | AES-256 server-side encryption |

## Usage

### Basic Example

```hcl
module "evidence_storage" {
  source = "./modules/worm-storage"

  bucket_name    = "msp-compliance-evidence-prod"
  retention_days = 90

  tags = {
    Environment = "Production"
    Client      = "all"
  }
}
```

### With Custom Retention

```hcl
module "evidence_storage" {
  source = "./modules/worm-storage"

  bucket_name                = "msp-compliance-evidence-prod"
  retention_days             = 180  # 6 months
  lifecycle_transition_days  = 60   # Move to Glacier after 60 days

  tags = {
    Environment = "Production"
    Compliance  = "HIPAA"
  }
}
```

## Outputs

| Output | Description |
|--------|-------------|
| `bucket_name` | Name of the created bucket |
| `bucket_arn` | ARN of the bucket |
| `bucket_region` | AWS region |
| `uploader_policy_arn` | IAM policy ARN for uploader service |
| `object_lock_enabled` | Confirmation that Object Lock is enabled |
| `retention_days` | Default retention period |

## IAM Setup for Uploader Service

Attach the generated policy to your uploader service credentials:

```bash
# Get policy ARN from Terraform output
POLICY_ARN=$(terraform output -raw uploader_policy_arn)

# Create IAM user for uploader
aws iam create-user --user-name mcp-evidence-uploader

# Attach policy
aws iam attach-user-policy \
  --user-name mcp-evidence-uploader \
  --policy-arn $POLICY_ARN

# Create access keys
aws iam create-access-key --user-name mcp-evidence-uploader
```

## Testing

```bash
# Initialize Terraform
cd terraform/modules/worm-storage
terraform init

# Plan (review changes)
terraform plan -var="bucket_name=msp-test-worm-storage"

# Apply
terraform apply -var="bucket_name=msp-test-worm-storage"

# Test upload with Python uploader
export MSP_WORM_BUCKET=$(terraform output -raw bucket_name)
export AWS_REGION=$(terraform output -raw bucket_region)
python3 ../../../mcp-server/evidence/uploader.py
```

## Important Notes

1. **Object Lock Cannot Be Disabled**: Once enabled, Object Lock cannot be disabled. Plan carefully before creating production buckets.

2. **Bucket Name Must Be Globally Unique**: S3 bucket names must be unique across all AWS accounts worldwide.

3. **COMPLIANCE Mode Cannot Be Overridden**: Objects with COMPLIANCE mode retention cannot be deleted by anyone, including the AWS account root user, until retention expires.

4. **Cost Optimization**: Lifecycle policy transitions evidence to Glacier after 30 days, significantly reducing storage costs while maintaining immutability.

5. **Retention Period**: Default 90 days meets HIPAA requirements. Some organizations may require longer retention (e.g., 6 years for certain records).

## Cost Estimate

| Storage Class | Cost (per GB/month) | Typical Use Case |
|---------------|---------------------|------------------|
| S3 Standard | $0.023 | First 30 days |
| S3 Glacier | $0.004 | After 30 days |

**Example:** 100 GB evidence per month:
- Month 1: 100 GB × $0.023 = $2.30
- Month 2: 100 GB × $0.004 = $0.40
- Month 3: 100 GB × $0.004 = $0.40
- **Total for 90 days**: ~$3.10

## Troubleshooting

### Error: Object Lock cannot be enabled on existing bucket

**Solution:** Object Lock must be enabled at bucket creation. Destroy and recreate:

```bash
terraform destroy
terraform apply
```

### Error: Access Denied when uploading

**Solution:** Verify IAM policy is attached and credentials are configured:

```bash
aws sts get-caller-identity
aws s3api get-object-lock-configuration --bucket $BUCKET_NAME
```

### Error: Cannot delete object (even after retention expires)

**Solution:** This is expected behavior for COMPLIANCE mode during retention period. After retention expires, objects can be deleted normally.

## Security Considerations

- **Credentials Management**: Store AWS credentials in SOPS/Vault, never in code
- **Least Privilege**: Uploader service only has permissions for `evidence/` prefix
- **Audit Trail**: Enable CloudTrail to log all S3 operations
- **Multi-Region**: Consider replication to secondary region for disaster recovery

## Compliance Documentation

When providing evidence to auditors, include:

1. Terraform configuration (this module)
2. AWS S3 Object Lock configuration screenshot
3. Example evidence bundle with retention metadata
4. IAM policy showing least-privilege access
5. CloudTrail logs showing upload activity

## References

- [AWS S3 Object Lock Documentation](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock.html)
- [HIPAA Security Rule - Data Backup and Storage](https://www.hhs.gov/hipaa/for-professionals/security/laws-regulations/index.html)
- [NIST SP 800-66 - HIPAA Security Rule Implementation Guide](https://csrc.nist.gov/publications/detail/sp/800-66/rev-2/final)
