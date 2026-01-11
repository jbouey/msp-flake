"""
Multi-Framework Compliance System

Enables OsirisCare to report against multiple compliance frameworks
(HIPAA, SOC 2, PCI DSS, NIST CSF) from the same infrastructure checks.

Design Principle: Same check → multiple framework mappings.
One backup verification can satisfy HIPAA 164.308(a)(7), SOC 2 A1.2,
PCI DSS 12.10.1, and NIST CSF PR.IP-4 simultaneously.
"""

from .schema import (
    ComplianceFramework,
    FrameworkControl,
    InfrastructureCheck,
    ApplianceFrameworkConfig,
    MultiFrameworkEvidence,
    ControlStatus,
    ComplianceScore,
)

from .framework_service import FrameworkService

__all__ = [
    "ComplianceFramework",
    "FrameworkControl",
    "InfrastructureCheck",
    "ApplianceFrameworkConfig",
    "MultiFrameworkEvidence",
    "ControlStatus",
    "ComplianceScore",
    "FrameworkService",
]
