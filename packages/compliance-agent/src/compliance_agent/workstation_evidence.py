"""
Workstation Evidence Generation.

Creates evidence bundles from workstation compliance data.
Same signing and chaining as server evidence.
Generates per-workstation and site-summary bundles.

HIPAA Requirements:
- §164.312(b) - Audit Controls
- §164.312(c)(1) - Integrity Controls
"""

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field

from .workstation_checks import WorkstationComplianceResult, CheckResult, ComplianceStatus
from .models import EvidenceBundle, ActionTaken

logger = logging.getLogger(__name__)


@dataclass
class WorkstationEvidenceBundle:
    """
    Evidence bundle for a single workstation's compliance state.

    Follows same structure as server evidence bundles but
    with workstation-specific metadata.
    """

    bundle_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    version: str = "1.0"

    # Site and device identification
    site_id: str = ""
    workstation_id: str = ""  # hostname or unique ID
    ip_address: Optional[str] = None
    os_name: Optional[str] = None

    # Timestamps
    timestamp_start: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    timestamp_end: Optional[datetime] = None

    # Compliance data
    checks: List[Dict[str, Any]] = field(default_factory=list)
    overall_status: str = "unknown"
    compliant_count: int = 0
    total_checks: int = 0
    compliance_percentage: float = 0.0

    # HIPAA mapping
    hipaa_controls: List[str] = field(default_factory=list)

    # Evidence integrity
    evidence_hash: str = ""
    previous_bundle_hash: Optional[str] = None  # For chain linking

    def __post_init__(self):
        """Calculate evidence hash after initialization."""
        if not self.evidence_hash:
            self.evidence_hash = self._calculate_hash()

    def _calculate_hash(self) -> str:
        """Calculate SHA256 hash of evidence content."""
        content = {
            "site_id": self.site_id,
            "workstation_id": self.workstation_id,
            "timestamp_start": self.timestamp_start.isoformat(),
            "checks": self.checks,
            "overall_status": self.overall_status,
        }
        import json
        content_str = json.dumps(content, sort_keys=True)
        return hashlib.sha256(content_str.encode()).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage/API."""
        return {
            "bundle_id": self.bundle_id,
            "version": self.version,
            "device_type": "workstation",
            "site_id": self.site_id,
            "workstation_id": self.workstation_id,
            "ip_address": self.ip_address,
            "os_name": self.os_name,
            "timestamp_start": self.timestamp_start.isoformat(),
            "timestamp_end": self.timestamp_end.isoformat() if self.timestamp_end else None,
            "checks": self.checks,
            "overall_status": self.overall_status,
            "compliant_count": self.compliant_count,
            "total_checks": self.total_checks,
            "compliance_percentage": self.compliance_percentage,
            "hipaa_controls": self.hipaa_controls,
            "evidence_hash": self.evidence_hash,
            "previous_bundle_hash": self.previous_bundle_hash,
        }


@dataclass
class SiteWorkstationSummary:
    """
    Aggregate summary of all workstation compliance for a site.

    Provides site-level view of workstation fleet compliance.
    """

    bundle_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    version: str = "1.0"

    # Site identification
    site_id: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Fleet statistics
    total_workstations: int = 0
    online_workstations: int = 0
    compliant_workstations: int = 0
    drifted_workstations: int = 0
    error_workstations: int = 0
    unknown_workstations: int = 0

    # Per-check compliance rates
    check_compliance: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # e.g., {"bitlocker": {"compliant": 45, "drifted": 5, "rate": 90.0}}

    # Overall metrics
    overall_compliance_rate: float = 0.0
    hipaa_controls: List[str] = field(default_factory=list)

    # Individual workstation bundle references
    workstation_bundle_ids: List[str] = field(default_factory=list)

    # Evidence integrity
    evidence_hash: str = ""

    def __post_init__(self):
        """Calculate evidence hash after initialization."""
        if not self.evidence_hash:
            self.evidence_hash = self._calculate_hash()

    def _calculate_hash(self) -> str:
        """Calculate SHA256 hash of summary content."""
        content = {
            "site_id": self.site_id,
            "timestamp": self.timestamp.isoformat(),
            "total_workstations": self.total_workstations,
            "check_compliance": self.check_compliance,
            "workstation_bundle_ids": self.workstation_bundle_ids,
        }
        import json
        content_str = json.dumps(content, sort_keys=True)
        return hashlib.sha256(content_str.encode()).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage/API."""
        return {
            "bundle_id": self.bundle_id,
            "version": self.version,
            "bundle_type": "site_workstation_summary",
            "site_id": self.site_id,
            "timestamp": self.timestamp.isoformat(),
            "total_workstations": self.total_workstations,
            "online_workstations": self.online_workstations,
            "compliant_workstations": self.compliant_workstations,
            "drifted_workstations": self.drifted_workstations,
            "error_workstations": self.error_workstations,
            "unknown_workstations": self.unknown_workstations,
            "check_compliance": self.check_compliance,
            "overall_compliance_rate": self.overall_compliance_rate,
            "hipaa_controls": self.hipaa_controls,
            "workstation_bundle_ids": self.workstation_bundle_ids,
            "evidence_hash": self.evidence_hash,
        }


class WorkstationEvidenceGenerator:
    """
    Generate evidence bundles from workstation compliance data.

    Creates:
    1. Per-workstation evidence bundles
    2. Site-level workstation summary bundles

    Follows same signing patterns as server evidence.
    """

    # All HIPAA controls covered by workstation checks
    ALL_HIPAA_CONTROLS = [
        "§164.312(a)(2)(iv)",  # Encryption (BitLocker)
        "§164.308(a)(5)(ii)(B)",  # Malware protection (Defender, Patches)
        "§164.312(a)(1)",  # Access Control (Firewall)
        "§164.312(a)(2)(iii)",  # Automatic Logoff (Screen Lock)
    ]

    def __init__(
        self,
        site_id: str,
        signing_key: Optional[bytes] = None,
        previous_bundle_hashes: Optional[Dict[str, str]] = None,
    ):
        """
        Initialize evidence generator.

        Args:
            site_id: Site identifier for all bundles
            signing_key: Ed25519 private key for signing (optional)
            previous_bundle_hashes: Dict of workstation_id -> last bundle hash for chaining
        """
        self.site_id = site_id
        self.signing_key = signing_key
        self.previous_bundle_hashes = previous_bundle_hashes or {}

    def create_workstation_bundle(
        self,
        compliance_result: WorkstationComplianceResult,
        workstation_metadata: Optional[Dict[str, Any]] = None,
    ) -> WorkstationEvidenceBundle:
        """
        Create evidence bundle for a single workstation.

        Args:
            compliance_result: Result from WorkstationComplianceChecker.run_all_checks()
            workstation_metadata: Optional additional metadata (OS, etc)

        Returns:
            WorkstationEvidenceBundle ready for storage/signing
        """
        metadata = workstation_metadata or {}

        # Convert check results to evidence format
        checks_evidence = []
        all_hipaa_controls = set()
        compliant_count = 0

        for check in compliance_result.checks:
            check_dict = check.to_dict()
            checks_evidence.append(check_dict)
            all_hipaa_controls.update(check.hipaa_controls)
            if check.compliant:
                compliant_count += 1

        total_checks = len(compliance_result.checks)
        compliance_percentage = (compliant_count / total_checks * 100) if total_checks > 0 else 0.0

        # Get previous bundle hash for this workstation (chain linking)
        previous_hash = self.previous_bundle_hashes.get(compliance_result.hostname)

        bundle = WorkstationEvidenceBundle(
            site_id=self.site_id,
            workstation_id=compliance_result.hostname,
            ip_address=compliance_result.ip_address,
            os_name=metadata.get("os_name"),
            timestamp_start=compliance_result.timestamp,
            timestamp_end=datetime.now(timezone.utc),
            checks=checks_evidence,
            overall_status=compliance_result.overall_status.value,
            compliant_count=compliant_count,
            total_checks=total_checks,
            compliance_percentage=compliance_percentage,
            hipaa_controls=list(all_hipaa_controls),
            previous_bundle_hash=previous_hash,
        )

        # Update chain tracking
        self.previous_bundle_hashes[compliance_result.hostname] = bundle.evidence_hash

        logger.info(
            f"Created workstation evidence bundle for {compliance_result.hostname}: "
            f"{compliant_count}/{total_checks} compliant ({compliance_percentage:.1f}%)"
        )

        return bundle

    def create_site_summary(
        self,
        workstation_bundles: List[WorkstationEvidenceBundle],
        total_discovered: int = 0,
        online_count: int = 0,
    ) -> SiteWorkstationSummary:
        """
        Create site-level summary from individual workstation bundles.

        Args:
            workstation_bundles: List of WorkstationEvidenceBundle from checked workstations
            total_discovered: Total workstations discovered from AD (may include offline)
            online_count: Number of online workstations

        Returns:
            SiteWorkstationSummary with aggregated metrics
        """
        # Initialize counters
        compliant = 0
        drifted = 0
        error = 0
        unknown = 0

        # Per-check compliance tracking
        check_stats: Dict[str, Dict[str, int]] = {}
        for check_type in ["bitlocker", "defender", "patches", "firewall", "screen_lock"]:
            check_stats[check_type] = {"compliant": 0, "drifted": 0, "error": 0}

        bundle_ids = []

        for bundle in workstation_bundles:
            bundle_ids.append(bundle.bundle_id)

            # Count overall status
            if bundle.overall_status == "compliant":
                compliant += 1
            elif bundle.overall_status == "drifted":
                drifted += 1
            elif bundle.overall_status == "error":
                error += 1
            else:
                unknown += 1

            # Count per-check status
            for check in bundle.checks:
                check_type = check.get("check_type", "unknown")
                if check_type in check_stats:
                    status = check.get("status", "unknown")
                    if status == "compliant":
                        check_stats[check_type]["compliant"] += 1
                    elif status == "drifted":
                        check_stats[check_type]["drifted"] += 1
                    else:
                        check_stats[check_type]["error"] += 1

        # Calculate per-check compliance rates
        check_compliance = {}
        total_checked = len(workstation_bundles)
        for check_type, stats in check_stats.items():
            rate = (stats["compliant"] / total_checked * 100) if total_checked > 0 else 0.0
            check_compliance[check_type] = {
                "compliant": stats["compliant"],
                "drifted": stats["drifted"],
                "error": stats["error"],
                "rate": round(rate, 1),
            }

        # Calculate overall compliance rate
        overall_rate = (compliant / total_checked * 100) if total_checked > 0 else 0.0

        summary = SiteWorkstationSummary(
            site_id=self.site_id,
            total_workstations=total_discovered or total_checked,
            online_workstations=online_count or total_checked,
            compliant_workstations=compliant,
            drifted_workstations=drifted,
            error_workstations=error,
            unknown_workstations=unknown,
            check_compliance=check_compliance,
            overall_compliance_rate=round(overall_rate, 1),
            hipaa_controls=self.ALL_HIPAA_CONTROLS,
            workstation_bundle_ids=bundle_ids,
        )

        logger.info(
            f"Created site workstation summary for {self.site_id}: "
            f"{compliant}/{total_checked} compliant ({overall_rate:.1f}%)"
        )

        return summary

    def convert_to_standard_bundle(
        self,
        workstation_bundle: WorkstationEvidenceBundle,
        deployment_mode: str = "direct",
        reseller_id: Optional[str] = None,
    ) -> EvidenceBundle:
        """
        Convert WorkstationEvidenceBundle to standard EvidenceBundle format.

        This allows workstation evidence to use the same signing and storage
        infrastructure as server evidence.

        Args:
            workstation_bundle: Workstation-specific bundle
            deployment_mode: "direct" or "reseller"
            reseller_id: MSP reseller ID if mode=reseller

        Returns:
            Standard EvidenceBundle compatible with existing evidence pipeline
        """
        # Map workstation overall status to evidence outcome
        outcome_map = {
            "compliant": "pass",
            "drifted": "drift",
            "error": "error",
            "unknown": "alert",
        }
        outcome = outcome_map.get(workstation_bundle.overall_status, "alert")

        return EvidenceBundle(
            bundle_id=workstation_bundle.bundle_id,
            site_id=workstation_bundle.site_id,
            host_id=workstation_bundle.workstation_id,
            deployment_mode=deployment_mode,
            reseller_id=reseller_id,
            timestamp_start=workstation_bundle.timestamp_start,
            timestamp_end=workstation_bundle.timestamp_end or datetime.now(timezone.utc),
            check="workstation_compliance",
            outcome=outcome,
            pre_state={
                "device_type": "workstation",
                "ip_address": workstation_bundle.ip_address,
                "os_name": workstation_bundle.os_name,
            },
            post_state={
                "overall_status": workstation_bundle.overall_status,
                "compliance_percentage": workstation_bundle.compliance_percentage,
                "checks": workstation_bundle.checks,
            },
            actions_taken=[],  # Workstation checks are read-only
            hipaa_controls=workstation_bundle.hipaa_controls,
            rollback_available=False,
        )


# Convenience function for appliance agent
def create_workstation_evidence(
    site_id: str,
    compliance_results: List[WorkstationComplianceResult],
    total_discovered: int = 0,
    online_count: int = 0,
) -> Dict[str, Any]:
    """
    Convenience function for appliance agent integration.

    Creates all workstation evidence bundles and site summary.

    Returns:
        Dict with 'workstation_bundles' and 'site_summary'
    """
    generator = WorkstationEvidenceGenerator(site_id=site_id)

    # Create per-workstation bundles
    workstation_bundles = []
    for result in compliance_results:
        bundle = generator.create_workstation_bundle(result)
        workstation_bundles.append(bundle)

    # Create site summary
    summary = generator.create_site_summary(
        workstation_bundles,
        total_discovered=total_discovered,
        online_count=online_count,
    )

    return {
        "workstation_bundles": [b.to_dict() for b in workstation_bundles],
        "site_summary": summary.to_dict(),
    }
