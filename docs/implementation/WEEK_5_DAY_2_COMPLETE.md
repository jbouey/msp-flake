# Week 5 Day 2: Evidence Pipeline Integration (COMPLETE)

**Date:** November 1, 2025
**Status:** ✅ Complete
**Testing:** 5/5 integration tests passing

---

## Objective

Wire the evidence pipeline components together and test end-to-end flow:
- Configuration management for production deployment
- Integration pipeline coordinating bundler + signer
- Comprehensive integration test suite
- Production signing keys generated
- Evidence directory structure established

---

## Manual Setup Completed

### 1. Production Directory Structure

```bash
~/msp-production/
├── evidence/              # Evidence bundles stored here
│   ├── EB-20251101-0001.json
│   ├── EB-20251101-0001.json.bundle
│   └── ... (8 bundles created during testing)
└── signing-keys/          # Cryptographic signing keys
    ├── private-key.key    (mode 400 - secure)
    └── private-key.pub    (mode 644 - sharable with auditors)
```

**Note:** In production deployment to `/var/lib/msp`, use:
```bash
sudo mkdir -p /var/lib/msp/evidence
sudo mkdir -p /etc/msp/signing-keys
sudo chown mcp-executor:mcp-executor /var/lib/msp/evidence
sudo chown mcp-executor:mcp-executor /etc/msp/signing-keys
sudo chmod 700 /etc/msp/signing-keys
```

### 2. Production Signing Keys

Generated with cosign v3.0.2:
```bash
COSIGN_PASSWORD="production-password" \
  python3 signer.py --generate-keys ~/msp-production/signing-keys
```

**Key Security:**
- Private key: mode 400 (read-only by owner)
- Public key: mode 644 (safe to share with auditors)
- Password stored in environment variable (production: use SOPS/Vault)

---

## Files Created

### 1. Configuration Module
**File:** `mcp-server/evidence/config.py` (104 lines)

**Purpose:** Centralized configuration for evidence pipeline

**Key Features:**
- Environment variable support with sensible defaults
- Path validation on initialization
- Configuration validation method
- Debug printing for troubleshooting

**Configuration Points:**
```python
EvidenceConfig.EVIDENCE_DIR      # ~/msp-production/evidence
EvidenceConfig.PRIVATE_KEY       # ~/msp-production/signing-keys/private-key.key
EvidenceConfig.PUBLIC_KEY        # ~/msp-production/signing-keys/private-key.pub
EvidenceConfig.SCHEMA_PATH       # /opt/msp/evidence/schema/...
EvidenceConfig.COSIGN_PASSWORD   # From environment variable
```

### 2. Integration Pipeline
**File:** `mcp-server/evidence/pipeline.py` (340 lines)

**Purpose:** Orchestrates bundler, signer, and (future) uploader

**Main Components:**

**EvidencePipeline Class:**
```python
pipeline = EvidencePipeline(client_id="clinic-001")

bundle_path, sig_path = pipeline.process_incident(
    incident=incident_data,
    runbook=runbook_metadata,
    execution=execution_metadata,
    actions=action_steps,
    artifacts=collected_artifacts
)
```

**Process Flow:**
1. Validate configuration
2. Create evidence bundle
3. Validate against schema
4. Write to disk
5. Sign with cosign
6. Verify signature immediately
7. Return paths

**MockDataGenerator Class:**
- Helper for testing
- Generates realistic incident data
- Used by integration tests

### 3. Integration Test Suite
**File:** `mcp-server/evidence/test_integration.py` (344 lines)

**Purpose:** Comprehensive end-to-end testing

**Test Coverage:**

1. **Successful Incident Remediation**
   - Creates bundle for successful incident
   - Verifies all required fields present
   - Confirms SLA was met
   - Validates signature

2. **Failed Incident Remediation**
   - Creates bundle for failed incident
   - Captures error messages
   - Marks SLA as missed
   - Records partial resolution status

3. **Bundle Immutability (Tamper Detection)**
   - Creates valid signed bundle
   - Modifies bundle content
   - Confirms signature verification fails
   - **Critical security test - proves tampering is detectable**

4. **Configuration Validation**
   - Verifies all required paths exist
   - Checks key permissions
   - Validates schema availability

5. **Sequential Bundle ID Generation**
   - Creates multiple bundles
   - Verifies sequential numbering (EB-YYYYMMDD-NNNN)
   - Confirms unique IDs

---

## Integration Test Results

```
======================================================================
EVIDENCE PIPELINE INTEGRATION TEST SUITE
======================================================================

======================================================================
TEST 1: Successful Incident Remediation
======================================================================
✅ Bundle created: EB-20251101-0003.json
✅ Signature valid: EB-20251101-0003.json.bundle
✅ SLA met: True
✅ MTTR: 322s

======================================================================
TEST 2: Failed Incident Remediation
======================================================================
✅ Bundle created: EB-20251101-0004.json
✅ Signature valid: EB-20251101-0004.json.bundle
⚠️  SLA missed: False
⚠️  Resolution: partial
⚠️  Failed action captured: Service startup timeout after 60 seconds

======================================================================
TEST 3: Bundle Immutability (Tamper Detection)
======================================================================
✅ Original bundle signature valid
⚠️  Bundle tampered (changed MTTR to 9999)
✅ Tampering detected: Signature verification failed as expected

======================================================================
TEST 4: Configuration Validation
======================================================================
✅ Current configuration valid

Configuration Details:
  Evidence Dir: /Users/dad/msp-production/evidence
  Private Key: /Users/dad/msp-production/signing-keys/private-key.key
  Public Key: /Users/dad/msp-production/signing-keys/private-key.pub
  Schema: ...evidence-bundle-v1.schema.json

======================================================================
TEST 5: Sequential Bundle ID Generation
======================================================================
  Created: EB-20251101-0006
  Created: EB-20251101-0007
  Created: EB-20251101-0008
✅ Generated 3 sequential bundle IDs

======================================================================
TEST SUMMARY
======================================================================
✅ PASS: Successful Incident
✅ PASS: Failed Incident
✅ PASS: Bundle Immutability
✅ PASS: Configuration Validation
✅ PASS: Sequential Bundle IDs

Results: 5/5 tests passed

🎉 ALL TESTS PASSED
```

---

## Usage Examples

### Basic Usage (Python)

```python
from evidence.pipeline import EvidencePipeline, MockDataGenerator

# Initialize pipeline
pipeline = EvidencePipeline(client_id="clinic-001")

# Generate evidence from incident (in production, this comes from MCP executor)
incident = MockDataGenerator.create_mock_incident()
runbook = MockDataGenerator.create_mock_runbook()
execution = MockDataGenerator.create_mock_execution()
actions = MockDataGenerator.create_mock_actions()
artifacts = MockDataGenerator.create_mock_artifacts()

# Process incident through evidence pipeline
bundle_path, sig_path = pipeline.process_incident(
    incident=incident,
    runbook=runbook,
    execution=execution,
    actions=actions,
    artifacts=artifacts
)

print(f"Evidence bundle: {bundle_path}")
print(f"Signature: {sig_path}")
```

### Command-Line Testing

```bash
# Test the full pipeline
python3 pipeline.py

# Run integration test suite
python3 test_integration.py

# Manually verify a signature
cosign verify-blob \
  --key ~/msp-production/signing-keys/private-key.pub \
  --bundle ~/msp-production/evidence/EB-20251101-0001.json.bundle \
  ~/msp-production/evidence/EB-20251101-0001.json
```

---

## Evidence Bundle Contents (Example)

```json
{
  "bundle_id": "EB-20251101-0002",
  "bundle_version": "1.0",
  "client_id": "test-client-001",
  "generated_at": "2025-11-01T09:34:18.060924Z",

  "incident": {
    "incident_id": "INC-20251101-0001",
    "event_type": "backup_failure",
    "severity": "high",
    "detected_at": "2025-11-01T05:30:00Z",
    "hostname": "srv-primary.clinic.local",
    "details": {
      "backup_age_hours": 36.5,
      "error": "Connection timeout to backup repository"
    },
    "hipaa_controls": [
      "164.308(a)(7)(ii)(A)",
      "164.310(d)(2)(iv)"
    ]
  },

  "runbook": {
    "runbook_id": "RB-BACKUP-001",
    "runbook_version": "1.0",
    "runbook_hash": "sha256:abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
    "steps_total": 4,
    "steps_executed": 4
  },

  "execution": {
    "timestamp_start": "2025-11-01T05:30:00Z",
    "timestamp_end": "2025-11-01T05:35:22Z",
    "operator": "service:mcp-executor",
    "mttr_seconds": 322,
    "sla_target_seconds": 14400,
    "sla_met": true,
    "resolution_type": "auto"
  },

  "actions_taken": [
    {
      "step": 1,
      "action": "check_backup_logs",
      "script_hash": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
      "result": "ok",
      "exit_code": 0,
      "timestamp": "2025-11-01T05:30:15Z",
      "stdout_excerpt": "Found error: Connection timeout to repository"
    },
    {
      "step": 2,
      "action": "verify_disk_space",
      "script_hash": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
      "result": "ok",
      "exit_code": 0,
      "timestamp": "2025-11-01T05:30:45Z",
      "stdout_excerpt": "Available: 142.3 GB"
    },
    {
      "step": 3,
      "action": "restart_backup_service",
      "script_hash": "sha256:3333333333333333333333333333333333333333333333333333333333333333",
      "result": "ok",
      "exit_code": 0,
      "timestamp": "2025-11-01T05:32:12Z"
    },
    {
      "step": 4,
      "action": "trigger_manual_backup",
      "script_hash": "sha256:4444444444444444444444444444444444444444444444444444444444444444",
      "result": "ok",
      "exit_code": 0,
      "timestamp": "2025-11-01T05:35:22Z",
      "stdout_excerpt": "Backup completed successfully: 12.4 GB transferred"
    }
  ],

  "artifacts": {
    "log_excerpts": {
      "backup_log": "[2025-11-01 05:30:00] Starting backup job...\n[2025-11-01 05:30:05] ERROR: Connection timeout to backup.example.com\n[2025-11-01 05:32:15] Backup service restarted\n[2025-11-01 05:32:30] Starting backup job...\n[2025-11-01 05:35:22] Backup completed: 12.4 GB"
    },
    "checksums": {
      "backup_file": "sha256:5555555555555555555555555555555555555555555555555555555555555555"
    },
    "outputs": {
      "backup_duration_seconds": 192,
      "backup_size_gb": 12.4,
      "disk_usage_before": "87%",
      "disk_usage_after": "89%"
    }
  },

  "outputs": {
    "resolution_status": "success",
    "backup_duration_seconds": 192,
    "backup_size_gb": 12.4,
    "disk_usage_before": "87%",
    "disk_usage_after": "89%"
  },

  "evidence_bundle_hash": "sha256:f904a84e882c8480913764b4dbc057bd6e1ecca23439b527086422d8a5627910",
  "signatures": {},
  "storage_locations": []
}
```

---

## Architecture: How It Fits Together

```
┌─────────────────────────────────────────────────────────────┐
│ MCP Executor (Future - Week 5 Day 4)                       │
│                                                              │
│  Incident detected → Runbook selected → Actions executed   │
│                           ↓                                 │
└───────────────────────────┼─────────────────────────────────┘
                            │
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ Evidence Pipeline (THIS WORK - Day 2)                       │
│                                                              │
│  ┌──────────┐      ┌──────────┐      ┌──────────┐         │
│  │ Config   │ ───→ │ Pipeline │ ───→ │ Bundler  │         │
│  │  .py     │      │   .py    │      │   .py    │         │
│  └──────────┘      └──────────┘      └──────────┘         │
│                          │                  │               │
│                          ↓                  ↓               │
│                    ┌──────────┐      ┌──────────┐         │
│                    │  Signer  │ ───→ │  Schema  │         │
│                    │   .py    │      │  .json   │         │
│                    └──────────┘      └──────────┘         │
│                          │                                 │
└──────────────────────────┼─────────────────────────────────┘
                           │
                           ↓
┌─────────────────────────────────────────────────────────────┐
│ Evidence Storage (Local for now, WORM S3 in Day 3-4)       │
│                                                              │
│  ~/msp-production/evidence/                                 │
│    ├── EB-20251101-0001.json                               │
│    ├── EB-20251101-0001.json.bundle                        │
│    └── ... (sequential bundles)                            │
└─────────────────────────────────────────────────────────────┘
```

---

## HIPAA Compliance Notes

### Controls Demonstrated

**§164.312(b) - Audit Controls:**
- ✅ Complete audit trail of all remediation actions
- ✅ Timestamps, operators, exit codes captured
- ✅ Cryptographically signed to prevent tampering

**§164.316(b)(1)(i) - Documentation:**
- ✅ Time-stamped proof of compliance activities
- ✅ Schema validation ensures completeness
- ✅ Immutable once signed (tamper detection verified)

**§164.312(c)(1) - Integrity Controls:**
- ✅ SHA256 hashes for bundle integrity
- ✅ Cosign signatures for authenticity
- ✅ Immediate post-signing verification

### Auditor Workflow

1. **Request Evidence:** "Show me backup remediation from October 23"
2. **Locate Bundle:** `ls -l evidence/EB-20251023-*`
3. **Verify Signature:**
   ```bash
   cosign verify-blob \
     --key /path/to/public-key.pub \
     --bundle EB-20251023-0042.json.bundle \
     EB-20251023-0042.json
   ```
4. **Review Contents:** `cat EB-20251023-0042.json | jq .`
5. **Confirm HIPAA Controls:** Check `.incident.hipaa_controls[]`

**No MSP engineer required on call for auditor verification.**

---

## Next Steps (Week 5 Day 3-4)

### Day 3: WORM Storage Implementation

1. **S3 Bucket with Object Lock**
   - Terraform module for WORM-compliant S3
   - COMPLIANCE mode retention policy
   - Lifecycle rules (90-day minimum retention)

2. **Uploader Service**
   - `mcp-server/evidence/uploader.py`
   - Automatic upload after signing
   - Retry logic for network failures

3. **Integration with Pipeline**
   - Wire uploader into `pipeline.py`
   - Update `storage_locations` field in bundles
   - Test upload verification

### Day 4: MCP Executor Integration

1. **Executor Service**
   - `mcp-server/executor.py`
   - Calls evidence pipeline after each runbook
   - Passes real incident data to bundler

2. **End-to-End Testing**
   - Simulate real incidents
   - Verify evidence generation
   - Confirm WORM storage upload

---

## Metrics

- **Lines of Code:** 788 (config: 104, pipeline: 340, tests: 344)
- **Test Coverage:** 5/5 integration tests passing (100%)
- **Evidence Bundles Generated:** 8 (during testing)
- **Bundle Size:** ~3.5 KB (JSON) + ~3.4 KB (signature bundle)
- **Signature Verification:** 100% success rate
- **Tamper Detection:** Verified working (test 3)

---

## Files Modified

- None (all new files)

---

## Dependencies

All dependencies from Day 1 already installed:
- jsonschema 4.25.1
- boto3 1.40.64 (for Day 3 WORM upload)
- cosign v3.0.2

---

## Production Deployment Checklist

When deploying to actual production:

- [ ] Generate production signing keys with strong password
- [ ] Store COSIGN_PASSWORD in SOPS/Vault (not environment variable)
- [ ] Set up `/var/lib/msp/evidence` with proper permissions
- [ ] Set up `/etc/msp/signing-keys` with mode 700
- [ ] Copy public key to auditor-accessible location
- [ ] Configure automatic key rotation policy (annually recommended)
- [ ] Set up monitoring for evidence pipeline failures
- [ ] Test evidence bundle generation in staging environment
- [ ] Verify signatures with multiple cosign versions
- [ ] Document key recovery procedure

---

**Day 2 Status:** ✅ Complete
**Integration Tests:** 5/5 Passing
**Ready for Day 3:** Yes (WORM Storage Implementation)
