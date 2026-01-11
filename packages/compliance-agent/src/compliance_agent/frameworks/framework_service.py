"""
Multi-Framework Compliance Service

Handles framework selection, control mapping, and multi-framework evidence generation.
This is the core business logic for the multi-framework compliance system.

Key responsibilities:
1. Load and cache control mappings from YAML
2. Map infrastructure checks to framework controls
3. Generate multi-framework evidence bundles
4. Calculate compliance scores per framework
5. Provide industry-based framework recommendations
"""

import yaml
import logging
from pathlib import Path
from typing import List, Dict, Optional, Set, Any
from datetime import datetime, timedelta, timezone

from .schema import (
    ComplianceFramework,
    FrameworkControl,
    InfrastructureCheck,
    ApplianceFrameworkConfig,
    MultiFrameworkEvidence,
    ComplianceScore,
    ControlStatus,
    FrameworkMetadata,
    get_recommended_frameworks,
)

logger = logging.getLogger(__name__)


class FrameworkService:
    """
    Service for managing multi-framework compliance mappings.

    This service provides:
    - Control mapping lookup (check → framework controls)
    - Multi-framework evidence generation
    - Compliance score calculation per framework
    - Industry-based framework recommendations

    Example usage:
        service = FrameworkService()

        # Get controls for a check
        controls = service.get_controls_for_check("backup_status")
        # Returns: {ComplianceFramework.HIPAA: ["164.308(a)(7)"], ...}

        # Create multi-framework evidence
        evidence = service.create_multi_framework_evidence(
            bundle_id="EB-001",
            check_id="backup_status",
            outcome="pass",
            enabled_frameworks=[ComplianceFramework.HIPAA, ComplianceFramework.SOC2]
        )

        # Calculate compliance score
        score = service.calculate_compliance_score(
            ComplianceFramework.HIPAA,
            evidence_bundles
        )
    """

    def __init__(self, mappings_path: Optional[Path] = None):
        """
        Initialize the framework service.

        Args:
            mappings_path: Optional path to control_mappings.yaml.
                          Defaults to the bundled mappings file.
        """
        self.mappings_path = mappings_path or (
            Path(__file__).parent / "mappings" / "control_mappings.yaml"
        )
        self._mappings: Dict = {}
        self._checks: Dict[str, InfrastructureCheck] = {}
        self._framework_metadata: Dict[ComplianceFramework, FrameworkMetadata] = {}
        self._control_details: Dict[ComplianceFramework, Dict[str, FrameworkControl]] = {}
        self._loaded = False

        # Load mappings on init
        self._load_mappings()

    def _load_mappings(self) -> None:
        """Load control mappings from YAML file"""
        try:
            if not self.mappings_path.exists():
                logger.warning(f"Mappings file not found: {self.mappings_path}")
                self._loaded = False
                return

            with open(self.mappings_path) as f:
                self._mappings = yaml.safe_load(f)

            # Parse checks
            for check_id, check_data in self._mappings.get("checks", {}).items():
                framework_controls: Dict[ComplianceFramework, List[str]] = {}

                for framework_name, controls in check_data.get("framework_mappings", {}).items():
                    try:
                        framework = ComplianceFramework(framework_name)
                        control_ids = [c["control_id"] for c in controls]
                        framework_controls[framework] = control_ids

                        # Store control details
                        if framework not in self._control_details:
                            self._control_details[framework] = {}

                        for ctrl in controls:
                            self._control_details[framework][ctrl["control_id"]] = FrameworkControl(
                                framework=framework,
                                control_id=ctrl["control_id"],
                                control_name=ctrl["control_name"],
                                description=ctrl.get("description", ""),
                                category=ctrl["category"],
                                subcategory=ctrl.get("subcategory"),
                                required=ctrl.get("required", True),
                            )
                    except ValueError:
                        logger.warning(f"Unknown framework: {framework_name}")
                        continue

                self._checks[check_id] = InfrastructureCheck(
                    check_id=check_id,
                    check_name=check_data["name"],
                    description=check_data["description"],
                    check_type=check_data["check_type"],
                    framework_controls=framework_controls,
                    runbook_id=check_data.get("runbook_id"),
                    evidence_type=check_data.get("evidence_type", "compliance_check"),
                )

            # Parse framework metadata
            for fw_name, fw_data in self._mappings.get("frameworks", {}).items():
                try:
                    framework = ComplianceFramework(fw_name)
                    self._framework_metadata[framework] = FrameworkMetadata(
                        framework=framework,
                        name=fw_data["name"],
                        version=fw_data["version"],
                        description=fw_data["description"],
                        regulatory_body=fw_data["regulatory_body"],
                        industry=fw_data["industry"],
                        categories=fw_data.get("categories", []),
                        documentation_url=fw_data.get("documentation_url"),
                    )
                except (ValueError, KeyError) as e:
                    logger.warning(f"Error parsing framework {fw_name}: {e}")

            self._loaded = True
            logger.info(
                f"Loaded {len(self._checks)} checks and "
                f"{len(self._framework_metadata)} frameworks"
            )

        except Exception as e:
            logger.error(f"Failed to load mappings: {e}")
            self._loaded = False

    def get_controls_for_check(
        self,
        check_id: str,
        frameworks: Optional[List[ComplianceFramework]] = None
    ) -> Dict[ComplianceFramework, List[str]]:
        """
        Get all framework controls satisfied by a given infrastructure check.

        Args:
            check_id: The infrastructure check ID (e.g., "backup_status")
            frameworks: Optional filter for specific frameworks

        Returns:
            Dict mapping framework to list of control IDs

        Example:
            controls = service.get_controls_for_check("backup_status")
            # Returns:
            # {
            #     ComplianceFramework.HIPAA: ["164.308(a)(7)(ii)(A)", "164.310(d)(2)(iv)"],
            #     ComplianceFramework.SOC2: ["A1.2", "A1.3"],
            #     ...
            # }
        """
        check = self._checks.get(check_id)
        if not check:
            return {}

        if frameworks:
            return {
                fw: controls
                for fw, controls in check.framework_controls.items()
                if fw in frameworks
            }
        return check.framework_controls

    def get_hipaa_controls_for_check(self, check_id: str) -> List[str]:
        """
        Backward compatibility: Get HIPAA controls for a check.

        Args:
            check_id: The infrastructure check ID

        Returns:
            List of HIPAA control IDs
        """
        controls = self.get_controls_for_check(
            check_id,
            frameworks=[ComplianceFramework.HIPAA]
        )
        return controls.get(ComplianceFramework.HIPAA, [])

    def get_checks_for_framework(
        self,
        framework: ComplianceFramework
    ) -> List[InfrastructureCheck]:
        """
        Get all infrastructure checks that map to a specific framework.

        Args:
            framework: The compliance framework

        Returns:
            List of InfrastructureCheck objects
        """
        return [
            check for check in self._checks.values()
            if framework in check.framework_controls
        ]

    def get_all_controls_for_framework(
        self,
        framework: ComplianceFramework
    ) -> Set[str]:
        """
        Get all control IDs for a framework (union of all check mappings).

        Args:
            framework: The compliance framework

        Returns:
            Set of control IDs
        """
        controls: Set[str] = set()
        for check in self._checks.values():
            if framework in check.framework_controls:
                controls.update(check.framework_controls[framework])
        return controls

    def get_control_details(
        self,
        framework: ComplianceFramework,
        control_id: str
    ) -> Optional[FrameworkControl]:
        """
        Get detailed information about a specific control.

        Args:
            framework: The compliance framework
            control_id: The control ID

        Returns:
            FrameworkControl with full details, or None if not found
        """
        return self._control_details.get(framework, {}).get(control_id)

    def get_check_by_id(self, check_id: str) -> Optional[InfrastructureCheck]:
        """Get infrastructure check by ID"""
        return self._checks.get(check_id)

    def get_all_checks(self) -> List[InfrastructureCheck]:
        """Get all infrastructure checks"""
        return list(self._checks.values())

    def create_multi_framework_evidence(
        self,
        bundle_id: str,
        appliance_id: str,
        site_id: str,
        check_id: str,
        check_type: str,
        outcome: str,
        raw_data: Dict[str, Any],
        signature: str = "",
        storage_locations: Optional[List[str]] = None,
        enabled_frameworks: Optional[List[ComplianceFramework]] = None,
        ots_proof: Optional[str] = None,
    ) -> MultiFrameworkEvidence:
        """
        Create an evidence bundle tagged for multiple frameworks.

        This is the key abstraction - one evidence bundle satisfies
        requirements across all enabled frameworks for this appliance.

        Args:
            bundle_id: Unique evidence bundle ID
            appliance_id: Appliance that generated the evidence
            site_id: Site the appliance belongs to
            check_id: Infrastructure check that was performed
            check_type: Type of check (windows, linux, network, etc.)
            outcome: Result of the check (pass, fail, remediated, etc.)
            raw_data: Raw check data for audit trail
            signature: Ed25519 signature of the bundle
            storage_locations: Where the evidence is stored (WORM, etc.)
            enabled_frameworks: Frameworks to tag (defaults to all)
            ots_proof: OpenTimestamps proof if available

        Returns:
            MultiFrameworkEvidence bundle tagged for all applicable frameworks
        """
        framework_mappings = self.get_controls_for_check(check_id, enabled_frameworks)

        # Extract HIPAA controls for backward compatibility
        hipaa_controls = framework_mappings.get(ComplianceFramework.HIPAA, [])

        return MultiFrameworkEvidence(
            bundle_id=bundle_id,
            appliance_id=appliance_id,
            site_id=site_id,
            check_id=check_id,
            check_type=check_type,
            timestamp=datetime.now(timezone.utc),
            outcome=outcome,
            framework_mappings=framework_mappings,
            hipaa_controls=hipaa_controls,
            raw_data=raw_data,
            signature=signature,
            storage_locations=storage_locations or [],
            ots_proof=ots_proof,
        )

    def calculate_compliance_score(
        self,
        framework: ComplianceFramework,
        evidence_bundles: List[MultiFrameworkEvidence],
        evidence_window_days: int = 30,
    ) -> ComplianceScore:
        """
        Calculate compliance score for a specific framework.

        The score is based on the latest evidence for each control.
        Controls with passing evidence count toward compliance.

        Args:
            framework: The compliance framework to score
            evidence_bundles: List of evidence bundles to analyze
            evidence_window_days: Only consider evidence within this window

        Returns:
            ComplianceScore with detailed breakdown
        """
        all_controls = self.get_all_controls_for_framework(framework)
        control_status: Dict[str, ControlStatus] = {
            c: ControlStatus.UNKNOWN for c in all_controls
        }

        # Filter to recent evidence
        cutoff = datetime.now(timezone.utc) - timedelta(days=evidence_window_days)
        recent_evidence = [
            e for e in evidence_bundles
            if e.timestamp >= cutoff
        ]

        # Sort by timestamp (newest first) to get latest status
        sorted_evidence = sorted(
            recent_evidence,
            key=lambda e: e.timestamp,
            reverse=True
        )

        # Determine status for each control based on latest evidence
        for evidence in sorted_evidence:
            if framework not in evidence.framework_mappings:
                continue

            for control_id in evidence.framework_mappings[framework]:
                # Only update if still unknown (first/latest wins)
                if control_status.get(control_id) == ControlStatus.UNKNOWN:
                    if evidence.outcome in ("pass", "remediated"):
                        control_status[control_id] = ControlStatus.PASS
                    elif evidence.outcome == "fail":
                        control_status[control_id] = ControlStatus.FAIL

        # Calculate metrics
        passing = sum(1 for s in control_status.values() if s == ControlStatus.PASS)
        failing = sum(1 for s in control_status.values() if s == ControlStatus.FAIL)
        unknown = sum(1 for s in control_status.values() if s == ControlStatus.UNKNOWN)
        total = len(all_controls)

        # Get framework metadata
        fw_meta = self._framework_metadata.get(framework)

        return ComplianceScore(
            framework=framework,
            framework_name=fw_meta.name if fw_meta else framework.value,
            framework_version=fw_meta.version if fw_meta else "",
            total_controls=total,
            passing_controls=passing,
            failing_controls=failing,
            unknown_controls=unknown,
            score_percentage=round((passing / total) * 100, 1) if total > 0 else 0,
            control_status=control_status,
            evidence_window_days=evidence_window_days,
        )

    def calculate_all_scores(
        self,
        enabled_frameworks: List[ComplianceFramework],
        evidence_bundles: List[MultiFrameworkEvidence],
        evidence_window_days: int = 30,
    ) -> Dict[ComplianceFramework, ComplianceScore]:
        """
        Calculate compliance scores for all enabled frameworks.

        Args:
            enabled_frameworks: List of frameworks to score
            evidence_bundles: Evidence to analyze
            evidence_window_days: Evidence window

        Returns:
            Dict mapping framework to ComplianceScore
        """
        return {
            framework: self.calculate_compliance_score(
                framework, evidence_bundles, evidence_window_days
            )
            for framework in enabled_frameworks
        }

    def get_framework_metadata(
        self,
        framework: ComplianceFramework
    ) -> Optional[FrameworkMetadata]:
        """Get metadata about a framework (name, version, categories, etc.)"""
        return self._framework_metadata.get(framework)

    def get_all_framework_metadata(self) -> Dict[ComplianceFramework, FrameworkMetadata]:
        """Get metadata for all frameworks"""
        return self._framework_metadata.copy()

    def get_industry_frameworks(
        self,
        industry: str
    ) -> List[ComplianceFramework]:
        """
        Get recommended frameworks for an industry.

        Args:
            industry: Industry name (healthcare, technology, retail, etc.)

        Returns:
            List of recommended ComplianceFramework values
        """
        return get_recommended_frameworks(industry)

    def get_industry_recommendations(self) -> Dict[str, Dict[str, Any]]:
        """
        Get framework recommendations for all industries.

        Returns:
            Dict with industry as key and recommendation details as value
        """
        recommendations = self._mappings.get("industry_recommendations", {})
        result = {}

        for industry, data in recommendations.items():
            result[industry] = {
                "primary": data.get("primary", "nist_csf"),
                "recommended": data.get("recommended", ["nist_csf"]),
                "description": data.get("description", ""),
            }

        return result

    def map_legacy_hipaa_controls(
        self,
        hipaa_controls: List[str]
    ) -> Dict[ComplianceFramework, List[str]]:
        """
        Map legacy HIPAA-only controls to all frameworks.

        This helps migrate existing evidence bundles that only have
        HIPAA controls to multi-framework format.

        Args:
            hipaa_controls: List of HIPAA control IDs

        Returns:
            Dict mapping all frameworks to equivalent controls
        """
        # Find which check these HIPAA controls came from
        framework_mappings: Dict[ComplianceFramework, List[str]] = {
            ComplianceFramework.HIPAA: hipaa_controls
        }

        for check in self._checks.values():
            hipaa_check_controls = check.framework_controls.get(
                ComplianceFramework.HIPAA, []
            )

            # If any HIPAA control matches, include all other frameworks
            if any(ctrl in hipaa_check_controls for ctrl in hipaa_controls):
                for framework, controls in check.framework_controls.items():
                    if framework != ComplianceFramework.HIPAA:
                        if framework not in framework_mappings:
                            framework_mappings[framework] = []
                        framework_mappings[framework].extend(controls)

        return framework_mappings

    @property
    def is_loaded(self) -> bool:
        """Check if mappings were loaded successfully"""
        return self._loaded

    @property
    def check_count(self) -> int:
        """Get number of loaded checks"""
        return len(self._checks)

    @property
    def framework_count(self) -> int:
        """Get number of supported frameworks"""
        return len(self._framework_metadata)


# Global singleton instance
_framework_service: Optional[FrameworkService] = None


def get_framework_service() -> FrameworkService:
    """
    Get the global FrameworkService instance.

    Creates the instance on first call (lazy initialization).
    """
    global _framework_service
    if _framework_service is None:
        _framework_service = FrameworkService()
    return _framework_service
