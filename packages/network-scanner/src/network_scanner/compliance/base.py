"""
Base classes for compliance checks.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .._types import Device, DeviceComplianceCheck, now_utc


@dataclass
class ComplianceResult:
    """Result of a compliance check."""
    check_type: str
    status: str  # pass, warn, fail
    hipaa_control: Optional[str] = None
    details: dict = field(default_factory=dict)
    checked_at: datetime = field(default_factory=now_utc)

    def to_check(self, device_id: str) -> DeviceComplianceCheck:
        """Convert to DeviceComplianceCheck for storage."""
        return DeviceComplianceCheck(
            device_id=device_id,
            check_type=self.check_type,
            hipaa_control=self.hipaa_control,
            status=self.status,
            details=self.details,
            checked_at=self.checked_at,
        )


class ComplianceCheck(ABC):
    """Base class for compliance checks."""

    @property
    @abstractmethod
    def check_type(self) -> str:
        """Type identifier for this check."""
        pass

    @property
    @abstractmethod
    def hipaa_control(self) -> Optional[str]:
        """HIPAA control this check maps to."""
        pass

    @property
    @abstractmethod
    def applicable_device_types(self) -> list[str]:
        """Device types this check applies to."""
        pass

    @abstractmethod
    async def run(self, device: Device) -> ComplianceResult:
        """
        Run this compliance check on a device.

        Returns ComplianceResult with pass/warn/fail status.
        """
        pass

    def is_applicable(self, device: Device) -> bool:
        """Check if this check applies to the device."""
        return device.device_type.value in self.applicable_device_types
