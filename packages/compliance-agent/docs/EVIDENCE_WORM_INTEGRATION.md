# Evidence Pipeline - WORM Storage Integration

**Date:** 2025-11-25
**Status:** Implemented
**HIPAA Controls:** §164.310(d)(2)(iv), §164.312(b), §164.312(c)(1)

---

## Overview

The compliance agent now supports automatic upload of evidence bundles to WORM (Write-Once-Read-Many) storage for immutable, tamper-proof evidence retention. This satisfies HIPAA requirements for audit controls and data integrity.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Compliance Agent                              │
│                                                                  │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐   │
│  │   Evidence   │───▶│  Local Store │───▶│  WORM Uploader  │   │
│  │  Generator   │    │  (Ed25519)   │    │  (S3 / MCP)     │   │
│  └──────────────┘    └──────────────┘    └────────┬─────────┘   │
│                                                   │             │
└───────────────────────────────────────────────────┼─────────────┘
                                                    │
                    ┌───────────────────────────────┴───────────────┐
                    │                                               │
         ┌──────────▼──────────┐              ┌────────────────────▼──┐
         │    Proxy Mode       │              │    Direct Mode        │
         │   (via MCP Server)  │              │   (S3 Direct)         │
         └──────────┬──────────┘              └────────────┬──────────┘
                    │                                      │
                    ▼                                      ▼
         ┌────────────────────┐              ┌─────────────────────────┐
         │    MCP Server      │              │  S3 Bucket              │
         │  /evidence/upload  │              │  (Object Lock enabled)  │
         └────────┬───────────┘              └─────────────────────────┘
                  │
                  ▼
         ┌────────────────────┐
         │  S3 WORM Storage   │
         │  (90+ day retain)  │
         └────────────────────┘
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `WORM_ENABLED` | `false` | Enable WORM storage upload |
| `WORM_MODE` | `proxy` | Upload mode: `proxy` or `direct` |
| `WORM_S3_BUCKET` | - | S3 bucket name (required for direct mode) |
| `WORM_S3_REGION` | `us-east-1` | AWS region |
| `WORM_RETENTION_DAYS` | `90` | Minimum retention period (HIPAA: 90+) |
| `WORM_AUTO_UPLOAD` | `true` | Auto-upload on evidence creation |
| `AWS_ACCESS_KEY_ID` | - | AWS credentials (direct mode) |
| `AWS_SECRET_ACCESS_KEY` | - | AWS credentials (direct mode) |

### NixOS Module Options

```nix
services.compliance-agent = {
  # ... existing options ...

  worm = {
    enable = true;
    mode = "proxy";  # or "direct"
    s3Bucket = "msp-compliance-worm";
    s3Region = "us-east-1";
    retentionDays = 90;
    autoUpload = true;
  };
};
```

## Upload Modes

### Proxy Mode (Recommended)

Upload through MCP server. Best for:
- Multi-tenant deployments
- Centralized credential management
- Audit consolidation

```
Agent → MCP Server → S3 WORM Storage
```

**Configuration:**
```bash
export WORM_ENABLED=true
export WORM_MODE=proxy
export MCP_URL=https://mcp.yourcompany.com
```

### Direct Mode

Upload directly to S3. Best for:
- Single-tenant deployments
- Network isolation requirements
- Offline operation with batch sync

```
Agent → S3 WORM Storage (direct)
```

**Configuration:**
```bash
export WORM_ENABLED=true
export WORM_MODE=direct
export WORM_S3_BUCKET=my-worm-bucket
export WORM_S3_REGION=us-east-1
export AWS_ACCESS_KEY_ID=AKIA...
export AWS_SECRET_ACCESS_KEY=...
```

## Usage

### Automatic Upload

When `WORM_AUTO_UPLOAD=true`, evidence is uploaded immediately after local storage:

```python
# Evidence is stored locally AND uploaded to WORM
bundle_path, sig_path, worm_uri = await generator.store_evidence(bundle)

# worm_uri contains S3 URI if upload succeeded
print(f"WORM URI: {worm_uri}")
# s3://msp-compliance-worm/evidence/client-001/2025/11/EB-20251125-0001.json
```

### Manual Sync

To catch up on pending uploads (e.g., after network outage):

```python
from compliance_agent.evidence import EvidenceGenerator

# Sync all pending bundles
result = await generator.sync_to_worm()

print(f"Uploaded: {result['uploaded']}")
print(f"Failed: {result['failed']}")
print(f"Pending: {result['pending']}")
```

### Check Upload Status

```python
# Get WORM stats
stats = generator.get_worm_stats()

print(f"Enabled: {stats['enabled']}")
print(f"Mode: {stats['mode']}")
print(f"Total uploaded: {stats['total_uploaded']}")
print(f"Pending: {stats['pending_count']}")
```

## S3 Bucket Requirements

The target S3 bucket must have:

1. **Object Lock enabled** (must be set at bucket creation)
2. **COMPLIANCE mode retention** (cannot be shortened)
3. **90+ day minimum retention** (HIPAA requirement)
4. **Server-side encryption** (AES-256)
5. **No public access** (block all public ACLs)

### Terraform Setup

Use the provided module:

```hcl
module "worm_storage" {
  source = "../../terraform/modules/worm-storage"

  bucket_name    = "msp-compliance-worm-${var.environment}"
  retention_days = 90

  tags = {
    Environment = var.environment
    Purpose     = "HIPAA-Compliance-Evidence"
  }
}
```

## Evidence Bundle Structure

Each bundle uploaded to WORM storage:

```
s3://bucket/evidence/{client_id}/{year}/{month}/
├── EB-20251125-0001.json       # Evidence bundle
├── EB-20251125-0001.sig        # Ed25519 signature
├── EB-20251125-0002.json
└── EB-20251125-0002.sig
```

## Upload Registry

The agent maintains a local registry of uploaded bundles:

```
/var/lib/msp-compliance-agent/evidence/.upload_registry.json
```

This tracks:
- Which bundles have been uploaded
- S3 URIs for verification
- Upload timestamps
- Retry history

## Error Handling

### Automatic Retry

Failed uploads are retried automatically:
- Default: 3 retries
- Delay: 5 seconds between retries
- Exponential backoff not implemented (constant delay)

### Manual Recovery

If uploads fail persistently:

```bash
# Check pending count
python -c "
from compliance_agent.worm_uploader import WormUploader, WormConfig
uploader = WormUploader(WormConfig(enabled=True), '/var/lib/msp-compliance-agent/evidence', 'client-001')
print(f'Pending: {uploader.get_pending_count()}')
"

# Force sync
python -c "
import asyncio
from compliance_agent.evidence import EvidenceGenerator
# ... initialize generator ...
result = asyncio.run(generator.sync_to_worm())
print(result)
"
```

## Monitoring

### Metrics to Track

1. **Upload success rate** - % of bundles successfully uploaded
2. **Pending queue depth** - Bundles waiting for upload
3. **Upload latency** - Time from creation to WORM storage
4. **Retry count** - Uploads requiring retries

### Alerts

Configure alerts for:
- Pending queue > 10 bundles
- Upload failures > 3 consecutive
- Any bundle older than 24h not uploaded

## HIPAA Compliance

This integration satisfies:

| Control | Requirement | How Addressed |
|---------|-------------|---------------|
| §164.310(d)(2)(iv) | Data Backup and Storage | Immutable S3 Object Lock storage |
| §164.312(b) | Audit Controls | Every action generates signed evidence |
| §164.312(c)(1) | Integrity Controls | Ed25519 signatures + S3 Object Lock |

## Testing

Run evidence pipeline tests:

```bash
cd packages/compliance-agent
python -m pytest tests/test_evidence.py -v
```

Test WORM-specific functionality:

```bash
python -m pytest tests/test_evidence.py -v -k worm
```

## Troubleshooting

### Upload Fails with "endpoint not configured"

**Cause:** Proxy mode selected but MCP_URL not set.

**Fix:**
```bash
export MCP_URL=https://mcp.yourcompany.com
```

### Upload Fails with "bucket not configured"

**Cause:** Direct mode selected but S3 bucket not set.

**Fix:**
```bash
export WORM_S3_BUCKET=your-bucket-name
```

### boto3 Not Found

**Cause:** Direct mode requires boto3 which may not be installed.

**Fix:**
```bash
pip install boto3
# or in Nix: add python3Packages.boto3 to dependencies
```

### Object Lock Error

**Cause:** S3 bucket doesn't have Object Lock enabled.

**Fix:** Object Lock must be enabled at bucket creation. Create a new bucket with the Terraform module.

---

**Related Documentation:**
- [Evidence Pipeline Architecture](../../docs/architecture/evidence-pipeline-detailed.md)
- [WORM Storage Terraform Module](../../terraform/modules/worm-storage/README.md)
- [SOP: Evidence Bundle Verification](../../docs/sop/SOP-013_EVIDENCE_BUNDLE_VERIFICATION.md)
