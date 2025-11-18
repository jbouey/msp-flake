      - printer
      - medical_device  # May require manual config
    
    agent_deployment:
      linux_servers: ssh_bootstrap
      windows_servers: winrm_bootstrap
      network_gear: agentless_snmp
  
  security:
    stealth_mode: true
    rate_limit_packets_per_sec: 100
    respect_robots_txt: true
    
  hipaa:
    avoid_phi_bearing_ports: [3306, 5432, 1433, 1521]  # Don't scan DBs
    log_all_discoveries: true
    require_baa_before_enrollment: true
```

### Dashboard View

Add to your compliance packets:

```markdown
## Automated Device Discovery Report

### Discovery Summary (October 2025)
- Total devices discovered: 47
- Devices enrolled in monitoring: 32
- Devices excluded (out of scope): 12
- Devices pending manual approval: 3

### Enrolled Device Breakdown
| Tier | Device Type | Count | Monitoring Method |
|------|-------------|-------|-------------------|
| 1 | Linux Server | 8 | Agent (full) |
| 1 | Windows Server | 4 | Agent (full) |
| 1 | Network Infrastructure | 6 | SNMP |
| 1 | Firewall | 2 | Syslog + SNMP |
| 1 | VPN Gateway | 1 | Syslog |
| 2 | Database Server | 5 | Agent (database module) |
| 2 | Application Server | 4 | Agent (app module) |
| 2 | Web Server | 2 | Agent + WAF logs |

### Excluded Devices
| Device Type | Count | Exclusion Reason |
|-------------|-------|------------------|
| Windows Workstation | 8 | Endpoint device (out of scope) |
| Printer | 3 | Not compliance-critical |
| Unknown | 1 | Unable to classify |

### Pending Manual Approval
- 10.0.1.45 - Medical Device (PACS server) - Tier 3
- 10.0.1.62 - Medical Device (Modality) - Tier 3
- 192.168.1.88 - Unknown Server - Needs investigation

### Discovery Method Effectiveness
- Active nmap scan: 35 devices
- Passive ARP monitoring: 12 devices (11 duplicates)
- SNMP walk: 6 devices
- mDNS discovery: 4 devices (printers)
- Switch API query: 47 devices (authoritative)
```

---

## Executive Dashboards & Audit-Ready Outputs

### Philosophy: Enforcement-First, Visuals Second

**Core Principle:** Dashboards expose automation, they don't replace it. Every red tile flows into real action via your MCP remediation pipeline.

**What This Section Adds:**
- Thin collector + rules-as-code
- Small HTML/PDF outputs proving what happened
- Print-ready monthly compliance packets
- Auditor-acceptable GUI with evidence links

**What This Section Skips:**
- Heavy SaaS sprawl
- Big data lakes
- Expensive BI tools
- Dashboards without enforcement backing

### Minimal Architecture (Rides What You Already Have)

#### A. Collectors (Pull Only What Matters)

**Local State (Near-Zero Cost):**
```python
# collectors/local_state.py
import json
from datetime import datetime
from pathlib import Path

class LocalStateCollector:
    def __init__(self, client_id: str, snapshot_dir: str):
        self.client_id = client_id
        self.snapshot_dir = Path(snapshot_dir)
    
    async def collect_snapshot(self) -> dict:
        """
        Collect current system state for compliance evidence
        Returns metadata only - no PHI, no content
        """
        timestamp = datetime.utcnow()
        
        snapshot = {
            "metadata": {
                "client_id": self.client_id,
                "timestamp": timestamp.isoformat(),
                "collector_version": "1.0.0"
            },
            "flake_state": await self._get_flake_state(),
            "patch_status": await self._get_patch_status(),
            "backup_status": await self._get_backup_status(),
            "service_health": await self._get_service_health(),
            "encryption_status": await self._get_encryption_status(),
            "time_sync": await self._get_time_sync_status()
        }
        
        # Write to timestamped file
        snapshot_path = self.snapshot_dir / f"{timestamp.strftime('%Y-%m-%d')}" / f"{timestamp.strftime('%H')}"
        snapshot_path.mkdir(parents=True, exist_ok=True)
        
        snapshot_file = snapshot_path / f"{self.client_id}_snapshot.json"
        with open(snapshot_file, 'w') as f:
            json.dump(snapshot, f, indent=2)
        
        return snapshot
    
    async def _get_flake_state(self) -> dict:
        """Query current NixOS flake state"""
        result = await run_command("nix flake metadata --json")
        flake_metadata = json.loads(result.stdout)
        
        return {
            "flake_hash": flake_metadata.get("locked", {}).get("narHash"),
            "commit_sha": flake_metadata.get("locked", {}).get("rev"),
            "last_modified": flake_metadata.get("locked", {}).get("lastModified"),
            "derivation_ids": await self._get_active_derivations()
        }
    
    async def _get_patch_status(self) -> dict:
        """Query patch/vulnerability status"""
        return {
            "last_applied": "2025-10-23T02:00:00Z",
            "critical_pending": 0,
            "high_pending": 2,
            "medium_pending": 8,
            "last_scan": datetime.utcnow().isoformat(),
            "mttr_critical_hours": 4.2
        }
    
    async def _get_backup_status(self) -> dict:
        """Query backup job status"""
        return {
            "last_success": "2025-10-23T02:00:00Z",
            "last_failure": None,
            "last_restore_test": "2025-10-15T03:00:00Z",
            "backup_size_gb": 127.4,
            "retention_days": 90,
            "checksum": "sha256:a1b2c3..."
        }
    
    async def _get_service_health(self) -> dict:
        """Query critical service status"""
        services = ["nginx", "postgresql", "redis", "msp-watcher"]
        health = {}
        
        for service in services:
            result = await run_command(f"systemctl is-active {service}")
            health[service] = result.stdout.strip()
        
        return health
    
    async def _get_encryption_status(self) -> dict:
        """Check encryption configuration"""
        return {
            "luks_volumes": await self._check_luks_status(),
            "tls_certificates": await self._check_cert_expiry(),
            "at_rest_encryption": True,
            "in_transit_encryption": True
        }
    
    async def _get_time_sync_status(self) -> dict:
        """Check NTP sync status"""
        result = await run_command("timedatectl show --property=NTPSynchronized,TimeUSec")
        return {
            "ntp_synchronized": "yes" in result.stdout.lower(),
            "time_usec": result.stdout.split('\n')[1].split('=')[1],
            "max_drift_ms": 45  # Example
        }
```

**External State (Minimal SaaS Taps):**
```python
# collectors/external_state.py
import aiohttp

class ExternalStateCollector:
    async def collect_idp_state(self, idp_type: str, credentials: dict) -> dict:
        """
        Collect MFA coverage, privileged users from IdP
        Supports: Okta, Azure AD, Google Workspace
        """
        if idp_type == "okta":
            return await self._collect_okta(credentials)
        elif idp_type == "azure_ad":
            return await self._collect_azure_ad(credentials)
        # ... etc
    
    async def _collect_okta(self, credentials: dict) -> dict:
        """Query Okta for user MFA status"""
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"SSWS {credentials['api_token']}"}
            
            # Get all users
            async with session.get(
                f"{credentials['domain']}/api/v1/users",
                headers=headers
            ) as resp:
                users = await resp.json()
            
            # Check MFA factors
            mfa_users = 0
            privileged_users = []
            
            for user in users:
                if user.get('status') == 'ACTIVE':
                    factors = await self._get_user_factors(session, user['id'], headers)
                    if factors:
                        mfa_users += 1
                    
                    # Check if user is in privileged groups
                    if self._is_privileged(user):
                        privileged_users.append({
                            "user_id": user['id'],
                            "email": user['profile']['email'],
                            "has_mfa": len(factors) > 0,
                            "groups": user.get('groups', [])
                        })
            
            return {
                "total_users": len(users),
                "mfa_enabled_users": mfa_users,
                "mfa_coverage_pct": (mfa_users / len(users)) * 100,
                "privileged_users": privileged_users,
                "break_glass_accounts": 2  # Should be configured
            }
    
    async def collect_git_state(self, git_provider: str, credentials: dict) -> dict:
        """
        Collect branch protections, admin access, deploy keys
        Supports: GitHub, GitLab, Bitbucket
        """
        if git_provider == "github":
            return await self._collect_github(credentials)
        # ... etc
    
    async def _collect_github(self, credentials: dict) -> dict:
        """Query GitHub org/repo settings"""
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"token {credentials['token']}"}
            
            repos_data = []
            
            # Get org repos
            async with session.get(
                f"https://api.github.com/orgs/{credentials['org']}/repos",
                headers=headers
            ) as resp:
                repos = await resp.json()
            
            for repo in repos:
                # Check branch protection
                async with session.get(
                    f"https://api.github.com/repos/{credentials['org']}/{repo['name']}/branches/main/protection",
                    headers=headers
                ) as resp:
                    if resp.status == 200:
                        protection = await resp.json()
                        repos_data.append({
                            "name": repo['name'],
                            "protected": True,
                            "requires_reviews": protection.get('required_pull_request_reviews', {}).get('required_approving_review_count', 0) >= 2,
                            "has_codeowners": protection.get('required_pull_request_reviews', {}).get('require_code_owner_reviews', False)
                        })
            
            # Get deploy keys
            deploy_keys = []
            # ... query deploy keys
            
            return {
                "total_repos": len(repos),
                "protected_repos": len([r for r in repos_data if r['protected']]),
                "repos_with_codeowners": len([r for r in repos_data if r.get('has_codeowners')]),
                "deploy_keys": deploy_keys
            }
```

#### B. Rules as Code

```yaml
# rules/compliance_rules.yaml
rules:
  - id: endpoint_drift
    name: "Endpoint Configuration Drift"
    description: "All managed nodes run approved flake hash"
    hipaa_controls:
      - "164.308(a)(1)(ii)(D)"
      - "164.310(d)(1)"
    severity: high
    check:
      type: flake_hash_equality
      target_hash: "{{baseline.target_flake_hash}}"
      scope: all_nodes
    thresholds:
      fail: "any_mismatch"
      warn: "none"
    auto_fix:
      enabled: true
      action: reflake_rollout
      runbook_id: RB-DRIFT-001
    evidence_required:
      - node_list
      - current_hash_per_node
      - rollout_log_ids
    
  - id: patch_freshness
    name: "Critical Patch Timeliness"
    description: "Critical patches remediated within 7 days"
    hipaa_controls:
      - "164.308(a)(5)(ii)(B)"
    severity: critical
    check:
      type: patch_age
      severity: critical
      max_age_days: 7
    thresholds:
      fail: ">7_days"
      warn: ">5_days"
    auto_fix:
      enabled: true
      action: trigger_patch_job
      runbook_id: RB-PATCH-001
    evidence_required:
      - patch_job_logs
      - ticket_refs
      - mttr_hours
    
  - id: backup_success
    name: "Backup Success & Restore Testing"
    description: "Successful backup in last 24h, restore test within 30 days"
    hipaa_controls:
      - "164.308(a)(7)(ii)(A)"
      - "164.310(d)(2)(iv)"
    severity: critical
    check:
      type: composite
      conditions:
        - backup_age: "<24h"
        - restore_test_age: "<30d"
    thresholds:
      fail: "either_condition_fail"
      warn: "restore_test_age_>20d"
    auto_fix:
      enabled: true
      action: run_backup_and_schedule_test
      runbook_id: RB-BACKUP-001
    evidence_required:
      - backup_checksum
      - restore_transcript_hash
      - test_timestamp
    
  - id: mfa_coverage
    name: "MFA Coverage for Human Accounts"
    description: "100% MFA for human accounts; ≤2 break-glass accounts"
    hipaa_controls:
      - "164.312(a)(2)(i)"
      - "164.308(a)(4)(ii)(C)"
    severity: high
    check:
      type: idp_mfa_coverage
      target: 100
      break_glass_max: 2
    thresholds:
      fail: "<95%"
      warn: "<100%"
    auto_fix:
      enabled: false  # Manual approval required
      action: quarantine_non_mfa_users
      runbook_id: RB-MFA-001
    evidence_required:
      - user_mfa_status_csv
      - break_glass_account_list
    
  - id: privileged_access
    name: "Privileged Access Review"
    description: "Privileged users explicitly approved in last 90 days"
    hipaa_controls:
      - "164.308(a)(3)(ii)(B)"
      - "164.308(a)(4)(ii)(B)"
    severity: high
    check:
      type: approval_freshness
      max_age_days: 90
      approval_source: git_repo
    thresholds:
      fail: ">90_days"
      warn: ">75_days"
    auto_fix:
      enabled: false  # Requires manual approval
      action: notify_for_review
      runbook_id: RB-ACCESS-001
    evidence_required:
      - approval_yaml
      - approval_commit_hash
      - user_group_membership
    
  - id: git_protections
    name: "Git Branch Protection"
    description: "Protected main branches with CODEOWNERS and 2 reviewers"
    hipaa_controls:
      - "164.312(b)"
      - "164.308(a)(5)(ii)(D)"
    severity: medium
    check:
      type: git_branch_protection
      requirements:
        - protected_main: true
        - min_reviewers: 2
        - codeowners_required: true
    thresholds:
      fail: "any_requirement_not_met"
      warn: "none"
    auto_fix:
      enabled: true
      action: apply_branch_protection
      runbook_id: RB-GIT-001
    evidence_required:
      - repo_settings_json
      - protection_policy_hash
    
  - id: secrets_hygiene
    name: "Secrets & Deploy Key Hygiene"
    description: "No long-lived deploy keys with admin scope"
    hipaa_controls:
      - "164.312(a)(2)(i)"
      - "164.308(a)(4)(ii)(B)"
    severity: high
    check:
      type: deploy_key_audit
      max_age_days: 90
      disallowed_scopes: ["admin", "write:all"]
    thresholds:
      fail: "admin_scope_exists"
      warn: "age_>60_days"
    auto_fix:
      enabled: false  # Requires coordination
      action: rotate_deploy_keys
      runbook_id: RB-SECRETS-001
    evidence_required:
      - key_inventory_hash
      - rotation_pr_ids
    
  - id: storage_posture
    name: "Object Storage ACL Posture"
    description: "No public buckets unless explicitly allow-listed"
    hipaa_controls:
      - "164.310(d)(2)(iii)"
      - "164.312(a)(1)"
    severity: critical
    check:
      type: bucket_acl_audit
      allowed_public: []  # Empty = none allowed
    thresholds:
      fail: "any_public_bucket"
      warn: "none"
    auto_fix:
      enabled: true
      action: privatize_bucket
      runbook_id: RB-STORAGE-001
    evidence_required:
      - bucket_list
      - acl_before_after_diff

exceptions:
  - rule_id: privileged_access
    scope: ["admin@clinic.com"]
    reason: "Executive approval pending board meeting"
    owner: "security_team"
    risk: "low"
    expires: "2025-11-15"
```

#### C. Evidence Packager (Nightly)

```python
# evidence/packager.py
from datetime import datetime, timedelta
import json
import subprocess
from pathlib import Path
from typing import Dict, List

class EvidencePackager:
    def __init__(self, client_id: str, output_dir: str):
        self.client_id = client_id
        self.output_dir = Path(output_dir)
        
    async def generate_nightly_packet(self, date: datetime = None) -> str:
        """
        Generate comprehensive evidence packet
        Returns: Path to signed evidence bundle
        """
        if date is None:
            date = datetime.utcnow()
        
        packet_id = f"EP-{date.strftime('%Y%m%d')}-{self.client_id}"
        packet_dir = self.output_dir / packet_id
        packet_dir.mkdir(parents=True, exist_ok=True)
        
        # 1. Collect all snapshots from last 24 hours
        snapshots = await self._collect_snapshots(date)
        
        # 2. Run compliance rules evaluation
        rule_results = await self._evaluate_rules(snapshots)
        
        # 3. Generate HTML posture report (single page)
        html_report = await self._generate_html_report(rule_results)
        with open(packet_dir / "posture_report.html", 'w') as f:
            f.write(html_report)
        
        # 4. Generate PDF from HTML
        await self._html_to_pdf(
            packet_dir / "posture_report.html",
            packet_dir / "posture_report.pdf"
        )
        
        # 5. Create evidence ZIP
        evidence_files = [
            packet_dir / "posture_report.pdf",
            *self._get_snapshot_files(date),
            *self._get_log_excerpts(date)
        ]
        
        zip_path = packet_dir / f"{packet_id}_evidence.zip"
        await self._create_zip(evidence_files, zip_path)
        
        # 6. Sign the ZIP
        signature_path = await self._sign_bundle(zip_path)
        
        # 7. Upload to WORM storage
        await self._upload_to_worm_storage(zip_path, signature_path)
        
        # 8. Generate manifest
        manifest = {
            "packet_id": packet_id,
            "client_id": self.client_id,
            "generated_at": datetime.utcnow().isoformat(),
            "date_range": {
                "start": (date - timedelta(days=1)).isoformat(),
                "end": date.isoformat()
            },
            "rule_results": rule_results,
            "evidence_files": [str(f.name) for f in evidence_files],
            "zip_hash": await self._compute_hash(zip_path),
            "signature": signature_path.read_text(),
            "worm_storage_url": f"s3://compliance-worm/{self.client_id}/{date.year}/{date.month:02d}/{packet_id}_evidence.zip"
        }
        
        with open(packet_dir / "manifest.json", 'w') as f:
            json.dump(manifest, f, indent=2)
        
        return str(zip_path)
    
    async def _evaluate_rules(self, snapshots: List[dict]) -> Dict:
        """Run all compliance rules against collected snapshots"""
        from .rules_engine import RulesEngine
        
        engine = RulesEngine()
        results = {}
        
        for rule in engine.load_rules():
            result = await engine.evaluate_rule(rule, snapshots)
            results[rule['id']] = {
                "status": result['status'],  # pass/warn/fail
                "checked_at": datetime.utcnow().isoformat(),
                "scope": result['scope'],
                "evidence_refs": result['evidence_refs'],
                "auto_fix_triggered": result.get('auto_fix_triggered', False),
                "fix_job_id": result.get('fix_job_id'),
                "exception_applied": result.get('exception_applied', False)
            }
        
        return results
    
    async def _generate_html_report(self, rule_results: Dict) -> str:
        """Generate single-page HTML posture report"""
        
        html = f"""
<!DOCTYPE html>
<html>
<head>
    <title>Compliance Posture Report - {self.client_id}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 40px; }}
        .header {{ background: #f0f0f0; padding: 20px; margin-bottom: 20px; }}
        .kpi {{ display: inline-block; margin: 10px 20px; }}
        .kpi-value {{ font-size: 32px; font-weight: bold; }}
        .kpi-label {{ font-size: 14px; color: #666; }}
        .control-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; }}
        .control-tile {{ border: 2px solid #ddd; padding: 15px; border-radius: 5px; }}
        .status-pass {{ border-color: #4CAF50; background: #f1f8f4; }}
        .status-warn {{ border-color: #FF9800; background: #fff8f0; }}
        .status-fail {{ border-color: #f44336; background: #fff0f0; }}
        .control-title {{ font-weight: bold; margin-bottom: 10px; }}
        .control-detail {{ font-size: 12px; color: #666; }}
        .timestamp {{ text-align: right; font-size: 12px; color: #999; }}
        @media print {{
            .no-print {{ display: none; }}
            body {{ margin: 20px; }}
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>HIPAA Compliance Posture Report</h1>
        <p><strong>Client:</strong> {self.client_id}</p>
        <p><strong>Generated:</strong> {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}</p>
        <p style="color: #666; font-size: 12px;">
            <strong>Disclaimer:</strong> This report contains system metadata only. No PHI is processed or transmitted.
        </p>
    </div>
    
    <h2>Key Performance Indicators</h2>
    <div style="margin-bottom: 40px;">
        {self._generate_kpi_html(rule_results)}
    </div>
    
    <h2>Control Posture</h2>
    <div class="control-grid">
        {self._generate_control_tiles_html(rule_results)}
    </div>
    
    <div class="timestamp">
        Report ID: EP-{datetime.utcnow().strftime('%Y%m%d')}-{self.client_id} | 
        Signature: See evidence bundle
    </div>
</body>
</html>
"""
        return html
    
    def _generate_kpi_html(self, rule_results: Dict) -> str:
        """Generate KPI HTML blocks"""
        
        # Calculate KPIs
        total_rules = len(rule_results)
        passed_rules = len([r for r in rule_results.values() if r['status'] == 'pass'])
        compliance_pct = (passed_rules / total_rules * 100) if total_rules > 0 else 0
        
        # Get patch MTTR from results
        patch_mttr = rule_results.get('patch_freshness', {}).get('scope', {}).get('mttr_hours', 0)
        
        # Get MFA coverage
        mfa_coverage = rule_results.get('mfa_coverage', {}).get('scope', {}).get('coverage_pct', 0)
        
        kpis = [
            ("Compliance Score", f"{compliance_pct:.1f}%", "pass" if compliance_pct >= 95 else "warn"),
            ("Patch MTTR (Critical)", f"{patch_mttr:.1f}h", "pass" if patch_mttr < 24 else "warn"),
            ("MFA Coverage", f"{mfa_coverage:.1f}%", "pass" if mfa_coverage == 100 else "warn"),
            ("Controls Passing", f"{passed_rules}/{total_rules}", "pass")
        ]
        
        html = ""
        for label, value, status in kpis:
            color = "#4CAF50" if status == "pass" else "#FF9800"
            html += f"""
            <div class="kpi">
                <div class="kpi-value" style="color: {color};">{value}</div>
                <div class="kpi-label">{label}</div>
            </div>
            """
        
        return html
    
    def _generate_control_tiles_html(self, rule_results: Dict) -> str:
        """Generate control tile HTML"""
        
        html = ""
        for rule_id, result in rule_results.items():
            status = result['status']
            status_class = f"status-{status}"
            
            # Get rule metadata
            rule_meta = self._get_rule_metadata(rule_id)
            
            auto_fix_note = ""
            if result.get('auto_fix_triggered'):
                auto_fix_note = f"<div style='color: #4CAF50; font-size: 11px; margin-top: 5px;'>✓ Auto-fixed in {result.get('fix_duration_sec', 0)}s</div>"
            
            exception_note = ""
            if result.get('exception_applied'):
                exception_note = "<div style='color: #FF9800; font-size: 11px; margin-top: 5px;'>⚠ Exception active</div>"
            
            html += f"""
            <div class="control-tile {status_class}">
                <div class="control-title">{rule_meta['name']}</div>
                <div class="control-detail">
                    <strong>Status:</strong> {status.upper()}<br>
                    <strong>HIPAA:</strong> {', '.join(rule_meta['hipaa_controls'])}<br>
                    <strong>Scope:</strong> {result['scope'].get('summary', 'N/A')}
                </div>
                {auto_fix_note}
                {exception_note}
                <div style="font-size: 10px; color: #999; margin-top: 10px;">
                    Evidence: {', '.join(result['evidence_refs'][:2])}
                </div>
            </div>
            """
        
        return html
    
    async def _sign_bundle(self, zip_path: Path) -> Path:
        """Sign evidence bundle with cosign or GPG"""
        signature_path = zip_path.with_suffix('.sig')
        
        # Using cosign (preferred)
        subprocess.run([
            'cosign', 'sign-blob',
            '--key', '/path/to/signing-key',
            '--output-signature', str(signature_path),
            str(zip_path)
        ])
        
        return signature_path
```

#### D. Thin Dashboard (Static Site)

```typescript
// dashboard/pages/index.tsx
import { useState, useEffect } from 'react'

interface RuleResult {
  id: string
  name: string
  status: 'pass' | 'warn' | 'fail'
  hipaa_controls: string[]
  last_checked: string
  evidence_refs: string[]
  auto_fix_triggered?: boolean
  fix_job_id?: string
}

export default function ComplianceDashboard() {
  const [rules, setRules] = useState<RuleResult[]>([])
  const [kpis, setKpis] = useState({})
  
  useEffect(() => {
    // Fetch latest compliance snapshot
    fetch('/api/compliance/latest')
      .then(r => r.json())
      .then(data => {
        setRules(data.rules)
        setKpis(data.kpis)
      })
  }, [])
  
  return (
    <div className="dashboard">
      <header className="bg-gray-100 p-6 mb-8">
        <h1 className="text-3xl font-bold">HIPAA Compliance Dashboard</h1>
        <p className="text-gray-600">Last updated: {new Date().toLocaleString()}</p>
      </header>
      
      {/* KPI Section */}
      <section className="grid grid-cols-4 gap-4 mb-8">
        <KPI
          label="Compliance Score"
          value={kpis.compliance_pct}
          unit="%"
          status={kpis.compliance_pct >= 95 ? 'pass' : 'warn'}
        />
        <KPI
          label="Patch MTTR"
          value={kpis.patch_mttr_hours}
          unit="hrs"
          status={kpis.patch_mttr_hours < 24 ? 'pass' : 'warn'}
        />
        <KPI
          label="MFA Coverage"
          value={kpis.mfa_coverage_pct}
          unit="%"
          status={kpis.mfa_coverage_pct === 100 ? 'pass' : 'warn'}
        />
        <KPI
          label="Auto-Fixes (24h)"
          value={kpis.auto_fixes_24h}
          unit=""
          status="pass"
        />
      </section>
      
      {/* Control Tiles */}
      <section>
        <h2 className="text-2xl font-bold mb-4">Control Status</h2>
        <div className="grid grid-cols-3 gap-4">
          {rules.map(rule => (
            <ControlTile key={rule.id} rule={rule} />
          ))}
        </div>
      </section>
      
      {/* Evidence Bundle Link */}
      <section className="mt-8 p-4 bg-gray-50 rounded">
        <h3 className="font-bold mb-2">Latest Evidence Bundle</h3>
        <a 
          href="/evidence/latest" 
          className="text-blue-600 hover:underline"
          download
        >
          Download EP-{new Date().toISOString().split('T')[0].replace(/-/g, '')}-bundle.zip
        </a>
        <span className="text-gray-500 ml-4 text-sm">
          (Signed, auditor-ready)
        </span>
      </section>
    </div>
  )
}

function KPI({ label, value, unit, status }) {
  const colors = {
    pass: 'text-green-600',
    warn: 'text-orange-500',
    fail: 'text-red-600'
  }
  
  return (
    <div className="bg-white p-4 rounded shadow">
      <div className={`text-4xl font-bold ${colors[status]}`}>
        {value}{unit}
      </div>
      <div className="text-gray-600 text-sm mt-2">{label}</div>
    </div>
  )
}

function ControlTile({ rule }) {
  const statusColors = {
    pass: 'border-green-500 bg-green-50',
    warn: 'border-orange-500 bg-orange-50',
    fail: 'border-red-500 bg-red-50'
  }
  
  return (
    <div className={`border-2 rounded p-4 ${statusColors[rule.status]}`}>
      <div className="font-bold mb-2">{rule.name}</div>
      <div className="text-sm text-gray-600 mb-2">
        <strong>Status:</strong> {rule.status.toUpperCase()}
      </div>
      <div className="text-sm text-gray-600 mb-2">
        <strong>HIPAA:</strong> {rule.hipaa_controls.join(', ')}
      </div>
      
      {rule.auto_fix_triggered && (
        <div className="text-xs text-green-600 mt-2">
          ✓ Auto-fixed: <a href={`/jobs/${rule.fix_job_id}`} className="underline">Job {rule.fix_job_id}</a>
        </div>
      )}
      
      <div className="text-xs text-gray-400 mt-2">
        Evidence: {rule.evidence_refs.slice(0, 2).join(', ')}
      </div>
    </div>
  )
}
```

### Print-Ready Monthly Compliance Packet

#### Template Structure

```markdown
# Monthly HIPAA Compliance Packet

**Client:** {{client_name}}  
**Period:** {{month}} {{year}}  
**Baseline:** NixOS-HIPAA {{baseline_version}}  
**Generated:** {{timestamp}}

---

## Executive Summary

**PHI Disclaimer:** This report contains system metadata and operational metrics only. No Protected Health Information (PHI) is processed, stored, or transmitted by the compliance monitoring system.

