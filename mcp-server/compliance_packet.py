"""
Compliance Packet Generator

Generates monthly HIPAA compliance packets with:
- Executive summary
- Control posture heatmap
- Backup/restore verification
- Incident summary
- Evidence bundle manifest

Output: Print-ready PDF for auditors

HIPAA Controls:
- §164.316(b)(1): Documentation (policies and procedures)
- §164.316(b)(2)(i): Time limit (retain for 6 years)
"""

from typing import Dict, List, Optional
from datetime import datetime, timedelta
from pathlib import Path
import json
import yaml
from jinja2 import Template
import subprocess


class CompliancePacket:
    """Generate monthly HIPAA compliance packet"""

    def __init__(
        self,
        client_id: str,
        month: int,
        year: int,
        baseline_version: str = "1.0",
        evidence_dir: str = "./evidence"
    ):
        self.client_id = client_id
        self.month = month
        self.year = year
        self.baseline_version = baseline_version
        self.evidence_dir = Path(evidence_dir)

        self.packet_id = f"CP-{year}{month:02d}-{client_id}"

    async def generate_packet(self) -> str:
        """
        Generate complete compliance packet

        Returns: Path to generated PDF
        """

        print(f"Generating compliance packet: {self.packet_id}")

        # Collect data
        data = {
            "client_id": self.client_id,
            "client_name": self._get_client_name(),
            "month": datetime(self.year, self.month, 1).strftime("%B"),
            "year": self.year,
            "baseline_version": self.baseline_version,
            "generated_timestamp": datetime.utcnow().isoformat(),
            "packet_id": self.packet_id,

            # Executive summary metrics
            "compliance_pct": await self._calculate_compliance_score(),
            "critical_issue_count": await self._count_critical_issues(),
            "auto_fixed_count": await self._count_auto_fixes(),
            "mttr_hours": await self._calculate_mttr(),
            "backup_success_rate": await self._calculate_backup_success_rate(),

            # Control posture
            "controls": await self._get_control_posture(),

            # Backups
            "backup_summary": await self._get_backup_summary(),

            # Time sync
            "time_sync": await self._get_time_sync_status(),

            # Access controls
            "access_controls": await self._get_access_controls(),

            # Patches
            "patch_posture": await self._get_patch_posture(),

            # Encryption
            "encryption_status": await self._get_encryption_status(),

            # Incidents
            "incidents": await self._get_incidents(),

            # Exceptions
            "exceptions": await self._get_baseline_exceptions(),

            # Evidence manifest
            "evidence_bundles": await self._get_evidence_bundles()
        }

        # Generate markdown
        markdown = self._render_markdown(data)

        # Save markdown
        md_path = self.evidence_dir / f"{self.packet_id}.md"
        with open(md_path, 'w') as f:
            f.write(markdown)

        print(f"Markdown saved: {md_path}")

        # Convert to PDF
        pdf_path = await self._markdown_to_pdf(md_path)

        print(f"PDF generated: {pdf_path}")

        return str(pdf_path)

    def _get_client_name(self) -> str:
        """Get human-readable client name"""
        # TODO: Load from client database
        return "Clinic ABC"

    async def _calculate_compliance_score(self) -> float:
        """Calculate overall compliance percentage"""
        # TODO: Implement actual calculation from controls
        # For demo, return synthetic data
        return 98.5

    async def _count_critical_issues(self) -> int:
        """Count critical issues this month"""
        return 2

    async def _count_auto_fixes(self) -> int:
        """Count auto-fixed incidents"""
        return 8

    async def _calculate_mttr(self) -> float:
        """Calculate mean time to resolution (hours)"""
        return 4.2

    async def _calculate_backup_success_rate(self) -> float:
        """Calculate backup success rate"""
        return 100.0

    async def _get_control_posture(self) -> List[Dict]:
        """Get status of all HIPAA controls"""

        # Demo data - in production, load from monitoring system
        controls = [
            {
                "control": "164.308(a)(1)(ii)(D)",
                "description": "Information System Activity Review",
                "status": "✅ Pass",
                "evidence_id": "EB-2025110-001",
                "last_checked": "2025-11-01T10:00:00Z"
            },
            {
                "control": "164.308(a)(5)(ii)(B)",
                "description": "Protection from Malicious Software",
                "status": "✅ Pass",
                "evidence_id": "EB-2025110-002",
                "last_checked": "2025-11-01T10:00:00Z"
            },
            {
                "control": "164.308(a)(7)(ii)(A)",
                "description": "Data Backup Plan",
                "status": "✅ Pass",
                "evidence_id": "EB-2025110-003",
                "last_checked": "2025-11-01T10:00:00Z"
            },
            {
                "control": "164.310(d)(1)",
                "description": "Device and Media Controls",
                "status": "✅ Pass",
                "evidence_id": "EB-2025110-004",
                "last_checked": "2025-11-01T10:00:00Z"
            },
            {
                "control": "164.312(a)(1)",
                "description": "Access Control",
                "status": "✅ Pass",
                "evidence_id": "EB-2025110-005",
                "last_checked": "2025-11-01T10:00:00Z"
            },
            {
                "control": "164.312(a)(2)(iv)",
                "description": "Encryption and Decryption",
                "status": "✅ Pass",
                "evidence_id": "EB-2025110-006",
                "last_checked": "2025-11-01T10:00:00Z"
            },
            {
                "control": "164.312(b)",
                "description": "Audit Controls",
                "status": "✅ Pass",
                "evidence_id": "EB-2025110-007",
                "last_checked": "2025-11-01T10:00:00Z"
            },
            {
                "control": "164.312(e)(1)",
                "description": "Transmission Security",
                "status": "✅ Pass",
                "evidence_id": "EB-2025110-008",
                "last_checked": "2025-11-01T10:00:00Z"
            }
        ]

        return controls

    async def _get_backup_summary(self) -> Dict:
        """Get backup status for the month"""

        # Demo data
        return {
            "schedule": "Daily at 02:00 UTC",
            "retention_days": 90,
            "encryption": "AES-256-GCM",
            "weeks": [
                {
                    "week": "Week 1",
                    "status": "✅ Success",
                    "size_gb": 127.4,
                    "checksum": "sha256:a1b2c3...",
                    "restore_test_date": "2025-10-15",
                    "restore_test_result": "✅ Pass (3 files, 1 DB)"
                },
                {
                    "week": "Week 2",
                    "status": "✅ Success",
                    "size_gb": 128.1,
                    "checksum": "sha256:c3d4e5...",
                    "restore_test_date": "2025-10-22",
                    "restore_test_result": "✅ Pass (5 files)"
                },
                {
                    "week": "Week 3",
                    "status": "✅ Success",
                    "size_gb": 129.3,
                    "checksum": "sha256:e5f6g7...",
                    "restore_test_date": None,
                    "restore_test_result": "Not yet scheduled"
                },
                {
                    "week": "Week 4",
                    "status": "✅ Success",
                    "size_gb": 130.8,
                    "checksum": "sha256:g7h8i9...",
                    "restore_test_date": None,
                    "restore_test_result": "Not yet scheduled"
                }
            ]
        }

    async def _get_time_sync_status(self) -> Dict:
        """Get NTP sync status"""

        return {
            "ntp_server": "pool.ntp.org",
            "sync_status": "✅ Synchronized",
            "max_drift_ms": 45,
            "threshold_ms": 90000,
            "systems": [
                {
                    "hostname": "srv-primary",
                    "drift_ms": 12,
                    "status": "✅",
                    "last_sync": "2025-11-01 14:32:00"
                },
                {
                    "hostname": "srv-backup",
                    "drift_ms": -8,
                    "status": "✅",
                    "last_sync": "2025-11-01 14:31:00"
                }
            ]
        }

    async def _get_access_controls(self) -> Dict:
        """Get access control metrics"""

        return {
            "failed_logins": {
                "total": 12,
                "threshold": 10,
                "lockouts": 1
            },
            "dormant_accounts": {
                "count": 2,
                "definition": "No login in 90+ days"
            },
            "mfa": {
                "total_users": 24,
                "mfa_enabled": 24,
                "coverage_pct": 100.0,
                "break_glass_accounts": 2
            }
        }

    async def _get_patch_posture(self) -> Dict:
        """Get patch/vulnerability status"""

        return {
            "last_scan": "2025-10-30",
            "critical_pending": 0,
            "high_pending": 2,
            "medium_pending": 8,
            "recent_patches": [
                {
                    "cve": "CVE-2025-1234",
                    "discovered": "2025-10-15",
                    "patched": "2025-10-15",
                    "mttr_hours": 4.2
                },
                {
                    "cve": "CVE-2025-5678",
                    "discovered": "2025-10-20",
                    "patched": "2025-10-21",
                    "mttr_hours": 18.7
                }
            ]
        }

    async def _get_encryption_status(self) -> Dict:
        """Get encryption status"""

        return {
            "at_rest": [
                {
                    "volume": "/dev/sda2",
                    "type": "LUKS",
                    "status": "✅ Encrypted",
                    "algorithm": "AES-256-XTS"
                },
                {
                    "volume": "Backups",
                    "type": "Object Storage",
                    "status": "✅ Encrypted",
                    "algorithm": "AES-256-GCM"
                }
            ],
            "in_transit": [
                {
                    "service": "Web Portal",
                    "protocol": "TLS 1.3",
                    "certificate": "wildcard.clinic.com",
                    "expiry": "2026-03-15"
                },
                {
                    "service": "VPN",
                    "protocol": "WireGuard",
                    "certificate": "psk+pubkey",
                    "expiry": "N/A (rotated)"
                }
            ]
        }

    async def _get_incidents(self) -> List[Dict]:
        """Get incidents for the month"""

        return [
            {
                "incident_id": "INC-2025-10-001",
                "type": "Backup Failure",
                "severity": "High",
                "auto_fixed": True,
                "resolution_minutes": 12
            },
            {
                "incident_id": "INC-2025-10-002",
                "type": "Cert Expiring",
                "severity": "Medium",
                "auto_fixed": True,
                "resolution_minutes": 8
            }
        ]

    async def _get_baseline_exceptions(self) -> List[Dict]:
        """Get active baseline exceptions"""

        return [
            {
                "rule": "privileged_access",
                "scope": "admin@clinic.com",
                "reason": "Board approval pending",
                "owner": "Security Team",
                "risk": "Low",
                "expires": "2025-11-15"
            }
        ]

    async def _get_evidence_bundles(self) -> Dict:
        """Get evidence bundle manifest"""

        return {
            "bundle_id": f"EB-{self.year}{self.month:02d}-{self.client_id}",
            "generated": datetime.utcnow().isoformat(),
            "signature": "sha256:x9y8z7...",
            "worm_url": f"s3://compliance-worm/{self.client_id}/{self.year}/{self.month:02d}/",
            "contents": [
                "posture_report.pdf",
                "snapshots/ (24 daily snapshots)",
                "rule_results.json",
                "evidence_artifacts.zip",
                "manifest.json (signed)"
            ]
        }

    def _render_markdown(self, data: Dict) -> str:
        """Render compliance packet markdown"""

        template = Template("""# Monthly HIPAA Compliance Packet

**Client:** {{ client_name }}
**Period:** {{ month }} {{ year }}
**Baseline:** NixOS-HIPAA v{{ baseline_version }}
**Generated:** {{ generated_timestamp }}
**Packet ID:** {{ packet_id }}

---

## Executive Summary

**PHI Disclaimer:** This report contains system metadata and operational metrics only. No Protected Health Information (PHI) is processed, stored, or transmitted by the compliance monitoring system.

**Compliance Status:** {{ compliance_pct }}% of controls passing
**Critical Issues:** {{ critical_issue_count }} ({{ auto_fixed_count }} auto-fixed)
**MTTR (Critical Patches):** {{ mttr_hours }}h
**Backup Success Rate:** {{ backup_success_rate }}%

---

## Control Posture Heatmap

| Control | Description | Status | Evidence ID | Last Checked |
|---------|-------------|--------|-------------|--------------|
{% for control in controls %}| {{ control.control }} | {{ control.description }} | {{ control.status }} | {{ control.evidence_id }} | {{ control.last_checked }} |
{% endfor %}

**Legend:** ✅ Pass | ⚠️ Warning | ❌ Fail

---

## Backups & Test-Restores

**Backup Schedule:** {{ backup_summary.schedule }}
**Retention:** {{ backup_summary.retention_days }} days
**Encryption:** {{ backup_summary.encryption }}

| Week | Backup Status | Size (GB) | Checksum | Restore Test | Test Result |
|------|--------------|-----------|----------|--------------|-------------|
{% for week in backup_summary.weeks %}| {{ week.week }} | {{ week.status }} | {{ week.size_gb }} | {{ week.checksum }} | {{ week.restore_test_date or 'Not scheduled' }} | {{ week.restore_test_result }} |
{% endfor %}

**HIPAA Control:** §164.308(a)(7)(ii)(A), §164.310(d)(2)(iv)

---

## Time Synchronization

**NTP Server:** {{ time_sync.ntp_server }}
**Sync Status:** {{ time_sync.sync_status }}
**Max Drift Observed:** {{ time_sync.max_drift_ms }}ms
**Threshold:** ±{{ time_sync.threshold_ms }}ms

| System | Drift (ms) | Status | Last Sync |
|--------|-----------|--------|-----------|
{% for system in time_sync.systems %}| {{ system.hostname }} | {{ system.drift_ms }} | {{ system.status }} | {{ system.last_sync }} |
{% endfor %}

**HIPAA Control:** §164.312(b) (Audit controls require accurate timestamps)

---

## Access Controls

### Failed Login Attempts

**Total Failed Logins:** {{ access_controls.failed_logins.total }}
**Threshold:** >{{ access_controls.failed_logins.threshold }} triggers alert
**Lockouts:** {{ access_controls.failed_logins.lockouts }}

### Dormant Accounts

**Found:** {{ access_controls.dormant_accounts.count }}
**Definition:** {{ access_controls.dormant_accounts.definition }}

### MFA Status

**Total Active Users:** {{ access_controls.mfa.total_users }}
**MFA Enabled:** {{ access_controls.mfa.mfa_enabled }} ({{ access_controls.mfa.coverage_pct }}%)
**Break-Glass Accounts:** {{ access_controls.mfa.break_glass_accounts }} (Target: ≤2)

**HIPAA Control:** §164.312(a)(2)(i), §164.308(a)(3)(ii)(C)

---

## Patch & Vulnerability Posture

**Last Vulnerability Scan:** {{ patch_posture.last_scan }}
**Critical Patches Pending:** {{ patch_posture.critical_pending }}
**High Patches Pending:** {{ patch_posture.high_pending }}
**Medium Patches Pending:** {{ patch_posture.medium_pending }}

### Patch Timeline (Critical)

| CVE | Discovered | Patched | MTTR |
|-----|-----------|---------|------|
{% for patch in patch_posture.recent_patches %}| {{ patch.cve }} | {{ patch.discovered }} | {{ patch.patched }} | {{ patch.mttr_hours }}h |
{% endfor %}

**HIPAA Control:** §164.308(a)(5)(ii)(B)

---

## Encryption Status

### At-Rest Encryption

| Volume | Type | Status | Algorithm |
|--------|------|--------|-----------|
{% for vol in encryption_status.at_rest %}| {{ vol.volume }} | {{ vol.type }} | {{ vol.status }} | {{ vol.algorithm }} |
{% endfor %}

### In-Transit Encryption

| Service | Protocol | Certificate | Expiry |
|---------|----------|-------------|--------|
{% for svc in encryption_status.in_transit %}| {{ svc.service }} | {{ svc.protocol }} | {{ svc.certificate }} | {{ svc.expiry }} |
{% endfor %}

**HIPAA Control:** §164.312(a)(2)(iv), §164.312(e)(1)

---

## Incidents & Exceptions

### Incidents This Month

| Incident ID | Type | Severity | Auto-Fixed | Resolution Time |
|-------------|------|----------|------------|-----------------|
{% for incident in incidents %}| {{ incident.incident_id }} | {{ incident.type }} | {{ incident.severity }} | {{ "Yes" if incident.auto_fixed else "No" }} | {{ incident.resolution_minutes }} minutes |
{% endfor %}

### Active Baseline Exceptions

| Rule | Scope | Reason | Owner | Risk | Expires |
|------|-------|--------|-------|------|---------|
{% for exc in exceptions %}| {{ exc.rule }} | {{ exc.scope }} | {{ exc.reason }} | {{ exc.owner }} | {{ exc.risk }} | {{ exc.expires }} |
{% endfor %}

---

## Evidence Bundle Manifest

**Bundle ID:** {{ evidence_bundles.bundle_id }}
**Generated:** {{ evidence_bundles.generated }}
**Signature:** `{{ evidence_bundles.signature }}`
**WORM Storage URL:** `{{ evidence_bundles.worm_url }}`

**Contents:**
{% for item in evidence_bundles.contents %}- {{ item }}
{% endfor %}

---

**End of Monthly Compliance Packet**
**Next Review:** {{ month }} 1st, {{ year + 1 if month == 12 else year }}

**Questions:** Contact security@clinic.com
**Audit Support:** All evidence bundles available for 24 months
""")

        return template.render(**data)

    async def _markdown_to_pdf(self, md_path: Path) -> Path:
        """Convert markdown to PDF using pandoc"""

        pdf_path = md_path.with_suffix('.pdf')

        try:
            # Try using pandoc
            subprocess.run([
                'pandoc',
                str(md_path),
                '-o', str(pdf_path),
                '--pdf-engine=xelatex',
                '-V', 'geometry:margin=1in',
                '-V', 'fontsize=10pt'
            ], check=True)

        except (subprocess.CalledProcessError, FileNotFoundError):
            print("Warning: pandoc not available, PDF not generated")
            print(f"Markdown available at: {md_path}")
            # Return markdown path if PDF generation fails
            return md_path

        return pdf_path


# CLI interface
if __name__ == "__main__":
    import asyncio
    import argparse

    parser = argparse.ArgumentParser(description="Generate HIPAA compliance packet")
    parser.add_argument("--client", required=True, help="Client ID")
    parser.add_argument("--month", type=int, default=datetime.now().month, help="Month (1-12)")
    parser.add_argument("--year", type=int, default=datetime.now().year, help="Year")
    parser.add_argument("--baseline", default="1.0", help="Baseline version")
    parser.add_argument("--evidence-dir", default="./evidence", help="Evidence directory")

    args = parser.parse_args()

    async def main():
        packet = CompliancePacket(
            client_id=args.client,
            month=args.month,
            year=args.year,
            baseline_version=args.baseline,
            evidence_dir=args.evidence_dir
        )

        pdf_path = await packet.generate_packet()
        print(f"\n✅ Compliance packet generated: {pdf_path}")

    asyncio.run(main())
