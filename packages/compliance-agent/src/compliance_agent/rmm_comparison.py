"""
RMM Comparison Engine - Workstation Deduplication and Gap Analysis.

Compares our AD-based workstation discovery data against RMM tool data
to identify duplicates, gaps, and provide reconciliation recommendations.

Supported RMM Tools:
- ConnectWise Automate (LabTech)
- Datto RMM (Autotask)
- NinjaRMM
- Syncro
- Manual CSV import

HIPAA Relevance:
- §164.308(a)(1) - Risk Analysis (complete device inventory)
- §164.310(d)(1) - Device/Media Controls (accurate asset tracking)
"""

import asyncio
import csv
import io
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Any, Optional, List, Tuple, Set
from collections import defaultdict

logger = logging.getLogger(__name__)


class RMMProvider(str, Enum):
    """Supported RMM providers."""
    CONNECTWISE = "connectwise"
    DATTO = "datto"
    NINJA = "ninja"
    SYNCRO = "syncro"
    MANUAL = "manual"


class MatchConfidence(str, Enum):
    """Match confidence levels for device correlation."""
    EXACT = "exact"           # 100% - All identifiers match
    HIGH = "high"             # 80%+ - Multiple identifiers match
    MEDIUM = "medium"         # 50-80% - Some identifiers match
    LOW = "low"               # <50% - Only hostname fuzzy match
    NO_MATCH = "no_match"     # 0% - No correlation found


class GapType(str, Enum):
    """Type of coverage gap identified."""
    MISSING_FROM_RMM = "missing_from_rmm"     # In our system but not RMM
    MISSING_FROM_AD = "missing_from_ad"       # In RMM but not our AD discovery
    DUPLICATE = "duplicate"                   # Same device tracked twice
    STALE_RMM = "stale_rmm"                   # RMM has stale/offline entry
    STALE_AD = "stale_ad"                     # AD has stale/disabled entry


@dataclass
class RMMDevice:
    """Normalized device record from any RMM tool."""

    # Identity
    hostname: str
    device_id: Optional[str] = None
    serial_number: Optional[str] = None

    # Network
    ip_address: Optional[str] = None
    mac_address: Optional[str] = None

    # System
    os_name: Optional[str] = None
    os_version: Optional[str] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None

    # RMM metadata
    rmm_provider: RMMProvider = RMMProvider.MANUAL
    rmm_agent_id: Optional[str] = None
    rmm_last_seen: Optional[datetime] = None
    rmm_online: bool = False

    # Extra data from RMM
    extra_data: Dict[str, Any] = field(default_factory=dict)

    def normalize_hostname(self) -> str:
        """Return normalized hostname for comparison."""
        return self.hostname.upper().strip().split('.')[0] if self.hostname else ""

    def normalize_mac(self) -> Optional[str]:
        """Return normalized MAC address (uppercase, no separators)."""
        if not self.mac_address:
            return None
        return re.sub(r'[^A-Fa-f0-9]', '', self.mac_address).upper()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API/evidence."""
        return {
            "hostname": self.hostname,
            "device_id": self.device_id,
            "serial_number": self.serial_number,
            "ip_address": self.ip_address,
            "mac_address": self.mac_address,
            "os_name": self.os_name,
            "os_version": self.os_version,
            "manufacturer": self.manufacturer,
            "model": self.model,
            "rmm_provider": self.rmm_provider.value,
            "rmm_agent_id": self.rmm_agent_id,
            "rmm_last_seen": self.rmm_last_seen.isoformat() if self.rmm_last_seen else None,
            "rmm_online": self.rmm_online,
        }


@dataclass
class DeviceMatch:
    """Result of matching a workstation to an RMM device."""

    our_hostname: str
    rmm_device: Optional[RMMDevice]
    confidence: MatchConfidence
    confidence_score: float  # 0.0 - 1.0
    matching_fields: List[str]  # Which fields matched
    discrepancies: List[str]  # Fields that don't match

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API/evidence."""
        return {
            "our_hostname": self.our_hostname,
            "rmm_device": self.rmm_device.to_dict() if self.rmm_device else None,
            "confidence": self.confidence.value,
            "confidence_score": self.confidence_score,
            "matching_fields": self.matching_fields,
            "discrepancies": self.discrepancies,
        }


@dataclass
class CoverageGap:
    """A coverage gap between our data and RMM data."""

    gap_type: GapType
    device: Dict[str, Any]  # Either our workstation or RMM device
    recommendation: str
    severity: str  # low, medium, high

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API/evidence."""
        return {
            "gap_type": self.gap_type.value,
            "device": self.device,
            "recommendation": self.recommendation,
            "severity": self.severity,
        }


@dataclass
class ComparisonReport:
    """Complete comparison report between our data and RMM data."""

    # Summary
    our_device_count: int
    rmm_device_count: int
    matched_count: int
    exact_match_count: int

    # Detailed results
    matches: List[DeviceMatch]
    gaps: List[CoverageGap]

    # Metrics
    coverage_rate: float  # % of our devices found in RMM
    accuracy_rate: float  # % of matches that are exact

    # Metadata
    rmm_provider: RMMProvider
    comparison_timestamp: datetime

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API/evidence."""
        return {
            "summary": {
                "our_device_count": self.our_device_count,
                "rmm_device_count": self.rmm_device_count,
                "matched_count": self.matched_count,
                "exact_match_count": self.exact_match_count,
                "coverage_rate": self.coverage_rate,
                "accuracy_rate": self.accuracy_rate,
            },
            "matches": [m.to_dict() for m in self.matches],
            "gaps": [g.to_dict() for g in self.gaps],
            "metadata": {
                "rmm_provider": self.rmm_provider.value,
                "comparison_timestamp": self.comparison_timestamp.isoformat(),
            },
        }


class RMMComparisonEngine:
    """
    Compare workstation data from AD discovery against RMM tools.

    Features:
    - Multi-field matching (hostname, IP, MAC, serial)
    - Confidence scoring for fuzzy matches
    - Gap analysis for coverage holes
    - Deduplication recommendations
    """

    # Weights for confidence scoring
    # Note: hostname + IP + MAC should be enough for EXACT match (>= 0.95)
    MATCH_WEIGHTS = {
        "hostname_exact": 0.35,
        "hostname_fuzzy": 0.15,
        "ip_address": 0.30,
        "mac_address": 0.35,
        "serial_number": 0.35,
        "os_name": 0.05,
    }

    # Minimum confidence to consider a match valid
    # Set to allow fuzzy hostname match (0.15) as valid
    MIN_MATCH_CONFIDENCE = 0.15

    def __init__(self):
        """Initialize comparison engine."""
        self._rmm_devices: List[RMMDevice] = []
        self._rmm_provider: Optional[RMMProvider] = None
        self._index_by_hostname: Dict[str, List[RMMDevice]] = {}
        self._index_by_ip: Dict[str, List[RMMDevice]] = {}
        self._index_by_mac: Dict[str, List[RMMDevice]] = {}

    def load_rmm_data(
        self,
        devices: List[RMMDevice],
        provider: RMMProvider = RMMProvider.MANUAL,
    ) -> None:
        """
        Load RMM device data for comparison.

        Args:
            devices: List of RMMDevice objects
            provider: Which RMM provider this data came from
        """
        self._rmm_devices = devices
        self._rmm_provider = provider
        self._build_indexes()
        logger.info(f"Loaded {len(devices)} devices from {provider.value}")

    def load_from_csv(
        self,
        csv_content: str,
        provider: RMMProvider = RMMProvider.MANUAL,
        column_mapping: Optional[Dict[str, str]] = None,
    ) -> int:
        """
        Load RMM data from CSV export.

        Args:
            csv_content: CSV file content as string
            provider: RMM provider name
            column_mapping: Optional mapping of CSV columns to RMMDevice fields

        Returns:
            Number of devices loaded
        """
        # Default column mapping
        mapping = column_mapping or {
            "hostname": ["hostname", "computer_name", "name", "device_name"],
            "ip_address": ["ip", "ip_address", "ipaddress", "internal_ip"],
            "mac_address": ["mac", "mac_address", "macaddress"],
            "os_name": ["os", "os_name", "operating_system"],
            "serial_number": ["serial", "serial_number", "serialnumber"],
            "rmm_agent_id": ["agent_id", "device_id", "id"],
        }

        devices = []
        reader = csv.DictReader(io.StringIO(csv_content))

        # Normalize CSV headers
        if reader.fieldnames:
            normalized_headers = {h.lower().strip(): h for h in reader.fieldnames}
        else:
            normalized_headers = {}

        for row in reader:
            device_data = {}

            # Map columns to fields
            for field_name, possible_columns in mapping.items():
                for col in possible_columns:
                    if col in normalized_headers:
                        actual_col = normalized_headers[col]
                        if row.get(actual_col):
                            device_data[field_name] = row[actual_col]
                            break

            if device_data.get("hostname"):
                devices.append(RMMDevice(
                    hostname=device_data.get("hostname", ""),
                    ip_address=device_data.get("ip_address"),
                    mac_address=device_data.get("mac_address"),
                    os_name=device_data.get("os_name"),
                    serial_number=device_data.get("serial_number"),
                    rmm_agent_id=device_data.get("rmm_agent_id"),
                    rmm_provider=provider,
                ))

        self.load_rmm_data(devices, provider)
        return len(devices)

    def load_from_connectwise(self, api_response: List[Dict[str, Any]]) -> int:
        """
        Load devices from ConnectWise Automate API response.

        Args:
            api_response: List of computer objects from CW Automate API

        Returns:
            Number of devices loaded
        """
        devices = []
        for item in api_response:
            devices.append(RMMDevice(
                hostname=item.get("ComputerName", ""),
                device_id=str(item.get("Id", "")),
                serial_number=item.get("SerialNumber"),
                ip_address=item.get("LocalIPAddress"),
                mac_address=item.get("MACAddress"),
                os_name=item.get("OS"),
                os_version=item.get("OSVersion"),
                manufacturer=item.get("BiosMfg"),
                model=item.get("BiosModel"),
                rmm_provider=RMMProvider.CONNECTWISE,
                rmm_agent_id=str(item.get("Id", "")),
                rmm_last_seen=self._parse_datetime(item.get("LastContact")),
                rmm_online=item.get("Status") == 1,
                extra_data=item,
            ))

        self.load_rmm_data(devices, RMMProvider.CONNECTWISE)
        return len(devices)

    def load_from_datto(self, api_response: List[Dict[str, Any]]) -> int:
        """
        Load devices from Datto RMM API response.

        Args:
            api_response: List of device objects from Datto API

        Returns:
            Number of devices loaded
        """
        devices = []
        for item in api_response:
            devices.append(RMMDevice(
                hostname=item.get("hostname", ""),
                device_id=item.get("uid"),
                serial_number=item.get("serialNumber"),
                ip_address=item.get("intIpAddress"),
                mac_address=item.get("macAddress"),
                os_name=item.get("operatingSystem"),
                manufacturer=item.get("manufacturer"),
                model=item.get("model"),
                rmm_provider=RMMProvider.DATTO,
                rmm_agent_id=item.get("uid"),
                rmm_last_seen=self._parse_datetime(item.get("lastSeen")),
                rmm_online=item.get("online", False),
                extra_data=item,
            ))

        self.load_rmm_data(devices, RMMProvider.DATTO)
        return len(devices)

    def load_from_ninja(self, api_response: List[Dict[str, Any]]) -> int:
        """
        Load devices from NinjaRMM API response.

        Args:
            api_response: List of device objects from NinjaRMM API

        Returns:
            Number of devices loaded
        """
        devices = []
        for item in api_response:
            system_info = item.get("system", {})
            devices.append(RMMDevice(
                hostname=item.get("systemName", ""),
                device_id=str(item.get("id", "")),
                serial_number=system_info.get("biosSerialNumber"),
                ip_address=item.get("ipAddress"),
                os_name=system_info.get("name"),
                manufacturer=system_info.get("manufacturer"),
                model=system_info.get("model"),
                rmm_provider=RMMProvider.NINJA,
                rmm_agent_id=str(item.get("id", "")),
                rmm_last_seen=self._parse_datetime(item.get("lastContact")),
                rmm_online=item.get("offline") is False,
                extra_data=item,
            ))

        self.load_rmm_data(devices, RMMProvider.NINJA)
        return len(devices)

    def compare_workstations(
        self,
        workstations: List[Dict[str, Any]],
    ) -> ComparisonReport:
        """
        Compare our AD-discovered workstations against loaded RMM data.

        Args:
            workstations: List of workstation dicts from workstation_discovery

        Returns:
            ComparisonReport with matches, gaps, and metrics
        """
        if not self._rmm_devices:
            logger.warning("No RMM data loaded - returning empty comparison")
            return ComparisonReport(
                our_device_count=len(workstations),
                rmm_device_count=0,
                matched_count=0,
                exact_match_count=0,
                matches=[],
                gaps=[CoverageGap(
                    gap_type=GapType.MISSING_FROM_RMM,
                    device=ws,
                    recommendation="Load RMM data before comparing",
                    severity="high",
                ) for ws in workstations],
                coverage_rate=0.0,
                accuracy_rate=0.0,
                rmm_provider=self._rmm_provider or RMMProvider.MANUAL,
                comparison_timestamp=datetime.now(timezone.utc),
            )

        matches: List[DeviceMatch] = []
        gaps: List[CoverageGap] = []
        matched_rmm_ids: Set[str] = set()

        # Match each of our workstations
        for ws in workstations:
            match = self._find_best_match(ws)
            matches.append(match)

            if match.rmm_device and match.rmm_device.rmm_agent_id:
                matched_rmm_ids.add(match.rmm_device.rmm_agent_id)

            # Check for gaps on our side
            if match.confidence == MatchConfidence.NO_MATCH:
                gaps.append(CoverageGap(
                    gap_type=GapType.MISSING_FROM_RMM,
                    device=ws,
                    recommendation=f"Add {ws.get('hostname', 'unknown')} to RMM or verify exclusion is intentional",
                    severity="medium",
                ))

        # Find RMM devices we didn't match (missing from our AD)
        for rmm_device in self._rmm_devices:
            agent_id = rmm_device.rmm_agent_id or rmm_device.hostname
            if agent_id not in matched_rmm_ids:
                # Check if device is stale
                is_stale = False
                if rmm_device.rmm_last_seen:
                    age_days = (datetime.now(timezone.utc) - rmm_device.rmm_last_seen).days
                    is_stale = age_days > 30

                if is_stale:
                    gaps.append(CoverageGap(
                        gap_type=GapType.STALE_RMM,
                        device=rmm_device.to_dict(),
                        recommendation=f"Remove stale device {rmm_device.hostname} from RMM (last seen {age_days} days ago)",
                        severity="low",
                    ))
                else:
                    gaps.append(CoverageGap(
                        gap_type=GapType.MISSING_FROM_AD,
                        device=rmm_device.to_dict(),
                        recommendation=f"Device {rmm_device.hostname} in RMM but not in AD - verify domain membership",
                        severity="medium",
                    ))

        # Calculate metrics
        exact_count = sum(1 for m in matches if m.confidence == MatchConfidence.EXACT)
        matched_count = sum(1 for m in matches if m.confidence != MatchConfidence.NO_MATCH)

        coverage_rate = (matched_count / len(workstations) * 100) if workstations else 0.0
        accuracy_rate = (exact_count / matched_count * 100) if matched_count else 0.0

        return ComparisonReport(
            our_device_count=len(workstations),
            rmm_device_count=len(self._rmm_devices),
            matched_count=matched_count,
            exact_match_count=exact_count,
            matches=matches,
            gaps=gaps,
            coverage_rate=round(coverage_rate, 1),
            accuracy_rate=round(accuracy_rate, 1),
            rmm_provider=self._rmm_provider or RMMProvider.MANUAL,
            comparison_timestamp=datetime.now(timezone.utc),
        )

    def get_deduplication_recommendations(
        self,
        report: ComparisonReport,
    ) -> List[Dict[str, Any]]:
        """
        Generate actionable deduplication recommendations.

        Args:
            report: Comparison report to analyze

        Returns:
            List of recommendations with priority
        """
        recommendations = []

        # High priority: Duplicates
        duplicates = [g for g in report.gaps if g.gap_type == GapType.DUPLICATE]
        if duplicates:
            recommendations.append({
                "priority": "high",
                "category": "duplicates",
                "count": len(duplicates),
                "action": "Remove duplicate device entries from RMM",
                "devices": [d.device.get("hostname") for d in duplicates],
            })

        # Medium priority: Missing from RMM
        missing_rmm = [g for g in report.gaps if g.gap_type == GapType.MISSING_FROM_RMM]
        if missing_rmm:
            recommendations.append({
                "priority": "medium",
                "category": "coverage_gaps",
                "count": len(missing_rmm),
                "action": "Deploy RMM agent to unmonitored workstations",
                "devices": [d.device.get("hostname") for d in missing_rmm],
            })

        # Medium priority: Missing from AD
        missing_ad = [g for g in report.gaps if g.gap_type == GapType.MISSING_FROM_AD]
        if missing_ad:
            recommendations.append({
                "priority": "medium",
                "category": "orphaned_agents",
                "count": len(missing_ad),
                "action": "Verify domain membership or remove orphaned RMM agents",
                "devices": [d.device.get("hostname") for d in missing_ad],
            })

        # Low priority: Stale entries
        stale = [g for g in report.gaps if g.gap_type in (GapType.STALE_RMM, GapType.STALE_AD)]
        if stale:
            recommendations.append({
                "priority": "low",
                "category": "stale_entries",
                "count": len(stale),
                "action": "Clean up stale device entries",
                "devices": [d.device.get("hostname") for d in stale],
            })

        # Low priority: Data discrepancies
        discrepancies = [m for m in report.matches
                        if m.discrepancies and m.confidence != MatchConfidence.NO_MATCH]
        if discrepancies:
            recommendations.append({
                "priority": "low",
                "category": "data_discrepancies",
                "count": len(discrepancies),
                "action": "Reconcile device data differences between systems",
                "examples": [
                    {
                        "hostname": m.our_hostname,
                        "discrepancies": m.discrepancies[:3],
                    }
                    for m in discrepancies[:5]
                ],
            })

        return sorted(recommendations, key=lambda r: {"high": 0, "medium": 1, "low": 2}[r["priority"]])

    def _build_indexes(self) -> None:
        """Build lookup indexes for fast matching."""
        self._index_by_hostname = defaultdict(list)
        self._index_by_ip = defaultdict(list)
        self._index_by_mac = defaultdict(list)

        for device in self._rmm_devices:
            # Hostname index (normalized)
            hostname = device.normalize_hostname()
            if hostname:
                self._index_by_hostname[hostname].append(device)

            # IP index
            if device.ip_address:
                self._index_by_ip[device.ip_address].append(device)

            # MAC index (normalized)
            mac = device.normalize_mac()
            if mac:
                self._index_by_mac[mac].append(device)

    def _find_best_match(self, workstation: Dict[str, Any]) -> DeviceMatch:
        """
        Find the best matching RMM device for a workstation.

        Args:
            workstation: Our workstation dict from AD discovery

        Returns:
            DeviceMatch with confidence and details
        """
        hostname = workstation.get("hostname", "").upper().strip().split('.')[0]
        ip_address = workstation.get("ip_address", "")
        mac_address = workstation.get("mac_address", "")

        # Normalize MAC
        if mac_address:
            mac_address = re.sub(r'[^A-Fa-f0-9]', '', mac_address).upper()

        candidates: List[Tuple[RMMDevice, float, List[str]]] = []

        # Exact hostname match
        if hostname in self._index_by_hostname:
            for device in self._index_by_hostname[hostname]:
                score, fields = self._calculate_match_score(workstation, device)
                candidates.append((device, score, fields))

        # IP match (if no hostname match)
        if ip_address and ip_address in self._index_by_ip:
            for device in self._index_by_ip[ip_address]:
                if device not in [c[0] for c in candidates]:
                    score, fields = self._calculate_match_score(workstation, device)
                    candidates.append((device, score, fields))

        # MAC match (strongest identifier)
        if mac_address and mac_address in self._index_by_mac:
            for device in self._index_by_mac[mac_address]:
                if device not in [c[0] for c in candidates]:
                    score, fields = self._calculate_match_score(workstation, device)
                    candidates.append((device, score, fields))

        # Fuzzy hostname match if no exact matches
        if not candidates:
            for norm_hostname, devices in self._index_by_hostname.items():
                if self._fuzzy_hostname_match(hostname, norm_hostname):
                    for device in devices:
                        score, fields = self._calculate_match_score(workstation, device)
                        # Add fuzzy hostname score (since _calculate_match_score only adds exact)
                        if "hostname_exact" not in fields:
                            score += self.MATCH_WEIGHTS["hostname_fuzzy"]
                            fields = ["hostname_fuzzy"] + fields
                        candidates.append((device, score, fields))

        if not candidates:
            return DeviceMatch(
                our_hostname=workstation.get("hostname", ""),
                rmm_device=None,
                confidence=MatchConfidence.NO_MATCH,
                confidence_score=0.0,
                matching_fields=[],
                discrepancies=[],
            )

        # Sort by score and take best
        candidates.sort(key=lambda x: x[1], reverse=True)
        best_device, best_score, matching_fields = candidates[0]

        # Apply minimum confidence threshold
        if best_score < self.MIN_MATCH_CONFIDENCE:
            return DeviceMatch(
                our_hostname=workstation.get("hostname", ""),
                rmm_device=None,
                confidence=MatchConfidence.NO_MATCH,
                confidence_score=best_score,
                matching_fields=[],
                discrepancies=[],
            )

        # Determine confidence level
        if best_score >= 0.90:
            confidence = MatchConfidence.EXACT
        elif best_score >= 0.60:
            confidence = MatchConfidence.HIGH
        elif best_score >= 0.35:
            confidence = MatchConfidence.MEDIUM
        else:
            confidence = MatchConfidence.LOW

        # Find discrepancies
        discrepancies = self._find_discrepancies(workstation, best_device)

        return DeviceMatch(
            our_hostname=workstation.get("hostname", ""),
            rmm_device=best_device,
            confidence=confidence,
            confidence_score=round(best_score, 2),
            matching_fields=matching_fields,
            discrepancies=discrepancies,
        )

    def _calculate_match_score(
        self,
        workstation: Dict[str, Any],
        rmm_device: RMMDevice,
    ) -> Tuple[float, List[str]]:
        """
        Calculate match confidence score between workstation and RMM device.

        Returns:
            Tuple of (score 0.0-1.0, list of matching fields)
        """
        score = 0.0
        matching_fields = []

        # Hostname exact match
        ws_hostname = workstation.get("hostname", "").upper().strip().split('.')[0]
        rmm_hostname = rmm_device.normalize_hostname()
        if ws_hostname and rmm_hostname and ws_hostname == rmm_hostname:
            score += self.MATCH_WEIGHTS["hostname_exact"]
            matching_fields.append("hostname_exact")

        # IP address match
        ws_ip = workstation.get("ip_address", "")
        if ws_ip and rmm_device.ip_address and ws_ip == rmm_device.ip_address:
            score += self.MATCH_WEIGHTS["ip_address"]
            matching_fields.append("ip_address")

        # MAC address match
        ws_mac = workstation.get("mac_address", "")
        if ws_mac:
            ws_mac_norm = re.sub(r'[^A-Fa-f0-9]', '', ws_mac).upper()
            rmm_mac_norm = rmm_device.normalize_mac()
            if ws_mac_norm and rmm_mac_norm and ws_mac_norm == rmm_mac_norm:
                score += self.MATCH_WEIGHTS["mac_address"]
                matching_fields.append("mac_address")

        # OS name match (partial)
        ws_os = workstation.get("os_name", "").lower()
        rmm_os = (rmm_device.os_name or "").lower()
        if ws_os and rmm_os:
            if "windows 10" in ws_os and "windows 10" in rmm_os:
                score += self.MATCH_WEIGHTS["os_name"]
                matching_fields.append("os_name")
            elif "windows 11" in ws_os and "windows 11" in rmm_os:
                score += self.MATCH_WEIGHTS["os_name"]
                matching_fields.append("os_name")

        return score, matching_fields

    def _fuzzy_hostname_match(self, hostname1: str, hostname2: str) -> bool:
        """Check if hostnames are similar (handles minor variations).

        This is intentionally strict to avoid false positives.
        Only matches:
        - Exact match (case insensitive, already handled before this)
        - Alphanumeric-only versions match (WS01 == WS-01)
        - One contains the other AND length difference is very small (<=1)

        Does NOT match:
        - Similar prefixes with different numbers (WORKSTATION01 vs WORKSTATION04)
        - Unrelated hostnames
        """
        if not hostname1 or not hostname2:
            return False

        # Exact match
        if hostname1 == hostname2:
            return True

        # Compare alphanumeric-only versions (handles WS01 vs WS-01)
        alpha1 = re.sub(r'[^A-Za-z0-9]', '', hostname1)
        alpha2 = re.sub(r'[^A-Za-z0-9]', '', hostname2)
        if alpha1 and alpha2 and alpha1 == alpha2:
            return True

        # One contains the other - very strict, only 1 char difference allowed
        # This handles cases like WS01 vs WS01A but NOT WORKSTATION01 vs WORKSTATION04
        if hostname1 in hostname2 or hostname2 in hostname1:
            len_diff = abs(len(hostname1) - len(hostname2))
            if len_diff <= 1:
                return True

        return False

    def _find_discrepancies(
        self,
        workstation: Dict[str, Any],
        rmm_device: RMMDevice,
    ) -> List[str]:
        """Find data discrepancies between matched devices."""
        discrepancies = []

        # IP mismatch
        ws_ip = workstation.get("ip_address")
        if ws_ip and rmm_device.ip_address and ws_ip != rmm_device.ip_address:
            discrepancies.append(f"IP: ours={ws_ip}, RMM={rmm_device.ip_address}")

        # OS mismatch
        ws_os = workstation.get("os_name", "")
        rmm_os = rmm_device.os_name or ""
        if ws_os and rmm_os:
            ws_os_norm = ws_os.lower().replace("enterprise", "").strip()
            rmm_os_norm = rmm_os.lower().replace("enterprise", "").strip()
            if ws_os_norm != rmm_os_norm:
                discrepancies.append(f"OS: ours={ws_os}, RMM={rmm_os}")

        # Online status mismatch
        ws_online = workstation.get("online", False)
        if ws_online != rmm_device.rmm_online:
            discrepancies.append(f"Online: ours={ws_online}, RMM={rmm_device.rmm_online}")

        return discrepancies

    @staticmethod
    def _parse_datetime(dt_value) -> Optional[datetime]:
        """Parse datetime from various formats."""
        if not dt_value:
            return None

        if isinstance(dt_value, datetime):
            return dt_value

        if isinstance(dt_value, (int, float)):
            # Unix timestamp
            try:
                return datetime.fromtimestamp(dt_value, tz=timezone.utc)
            except (ValueError, OSError):
                return None

        if isinstance(dt_value, str):
            # Try ISO format
            try:
                return datetime.fromisoformat(dt_value.replace("Z", "+00:00"))
            except ValueError:
                pass

            # Try common formats
            for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y %H:%M"]:
                try:
                    return datetime.strptime(dt_value, fmt).replace(tzinfo=timezone.utc)
                except ValueError:
                    continue

        return None


# Convenience function for appliance agent integration
async def compare_with_rmm(
    workstations: List[Dict[str, Any]],
    rmm_data: List[Dict[str, Any]],
    rmm_provider: str = "manual",
) -> Dict[str, Any]:
    """
    Convenience function to compare workstations with RMM data.

    Args:
        workstations: List of workstation dicts from AD discovery
        rmm_data: List of device dicts from RMM export
        rmm_provider: RMM provider name

    Returns:
        Comparison report as dictionary
    """
    engine = RMMComparisonEngine()

    # Convert RMM data to RMMDevice objects
    provider = RMMProvider(rmm_provider) if rmm_provider in [p.value for p in RMMProvider] else RMMProvider.MANUAL
    devices = [
        RMMDevice(
            hostname=d.get("hostname", d.get("name", "")),
            ip_address=d.get("ip_address", d.get("ip", "")),
            mac_address=d.get("mac_address", d.get("mac", "")),
            os_name=d.get("os_name", d.get("os", "")),
            serial_number=d.get("serial_number", d.get("serial", "")),
            rmm_agent_id=d.get("device_id", d.get("id", "")),
            rmm_provider=provider,
        )
        for d in rmm_data
    ]

    engine.load_rmm_data(devices, provider)
    report = engine.compare_workstations(workstations)

    return report.to_dict()
