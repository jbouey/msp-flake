# Executive Dashboards & Audit-Ready Outputs

## Philosophy: Enforcement-First, Visuals Second

**Core Principle:** Dashboards expose automation, they don't replace it. Every red tile flows into real action via MCP remediation pipeline.

**What This Adds:**
- Thin collector + rules-as-code
- Small HTML/PDF outputs proving what happened
- Print-ready monthly compliance packets
- Auditor-acceptable GUI with evidence links

**What This Skips:**
- Heavy SaaS sprawl
- Big data lakes
- Expensive BI tools
- Dashboards without enforcement

## Minimal Architecture

### Collectors (Pull Only What Matters)

```python
class LocalStateCollector:
    async def collect_snapshot(self) -> dict:
        """Collect system state - metadata only, no PHI"""
        return {
            "metadata": {
                "client_id": self.client_id,
                "timestamp": timestamp.isoformat(),
            },
            "flake_state": await self._get_flake_state(),
            "patch_status": await self._get_patch_status(),
            "backup_status": await self._get_backup_status(),
            "service_health": await self._get_service_health(),
            "encryption_status": await self._get_encryption_status(),
            "time_sync": await self._get_time_sync_status()
        }
```

### Rules as Code

```yaml
rules:
  - id: endpoint_drift
    name: "Endpoint Configuration Drift"
    hipaa_controls: ["164.308(a)(1)(ii)(D)", "164.310(d)(1)"]
    severity: high
    check:
      type: flake_hash_equality
      target_hash: "{{baseline.target_flake_hash}}"
    auto_fix:
      enabled: true
      runbook_id: RB-DRIFT-001

  - id: patch_freshness
    name: "Critical Patch Timeliness"
    hipaa_controls: ["164.308(a)(5)(ii)(B)"]
    severity: critical
    check:
      type: patch_age
      max_age_days: 7
    auto_fix:
      enabled: true
      runbook_id: RB-PATCH-001

  - id: backup_success
    name: "Backup Success & Restore Testing"
    hipaa_controls: ["164.308(a)(7)(ii)(A)", "164.310(d)(2)(iv)"]
    severity: critical
    check:
      type: composite
      conditions:
        - backup_age: "<24h"
        - restore_test_age: "<30d"
    auto_fix:
      enabled: true
      runbook_id: RB-BACKUP-001

  - id: mfa_coverage
    name: "MFA Coverage"
    hipaa_controls: ["164.312(a)(2)(i)"]
    severity: high
    check:
      type: idp_mfa_coverage
      target: 100
      break_glass_max: 2
    auto_fix:
      enabled: false  # Manual approval

  - id: privileged_access
    name: "Privileged Access Review"
    hipaa_controls: ["164.308(a)(3)(ii)(B)"]
    check:
      type: approval_freshness
      max_age_days: 90

  - id: git_protections
    name: "Git Branch Protection"
    hipaa_controls: ["164.312(b)"]
    check:
      type: git_branch_protection
      requirements:
        - protected_main: true
        - min_reviewers: 2

  - id: secrets_hygiene
    name: "Secrets & Deploy Key Hygiene"
    check:
      type: deploy_key_audit
      max_age_days: 90

  - id: storage_posture
    name: "Object Storage ACL"
    hipaa_controls: ["164.310(d)(2)(iii)"]
    check:
      type: bucket_acl_audit
      allowed_public: []
```

## Evidence Packager (Nightly)

```python
class EvidencePackager:
    async def generate_nightly_packet(self, date: datetime = None):
        packet_id = f"EP-{date.strftime('%Y%m%d')}-{self.client_id}"

        # 1. Collect snapshots from last 24 hours
        snapshots = await self._collect_snapshots(date)

        # 2. Run compliance rules evaluation
        rule_results = await self._evaluate_rules(snapshots)

        # 3. Generate HTML posture report
        html_report = await self._generate_html_report(rule_results)

        # 4. Generate PDF from HTML
        await self._html_to_pdf(...)

        # 5. Create evidence ZIP
        await self._create_zip(evidence_files, zip_path)

        # 6. Sign the ZIP (cosign)
        signature_path = await self._sign_bundle(zip_path)

        # 7. Upload to WORM storage
        await self._upload_to_worm_storage(zip_path, signature_path)

        return manifest
```

## Weekly Executive Postcard

Auto-emailed Monday 8 AM:

```html
<div class="header">
    <h2>Weekly Compliance Update</h2>
    <p>Clinic ABC | Week of 2025-10-21</p>
</div>

<div class="metric">
    <strong>Drift Events Auto-Fixed:</strong>
    <span class="highlight">2</span>
    <span>(avg 3m resolution)</span>
</div>

<div class="metric">
    <strong>MFA Coverage:</strong>
    <span class="highlight">100%</span>
    <span>✓ Target maintained</span>
</div>

<div class="metric">
    <strong>Patch MTTR (Critical):</strong>
    <span class="highlight">18.2h</span>
    <span>✓ Within SLA</span>
</div>

<div class="metric">
    <strong>Backup Success Rate:</strong>
    <span class="highlight">100%</span>
    <span>(1 restore test completed)</span>
</div>

<div class="footer">
    <a href="{{dashboard_url}}">View Full Dashboard</a> |
    <a href="{{evidence_url}}">Download Evidence Bundle</a>
</div>
```

## Grafana Dashboard (Print-Friendly)

```json
{
  "dashboard": {
    "title": "HIPAA Compliance - Print View",
    "panels": [
      {
        "id": 1,
        "title": "Compliance Heatmap",
        "type": "table",
        "fieldConfig": {
          "overrides": [{
            "matcher": {"id": "byName", "options": "Status"},
            "properties": [{
              "id": "mappings",
              "value": [
                {"value": "pass", "text": "✅", "color": "green"},
                {"value": "warn", "text": "⚠️", "color": "orange"},
                {"value": "fail", "text": "❌", "color": "red"}
              ]
            }]
          }]
        }
      },
      {
        "id": 2,
        "title": "Backup SLO & Restore Tests",
        "type": "timeseries"
      },
      {
        "id": 3,
        "title": "Time Drift (±90s threshold)",
        "type": "gauge"
      },
      {
        "id": 4,
        "title": "Failed Login Attempts",
        "type": "bargauge"
      }
    ]
  }
}
```

## NixOS Module

```nix
{ config, lib, pkgs, ... }: {
  services.msp-reporting = {
    enable = true;

    collectors = {
      local_state = { enable = true; interval = "300s"; };
      idp = { enable = true; provider = "okta"; };
      git = { enable = true; provider = "github"; };
    };

    evidence_packager = {
      enable = true;
      schedule = "0 6 * * *";  # Daily 6 AM UTC
      retention_days = 90;
      signing_key = config.sops.secrets.evidence_signing_key.path;
    };

    dashboard = {
      enable = true;
      port = 3000;
      auth = "oauth2_proxy";
    };

    executive_postcard = {
      enable = true;
      schedule = "0 8 * * 1";  # Monday 8 AM
      recipients = ["admin@clinic.com"];
    };
  };
}
```

## Compliance Tiers

| Tier | Price | Features |
|------|-------|----------|
| Essential | $200-400/mo | Basic NTP, unsigned bundles, 30-day retention, monthly packets |
| Professional | $600-1200/mo | Multi-source time (NTP+GPS), signed bundles, 90-day retention, SBOM, weekly packets |
| Enterprise | $1500-3000/mo | NTP+GPS+Bitcoin time, blockchain-anchored, 2-year retention, daily packets, forensic mode |
