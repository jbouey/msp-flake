# Manual Operations Checklist

**Purpose:** Track all manual interventions required for MSP HIPAA Compliance Platform
**Owner:** Lead Operator
**Last Updated:** November 1, 2025
**Status:** Living document - update as procedures change

---

## Table of Contents

1. [Initial Deployment (One-Time)](#initial-deployment-one-time)
2. [Per-Client Onboarding](#per-client-onboarding)
3. [Weekly Operations](#weekly-operations)
4. [Monthly Operations](#monthly-operations)
5. [Quarterly Operations](#quarterly-operations)
6. [Annual Operations](#annual-operations)
7. [Emergency Procedures](#emergency-procedures)
8. [Audit Support](#audit-support)

---

## Initial Deployment (One-Time)

### System Setup

**Location:** Production server or VM

- [ ] Create base directories
  ```bash
  sudo mkdir -p /var/lib/msp/evidence
  sudo mkdir -p /etc/msp/signing-keys
  sudo mkdir -p /opt/msp/evidence/schema
  sudo mkdir -p /var/log/msp
  ```

- [ ] Set ownership and permissions
  ```bash
  sudo useradd -r -s /bin/false mcp-executor
  sudo chown -R mcp-executor:mcp-executor /var/lib/msp
  sudo chown -R mcp-executor:mcp-executor /etc/msp/signing-keys
  sudo chmod 700 /etc/msp/signing-keys
  sudo chown -R mcp-executor:mcp-executor /var/log/msp
  ```

- [ ] Install system dependencies
  ```bash
  # Install cosign
  brew install cosign  # macOS
  # OR
  sudo apt install cosign  # Debian/Ubuntu

  # Install Python dependencies
  pip3 install -r mcp-server/evidence/requirements.txt
  ```

### Signing Key Generation

**Location:** `/etc/msp/signing-keys/`
**Security:** Critical - these keys sign all evidence bundles

- [ ] Generate production signing keys
  ```bash
  # Generate strong password (store in password manager)
  export COSIGN_PASSWORD=$(openssl rand -base64 32)

  # Save password to SOPS-encrypted file
  echo "$COSIGN_PASSWORD" | sops -e /dev/stdin > /etc/msp/secrets/cosign-password.enc

  # Generate keys
  cosign generate-key-pair --output-key-prefix /etc/msp/signing-keys/private-key

  # Verify keys created
  ls -la /etc/msp/signing-keys/
  # Should see: private-key.key (mode 400) and private-key.pub (mode 644)
  ```

- [ ] Secure private key permissions
  ```bash
  sudo chmod 400 /etc/msp/signing-keys/private-key.key
  sudo chown mcp-executor:mcp-executor /etc/msp/signing-keys/private-key.key
  ```

- [ ] Backup public key to safe location
  ```bash
  # Copy to auditor-accessible location
  sudo cp /etc/msp/signing-keys/private-key.pub /var/www/compliance/public-signing-key.pub

  # Backup to secure storage (e.g., 1Password, encrypted USB)
  cp /etc/msp/signing-keys/private-key.pub ~/backups/msp-public-key-$(date +%Y%m%d).pub
  ```

- [ ] **CRITICAL:** Store private key password
  - Location 1: SOPS-encrypted file (`/etc/msp/secrets/cosign-password.enc`)
  - Location 2: Company password manager (1Password, LastPass, etc.)
  - Location 3: Printed copy in company safe (disaster recovery)

- [ ] Document key metadata
  ```bash
  # Create key info file
  cat > /etc/msp/signing-keys/key-info.txt <<EOF
  Generated: $(date)
  Operator: $(whoami)
  Valid From: $(date +%Y-%m-%d)
  Valid Until: $(date -d "+1 year" +%Y-%m-%d)
  Public Key Hash: $(sha256sum /etc/msp/signing-keys/private-key.pub | cut -d' ' -f1)
  EOF
  ```

### SOPS/Vault Setup

**Purpose:** Secure secrets management

- [ ] Install SOPS
  ```bash
  brew install sops  # macOS
  # OR
  wget https://github.com/mozilla/sops/releases/download/v3.7.3/sops-v3.7.3.linux
  sudo mv sops-v3.7.3.linux /usr/local/bin/sops
  sudo chmod +x /usr/local/bin/sops
  ```

- [ ] Configure SOPS with age or GPG
  ```bash
  # Option A: age (simpler)
  age-keygen -o ~/.config/sops/age/keys.txt

  # Option B: GPG (more common in enterprise)
  gpg --gen-key
  ```

- [ ] Create SOPS config
  ```yaml
  # .sops.yaml
  creation_rules:
    - path_regex: secrets/.*\.enc$
      age: age1ql3z7hjy54pw3hyww5ayyfg7zqgvc7w3j2elw8zmrj2kg5sfn9aqmcac8p
  ```

- [ ] Test encryption/decryption
  ```bash
  echo "test-secret" | sops -e /dev/stdin > test.enc
  sops -d test.enc
  rm test.enc
  ```

### Schema Deployment

- [ ] Copy JSON schema to standard location
  ```bash
  sudo cp opt/msp/evidence/schema/evidence-bundle-v1.schema.json \
          /opt/msp/evidence/schema/
  sudo chown mcp-executor:mcp-executor /opt/msp/evidence/schema/evidence-bundle-v1.schema.json
  ```

### Environment Variables

**Location:** `/etc/systemd/system/mcp-executor.service.d/override.conf`

- [ ] Create service override file
  ```ini
  [Service]
  Environment="MCP_EVIDENCE_DIR=/var/lib/msp/evidence"
  Environment="MCP_SIGNING_KEY_DIR=/etc/msp/signing-keys"
  Environment="MCP_SCHEMA_PATH=/opt/msp/evidence/schema/evidence-bundle-v1.schema.json"
  Environment="COSIGN_PASSWORD_FILE=/etc/msp/secrets/cosign-password.enc"
  ```

- [ ] Reload systemd
  ```bash
  sudo systemctl daemon-reload
  ```

### WORM Storage Setup (Optional - Production Recommended)

**Purpose:** Immutable evidence archival in AWS S3 with Object Lock
**HIPAA Control:** §164.310(d)(2)(iv) - Data Backup and Storage

- [ ] Deploy S3 bucket with Terraform
  ```bash
  cd terraform/modules/worm-storage
  terraform init

  # Generate unique bucket name
  BUCKET_NAME="msp-compliance-evidence-$(date +%s)"

  # Plan and review
  terraform plan -var="bucket_name=$BUCKET_NAME"

  # Apply
  terraform apply -var="bucket_name=$BUCKET_NAME"

  # Save outputs
  terraform output -json > /etc/msp/terraform-outputs.json
  ```

- [ ] Create IAM user for uploader
  ```bash
  # Get policy ARN from Terraform output
  POLICY_ARN=$(terraform output -raw uploader_policy_arn)

  # Create IAM user
  aws iam create-user --user-name mcp-evidence-uploader-prod

  # Attach policy
  aws iam attach-user-policy \
    --user-name mcp-evidence-uploader-prod \
    --policy-arn $POLICY_ARN

  # Create access keys
  aws iam create-access-key --user-name mcp-evidence-uploader-prod > /tmp/aws-creds.json

  # Extract credentials
  AWS_ACCESS_KEY_ID=$(cat /tmp/aws-creds.json | jq -r '.AccessKey.AccessKeyId')
  AWS_SECRET_ACCESS_KEY=$(cat /tmp/aws-creds.json | jq -r '.AccessKey.SecretAccessKey')
  ```

- [ ] Store AWS credentials securely
  ```bash
  # Encrypt with SOPS
  cat > /tmp/aws-credentials.yaml <<EOF
  aws_access_key_id: $AWS_ACCESS_KEY_ID
  aws_secret_access_key: $AWS_SECRET_ACCESS_KEY
  EOF

  sops -e /tmp/aws-credentials.yaml > /etc/msp/secrets/aws-credentials.enc
  rm /tmp/aws-credentials.yaml /tmp/aws-creds.json

  # Verify encryption
  sops -d /etc/msp/secrets/aws-credentials.enc
  ```

- [ ] Configure environment variables
  ```bash
  # Add to /etc/systemd/system/mcp-executor.service.d/override.conf
  [Service]
  Environment="MSP_WORM_BUCKET=<bucket-name-from-terraform>"
  Environment="AWS_REGION=us-east-1"
  Environment="AWS_SHARED_CREDENTIALS_FILE=/etc/msp/secrets/aws-credentials"

  # Reload systemd
  sudo systemctl daemon-reload
  ```

- [ ] Test WORM storage upload
  ```bash
  # Set environment variables
  export MSP_WORM_BUCKET=$(terraform output -raw bucket_name)
  export AWS_REGION=$(terraform output -raw bucket_region)
  export AWS_ACCESS_KEY_ID=$(sops -d /etc/msp/secrets/aws-credentials.enc | grep aws_access_key_id | cut -d: -f2 | tr -d ' ')
  export AWS_SECRET_ACCESS_KEY=$(sops -d /etc/msp/secrets/aws-credentials.enc | grep aws_secret_access_key | cut -d: -f2 | tr -d ' ')

  # Test uploader
  cd /opt/msp/mcp-server/evidence
  python3 uploader.py

  # Should output:
  # ✓ Bucket validated: <bucket-name> (Object Lock enabled)
  # ✓ Upload complete: s3://<bucket-name>/evidence/...
  ```

- [ ] Verify Object Lock applied
  ```bash
  BUNDLE_KEY=$(aws s3 ls s3://$MSP_WORM_BUCKET/evidence/ --recursive | tail -1 | awk '{print $4}')

  aws s3api get-object-retention \
    --bucket $MSP_WORM_BUCKET \
    --key $BUNDLE_KEY

  # Should show:
  # {
  #   "Retention": {
  #     "Mode": "COMPLIANCE",
  #     "RetainUntilDate": "..."
  #   }
  # }
  ```

- [ ] Enable CloudTrail (optional but recommended)
  ```bash
  # Create trail for S3 audit logging
  aws cloudtrail create-trail \
    --name msp-evidence-audit \
    --s3-bucket-name msp-cloudtrail-logs

  aws cloudtrail start-logging --name msp-evidence-audit
  ```

- [ ] Document WORM storage configuration
  ```bash
  cat > /etc/msp/worm-storage-info.txt <<EOF
  Bucket Name: $(terraform output -raw bucket_name)
  Bucket ARN: $(terraform output -raw bucket_arn)
  Region: $(terraform output -raw bucket_region)
  Retention Period: $(terraform output -raw retention_days) days
  Object Lock: COMPLIANCE mode
  Deployed: $(date)
  Operator: $(whoami)
  EOF
  ```

**Note:** If you skip WORM storage setup, evidence bundles are still created, signed, and stored locally. WORM storage adds immutable cloud backup but is not required for basic operation.

### Validation

- [ ] Test configuration
  ```bash
  cd /opt/msp/mcp-server/evidence
  python3 config.py
  # Should output: "✅ Configuration valid"
  ```

- [ ] Test evidence pipeline (without WORM)
  ```bash
  unset MSP_WORM_BUCKET
  python3 pipeline.py
  # Should create test bundle and signature
  # Warning: "WORM storage disabled" (expected if not configured)
  ```

- [ ] Test evidence pipeline (with WORM, if configured)
  ```bash
  export MSP_WORM_BUCKET=<your-bucket-name>
  python3 pipeline.py
  # Should create bundle, sign, and upload to S3
  # Log should show: "WORM storage upload complete"
  ```

- [ ] Test signature verification
  ```bash
  cosign verify-blob \
    --key /etc/msp/signing-keys/private-key.pub \
    --bundle /var/lib/msp/evidence/EB-*.json.bundle \
    /var/lib/msp/evidence/EB-*.json
  # Should output: "Verified OK"
  ```

---

## Per-Client Onboarding

### Pre-Onboarding Checklist

- [ ] Business Associate Agreement (BAA) signed
- [ ] Client ID assigned (format: `clinic-NNN`)
- [ ] Contact information documented
  - Primary contact (name, email, phone)
  - After-hours contact
  - Escalation contact
- [ ] Network access credentials received
  - VPN credentials (if applicable)
  - SSH keys or passwords
  - Firewall whitelist requirements

### Technical Setup

- [ ] Create client workspace directory
  ```bash
  sudo mkdir -p /var/lib/msp/clients/{client-id}/
  sudo chown mcp-executor:mcp-executor /var/lib/msp/clients/{client-id}/
  ```

- [ ] Generate client-specific API key
  ```bash
  export CLIENT_API_KEY=$(openssl rand -hex 32)
  echo "$CLIENT_API_KEY" | sops -e /dev/stdin > \
    /etc/msp/secrets/{client-id}-api-key.enc
  ```

- [ ] Create Terraform workspace
  ```bash
  cd terraform/clients/
  terraform workspace new {client-id}
  terraform workspace select {client-id}
  ```

- [ ] Configure client flake
  ```bash
  cp client-flake/template.nix client-flake/{client-id}.nix
  # Edit {client-id}.nix with client-specific settings
  ```

- [ ] Deploy client infrastructure
  ```bash
  terraform apply -var="client_id={client-id}"
  ```

- [ ] Test client connectivity
  ```bash
  # SSH to client system
  ssh mcp-executor@{client-hostname}

  # Verify watcher service running
  systemctl status msp-watcher

  # Check logs
  journalctl -u msp-watcher -n 50
  ```

### Client Documentation

- [ ] Create client-specific runbook
  - Document client network topology
  - List monitored systems
  - Note any exceptions to baseline
  - Record escalation procedures

- [ ] Add client to monitoring dashboard
  - Client name
  - Number of monitored systems
  - SLA targets
  - Evidence bundle retention period

- [ ] Share public signing key with client
  ```bash
  scp /etc/msp/signing-keys/private-key.pub \
      {client-contact}@{client-server}:/opt/compliance/
  ```

---

## Weekly Operations

### Evidence Bundle Review (Every Monday)

**Time Required:** 15-30 minutes per client
**Purpose:** Spot-check evidence quality and catch issues early

- [ ] List last week's evidence bundles
  ```bash
  ls -lh /var/lib/msp/evidence/EB-$(date -d "last monday" +%Y%m%d)-* \
                                EB-$(date +%Y%m%d)-*
  ```

- [ ] Verify bundle signatures (random sample)
  ```bash
  # Pick 3 random bundles
  for bundle in $(ls /var/lib/msp/evidence/EB-*.json | shuf -n 3); do
    echo "Verifying: $bundle"
    cosign verify-blob \
      --key /etc/msp/signing-keys/private-key.pub \
      --bundle ${bundle}.bundle \
      $bundle
  done
  ```

- [ ] Check for failed incidents
  ```bash
  # Find bundles with SLA misses or failed resolutions
  grep -l '"sla_met": false' /var/lib/msp/evidence/EB-*.json
  grep -l '"resolution_status": "failed"' /var/lib/msp/evidence/EB-*.json
  ```

- [ ] Review and document any patterns
  - Recurring incidents (same incident_id pattern)
  - Consistently missed SLAs
  - Failed remediations requiring manual intervention

### Backup Verification (Every Friday)

- [ ] Verify WORM storage uploads (Day 3+ implementation)
  ```bash
  # Check S3 bucket for recent uploads
  aws s3 ls s3://msp-compliance-worm/evidence/ --recursive | tail -20
  ```

- [ ] Test restore from WORM storage (monthly, see below)

- [ ] Check disk usage
  ```bash
  df -h /var/lib/msp/evidence
  # Alert if >80% full
  ```

### Log Review

- [ ] Check MCP executor logs for errors
  ```bash
  journalctl -u mcp-executor --since "1 week ago" | grep -i error
  ```

- [ ] Review evidence pipeline errors
  ```bash
  grep -i error /var/log/msp/evidence-pipeline.log | tail -50
  ```

---

## Monthly Operations

### Evidence Bundle Archive (First of Month)

**Time Required:** 30-60 minutes
**Purpose:** Generate monthly compliance packets for clients

- [ ] Generate monthly compliance packet
  ```bash
  # Run packager for each client (Day 5+ implementation)
  for client in $(ls /var/lib/msp/clients/); do
    python3 evidence/packager.py --client-id $client --month $(date -d "last month" +%Y-%m)
  done
  ```

- [ ] Review generated packets
  - Check PDF renders correctly
  - Verify all required sections present
  - Spot-check evidence bundle references

- [ ] Deliver to clients
  ```bash
  # Upload to client portal or send via secure email
  for client in $(ls /var/lib/msp/clients/); do
    scp /var/lib/msp/packets/${client}-$(date -d "last month" +%Y-%m).pdf \
        client-portal:/uploads/${client}/
  done
  ```

### Key Rotation Check (15th of Month)

- [ ] Check key age
  ```bash
  # Keys should be rotated annually
  key_created=$(stat -c %Y /etc/msp/signing-keys/private-key.key)
  now=$(date +%s)
  age_days=$(( ($now - $key_created) / 86400 ))
  echo "Signing key age: $age_days days"

  # Alert if >330 days (rotate at 365)
  if [ $age_days -gt 330 ]; then
    echo "⚠️  Key rotation needed soon"
  fi
  ```

### Backup Restore Test (Third Friday)

**Time Required:** 45-90 minutes
**Purpose:** Verify evidence bundles are restorable from WORM storage

- [ ] Select random evidence bundles from last month
  ```bash
  aws s3 ls s3://msp-compliance-worm/evidence/$(date -d "last month" +%Y-%m)/ \
    | shuf -n 5
  ```

- [ ] Download to temporary location
  ```bash
  mkdir -p /tmp/restore-test
  aws s3 cp s3://msp-compliance-worm/evidence/{bundle-id}.json /tmp/restore-test/
  aws s3 cp s3://msp-compliance-worm/evidence/{bundle-id}.json.bundle /tmp/restore-test/
  ```

- [ ] Verify signatures
  ```bash
  cd /tmp/restore-test
  for bundle in *.json; do
    cosign verify-blob \
      --key /etc/msp/signing-keys/private-key.pub \
      --bundle ${bundle}.bundle \
      $bundle
  done
  ```

- [ ] Validate bundle contents
  ```bash
  # Check schema compliance
  for bundle in *.json; do
    python3 -c "
import json
import jsonschema
with open('$bundle') as f:
    data = json.load(f)
with open('/opt/msp/evidence/schema/evidence-bundle-v1.schema.json') as f:
    schema = json.load(f)
jsonschema.validate(data, schema)
print('✅ $bundle valid')
"
  done
  ```

- [ ] Document test results
  ```bash
  # Log to operations journal
  echo "$(date): Monthly restore test - 5/5 bundles verified OK" >> \
    /var/log/msp/operations-journal.log
  ```

- [ ] Clean up
  ```bash
  rm -rf /tmp/restore-test
  ```

### System Health Check

- [ ] Disk usage trends
  ```bash
  # Check growth rate
  du -sh /var/lib/msp/evidence
  # Compare to last month's number
  ```

- [ ] Evidence bundle statistics
  ```bash
  # Count bundles per client
  for client in $(ls /var/lib/msp/clients/); do
    count=$(ls /var/lib/msp/evidence/ | grep -c "$client")
    echo "$client: $count bundles"
  done
  ```

- [ ] Failed incident review
  ```bash
  # Summary of failed incidents last month
  grep '"resolution_status": "failed"' /var/lib/msp/evidence/EB-*.json | wc -l
  ```

---

## Quarterly Operations

### Client Business Review (Every 3 Months)

**Time Required:** 1-2 hours per client
**Purpose:** Demonstrate value and identify improvement opportunities

- [ ] Generate quarterly report
  - Total incidents detected
  - Incidents auto-remediated
  - Average MTTR
  - SLA compliance percentage
  - Failed incidents requiring manual intervention

- [ ] Prepare client-facing presentation
  - Compliance posture summary
  - HIPAA controls coverage
  - Evidence bundle statistics
  - Recommendations for improvement

- [ ] Schedule review meeting with client
  - Primary contact
  - Technical lead
  - Compliance officer (if applicable)

### Baseline Review

- [ ] Review client baseline exceptions
  ```bash
  # Check expiring exceptions
  grep -A 3 "expires:" /var/lib/msp/clients/*/baseline-exceptions.yaml | \
    grep -B 3 "$(date -d "+30 days" +%Y-%m-%d)"
  ```

- [ ] Document any new exceptions required
  - Reason for exception
  - Risk assessment
  - Expiry date
  - Owner/approver

- [ ] Update baseline if needed
  ```bash
  # Edit baseline YAML
  vi /opt/msp/baseline/hipaa-v1.yaml

  # Bump version
  # Update CHANGELOG
  # Test with staging client
  ```

### Security Review

- [ ] Review signing key access logs
  ```bash
  sudo ausearch -k signing-key-access | tail -100
  ```

- [ ] Check for unauthorized access attempts
  ```bash
  journalctl -u mcp-executor --since "3 months ago" | grep -i unauthorized
  ```

- [ ] Review SOPS access logs
  ```bash
  # Check who decrypted secrets
  grep sops /var/log/auth.log | tail -50
  ```

---

## Annual Operations

### Signing Key Rotation (Anniversary of Initial Key Generation)

**Time Required:** 2-4 hours
**Purpose:** Limit blast radius if private key compromised

⚠️ **CRITICAL:** This procedure affects all evidence bundle verification

- [ ] **Pre-rotation checklist**
  - [ ] Notify all clients 30 days in advance
  - [ ] Schedule maintenance window (low-traffic period)
  - [ ] Backup all existing evidence bundles
  - [ ] Document current key metadata

- [ ] Generate new signing key pair
  ```bash
  # Generate new password
  export NEW_COSIGN_PASSWORD=$(openssl rand -base64 32)

  # Save to SOPS
  echo "$NEW_COSIGN_PASSWORD" | sops -e /dev/stdin > \
    /etc/msp/secrets/cosign-password-new.enc

  # Generate new keys
  cosign generate-key-pair \
    --output-key-prefix /etc/msp/signing-keys/private-key-new
  ```

- [ ] Archive old public key
  ```bash
  # Archive old key with validity period
  python3 signer.py archive-old-key \
    --old-key /etc/msp/signing-keys/private-key.pub \
    --archive-dir /etc/msp/signing-keys/archive/ \
    --validity-period "2024-11-01-to-2025-10-31"
  ```

- [ ] Activate new key
  ```bash
  # Rename old keys
  sudo mv /etc/msp/signing-keys/private-key.key \
          /etc/msp/signing-keys/private-key-old.key
  sudo mv /etc/msp/signing-keys/private-key.pub \
          /etc/msp/signing-keys/private-key-old.pub

  # Activate new keys
  sudo mv /etc/msp/signing-keys/private-key-new.key \
          /etc/msp/signing-keys/private-key.key
  sudo mv /etc/msp/signing-keys/private-key-new.pub \
          /etc/msp/signing-keys/private-key.pub

  # Set permissions
  sudo chmod 400 /etc/msp/signing-keys/private-key.key
  sudo chmod 644 /etc/msp/signing-keys/private-key.pub
  ```

- [ ] Update password in systemd service
  ```bash
  # Update service to use new password
  sudo systemctl edit mcp-executor
  # Change COSIGN_PASSWORD_FILE to cosign-password-new.enc

  sudo systemctl daemon-reload
  sudo systemctl restart mcp-executor
  ```

- [ ] Test new key
  ```bash
  # Generate test bundle
  python3 pipeline.py

  # Verify signature
  cosign verify-blob \
    --key /etc/msp/signing-keys/private-key.pub \
    --bundle /var/lib/msp/evidence/EB-*.json.bundle \
    /var/lib/msp/evidence/EB-*.json
  ```

- [ ] Distribute new public key to clients
  ```bash
  for client in $(ls /var/lib/msp/clients/); do
    # Send new public key
    scp /etc/msp/signing-keys/private-key.pub \
        {client-contact}@{client-server}:/opt/compliance/

    # Send archive of old key for historical verification
    scp /etc/msp/signing-keys/archive/private-key-*.pub \
        {client-contact}@{client-server}:/opt/compliance/archive/
  done
  ```

- [ ] **Post-rotation validation**
  - [ ] Verify old bundles still verify with archived old key
  - [ ] Verify new bundles verify with new key
  - [ ] Update documentation with key rotation date
  - [ ] Store old private key in secure offline storage (USB in safe)

- [ ] **NEVER DELETE OLD PRIVATE KEY** (needed to verify historical bundles)
  ```bash
  # Move old key to secure archive
  sudo mv /etc/msp/signing-keys/private-key-old.key \
          /mnt/secure-backup/signing-keys/private-key-2024-2025.key
  ```

### Compliance Audit Preparation

**Time Required:** 1-2 weeks
**Purpose:** Prepare for annual HIPAA audit

- [ ] Generate annual summary report
  - Total evidence bundles generated
  - Incident remediation statistics
  - SLA compliance trends
  - Failed incident analysis

- [ ] Prepare evidence bundle samples
  - One bundle per quarter per client
  - Include successful and failed incidents
  - Ensure all signatures verify

- [ ] Document exceptions and risks
  - All active baseline exceptions
  - Risk assessments
  - Mitigation plans

- [ ] Prepare system documentation
  - Architecture diagrams
  - Data flow diagrams
  - Access control matrices
  - Encryption inventory

- [ ] Test auditor verification workflow
  ```bash
  # Simulate auditor verifying a bundle
  cosign verify-blob \
    --key /var/www/compliance/public-signing-key.pub \
    --bundle /var/lib/msp/evidence/EB-20251015-0042.json.bundle \
    /var/lib/msp/evidence/EB-20251015-0042.json
  ```

---

## Emergency Procedures

### Private Key Compromise (CRITICAL)

**Trigger:** Suspected or confirmed unauthorized access to private signing key

⚠️ **This is a CRITICAL security incident**

1. [ ] **Immediate Actions (within 1 hour)**
   - [ ] Disable MCP executor service
     ```bash
     sudo systemctl stop mcp-executor
     ```
   - [ ] Revoke compromised key access
     ```bash
     sudo chmod 000 /etc/msp/signing-keys/private-key.key
     ```
   - [ ] Alert all clients (security incident notification)
   - [ ] Document incident timeline

2. [ ] **Investigation (within 24 hours)**
   - [ ] Review access logs
     ```bash
     sudo ausearch -f /etc/msp/signing-keys/private-key.key
     ```
   - [ ] Check for unauthorized evidence bundles
     ```bash
     # Look for bundles created outside normal business hours
     find /var/lib/msp/evidence -type f -name "EB-*.json" -mtime -7
     ```
   - [ ] Verify all recent bundle signatures
   - [ ] Document findings

3. [ ] **Key Rotation (within 48 hours)**
   - [ ] Follow annual key rotation procedure (see above)
   - [ ] Generate new keys immediately
   - [ ] Notify clients of new public key
   - [ ] Update all client systems

4. [ ] **Post-Incident**
   - [ ] Root cause analysis
   - [ ] Implement additional controls
   - [ ] Update incident response procedures
   - [ ] Client communication (resolution notification)

### Evidence Bundle Corruption

**Trigger:** Bundle fails signature verification

- [ ] Isolate corrupted bundle
  ```bash
  sudo mv /var/lib/msp/evidence/{bundle-id}.json \
          /var/lib/msp/quarantine/
  sudo mv /var/lib/msp/evidence/{bundle-id}.json.bundle \
          /var/lib/msp/quarantine/
  ```

- [ ] Attempt restoration from WORM storage
  ```bash
  aws s3 cp s3://msp-compliance-worm/evidence/{bundle-id}.json \
            /var/lib/msp/evidence/
  aws s3 cp s3://msp-compliance-worm/evidence/{bundle-id}.json.bundle \
            /var/lib/msp/evidence/
  ```

- [ ] Verify restored bundle
  ```bash
  cosign verify-blob \
    --key /etc/msp/signing-keys/private-key.pub \
    --bundle /var/lib/msp/evidence/{bundle-id}.json.bundle \
    /var/lib/msp/evidence/{bundle-id}.json
  ```

- [ ] Document incident
  - Corruption detected timestamp
  - Restoration source
  - Verification result
  - Root cause (if determined)

### WORM Storage Failure (Day 3+ implementation)

**Trigger:** Cannot upload evidence bundles to S3

- [ ] Check local disk space
  ```bash
  df -h /var/lib/msp/evidence
  # Ensure sufficient space for 48 hours of bundles
  ```

- [ ] Verify AWS credentials
  ```bash
  aws sts get-caller-identity
  ```

- [ ] Check S3 bucket accessibility
  ```bash
  aws s3 ls s3://msp-compliance-worm/
  ```

- [ ] Enable local retention extension
  ```bash
  # Keep bundles local until WORM restored
  # Update retention policy temporarily
  ```

- [ ] Alert monitoring system

- [ ] Once resolved, batch upload backlog
  ```bash
  # Upload all bundles created during outage
  for bundle in /var/lib/msp/evidence/EB-*.json; do
    aws s3 cp $bundle s3://msp-compliance-worm/evidence/
    aws s3 cp ${bundle}.bundle s3://msp-compliance-worm/evidence/
  done
  ```

---

## Audit Support

### Preparing for Auditor Access

**Timeline:** 2 weeks before audit

- [ ] Create auditor account (read-only)
  ```bash
  sudo useradd -r -s /bin/bash auditor
  sudo mkdir -p /home/auditor/.ssh
  # Add auditor's SSH public key
  ```

- [ ] Grant read-only access to evidence
  ```bash
  sudo setfacl -R -m u:auditor:rx /var/lib/msp/evidence
  ```

- [ ] Provide public signing key
  ```bash
  sudo cp /etc/msp/signing-keys/private-key.pub \
          /home/auditor/msp-public-signing-key.pub
  sudo chown auditor:auditor /home/auditor/msp-public-signing-key.pub
  ```

- [ ] Create auditor verification script
  ```bash
  cat > /home/auditor/verify-bundle.sh <<'EOF'
#!/bin/bash
# Usage: ./verify-bundle.sh EB-20251015-0042.json

BUNDLE=$1
PUBKEY=/home/auditor/msp-public-signing-key.pub

cosign verify-blob \
  --key $PUBKEY \
  --bundle /var/lib/msp/evidence/${BUNDLE}.bundle \
  /var/lib/msp/evidence/$BUNDLE

echo ""
echo "Bundle contents:"
cat /var/lib/msp/evidence/$BUNDLE | jq .
EOF

  sudo chmod +x /home/auditor/verify-bundle.sh
  sudo chown auditor:auditor /home/auditor/verify-bundle.sh
  ```

### Common Auditor Requests

**"Show me backup remediation from October 15"**

```bash
# Find bundles from that date with backup events
grep -l "backup" /var/lib/msp/evidence/EB-20251015-*.json

# Show details
cat /var/lib/msp/evidence/EB-20251015-0042.json | jq '.incident, .runbook, .execution'
```

**"Verify this evidence hasn't been tampered with"**

```bash
# Verify signature
cosign verify-blob \
  --key /etc/msp/signing-keys/private-key.pub \
  --bundle /var/lib/msp/evidence/EB-20251015-0042.json.bundle \
  /var/lib/msp/evidence/EB-20251015-0042.json

# Show bundle hash
cat /var/lib/msp/evidence/EB-20251015-0042.json | \
  jq -r '.evidence_bundle_hash'
```

**"What HIPAA controls does this address?"**

```bash
cat /var/lib/msp/evidence/EB-20251015-0042.json | \
  jq -r '.incident.hipaa_controls[]'
```

**"How long did remediation take?"**

```bash
cat /var/lib/msp/evidence/EB-20251015-0042.json | \
  jq -r '.execution | {mttr: .mttr_seconds, sla_met: .sla_met, sla_target: .sla_target_seconds}'
```

### Post-Audit Cleanup

- [ ] Revoke auditor access
  ```bash
  sudo userdel -r auditor
  ```

- [ ] Document audit findings
  - Any evidence bundles reviewed
  - Questions asked
  - Issues identified
  - Action items

---

## Automation Opportunities

**Items that should eventually be automated:**

1. Weekly evidence bundle signature verification (cron job)
2. Monthly compliance packet generation (scheduled task)
3. Disk usage monitoring and alerting (monitoring system)
4. Failed incident notifications (alert integration)
5. Backup restore testing (automated monthly test)
6. Key rotation reminders (calendar alerts at 11 months)
7. Client quarterly report generation (scheduled task)

---

## Documentation Maintenance

**This document should be updated when:**

- New clients are onboarded (update client count)
- Procedures change (update steps)
- New tools are added (update dependencies)
- Issues are discovered (update troubleshooting)
- Automation is implemented (remove manual steps)

**Review Schedule:**
- Lead Operator: Monthly review
- Team review: Quarterly
- Full audit: Annually

---

## Quick Reference Commands

```bash
# Verify configuration
python3 /opt/msp/mcp-server/evidence/config.py

# Test evidence pipeline
python3 /opt/msp/mcp-server/evidence/pipeline.py

# Verify a signature
cosign verify-blob \
  --key /etc/msp/signing-keys/private-key.pub \
  --bundle {bundle}.json.bundle \
  {bundle}.json

# Check signing key age
stat -c %Y /etc/msp/signing-keys/private-key.key | \
  awk '{print int((systime()-$1)/86400)" days old"}'

# Count evidence bundles
ls /var/lib/msp/evidence/EB-*.json | wc -l

# Check disk usage
df -h /var/lib/msp/evidence

# View recent logs
journalctl -u mcp-executor -n 100

# List clients
ls /var/lib/msp/clients/
```

---

**Last Updated:** November 1, 2025
**Next Review:** December 1, 2025
**Owner:** Lead Operator
**Version:** 1.0
