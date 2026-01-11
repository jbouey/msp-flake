"""
Multi-Framework Compliance Control Mapping Schema.

Design Principle: Same infrastructure check → multiple framework mappings.
One backup check can satisfy HIPAA 164.308(a)(7), SOC 2 A1.2,
PCI DSS 12.10.1, and NIST CSF PR.IP-4 simultaneously.

This module defines the data models for:
- Compliance frameworks (HIPAA, SOC2, PCI-DSS, NIST CSF, CIS)
- Framework controls and their metadata
- Infrastructure check to control mappings
- Per-appliance framework configuration
- Multi-framework evidence bundles
"""

from enum import Enum
from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime, timezone


class ComplianceFramework(str, Enum):
    """
    Supported compliance frameworks.

    Each framework represents a different regulatory or industry standard
    that OsirisCare can report against.
    """
    HIPAA = "hipaa"           # Healthcare (default)
    SOC2 = "soc2"             # Technology/SaaS
    PCI_DSS = "pci_dss"       # Retail/Payment processing
    NIST_CSF = "nist_csf"     # General cybersecurity
    CIS_CONTROLS = "cis"      # CIS Critical Security Controls


class ControlStatus(str, Enum):
    """Status of a compliance control based on evidence"""
    PASS = "pass"
    FAIL = "fail"
    REMEDIATED = "remediated"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "n/a"


class FrameworkControl(BaseModel):
    """
    A control within a specific compliance framework.

    Examples:
    - HIPAA: 164.308(a)(7)(ii)(A) - Data Backup Plan
    - SOC 2: A1.2 - System Backup
    - PCI DSS: 12.10.1 - Incident Response Plan
    - NIST CSF: PR.IP-4 - Backups of information
    """
    model_config = ConfigDict(use_enum_values=True)

    framework: ComplianceFramework
    control_id: str  # e.g., "164.308(a)(7)(ii)(A)", "CC6.1", "PR.IP-4"
    control_name: str
    description: str
    category: str  # e.g., "Administrative Safeguards", "Security", "Protect"
    subcategory: Optional[str] = None
    required: bool = True  # False for addressable/optional controls
    evidence_requirements: List[str] = Field(default_factory=list)


class InfrastructureCheck(BaseModel):
    """
    Maps a single infrastructure check to multiple framework controls.

    This is the core abstraction - one check satisfies requirements
    across many frameworks simultaneously.

    Example: backup_status check maps to:
    - HIPAA: 164.308(a)(7)(ii)(A), 164.310(d)(2)(iv)
    - SOC 2: A1.2, A1.3
    - PCI DSS: 12.10.1
    - NIST CSF: PR.IP-4, RC.RP-1
    """
    model_config = ConfigDict(use_enum_values=True)

    check_id: str  # e.g., "backup_status", "encryption_at_rest"
    check_name: str
    description: str
    check_type: str  # "windows", "linux", "network", "application"

    # Framework mappings - which controls this check satisfies
    # Dict[framework -> List[control_ids]]
    framework_controls: Dict[ComplianceFramework, List[str]] = Field(default_factory=dict)

    # Runbook reference for auto-remediation
    runbook_id: Optional[str] = None

    # Evidence configuration
    evidence_type: str = "compliance_check"
    evidence_retention_days: int = 365

    def get_controls_for_framework(
        self,
        framework: ComplianceFramework
    ) -> List[str]:
        """Get control IDs for a specific framework"""
        return self.framework_controls.get(framework, [])

    def get_all_hipaa_controls(self) -> List[str]:
        """Backward compatibility - get HIPAA controls"""
        return self.get_controls_for_framework(ComplianceFramework.HIPAA)


class ApplianceFrameworkConfig(BaseModel):
    """
    Per-appliance framework configuration.

    Allows each appliance to report against different frameworks.
    A healthcare clinic uses HIPAA, a SaaS company uses SOC 2,
    a retailer uses PCI DSS - all from the same appliance software.
    """
    model_config = ConfigDict(use_enum_values=True)

    appliance_id: str
    site_id: str
    enabled_frameworks: List[ComplianceFramework] = Field(
        default_factory=lambda: [ComplianceFramework.HIPAA]
    )
    primary_framework: ComplianceFramework = ComplianceFramework.HIPAA

    # Industry for recommendations
    industry: str = "healthcare"

    # Framework-specific metadata
    # e.g., {"pci_dss": {"merchant_level": 4, "saq_type": "A"}}
    framework_metadata: Dict[str, Dict[str, Any]] = Field(default_factory=dict)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def is_framework_enabled(self, framework: ComplianceFramework) -> bool:
        """Check if a framework is enabled for this appliance"""
        return framework in self.enabled_frameworks


class MultiFrameworkEvidence(BaseModel):
    """
    Evidence bundle extended for multi-framework compliance.

    Same evidence, tagged for all applicable frameworks.
    One backup verification evidence bundle satisfies requirements
    across HIPAA, SOC 2, PCI DSS, and NIST CSF.
    """
    model_config = ConfigDict(use_enum_values=True)

    bundle_id: str
    appliance_id: str
    site_id: str
    check_id: str
    check_type: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    outcome: str  # "pass", "fail", "remediated", "escalated"

    # Multi-framework tagging
    # Dict[framework -> List[control_ids]]
    framework_mappings: Dict[ComplianceFramework, List[str]] = Field(default_factory=dict)

    # Backward compatibility - HIPAA controls flat list
    hipaa_controls: List[str] = Field(default_factory=list)

    # Evidence data
    raw_data: Dict[str, Any] = Field(default_factory=dict)
    signature: str = ""
    signature_algorithm: str = "ed25519"
    storage_locations: List[str] = Field(default_factory=list)

    # OpenTimestamps proof (if enabled)
    ots_proof: Optional[str] = None

    def get_controls_for_framework(
        self,
        framework: ComplianceFramework
    ) -> List[str]:
        """Get controls this evidence satisfies for a framework"""
        return self.framework_mappings.get(framework, [])

    def satisfies_control(
        self,
        framework: ComplianceFramework,
        control_id: str
    ) -> bool:
        """Check if this evidence satisfies a specific control"""
        return control_id in self.get_controls_for_framework(framework)


class ComplianceScore(BaseModel):
    """
    Compliance score for a specific framework.

    Calculated by aggregating evidence outcomes across all
    controls mapped to the framework.
    """
    model_config = ConfigDict(use_enum_values=True)

    framework: ComplianceFramework
    framework_name: str
    framework_version: str = ""

    # Score metrics
    total_controls: int = 0
    passing_controls: int = 0
    failing_controls: int = 0
    unknown_controls: int = 0

    # Percentage (0-100)
    score_percentage: float = 0.0

    # Control-level status
    control_status: Dict[str, ControlStatus] = Field(default_factory=dict)

    # Metadata
    calculated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    evidence_window_days: int = 30

    @property
    def is_compliant(self) -> bool:
        """Check if score meets compliance threshold (80%)"""
        return self.score_percentage >= 80.0

    @property
    def at_risk(self) -> bool:
        """Check if score indicates risk (below 70%)"""
        return self.score_percentage < 70.0


class FrameworkMetadata(BaseModel):
    """
    Metadata about a compliance framework.

    Used for display, reporting, and industry recommendations.
    """
    model_config = ConfigDict(use_enum_values=True)

    framework: ComplianceFramework
    name: str
    version: str
    description: str
    regulatory_body: str
    industry: str  # Primary industry
    categories: List[str] = Field(default_factory=list)

    # URLs for reference
    documentation_url: Optional[str] = None


# Industry to framework recommendations mapping
INDUSTRY_FRAMEWORKS: Dict[str, List[ComplianceFramework]] = {
    "healthcare": [ComplianceFramework.HIPAA, ComplianceFramework.NIST_CSF],
    "technology": [ComplianceFramework.SOC2, ComplianceFramework.NIST_CSF],
    "saas": [ComplianceFramework.SOC2, ComplianceFramework.NIST_CSF],
    "retail": [ComplianceFramework.PCI_DSS, ComplianceFramework.SOC2],
    "finance": [ComplianceFramework.SOC2, ComplianceFramework.PCI_DSS, ComplianceFramework.NIST_CSF],
    "government": [ComplianceFramework.NIST_CSF, ComplianceFramework.CIS_CONTROLS],
    "general": [ComplianceFramework.NIST_CSF, ComplianceFramework.CIS_CONTROLS],
}


def get_recommended_frameworks(industry: str) -> List[ComplianceFramework]:
    """Get recommended frameworks for an industry"""
    return INDUSTRY_FRAMEWORKS.get(
        industry.lower(),
        [ComplianceFramework.NIST_CSF]
    )
