**Compliance Status:** {{compliance_pct}}% of controls passing  
**Critical Issues:** {{critical_issue_count}} ({{auto_fixed_count}} auto-fixed)  
**MTTR (Critical Patches):** {{mttr_hours}}h  
**Backup Success Rate:** {{backup_success_rate}}%  

**Key Highlights:**
- {{highlight_1}}
- {{highlight_2}}
- {{highlight_3}}

---

## Control Posture Heatmap

| Control | Description | Status | Evidence ID | Last Checked |
|---------|-------------|--------|-------------|--------------|
| 164.308(a)(1)(ii)(D) | Information System Activity Review | {{status_icon}} | {{evidence_id}} | {{timestamp}} |
| 164.308(a)(5)(ii)(B) | Protection from Malicious Software | {{status_icon}} | {{evidence_id}} | {{timestamp}} |
| 164.308(a)(7)(ii)(A) | Data Backup Plan | {{status_icon}} | {{evidence_id}} | {{timestamp}} |
| 164.310(d)(1) | Device and Media Controls | {{status_icon}} | {{evidence_id}} | {{timestamp}} |
| 164.310(d)(2)(iv) | Data Backup and Storage | {{status_icon}} | {{evidence_id}} | {{timestamp}} |
| 164.312(a)(1) | Access Control | {{status_icon}} | {{evidence_id}} | {{timestamp}} |
| 164.312(a)(2)(i) | Unique User Identification | {{status_icon}} | {{evidence_id}} | {{timestamp}} |
| 164.312(a)(2)(iv) | Encryption and Decryption | {{status_icon}} | {{evidence_id}} | {{timestamp}} |
| 164.312(b) | Audit Controls | {{status_icon}} | {{evidence_id}} | {{timestamp}} |
| 164.312(e)(1) | Transmission Security | {{status_icon}} | {{evidence_id}} | {{timestamp}} |
| 164.316(b)(1) | Policies and Procedures | {{status_icon}} | {{evidence_id}} | {{timestamp}} |

**Legend:** ✅ Pass | ⚠️ Warning (Exception/In Progress) | ❌ Fail

---

## Backups & Test-Restores

**Backup Schedule:** Daily at 02:00 UTC  
**Retention:** 90 days  
**Encryption:** AES-256-GCM (at rest)

| Week | Backup Status | Size (GB) | Checksum | Restore Test | Test Result |
|------|--------------|-----------|----------|--------------|-------------|
| Week 1 | ✅ Success | 127.4 | sha256:a1b2... | 2025-10-15 | ✅ Pass (3 files, 1 DB) |
| Week 2 | ✅ Success | 128.1 | sha256:c3d4... | 2025-10-22 | ✅ Pass (5 files) |
| Week 3 | ✅ Success | 129.3 | sha256:e5f6... | Not yet scheduled | - |
| Week 4 | ✅ Success | 130.8 | sha256:g7h8... | Not yet scheduled | - |

**Evidence:** `EB-BACKUP-2025-10.zip` (signed, 24.3 MB)

**HIPAA Control:** §164.308(a)(7)(ii)(A), §164.310(d)(2)(iv)

---

## Time Synchronization

**NTP Server:** {{ntp_server}}  
**Sync Status:** {{ntp_sync_status}}  
**Max Drift Observed:** {{max_drift_ms}}ms  
**Threshold:** ±90 seconds

| System | Drift (ms) | Status | Last Sync |
|--------|-----------|--------|-----------|
| srv-primary | +12 | ✅ | 2025-10-23 14:32 |
| srv-backup | -8 | ✅ | 2025-10-23 14:31 |
| srv-database | +45 | ✅ | 2025-10-23 14:30 |

**Evidence:** `EB-TIMESYNC-2025-10.json`

**HIPAA Control:** §164.312(b) (Audit controls require accurate timestamps)

---

## Access Controls

### Failed Login Attempts

**Total Failed Logins:** {{failed_login_count}}  
**Threshold:** >10 per user triggers alert  
**Actions Taken:** {{lockout_count}} accounts temporarily locked

| User | Failed Attempts | Action | Timestamp |
|------|----------------|--------|-----------|
| user123 | 6 | Monitored | 2025-10-15 09:23 |
| user456 | 12 | Auto-locked (15min) | 2025-10-18 14:45 |

### Dormant Accounts

**Definition:** No login in 90+ days  
**Found:** {{dormant_account_count}}  
**Action:** Flagged for review

### MFA Status

**Total Active Users:** {{total_users}}  
**MFA Enabled:** {{mfa_enabled_users}} ({{mfa_coverage_pct}}%)  
**Break-Glass Accounts:** {{break_glass_count}} (Target: ≤2)

**Evidence:** `EB-ACCESS-2025-10.csv` (user IDs redacted)

**HIPAA Control:** §164.312(a)(2)(i), §164.308(a)(3)(ii)(C)

---

## Patch & Vulnerability Posture

**Last Vulnerability Scan:** {{last_scan_date}}  
**Critical Patches Pending:** {{critical_pending}}  
**High Patches Pending:** {{high_pending}}  
**Medium Patches Pending:** {{medium_pending}}

### Patch Timeline (Critical)

| CVE | Discovered | Patched | MTTR |
|-----|-----------|---------|------|
| CVE-2025-1234 | 2025-10-15 | 2025-10-15 | 4.2h |
| CVE-2025-5678 | 2025-10-20 | 2025-10-21 | 18.7h |

**Average MTTR (Critical):** {{mttr_critical_hours}}h (Target: <24h)

**Evidence:** `EB-PATCH-2025-10.json`

**HIPAA Control:** §164.308(a)(5)(ii)(B)

---

## Encryption Status

### At-Rest Encryption

| Volume | Type | Status | Algorithm |
|--------|------|--------|-----------|
| /dev/sda2 | LUKS | ✅ Encrypted | AES-256-XTS |
| /dev/sdb1 | LUKS | ✅ Encrypted | AES-256-XTS |
| Backups | Object Storage | ✅ Encrypted | AES-256-GCM |

### In-Transit Encryption

| Service | Protocol | Certificate | Expiry |
|---------|----------|-------------|--------|
| Web Portal | TLS 1.3 | wildcard.clinic.com | 2026-03-15 |
| Database | TLS 1.2 | db.clinic.internal | 2026-01-20 |
| VPN | WireGuard | psk+pubkey | N/A (rotated) |

**Evidence:** `EB-ENCRYPTION-2025-10.json`

**HIPAA Control:** §164.312(a)(2)(iv), §164.312(e)(1)

---

## EHR/API Audit Trends (Metadata Only)

**Total API Calls:** {{total_api_calls}}  
**Failed Auth Attempts:** {{failed_auth}}  
**Bulk Exports:** {{bulk_export_count}} (all authorized)

| Action Type | Count | % of Total |
|-------------|-------|------------|
| patient.read | {{read_count}} | {{read_pct}}% |
| patient.write | {{write_count}} | {{write_pct}}% |
| admin.access | {{admin_count}} | {{admin_pct}}% |

**Note:** Counts only. No PHI processed.

**Evidence:** `EB-API-AUDIT-2025-10.json`

**HIPAA Control:** §164.312(b), §164.308(a)(1)(ii)(D)

---

## Incidents & Exceptions

### Incidents This Month

| Incident ID | Type | Severity | Auto-Fixed | Resolution Time |
|-------------|------|----------|------------|-----------------|
| INC-2025-10-001 | Backup Failure | High | Yes | 12 minutes |
| INC-2025-10-002 | Cert Expiring | Medium | Yes | 8 minutes |

### Active Baseline Exceptions

| Rule | Scope | Reason | Owner | Risk | Expires |
|------|-------|--------|-------|------|---------|
| privileged_access | admin@clinic.com | Board approval pending | Security Team | Low | 2025-11-15 |

**Evidence:** `exceptions.yaml` (commit hash: {{exception_commit}})

**HIPAA Control:** §164.308(a)(8) (Evaluation process)

---

## Attestations & Review

**System Administrator Attestation:**

I, {{admin_name}}, attest that:
- All automated remediation actions were reviewed
- Exceptions are approved and time-bounded
- Evidence bundles are complete and accurate
- No PHI was processed by compliance systems

**Signature:** _________________________  
**Date:** _________________________

**Security Officer Review:**

**Reviewed By:** _________________________  
**Date:** _________________________  
**Comments:** _________________________

---

## Evidence Bundle Manifest

**Bundle ID:** {{bundle_id}}  
**Generated:** {{timestamp}}  
**Signature:** `{{signature_hash}}`  
**WORM Storage URL:** `{{worm_url}}`

**Contents:**
- posture_report.pdf
- snapshots/ (24 daily snapshots)
- rule_results.json
- evidence_artifacts.zip
- manifest.json (signed)

**Verification:**
```bash
cosign verify-blob \
  --key /path/to/public-key \
  --signature {{signature_hash}} \
  {{bundle_id}}.zip
```

---

**End of Monthly Compliance Packet**  
**Next Review:** {{next_month}} 1st, {{next_year}}

**Questions:** Contact security@{{client_domain}}  
**Audit Support:** All evidence bundles available for 24 months
```

### Grafana Dashboards for Print-Friendly GUI

```yaml
# dashboards/hipaa-compliance-print.json
{
  "dashboard": {
    "title": "HIPAA Compliance - Print View",
    "tags": ["compliance", "hipaa", "print-ready"],
    "timezone": "utc",
    "schemaVersion": 38,
    "version": 1,
    "editable": false,
    "graphTooltip": 0,
    
    "panels": [
      {
        "id": 1,
        "title": "Compliance Heatmap",
        "type": "table",
        "gridPos": {"x": 0, "y": 0, "w": 24, "h": 12},
        "targets": [{
          "expr": "compliance_rule_status",
          "format": "table"
        }],
        "fieldConfig": {
          "overrides": [
            {
              "matcher": {"id": "byName", "options": "Status"},
              "properties": [{
                "id": "mappings",
                "value": [
                  {"value": "pass", "text": "✅", "color": "green"},
                  {"value": "warn", "text": "⚠️", "color": "orange"},
                  {"value": "fail", "text": "❌", "color": "red"}
                ]
              }]
            }
          ]
        },
        "options": {
          "showHeader": true,
          "cellHeight": "sm",
          "footer": {"show": false}
        }
      },
      
      {
        "id": 2,
        "title": "Backup SLO & Restore Tests",
        "type": "timeseries",
        "gridPos": {"x": 0, "y": 12, "w": 12, "h": 8},
        "targets": [{
          "expr": "backup_success_rate",
          "legendFormat": "Success Rate"
        }, {
          "expr": "restore_test_count",
          "legendFormat": "Restore Tests"
        }],
        "options": {
          "legend": {"displayMode": "table", "placement": "bottom"},
          "tooltip": {"mode": "multi"}
        }
      },
      
      {
        "id": 3,
        "title": "Time Drift (±90s threshold)",
        "type": "gauge",
        "gridPos": {"x": 12, "y": 12, "w": 12, "h": 8},
        "targets": [{
          "expr": "max(abs(ntp_drift_ms))"
        }],
        "fieldConfig": {
          "defaults": {
            "unit": "ms",
            "thresholds": {
              "mode": "absolute",
              "steps": [
                {"value": 0, "color": "green"},
                {"value": 70000, "color": "orange"},
                {"value": 90000, "color": "red"}
              ]
            }
          }
        }
      },
      
      {
        "id": 4,
        "title": "Failed Login Attempts (Last 30d)",
        "type": "bargauge",
        "gridPos": {"x": 0, "y": 20, "w": 12, "h": 8},
        "targets": [{
          "expr": "topk(10, sum by (user) (rate(failed_login_attempts[30d])))"
        }],
        "options": {
          "orientation": "horizontal",
          "displayMode": "gradient",
          "showUnfilled": true
        }
      },
      
      {
        "id": 5,
        "title": "Patch Posture - Critical Outstanding",
        "type": "stat",
        "gridPos": {"x": 12, "y": 20, "w": 6, "h": 4},
        "targets": [{
          "expr": "count(critical_patches_pending)"
        }],
        "fieldConfig": {
          "defaults": {
            "thresholds": {
              "mode": "absolute",
              "steps": [
                {"value": 0, "color": "green"},
                {"value": 1, "color": "red"}
              ]
            }
          }
        },
        "options": {
          "graphMode": "none",
          "textMode": "value_and_name"
        }
      },
      
      {
        "id": 6,
        "title": "Encryption Status",
        "type": "stat",
        "gridPos": {"x": 18, "y": 20, "w": 6, "h": 4},
        "targets": [{
          "expr": "count(encryption_enabled == 1)"
        }],
        "fieldConfig": {
          "defaults": {
            "mappings": [{
              "value": "{{total_volumes}}",
              "text": "All Volumes Encrypted"
            }]
          }
        }
      },
      
      {
        "id": 7,
        "title": "EHR/API Event Counts (Metadata Only)",
        "type": "piechart",
        "gridPos": {"x": 0, "y": 28, "w": 24, "h": 8},
        "targets": [{
          "expr": "sum by (action_type) (ehr_api_calls)"
        }],
        "options": {
          "legend": {"displayMode": "table", "placement": "right"},
          "pieType": "donut"
        }
      }
    ],
    
    "templating": {
      "list": [
        {
          "name": "client",
          "type": "query",
          "query": "label_values(client_id)",
          "current": {"value": "clinic-001"}
        }
      ]
    },
    
    "annotations": {
      "list": [
        {
          "datasource": "prometheus",
          "expr": "ALERTS{alertstate=\"firing\"}",
          "tagKeys": "alertname,severity",
          "titleFormat": "{{alertname}}",
          "textFormat": "{{description}}"
        }
      ]
    }
  }
}
```

### Weekly Executive Postcard (Auto-Email)

```python
# reporting/executive_postcard.py
from datetime import datetime, timedelta
from jinja2 import Template

POSTCARD_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <style>
        body { font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; }
        .header { background: #4CAF50; color: white; padding: 15px; text-align: center; }
        .metric { background: #f5f5f5; padding: 10px; margin: 10px 0; border-left: 4px solid #4CAF50; }
        .highlight { font-size: 24px; font-weight: bold; color: #4CAF50; }
        .footer { text-align: center; font-size: 12px; color: #666; margin-top: 20px; }
    </style>
</head>
<body>
    <div class="header">
        <h2>Weekly Compliance Update</h2>
        <p>{{ client_name }} | Week of {{ week_start }}</p>
    </div>
    
    <h3>🎯 Key Highlights</h3>
    
    <div class="metric">
        <strong>Drift Events Auto-Fixed:</strong>
        <span class="highlight">{{ drift_events }}</span>
        <span style="font-size: 12px; color: #666;">
            (avg {{ avg_fix_time }}m resolution time)
        </span>
    </div>
    
    <div class="metric">
        <strong>MFA Coverage:</strong>
        <span class="highlight">{{ mfa_coverage }}%</span>
        <span style="font-size: 12px; color: #666;">
            {% if mfa_coverage == 100 %}✓ Target maintained{% else %}⚠️ Below target{% endif %}
        </span>
    </div>
    
    <div class="metric">
        <strong>Patch MTTR (Critical):</strong>
        <span class="highlight">{{ patch_mttr }}h</span>
        <span style="font-size: 12px; color: #666;">
            {% if patch_mttr < 24 %}✓ Within SLA{% else %}⚠️ Exceeds 24h target{% endif %}
        </span>
    </div>
    
    <div class="metric">
        <strong>Backup Success Rate:</strong>
        <span class="highlight">{{ backup_success_rate }}%</span>
        <span style="font-size: 12px; color: #666;">
            ({{ restore_tests }} restore tests completed)
        </span>
    </div>
    
    {% if incidents_resolved > 0 %}
    <div class="metric">
        <strong>Security Posture Actions:</strong>
        <ul style="margin: 5px 0; padding-left: 20px;">
            {% for action in security_actions %}
            <li>{{ action }}</li>
            {% endfor %}
        </ul>
    </div>
    {% endif %}
    
    <div class="footer">
        <p>
            <a href="{{ dashboard_url }}">View Full Dashboard</a> |
            <a href="{{ evidence_url }}">Download Evidence Bundle</a>
        </p>
        <p style="font-size: 10px; color: #999;">
            This report contains system metadata only. No PHI processed.
        </p>
    </div>
</body>
</html>
"""

class ExecutivePostcard:
    async def generate_weekly_postcard(self, client_id: str) -> str:
        """Generate one-page executive summary email"""
        
        # Collect weekly metrics
        week_start = datetime.utcnow() - timedelta(days=7)
        metrics = await self._collect_weekly_metrics(client_id, week_start)
        
        template = Template(POSTCARD_TEMPLATE)
        html = template.render(**metrics)
        
        return html
    
    async def _collect_weekly_metrics(self, client_id: str, week_start: datetime) -> dict:
        """Aggregate key metrics from the past week"""
        
        return {
            "client_name": "Clinic ABC",
            "week_start": week_start.strftime("%Y-%m-%d"),
            "drift_events": 2,
            "avg_fix_time": 3,
            "mfa_coverage": 100,
            "patch_mttr": 18.2,
            "backup_success_rate": 100,
            "restore_tests": 1,
            "incidents_resolved": 3,
            "security_actions": [
                "2 public S3 buckets auto-privatized",
                "1 expiring certificate auto-renewed",
                "3 dormant accounts flagged for review"
            ],
            "dashboard_url": f"https://compliance.yourcompany.com/clients/{client_id}",
            "evidence_url": f"https://compliance.yourcompany.com/evidence/latest/{client_id}"
        }
```

### Deployment Configuration

```nix
# reporting/flake.nix
{
  description = "Compliance Reporting & Dashboard Services";
  
  outputs = { self, nixpkgs }: {
    nixosModules.reporting = { config, lib, pkgs, ... }: {
      services.msp-reporting = {
        enable = true;
        
        collectors = {
          local_state = {
            enable = true;
            interval = "300s";  # 5 minutes
          };
          idp = {
            enable = config.services.msp-reporting.idp.provider != null;
            provider = lib.mkOption {
              type = lib.types.enum ["okta" "azure_ad" "google"];
              default = null;
            };
          };
          git = {
            enable = config.services.msp-reporting.git.provider != null;
            provider = lib.mkOption {
              type = lib.types.enum ["github" "gitlab"];
              default = null;
            };
          };
        };
        
        evidence_packager = {
          enable = true;
          schedule = "0 6 * * *";  # Daily at 6 AM UTC
          retention_days = 90;
          signing_key = config.sops.secrets.evidence_signing_key.path;
        };
        
        dashboard = {
          enable = true;
          port = 3000;
          auth = "oauth2_proxy";  # Or whatever you use
        };
        
        executive_postcard = {
          enable = true;
          schedule = "0 8 * * 1";  # Monday 8 AM UTC
          recipients = ["admin@clinic.com"];
        };
      };
    };
  };
}
```

---

## Software Provenance & Time Framework

### Overview & Philosophy

**Core Principle:** Every action, every build, every log entry must be cryptographically provable as authentic and temporally ordered.

In healthcare compliance, you need to prove not just *what* happened, but *when* it happened, *who* did it, and that the evidence hasn't been tampered with. Traditional compliance systems rely on log aggregation and manual attestation. This framework makes tampering mathematically impossible.

**What This Section Adds:**
- Cryptographic signing of all builds, deployments, and evidence bundles
- Multi-source time synchronization with tamper-evident hash chains
- SBOM (Software Bill of Materials) generation for supply chain attestation
- Blockchain anchoring for Enterprise-tier immutability proof
- Tier-based feature system (Essential → Professional → Enterprise)
- Forensic-grade audit trails that would satisfy criminal investigations

**Business Value:**
- **Essential Tier:** Proves basic compliance (small clinics, $200-400/mo)
- **Professional Tier:** Adds advanced attestation (mid-size, $600-1200/mo)
- **Enterprise Tier:** Forensic-grade evidence (large practices, $1500-3000/mo)

### What NixOS Gives You Free

NixOS's content-addressed store already provides foundational provenance:

**Built-In Provenance Features:**
1. **Content Addressing:** Every package/derivation has a unique hash based on ALL inputs (source, dependencies, build scripts, compiler flags)
2. **Reproducible Builds:** Same inputs → identical binary → same hash (bit-for-bit reproducibility)
3. **Derivation Files:** Machine-readable record of how every artifact was built
4. **Closure Tracking:** Complete dependency graph from kernel to userspace

**What This Means:**
```bash
# Query what built a package
$ nix-store --query --deriver /nix/store/abc123-nginx-1.24.0

# Get complete dependency graph
$ nix-store --query --requisites /nix/store/abc123-nginx-1.24.0

# Verify integrity
$ nix-store --verify --check-contents /nix/store/abc123-nginx-1.24.0
```

**What's Missing (That This Framework Adds):**
- Cryptographic signatures proving WHO authorized the build
- SBOM export in industry-standard formats (SPDX, CycloneDX)
- Multi-source time attestation (not just system clock)
- Hash chain linking evidence bundles over time
- Blockchain anchoring for external verification

### Signing and Verification

#### Build Signing (Essential Tier)

Every NixOS derivation is signed by your build server:

```nix
# flake/modules/signing/build-signing.nix
{ config, lib, pkgs, ... }:

with lib;

let
  cfg = config.services.msp.buildSigning;

in {
  options.services.msp.buildSigning = {
    enable = mkEnableOption "MSP build signing";

    signingKey = mkOption {
      type = types.path;
      description = "Path to Nix signing key (via SOPS)";
      example = "/run/secrets/nix-signing-key";
    };

    publicKeys = mkOption {
      type = types.listOf types.str;
      description = "List of trusted public keys";
      example = ["cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY="];
    };
  };

  config = mkIf cfg.enable {
    nix.settings = {
      # Require signatures on all store paths
      require-sigs = true;

      # Trusted public keys (your build server + NixOS cache)
      trusted-public-keys = cfg.publicKeys;

      # Secret key for signing (only on build server)
      secret-key-files = mkIf (cfg.signingKey != null) [ cfg.signingKey ];
    };

    # Generate signing key on first boot (if not present)
    systemd.services.nix-signing-key-bootstrap = {
      description = "Generate Nix signing key";
      wantedBy = [ "multi-user.target" ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
      };

      script = ''
        if [ ! -f ${cfg.signingKey} ]; then
          echo "Generating new Nix signing key..."
          ${pkgs.nix}/bin/nix-store --generate-binary-cache-key \
            msp-build-server-1 \
            ${cfg.signingKey} \
            ${cfg.signingKey}.pub

          echo "Public key:"
          cat ${cfg.signingKey}.pub

          echo "Add this public key to all client configurations!"
        fi
      '';
    };

    # Automatically sign all locally-built paths
    nix.settings.post-build-hook = pkgs.writeShellScript "sign-build" ''
      set -euo pipefail

      export IFS=' '
      for path in $OUT_PATHS; do
        ${pkgs.nix}/bin/nix store sign \
          --key-file ${cfg.signingKey} \
          "$path"

        echo "Signed: $path"
      done
    '';
  };
}
```

**Usage on Build Server:**
```nix
# Build server configuration
{
  services.msp.buildSigning = {
    enable = true;
    signingKey = config.sops.secrets."nix-signing-key".path;
    publicKeys = [
      "cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY="
      "msp-build-server-1:YOUR_PUBLIC_KEY_HERE"
    ];
  };
}
```

**Usage on Client Machines:**
```nix
# Client configuration
{
  services.msp.buildSigning = {
    enable = true;
    signingKey = null;  # Clients don't sign, only verify
    publicKeys = [
      "cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY="
      "msp-build-server-1:YOUR_PUBLIC_KEY_HERE"
    ];
  };

  # Reject unsigned packages
  nix.settings.require-sigs = true;
}
```

#### Evidence Signing (Professional Tier)

Every evidence bundle is signed with cosign:

```python
# mcp-server/signing/evidence_signer.py
import subprocess
import json
from pathlib import Path
from datetime import datetime
from typing import Optional

class EvidenceSigner:
    """Sign evidence bundles with cosign for Professional/Enterprise tiers"""

    def __init__(self,
                 key_path: str = "/run/secrets/evidence-signing-key",
                 password_env: str = "COSIGN_PASSWORD"):
        self.key_path = key_path
        self.password_env = password_env

    def sign_bundle(self, bundle_path: Path) -> dict:
        """
        Sign evidence bundle and return signature metadata
        Uses cosign for container-style signing
        """
        sig_path = bundle_path.with_suffix('.sig')

        # Sign with cosign
        result = subprocess.run([
            'cosign', 'sign-blob',
            '--key', self.key_path,
            '--output-signature', str(sig_path),
            '--yes',  # Non-interactive
            str(bundle_path)
        ], capture_output=True, text=True, check=True)

        # Generate signature metadata
        metadata = {
            "bundle_path": str(bundle_path),
            "signature_path": str(sig_path),
            "signed_at": datetime.utcnow().isoformat(),
            "signer": "msp-evidence-signer",
            "algorithm": "ECDSA-P256-SHA256",
            "bundle_hash": self._compute_hash(bundle_path),
            "signature_hash": self._compute_hash(sig_path)
        }

        # Write metadata
        metadata_path = bundle_path.with_suffix('.sig.json')
        with open(metadata_path, 'w') as f:
            json.dump(metadata, f, indent=2)

        return metadata

    def verify_bundle(self, bundle_path: Path, public_key_path: str) -> bool:
        """Verify evidence bundle signature"""
        sig_path = bundle_path.with_suffix('.sig')

        try:
            subprocess.run([
                'cosign', 'verify-blob',
                '--key', public_key_path,
                '--signature', str(sig_path),
                str(bundle_path)
            ], check=True, capture_output=True)
            return True
        except subprocess.CalledProcessError:
            return False

    def _compute_hash(self, path: Path) -> str:
        """Compute SHA256 hash of file"""
        import hashlib
        with open(path, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()
```

**Integration with Evidence Packager:**
```python
# mcp-server/evidence/packager.py (updated)
async def generate_nightly_packet(self, date: datetime = None) -> str:
    # ... existing evidence collection ...

    # Create evidence ZIP
    zip_path = packet_dir / f"{packet_id}_evidence.zip"
    await self._create_zip(evidence_files, zip_path)

    # Sign the ZIP (Professional/Enterprise tier)
    if self.tier in ['professional', 'enterprise']:
        signer = EvidenceSigner()
        signature_metadata = signer.sign_bundle(zip_path)

        # Add signature to manifest
        manifest['signature'] = signature_metadata

    # Upload to WORM storage
    await self._upload_to_worm_storage(zip_path, signature_path)

    return str(zip_path)
```

### Evidence Registry

Append-only registry of all evidence bundles:

```python
# mcp-server/evidence/registry.py
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional

class EvidenceRegistry:
    """
    Append-only registry of all evidence bundles
    Cannot delete or modify entries (WORM pattern at DB level)
    """

    def __init__(self, db_path: str = "/var/lib/msp/evidence-registry.db"):
        self.db_path = Path(db_path)
        self._init_db()

    def _init_db(self):
        """Initialize database with WORM constraints"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.db_path)
