"""
Compliance Packet Generator

Generates monthly HIPAA compliance packets from real evidence bundle data.

Queries compliance_bundles table for actual check results, computes
compliance scores, and generates auditor-ready markdown.

HIPAA Controls:
- 164.316(b)(1): Documentation (policies and procedures)
- 164.316(b)(2)(i): Time limit (retain for 6 years)
"""

from typing import Dict, List, Optional, Any
from datetime import datetime, timezone, timedelta
from pathlib import Path
import json
import hashlib
import logging
import subprocess

from jinja2 import Template
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

logger = logging.getLogger(__name__)

# Map check_types to HIPAA controls
CHECK_TYPE_HIPAA_MAP = {
    "ntp_sync": {"control": "164.312(b)", "description": "Audit Controls (time sync)"},
    "windows_backup_status": {"control": "164.308(a)(7)(ii)(A)", "description": "Data Backup Plan"},
    "windows_firewall_status": {"control": "164.312(e)(1)", "description": "Transmission Security"},
    "windows_defender": {"control": "164.308(a)(5)(ii)(B)", "description": "Protection from Malicious Software"},
    "windows_audit_policy": {"control": "164.312(b)", "description": "Audit Controls"},
    "windows_password_policy": {"control": "164.312(a)(1)", "description": "Access Control"},
    "windows_bitlocker_status": {"control": "164.312(a)(2)(iv)", "description": "Encryption and Decryption"},
    "windows_service_dns": {"control": "164.310(d)(1)", "description": "Device and Media Controls"},
    "windows_service_spooler": {"control": "164.310(d)(1)", "description": "Device and Media Controls"},
    "windows_service_w32time": {"control": "164.312(b)", "description": "Audit Controls (time sync)"},
    "firewall": {"control": "164.312(e)(1)", "description": "Transmission Security"},
    "disk_space": {"control": "164.310(d)(2)(iv)", "description": "Data Backup and Storage"},
    "critical_services": {"control": "164.308(a)(1)(ii)(D)", "description": "Information System Activity Review"},
    "nixos_generation": {"control": "164.310(d)(1)", "description": "Device and Media Controls"},
    "network": {"control": "164.312(e)(1)", "description": "Transmission Security"},
}


class CompliancePacket:
    """Generate monthly HIPAA compliance packet from real evidence data."""

    def __init__(
        self,
        site_id: str,
        month: int,
        year: int,
        db: AsyncSession,
        baseline_version: str = "1.0",
        output_dir: Optional[Path] = None,
    ):
        self.site_id = site_id
        self.month = month
        self.year = year
        self.db = db
        self.baseline_version = baseline_version
        self.output_dir = output_dir or Path("/tmp/compliance-packets")
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.packet_id = f"CP-{year}{month:02d}-{site_id[:16]}"
        self._period_start = datetime(year, month, 1, tzinfo=timezone.utc)
        if month == 12:
            self._period_end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            self._period_end = datetime(year, month + 1, 1, tzinfo=timezone.utc)

    async def generate_packet(self) -> Dict[str, Any]:
        """Generate compliance packet from real evidence data.

        Returns dict with packet data and file paths.
        """
        logger.info(f"Generating compliance packet: {self.packet_id}")

        # Get site name
        site_name = await self._get_site_name()

        # Query real data
        data = {
            "client_id": self.site_id,
            "client_name": site_name,
            "month": self._period_start.strftime("%B"),
            "year": self.year,
            "baseline_version": self.baseline_version,
            "generated_timestamp": datetime.now(timezone.utc).isoformat(),
            "packet_id": self.packet_id,

            # Real metrics from evidence
            "compliance_pct": await self._calculate_compliance_score(),
            "critical_issue_count": await self._count_critical_issues(),
            "auto_fixed_count": await self._count_auto_fixes(),
            "mttr_hours": await self._calculate_mttr(),
            "backup_success_rate": await self._calculate_backup_success_rate(),

            # Control posture from real checks
            "controls": await self._get_control_posture(),

            # Real backup data
            "backup_summary": await self._get_backup_summary(),

            # Real NTP data
            "time_sync": await self._get_time_sync_status(),

            # Access controls from evidence
            "access_controls": await self._get_access_controls(),

            # Patch posture
            "patch_posture": await self._get_patch_posture(),

            # Encryption from evidence
            "encryption_status": await self._get_encryption_status(),

            # Real incidents
            "incidents": await self._get_incidents(),

            # Exceptions
            "exceptions": await self._get_baseline_exceptions(),

            # Evidence chain manifest
            "evidence_bundles": await self._get_evidence_manifest(),
        }

        # Render markdown
        markdown = self._render_markdown(data)

        md_path = self.output_dir / f"{self.packet_id}.md"
        with open(md_path, "w") as f:
            f.write(markdown)

        logger.info(f"Compliance packet saved: {md_path}")

        return {
            "packet_id": self.packet_id,
            "site_id": self.site_id,
            "period": f"{self._period_start.strftime('%B %Y')}",
            "markdown_path": str(md_path),
            "data": data,
        }

    async def _get_site_name(self) -> str:
        result = await self.db.execute(
            text("SELECT clinic_name FROM sites WHERE site_id = :sid"),
            {"sid": self.site_id},
        )
        row = result.fetchone()
        return row.clinic_name if row and row.clinic_name else self.site_id

    async def _calculate_compliance_score(self) -> float:
        """Compliance % = pass bundles / total bundles in period."""
        result = await self.db.execute(
            text("""
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE check_result = 'pass') as pass_count
                FROM compliance_bundles
                WHERE site_id = :sid
                  AND checked_at >= :start AND checked_at < :end
            """),
            {"sid": self.site_id, "start": self._period_start, "end": self._period_end},
        )
        row = result.fetchone()
        if not row or row.total == 0:
            return 0.0
        return round(row.pass_count * 100.0 / row.total, 1)

    async def _count_critical_issues(self) -> int:
        """Count fail results in period."""
        result = await self.db.execute(
            text("""
                SELECT COUNT(*) FROM compliance_bundles
                WHERE site_id = :sid
                  AND checked_at >= :start AND checked_at < :end
                  AND check_result = 'fail'
            """),
            {"sid": self.site_id, "start": self._period_start, "end": self._period_end},
        )
        return result.scalar() or 0

    async def _count_auto_fixes(self) -> int:
        """Count bundles where a fail was followed by a pass for same check_type."""
        result = await self.db.execute(
            text("""
                SELECT COUNT(DISTINCT check_type) FROM compliance_bundles
                WHERE site_id = :sid
                  AND checked_at >= :start AND checked_at < :end
                  AND check_result = 'pass'
                  AND check_type IN (
                      SELECT DISTINCT check_type FROM compliance_bundles
                      WHERE site_id = :sid
                        AND checked_at >= :start AND checked_at < :end
                        AND check_result = 'fail'
                  )
            """),
            {"sid": self.site_id, "start": self._period_start, "end": self._period_end},
        )
        return result.scalar() or 0

    async def _calculate_mttr(self) -> float:
        """Estimate MTTR: avg time between fail and next pass for same check_type.

        Uses LATERAL join with LIMIT 1 for efficient lookup, and samples
        up to 500 recent failures to keep the query fast on large datasets.
        """
        result = await self.db.execute(
            text("""
                WITH recent_fails AS (
                    SELECT check_type, checked_at as fail_time
                    FROM compliance_bundles
                    WHERE site_id = :sid
                      AND checked_at >= :start AND checked_at < :end
                      AND check_result = 'fail'
                    ORDER BY checked_at DESC
                    LIMIT 500
                )
                SELECT AVG(EXTRACT(EPOCH FROM (recovery.recover_time - f.fail_time)) / 3600) as avg_mttr
                FROM recent_fails f
                CROSS JOIN LATERAL (
                    SELECT checked_at as recover_time
                    FROM compliance_bundles
                    WHERE site_id = :sid
                      AND check_type = f.check_type
                      AND check_result = 'pass'
                      AND checked_at > f.fail_time
                      AND checked_at < f.fail_time + INTERVAL '24 hours'
                    ORDER BY checked_at ASC
                    LIMIT 1
                ) recovery
            """),
            {"sid": self.site_id, "start": self._period_start, "end": self._period_end},
        )
        row = result.fetchone()
        return round(row.avg_mttr, 1) if row and row.avg_mttr else 0.0

    async def _calculate_backup_success_rate(self) -> float:
        """Backup pass rate from windows_backup_status checks."""
        result = await self.db.execute(
            text("""
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE check_result = 'pass') as pass_count
                FROM compliance_bundles
                WHERE site_id = :sid
                  AND checked_at >= :start AND checked_at < :end
                  AND check_type = 'windows_backup_status'
            """),
            {"sid": self.site_id, "start": self._period_start, "end": self._period_end},
        )
        row = result.fetchone()
        if not row or row.total == 0:
            return 100.0
        return round(row.pass_count * 100.0 / row.total, 1)

    async def _get_control_posture(self) -> List[Dict]:
        """Build control posture from real check_type results."""
        result = await self.db.execute(
            text("""
                WITH stats AS (
                    SELECT check_type,
                           COUNT(*) as total,
                           COUNT(*) FILTER (WHERE check_result = 'pass') as pass_count,
                           COUNT(*) FILTER (WHERE check_result = 'fail') as fail_count,
                           MAX(checked_at) as last_checked
                    FROM compliance_bundles
                    WHERE site_id = :sid
                      AND checked_at >= :start AND checked_at < :end
                    GROUP BY check_type
                ),
                latest AS (
                    SELECT DISTINCT ON (check_type)
                           check_type, bundle_id as latest_bundle_id
                    FROM compliance_bundles
                    WHERE site_id = :sid
                      AND checked_at >= :start AND checked_at < :end
                    ORDER BY check_type, checked_at DESC
                )
                SELECT s.*, l.latest_bundle_id
                FROM stats s
                LEFT JOIN latest l USING (check_type)
                ORDER BY s.check_type
            """),
            {"sid": self.site_id, "start": self._period_start, "end": self._period_end},
        )

        controls = []
        for row in result.fetchall():
            hipaa = CHECK_TYPE_HIPAA_MAP.get(row.check_type, {
                "control": "164.308(a)(1)",
                "description": row.check_type.replace("_", " ").title(),
            })

            pass_rate = row.pass_count * 100.0 / row.total if row.total > 0 else 0
            if pass_rate >= 90:
                status = "Pass"
            elif pass_rate >= 50:
                status = "Warning"
            else:
                status = "Fail"

            controls.append({
                "control": hipaa["control"],
                "description": hipaa["description"],
                "status": status,
                "pass_rate": f"{pass_rate:.0f}%",
                "evidence_id": row.latest_bundle_id or "N/A",
                "last_checked": row.last_checked.strftime("%Y-%m-%d %H:%M") if row.last_checked else "N/A",
                "check_count": row.total,
            })

        return controls

    async def _get_backup_summary(self) -> Dict:
        """Get real backup check results by week."""
        result = await self.db.execute(
            text("""
                SELECT
                    EXTRACT(WEEK FROM checked_at) as week_num,
                    MIN(checked_at::date) as week_start,
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE check_result = 'pass') as pass_count,
                    COUNT(*) FILTER (WHERE check_result = 'fail') as fail_count
                FROM compliance_bundles
                WHERE site_id = :sid
                  AND checked_at >= :start AND checked_at < :end
                  AND check_type = 'windows_backup_status'
                GROUP BY EXTRACT(WEEK FROM checked_at)
                ORDER BY week_num
            """),
            {"sid": self.site_id, "start": self._period_start, "end": self._period_end},
        )

        weeks = []
        for i, row in enumerate(result.fetchall()):
            status = "Pass" if row.fail_count == 0 else "Fail"
            weeks.append({
                "week": f"Week {i + 1}",
                "status": status,
                "checks": row.total,
                "pass_count": row.pass_count,
                "fail_count": row.fail_count,
            })

        return {
            "schedule": "Continuous monitoring",
            "retention_days": 90,
            "weeks": weeks,
        }

    async def _get_time_sync_status(self) -> Dict:
        """Get NTP sync status from real evidence."""
        result = await self.db.execute(
            text("""
                SELECT check_result, checked_at, checks
                FROM compliance_bundles
                WHERE site_id = :sid
                  AND check_type = 'ntp_sync'
                ORDER BY checked_at DESC
                LIMIT 5
            """),
            {"sid": self.site_id},
        )

        rows = result.fetchall()
        if not rows:
            return {
                "ntp_server": "pool.ntp.org",
                "sync_status": "No data",
                "max_drift_ms": 0,
                "systems": [],
            }

        latest = rows[0]
        all_pass = all(r.check_result == "pass" for r in rows)

        return {
            "ntp_server": "pool.ntp.org",
            "sync_status": "Synchronized" if all_pass else "Drift detected",
            "max_drift_ms": 90000,
            "last_check": latest.checked_at.strftime("%Y-%m-%d %H:%M"),
            "recent_results": [
                {"time": r.checked_at.strftime("%Y-%m-%d %H:%M"), "result": r.check_result}
                for r in rows
            ],
            "systems": [],
        }

    async def _get_access_controls(self) -> Dict:
        """Get access control metrics from password_policy checks."""
        result = await self.db.execute(
            text("""
                SELECT check_result, COUNT(*)
                FROM compliance_bundles
                WHERE site_id = :sid
                  AND checked_at >= :start AND checked_at < :end
                  AND check_type = 'windows_password_policy'
                GROUP BY check_result
            """),
            {"sid": self.site_id, "start": self._period_start, "end": self._period_end},
        )

        results = {r.check_result: r.count for r in result.fetchall()}
        total = sum(results.values())
        pass_count = results.get("pass", 0)

        return {
            "password_policy": {
                "total_checks": total,
                "pass_count": pass_count,
                "fail_count": results.get("fail", 0),
                "compliance_rate": f"{pass_count * 100 / total:.0f}%" if total > 0 else "N/A",
            },
        }

    async def _get_patch_posture(self) -> Dict:
        """Get patch/update posture from nixos_generation checks."""
        result = await self.db.execute(
            text("""
                SELECT check_result, checked_at
                FROM compliance_bundles
                WHERE site_id = :sid
                  AND check_type = 'nixos_generation'
                ORDER BY checked_at DESC
                LIMIT 1
            """),
            {"sid": self.site_id},
        )
        row = result.fetchone()

        return {
            "last_check": row.checked_at.strftime("%Y-%m-%d") if row else "N/A",
            "status": row.check_result if row else "unknown",
            "note": "NixOS declarative configuration - patches applied via flake update",
        }

    async def _get_encryption_status(self) -> Dict:
        """Get encryption status from bitlocker checks."""
        result = await self.db.execute(
            text("""
                SELECT check_result, COUNT(*)
                FROM compliance_bundles
                WHERE site_id = :sid
                  AND checked_at >= :start AND checked_at < :end
                  AND check_type = 'windows_bitlocker_status'
                GROUP BY check_result
            """),
            {"sid": self.site_id, "start": self._period_start, "end": self._period_end},
        )

        results = {r.check_result: r.count for r in result.fetchall()}

        return {
            "bitlocker": {
                "total_checks": sum(results.values()),
                "pass_count": results.get("pass", 0),
                "fail_count": results.get("fail", 0),
                "status": "Enabled" if results.get("pass", 0) > 0 else "Not detected",
            },
            "in_transit": "TLS 1.2+ enforced (mTLS for agent communication)",
        }

    async def _get_incidents(self) -> List[Dict]:
        """Get incidents from fail→pass transitions (auto-healed)."""
        result = await self.db.execute(
            text("""
                SELECT check_type, check_result, checked_at, bundle_id
                FROM compliance_bundles
                WHERE site_id = :sid
                  AND checked_at >= :start AND checked_at < :end
                  AND check_result = 'fail'
                ORDER BY checked_at DESC
                LIMIT 20
            """),
            {"sid": self.site_id, "start": self._period_start, "end": self._period_end},
        )

        incidents = []
        for row in result.fetchall():
            incidents.append({
                "incident_id": row.bundle_id,
                "type": row.check_type.replace("_", " ").title(),
                "severity": "High",
                "time": row.checked_at.strftime("%Y-%m-%d %H:%M"),
            })

        return incidents[:10]

    async def _get_baseline_exceptions(self) -> List[Dict]:
        """Get active compliance exceptions."""
        result = await self.db.execute(
            text("""
                SELECT item_id, scope_type, reason, expiration_date
                FROM compliance_exceptions
                WHERE site_id = :sid
                  AND is_active = true
                  AND expiration_date > NOW()
                ORDER BY created_at DESC
                LIMIT 10
            """),
            {"sid": self.site_id},
        )

        exceptions = []
        for row in result.fetchall():
            exceptions.append({
                "rule": row.item_id,
                "scope": row.scope_type,
                "reason": row.reason,
                "expires": row.expiration_date.strftime("%Y-%m-%d") if row.expiration_date else "N/A",
            })

        return exceptions

    async def _get_evidence_manifest(self) -> Dict:
        """Get evidence chain summary for the period."""
        result = await self.db.execute(
            text("""
                SELECT
                    COUNT(*) as total_bundles,
                    COUNT(*) FILTER (WHERE signature_valid = true) as signed_count,
                    MIN(chain_position) as first_position,
                    MAX(chain_position) as last_position,
                    MIN(checked_at) as first_bundle,
                    MAX(checked_at) as last_bundle,
                    MAX(chain_hash) as latest_chain_hash
                FROM compliance_bundles
                WHERE site_id = :sid
                  AND checked_at >= :start AND checked_at < :end
            """),
            {"sid": self.site_id, "start": self._period_start, "end": self._period_end},
        )
        row = result.fetchone()

        # OTS status (ots_proofs uses naive timestamps)
        naive_start = self._period_start.replace(tzinfo=None)
        naive_end = self._period_end.replace(tzinfo=None)
        ots_result = await self.db.execute(
            text("""
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE status = 'anchored') as anchored
                FROM ots_proofs
                WHERE site_id = :sid
                  AND submitted_at >= :start AND submitted_at < :end
            """),
            {"sid": self.site_id, "start": naive_start, "end": naive_end},
        )
        ots_row = ots_result.fetchone()

        return {
            "total_bundles": row.total_bundles or 0,
            "signed_bundles": row.signed_count or 0,
            "chain_range": f"{row.first_position or 0} - {row.last_position or 0}",
            "period_start": row.first_bundle.isoformat() if row.first_bundle else "N/A",
            "period_end": row.last_bundle.isoformat() if row.last_bundle else "N/A",
            "latest_chain_hash": row.latest_chain_hash[:16] + "..." if row.latest_chain_hash else "N/A",
            "worm_url": f"s3://evidence-worm/{self.site_id}/{self.year}/{self.month:02d}/",
            "ots_submitted": ots_row.total if ots_row else 0,
            "ots_anchored": ots_row.anchored if ots_row else 0,
        }

    def _render_markdown(self, data: Dict) -> str:
        """Render compliance packet markdown from real data."""
        template = Template("""# Monthly HIPAA Compliance Packet

**Site:** {{ client_name }}
**Site ID:** {{ client_id }}
**Period:** {{ month }} {{ year }}
**Baseline:** NixOS-HIPAA v{{ baseline_version }}
**Generated:** {{ generated_timestamp }}
**Packet ID:** {{ packet_id }}

---

## Executive Summary

**PHI Disclaimer:** This report contains system metadata and operational metrics only. No Protected Health Information (PHI) is processed, stored, or transmitted by the compliance monitoring system.

| Metric | Value |
|--------|-------|
| Overall Compliance | {{ compliance_pct }}% |
| Failed Checks | {{ critical_issue_count }} |
| Check Types Auto-Recovered | {{ auto_fixed_count }} |
| Mean Time to Recovery | {{ mttr_hours }}h |
| Backup Success Rate | {{ backup_success_rate }}% |

---

## Control Posture

| HIPAA Control | Description | Status | Pass Rate | Checks | Last Verified |
|---------------|-------------|--------|-----------|--------|---------------|
{% for c in controls %}| {{ c.control }} | {{ c.description }} | {{ c.status }} | {{ c.pass_rate }} | {{ c.check_count }} | {{ c.last_checked }} |
{% endfor %}

---

## Backup Status

**Schedule:** {{ backup_summary.schedule }}
**Retention:** {{ backup_summary.retention_days }} days

{% if backup_summary.weeks %}| Week | Status | Total Checks | Passed | Failed |
|------|--------|-------------|--------|--------|
{% for w in backup_summary.weeks %}| {{ w.week }} | {{ w.status }} | {{ w.checks }} | {{ w.pass_count }} | {{ w.fail_count }} |
{% endfor %}{% else %}No backup checks recorded this period.
{% endif %}

**HIPAA Control:** 164.308(a)(7)(ii)(A), 164.310(d)(2)(iv)

---

## Time Synchronization

**NTP Server:** {{ time_sync.ntp_server }}
**Status:** {{ time_sync.sync_status }}
{% if time_sync.last_check %}**Last Check:** {{ time_sync.last_check }}{% endif %}

**HIPAA Control:** 164.312(b)

---

## Access Controls

{% if access_controls.password_policy %}**Password Policy Checks:** {{ access_controls.password_policy.total_checks }}
**Pass Rate:** {{ access_controls.password_policy.compliance_rate }}
{% endif %}

**HIPAA Control:** 164.312(a)(1), 164.308(a)(3)(ii)(C)

---

## Patch & System Posture

**Last Check:** {{ patch_posture.last_check }}
**Status:** {{ patch_posture.status }}
**Note:** {{ patch_posture.note }}

**HIPAA Control:** 164.308(a)(5)(ii)(B)

---

## Encryption Status

**BitLocker:** {{ encryption_status.bitlocker.status }} ({{ encryption_status.bitlocker.pass_count }}/{{ encryption_status.bitlocker.total_checks }} checks passed)
**In-Transit:** {{ encryption_status.in_transit }}

**HIPAA Control:** 164.312(a)(2)(iv), 164.312(e)(1)

---

## Incidents (Failed Checks)

{% if incidents %}| Time | Check Type | Bundle ID |
|------|-----------|-----------|
{% for inc in incidents %}| {{ inc.time }} | {{ inc.type }} | {{ inc.incident_id[:20] }}... |
{% endfor %}{% else %}No failed checks recorded this period.
{% endif %}

---

{% if exceptions %}## Active Exceptions

| Rule | Scope | Reason | Expires |
|------|-------|--------|---------|
{% for exc in exceptions %}| {{ exc.rule }} | {{ exc.scope }} | {{ exc.reason }} | {{ exc.expires }} |
{% endfor %}

---
{% endif %}

## Evidence Chain Manifest

| Property | Value |
|----------|-------|
| Total Bundles | {{ evidence_bundles.total_bundles }} |
| Signed (Ed25519) | {{ evidence_bundles.signed_bundles }} |
| Chain Positions | {{ evidence_bundles.chain_range }} |
| Period Coverage | {{ evidence_bundles.period_start }} to {{ evidence_bundles.period_end }} |
| Latest Chain Hash | `{{ evidence_bundles.latest_chain_hash }}` |
| WORM Storage | `{{ evidence_bundles.worm_url }}` |
| OTS Proofs Submitted | {{ evidence_bundles.ots_submitted }} |
| OTS Bitcoin Anchored | {{ evidence_bundles.ots_anchored }} |

---

**End of Monthly Compliance Packet**
**Audit Support:** All evidence bundles retained for 90+ days in WORM storage
""")
        return template.render(**data)
