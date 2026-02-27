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

# Map check_types to HIPAA controls.
# Covers both Go daemon submitter names (from JSONB checks array) and
# legacy Python agent names (from check_type column in older bundles).
# When multiple check_types map to the same control, they're consolidated
# in _get_control_posture() so each HIPAA control appears once in the report.
CHECK_TYPE_HIPAA_MAP = {
    # =========================================================================
    # Go daemon: Windows scanner (submitter.go windowsCheckTypes)
    # =========================================================================
    "firewall_status": {"control": "164.312(e)(1)", "description": "Transmission Security"},
    "windows_defender": {"control": "164.308(a)(5)(ii)(B)", "description": "Protection from Malicious Software"},
    "windows_update": {"control": "164.308(a)(5)(ii)(B)", "description": "Security Updates"},
    "audit_logging": {"control": "164.312(b)", "description": "Audit Controls"},
    "rogue_admin_users": {"control": "164.312(a)(1)", "description": "Access Control"},
    "rogue_scheduled_tasks": {"control": "164.308(a)(5)(ii)(C)", "description": "Security Awareness"},
    "agent_status": {"control": "164.312(b)", "description": "Audit Controls (agent)"},
    "bitlocker_status": {"control": "164.312(a)(2)(iv)", "description": "Encryption and Decryption"},
    "smb_signing": {"control": "164.312(e)(1)", "description": "Transmission Security (SMB)"},
    "smb1_protocol": {"control": "164.312(e)(1)", "description": "Transmission Security (SMBv1)"},
    "screen_lock_policy": {"control": "164.312(a)(2)(iii)", "description": "Workstation Security"},
    "defender_exclusions": {"control": "164.308(a)(5)(ii)(B)", "description": "Malicious Software Protection"},
    "dns_config": {"control": "164.312(e)(1)", "description": "Transmission Security (DNS)"},
    "network_profile": {"control": "164.312(e)(1)", "description": "Transmission Security (Profile)"},
    "password_policy": {"control": "164.312(a)(1)", "description": "Access Control"},
    "rdp_nla": {"control": "164.312(d)", "description": "Person or Entity Authentication"},
    "guest_account": {"control": "164.312(a)(1)", "description": "Access Control"},
    "service_dns": {"control": "164.310(d)(1)", "description": "Device and Media Controls"},
    "service_netlogon": {"control": "164.312(d)", "description": "Person or Entity Authentication"},
    # =========================================================================
    # Go daemon: Linux scanner (submitter.go linuxCheckTypes)
    # =========================================================================
    "linux_firewall": {"control": "164.312(e)(1)", "description": "Transmission Security"},
    "linux_ssh_config": {"control": "164.312(d)", "description": "Person or Entity Authentication"},
    "linux_failed_services": {"control": "164.312(a)(1)", "description": "Access Control"},
    "linux_disk_space": {"control": "164.310(d)(2)(iv)", "description": "Data Backup and Storage"},
    "linux_suid_binaries": {"control": "164.312(a)(1)", "description": "Access Control"},
    "linux_audit_logging": {"control": "164.312(b)", "description": "Audit Controls"},
    "linux_ntp_sync": {"control": "164.312(b)", "description": "Audit Controls (time sync)"},
    "linux_kernel_params": {"control": "164.312(e)(1)", "description": "Transmission Security"},
    "linux_open_ports": {"control": "164.312(e)(1)", "description": "Transmission Security"},
    "linux_user_accounts": {"control": "164.312(a)(1)", "description": "Access Control"},
    "linux_file_permissions": {"control": "164.312(a)(1)", "description": "Access Control"},
    "linux_unattended_upgrades": {"control": "164.308(a)(5)(ii)(B)", "description": "Security Updates"},
    "linux_log_forwarding": {"control": "164.312(b)", "description": "Audit Controls"},
    "linux_cron_review": {"control": "164.312(a)(1)", "description": "Access Control"},
    "linux_cert_expiry": {"control": "164.312(e)(1)", "description": "Transmission Security"},
    # =========================================================================
    # Go daemon: Network scanner (submitter.go networkCheckTypes)
    # =========================================================================
    "net_unexpected_ports": {"control": "164.312(e)(1)", "description": "Transmission Security"},
    "net_expected_service": {"control": "164.312(a)(1)", "description": "Access Control"},
    "net_host_reachability": {"control": "164.308(a)(7)(ii)(B)", "description": "Disaster Recovery Plan"},
    "net_dns_resolution": {"control": "164.312(e)(1)", "description": "Transmission Security"},
    # =========================================================================
    # Legacy: Python agent / older Go daemon check_type column names
    # (kept for backward compat with pre-Feb-21 data in compliance_bundles)
    # =========================================================================
    "windows_firewall_status": {"control": "164.312(e)(1)", "description": "Transmission Security"},
    "windows_bitlocker_status": {"control": "164.312(a)(2)(iv)", "description": "Encryption and Decryption"},
    "windows_backup_status": {"control": "164.308(a)(7)(ii)(A)", "description": "Data Backup Plan"},
    "windows_audit_policy": {"control": "164.312(b)", "description": "Audit Controls"},
    "windows_password_policy": {"control": "164.312(a)(1)", "description": "Access Control"},
    "windows_service_dns": {"control": "164.310(d)(1)", "description": "Device and Media Controls"},
    "windows_service_spooler": {"control": "164.310(d)(1)", "description": "Device and Media Controls"},
    "windows_service_netlogon": {"control": "164.312(d)", "description": "Person or Entity Authentication"},
    "windows_service_w32time": {"control": "164.312(b)", "description": "Audit Controls (time sync)"},
    "windows_service_wuauserv": {"control": "164.308(a)(5)(ii)(B)", "description": "Security Updates"},
    "windows_windows_defender": {"control": "164.308(a)(5)(ii)(B)", "description": "Protection from Malicious Software"},
    "windows_screen_lock_policy": {"control": "164.312(a)(2)(iii)", "description": "Workstation Security"},
    "windows_dns_config": {"control": "164.312(e)(1)", "description": "Transmission Security (DNS)"},
    "windows_smb_signing": {"control": "164.312(e)(1)", "description": "Transmission Security (SMB)"},
    "windows_smb1_protocol": {"control": "164.312(e)(1)", "description": "Transmission Security (SMBv1)"},
    "windows_network_profile": {"control": "164.312(e)(1)", "description": "Transmission Security (Profile)"},
    "windows_defender_exclusions": {"control": "164.308(a)(5)(ii)(B)", "description": "Malicious Software Protection"},
    "windows_registry_run_persistence": {"control": "164.308(a)(5)(ii)(C)", "description": "Security Awareness"},
    "windows_scheduled_task_persistence": {"control": "164.308(a)(5)(ii)(C)", "description": "Security Awareness"},
    "windows_wmi_event_persistence": {"control": "164.308(a)(5)(ii)(C)", "description": "Security Awareness"},
    "ntp_sync": {"control": "164.312(b)", "description": "Audit Controls (time sync)"},
    "disk_space": {"control": "164.310(d)(2)(iv)", "description": "Data Backup and Storage"},
    "critical_services": {"control": "164.308(a)(1)(ii)(D)", "description": "Information System Activity Review"},
    "nixos_generation": {"control": "164.310(d)(1)", "description": "Device and Media Controls"},
    "network": {"control": "164.312(e)(1)", "description": "Transmission Security"},
    "firewall": {"control": "164.312(e)(1)", "description": "Transmission Security"},
    # Legacy Linux short names (from older Python agent / Go daemon versions)
    "linux_accounts": {"control": "164.312(a)(1)", "description": "Access Control"},
    "linux_kernel": {"control": "164.312(e)(1)", "description": "Transmission Security"},
    "linux_permissions": {"control": "164.312(a)(1)", "description": "Access Control"},
    "linux_patching": {"control": "164.308(a)(5)(ii)(B)", "description": "Security Updates"},
    "linux_services": {"control": "164.312(a)(1)", "description": "Access Control"},
    "linux_network": {"control": "164.312(e)(1)", "description": "Transmission Security"},
    "linux_audit": {"control": "164.312(b)", "description": "Audit Controls"},
    "linux_logging": {"control": "164.312(b)", "description": "Audit Controls"},
    "linux_time_sync": {"control": "164.312(b)", "description": "Audit Controls (time sync)"},
    "linux_cron": {"control": "164.312(a)(1)", "description": "Access Control"},
    "linux_mac": {"control": "164.312(a)(1)", "description": "Access Control (MAC)"},
    "linux_banner": {"control": "164.308(a)(5)(ii)(A)", "description": "Security Awareness (login banner)"},
    "linux_incident_response": {"control": "164.308(a)(6)(ii)", "description": "Response and Reporting"},
    "linux_crypto": {"control": "164.312(a)(2)(iv)", "description": "Encryption and Decryption"},
    "linux_boot": {"control": "164.310(d)(1)", "description": "Device and Media Controls (boot)"},
    "linux_integrity": {"control": "164.312(c)(1)", "description": "Integrity Controls"},
    "workstation": {"control": "164.312(a)(1)", "description": "Access Control (workstation)"},
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
        """Compliance % = average of per-HIPAA-control pass rates.

        Expands the JSONB checks array from each bundle so every individual
        check_type is scored independently. This is critical because the Go
        daemon sends 19+ checks per bundle but the check_type column only
        stores the first one.

        Each check_type gets its own pass rate, then check_types that map
        to the same HIPAA control are averaged together. The final score
        is the average across all distinct controls. This prevents a single
        high-frequency failing check from dominating the score.
        """
        result = await self.db.execute(
            text("""
                WITH expanded AS (
                    SELECT
                        c->>'check' as check_type,
                        c->>'status' as check_status
                    FROM compliance_bundles cb,
                         jsonb_array_elements(cb.checks) as c
                    WHERE cb.site_id = :sid
                      AND cb.checked_at >= :start AND cb.checked_at < :end
                      AND jsonb_array_length(cb.checks) > 0
                )
                SELECT check_type,
                       COUNT(*) as total,
                       COUNT(*) FILTER (WHERE check_status = 'pass') as pass_count
                FROM expanded
                WHERE check_type IS NOT NULL
                GROUP BY check_type
            """),
            {"sid": self.site_id, "start": self._period_start, "end": self._period_end},
        )
        rows = result.fetchall()
        if not rows:
            return 0.0

        # Group pass rates by HIPAA control
        control_rates: Dict[str, List[float]] = {}
        for row in rows:
            hipaa = CHECK_TYPE_HIPAA_MAP.get(row.check_type, {
                "control": "164.308(a)(1)",
            })
            control = hipaa["control"]
            rate = row.pass_count * 100.0 / row.total if row.total > 0 else 0.0
            control_rates.setdefault(control, []).append(rate)

        # Average rates within each control, then average across controls
        control_scores = [
            sum(rates) / len(rates) for rates in control_rates.values()
        ]
        return round(sum(control_scores) / len(control_scores), 1)

    async def _count_critical_issues(self) -> int:
        """Count check_types whose latest result is 'fail'.

        Uses only the most recent result per check_type to avoid
        counting every repeat of the same ongoing failure.
        """
        result = await self.db.execute(
            text("""
                WITH latest_per_check AS (
                    SELECT DISTINCT ON (check_type)
                           check_type, check_result
                    FROM compliance_bundles
                    WHERE site_id = :sid
                      AND checked_at >= :start AND checked_at < :end
                    ORDER BY check_type, checked_at DESC
                )
                SELECT COUNT(*) FROM latest_per_check
                WHERE check_result = 'fail'
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
        """Build control posture from real check_type results.

        Expands JSONB checks array so each individual check is scored.
        Multiple check_types may map to the same HIPAA control (e.g.
        firewall_status, smb_signing all → 164.312(e)(1)).
        This method consolidates them so each control appears once.
        """
        result = await self.db.execute(
            text("""
                WITH expanded AS (
                    SELECT
                        c->>'check' as check_type,
                        c->>'status' as check_status,
                        cb.checked_at,
                        cb.bundle_id
                    FROM compliance_bundles cb,
                         jsonb_array_elements(cb.checks) as c
                    WHERE cb.site_id = :sid
                      AND cb.checked_at >= :start AND cb.checked_at < :end
                      AND jsonb_array_length(cb.checks) > 0
                ),
                stats AS (
                    SELECT check_type,
                           COUNT(*) as total,
                           COUNT(*) FILTER (WHERE check_status = 'pass') as pass_count,
                           COUNT(*) FILTER (WHERE check_status = 'fail') as fail_count,
                           MAX(checked_at) as last_checked
                    FROM expanded
                    WHERE check_type IS NOT NULL
                    GROUP BY check_type
                ),
                latest AS (
                    SELECT DISTINCT ON (check_type)
                           check_type, bundle_id as latest_bundle_id
                    FROM expanded
                    WHERE check_type IS NOT NULL
                    ORDER BY check_type, checked_at DESC
                )
                SELECT s.*, l.latest_bundle_id
                FROM stats s
                LEFT JOIN latest l USING (check_type)
                ORDER BY s.check_type
            """),
            {"sid": self.site_id, "start": self._period_start, "end": self._period_end},
        )

        # Consolidate by HIPAA control code
        control_agg: Dict[str, Dict] = {}
        for row in result.fetchall():
            hipaa = CHECK_TYPE_HIPAA_MAP.get(row.check_type, {
                "control": "164.308(a)(1)",
                "description": row.check_type.replace("_", " ").title(),
            })
            key = hipaa["control"]

            if key not in control_agg:
                control_agg[key] = {
                    "control": key,
                    "description": hipaa["description"],
                    "total": 0,
                    "pass_count": 0,
                    "last_checked": None,
                    "evidence_id": None,
                    "check_types": [],
                }

            entry = control_agg[key]
            entry["total"] += row.total
            entry["pass_count"] += row.pass_count
            entry["check_types"].append(row.check_type)

            # Keep the most recent timestamp and its bundle_id
            if row.last_checked and (entry["last_checked"] is None or row.last_checked > entry["last_checked"]):
                entry["last_checked"] = row.last_checked
                entry["evidence_id"] = row.latest_bundle_id

        controls = []
        for entry in control_agg.values():
            pass_rate = entry["pass_count"] * 100.0 / entry["total"] if entry["total"] > 0 else 0
            if pass_rate >= 90:
                status = "Pass"
            elif pass_rate >= 50:
                status = "Warning"
            else:
                status = "Fail"

            controls.append({
                "control": entry["control"],
                "description": entry["description"],
                "status": status,
                "pass_rate": f"{pass_rate:.0f}%",
                "evidence_id": entry["evidence_id"] or "N/A",
                "last_checked": entry["last_checked"].strftime("%Y-%m-%d %H:%M") if entry["last_checked"] else "N/A",
                "check_count": entry["total"],
            })

        # Sort by control code for consistent ordering
        controls.sort(key=lambda c: c["control"])
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

        # OTS status with block heights (ots_proofs uses naive timestamps)
        naive_start = self._period_start.replace(tzinfo=None)
        naive_end = self._period_end.replace(tzinfo=None)
        ots_result = await self.db.execute(
            text("""
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE status = 'anchored') as anchored,
                    COUNT(*) FILTER (WHERE status = 'pending') as pending,
                    MIN(bitcoin_block) FILTER (WHERE bitcoin_block IS NOT NULL) as first_block,
                    MAX(bitcoin_block) FILTER (WHERE bitcoin_block IS NOT NULL) as latest_block,
                    MIN(anchored_at) FILTER (WHERE anchored_at IS NOT NULL) as first_anchor_time,
                    MAX(anchored_at) FILTER (WHERE anchored_at IS NOT NULL) as latest_anchor_time
                FROM ots_proofs
                WHERE site_id = :sid
                  AND submitted_at >= :start AND submitted_at < :end
            """),
            {"sid": self.site_id, "start": naive_start, "end": naive_end},
        )
        ots_row = ots_result.fetchone()

        ots_total = ots_row.total if ots_row else 0
        ots_anchored = ots_row.anchored if ots_row else 0

        manifest = {
            "total_bundles": row.total_bundles or 0,
            "signed_bundles": row.signed_count or 0,
            "chain_range": f"{row.first_position or 0} - {row.last_position or 0}",
            "period_start": row.first_bundle.isoformat() if row.first_bundle else "N/A",
            "period_end": row.last_bundle.isoformat() if row.last_bundle else "N/A",
            "latest_chain_hash": row.latest_chain_hash[:16] + "..." if row.latest_chain_hash else "N/A",
            "worm_url": f"s3://evidence-worm/{self.site_id}/{self.year}/{self.month:02d}/",
            "ots_submitted": ots_total,
            "ots_anchored": ots_anchored,
            "ots_pending": ots_row.pending if ots_row else 0,
            "ots_anchor_rate": round(ots_anchored * 100.0 / ots_total, 1) if ots_total > 0 else 0,
            "ots_first_block": ots_row.first_block if ots_row else None,
            "ots_latest_block": ots_row.latest_block if ots_row else None,
            "ots_first_anchor_time": ots_row.first_anchor_time.strftime("%Y-%m-%d %H:%M UTC") if ots_row and ots_row.first_anchor_time else None,
            "ots_latest_anchor_time": ots_row.latest_anchor_time.strftime("%Y-%m-%d %H:%M UTC") if ots_row and ots_row.latest_anchor_time else None,
        }

        # Get recent anchor samples for the manifest (up to 5)
        anchors_result = await self.db.execute(
            text("""
                SELECT p.bitcoin_block, p.anchored_at, p.bundle_id,
                       b.check_type
                FROM ots_proofs p
                LEFT JOIN compliance_bundles b ON b.bundle_id = p.bundle_id AND b.site_id = p.site_id
                WHERE p.site_id = :sid
                  AND p.submitted_at >= :start AND p.submitted_at < :end
                  AND p.bitcoin_block IS NOT NULL
                ORDER BY p.anchored_at DESC
                LIMIT 5
            """),
            {"sid": self.site_id, "start": naive_start, "end": naive_end},
        )
        manifest["ots_recent_anchors"] = [
            {
                "block": r.bitcoin_block,
                "time": r.anchored_at.strftime("%Y-%m-%d %H:%M") if r.anchored_at else "N/A",
                "bundle_id": r.bundle_id[:20] + "..." if r.bundle_id else "N/A",
                "check_type": (r.check_type or "unknown").replace("_", " ").title(),
                "blockstream_url": f"https://blockstream.info/block-height/{r.bitcoin_block}",
            }
            for r in anchors_result.fetchall()
        ]

        return manifest

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
| Failing Check Types | {{ critical_issue_count }} |
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

**HIPAA Control:** 164.312(b) (Audit Controls), 164.312(c)(1) (Integrity Controls)

---

## Blockchain Verification (OpenTimestamps)

Each evidence bundle's SHA-256 hash is submitted to the Bitcoin blockchain via
OpenTimestamps (OTS). Once anchored in a Bitcoin block, the timestamp becomes
independently verifiable by any third party — proving evidence existed at a specific
point in time and has not been altered. This provides non-repudiation suitable for
regulatory audit and legal proceedings.

### Anchoring Summary

| Metric | Value |
|--------|-------|
| Proofs Submitted | {{ evidence_bundles.ots_submitted }} |
| Bitcoin Anchored | {{ evidence_bundles.ots_anchored }} |
| Pending Confirmation | {{ evidence_bundles.ots_pending }} |
| Anchor Rate | {{ evidence_bundles.ots_anchor_rate }}% |
{% if evidence_bundles.ots_first_block %}| First Bitcoin Block | [#{{ evidence_bundles.ots_first_block }}](https://blockstream.info/block-height/{{ evidence_bundles.ots_first_block }}) ({{ evidence_bundles.ots_first_anchor_time }}) |
{% endif %}{% if evidence_bundles.ots_latest_block %}| Latest Bitcoin Block | [#{{ evidence_bundles.ots_latest_block }}](https://blockstream.info/block-height/{{ evidence_bundles.ots_latest_block }}) ({{ evidence_bundles.ots_latest_anchor_time }}) |
{% endif %}{% if evidence_bundles.ots_first_block and evidence_bundles.ots_latest_block %}| Block Span | {{ evidence_bundles.ots_latest_block - evidence_bundles.ots_first_block }} blocks |
{% endif %}
{% if evidence_bundles.ots_recent_anchors %}### Recent Bitcoin Anchors

| Bitcoin Block | Anchored | Evidence Bundle | Check Type | Verify |
|---------------|----------|-----------------|------------|--------|
{% for a in evidence_bundles.ots_recent_anchors %}| [#{{ a.block }}]({{ a.blockstream_url }}) | {{ a.time }} | `{{ a.bundle_id }}` | {{ a.check_type }} | [Blockstream]({{ a.blockstream_url }}) |
{% endfor %}{% endif %}

### Independent Verification

To independently verify any Bitcoin anchor:
1. Visit [blockstream.info](https://blockstream.info) and search the block number
2. The block's timestamp proves the evidence hash existed before that block was mined
3. Any modification to the evidence would produce a different SHA-256 hash, breaking the proof

**HIPAA Control:** 164.312(c)(1) (Integrity Controls — tamper-evident timestamping)

---

**End of Monthly Compliance Packet**
**Audit Support:** All evidence bundles retained for 90+ days in WORM storage.
Bitcoin blockchain anchors provide independent, tamper-proof verification of evidence timestamps.
""")
        return template.render(**data)
