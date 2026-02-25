"""
HIPAA-mapped network compliance checks.

7 checks that evaluate device port data against HIPAA Technical Safeguard
requirements. Each check implements ComplianceCheck ABC and returns
pass/warn/fail with the mapped HIPAA control reference.
"""

from __future__ import annotations

from typing import Optional

from .._types import Device
from .base import ComplianceCheck, ComplianceResult


# Port sets used by multiple checks
PROHIBITED_PORTS = {
    21: "FTP (cleartext)",
    23: "Telnet (cleartext)",
    69: "TFTP (cleartext, no auth)",
    512: "rexec (legacy, cleartext)",
    513: "rlogin (legacy, cleartext)",
    514: "rsh (legacy, cleartext)",
}

DATABASE_PORTS = {
    3306: "MySQL",
    5432: "PostgreSQL",
    1433: "MSSQL",
    1434: "MSSQL Browser",
    27017: "MongoDB",
    6379: "Redis",
    9042: "Cassandra",
}


class ProhibitedPortsCheck(ComplianceCheck):
    """Detect cleartext and legacy protocols that violate access controls.

    FTP, Telnet, TFTP, rsh/rlogin transmit credentials and data in cleartext
    and must not be exposed on HIPAA-regulated networks.
    """

    @property
    def check_type(self) -> str:
        return "prohibited_ports"

    @property
    def hipaa_control(self) -> Optional[str]:
        return "§164.312(a)(1)"

    @property
    def applicable_device_types(self) -> list[str]:
        return ["workstation", "server", "network", "printer", "unknown"]

    async def run(self, device: Device) -> ComplianceResult:
        port_numbers = {p.port for p in device.open_ports}
        found = {p: PROHIBITED_PORTS[p] for p in port_numbers if p in PROHIBITED_PORTS}

        if found:
            return ComplianceResult(
                check_type=self.check_type,
                status="fail",
                hipaa_control=self.hipaa_control,
                details={
                    "prohibited_ports": found,
                    "message": f"Cleartext/legacy protocols exposed: {', '.join(found.values())}",
                },
            )

        return ComplianceResult(
            check_type=self.check_type,
            status="pass",
            hipaa_control=self.hipaa_control,
            details={"message": "No prohibited ports detected"},
        )


class EncryptedServicesCheck(ComplianceCheck):
    """Verify web services use encryption in transit.

    HTTP (80) without HTTPS (443) means ePHI could traverse the network
    unencrypted. Both present is a warning (HTTP should redirect to HTTPS).
    """

    @property
    def check_type(self) -> str:
        return "encrypted_services"

    @property
    def hipaa_control(self) -> Optional[str]:
        return "§164.312(e)(1)"

    @property
    def applicable_device_types(self) -> list[str]:
        return ["workstation", "server", "unknown"]

    async def run(self, device: Device) -> ComplianceResult:
        port_numbers = {p.port for p in device.open_ports}
        has_http = 80 in port_numbers
        has_https = 443 in port_numbers

        if has_http and not has_https:
            return ComplianceResult(
                check_type=self.check_type,
                status="fail",
                hipaa_control=self.hipaa_control,
                details={"message": "HTTP (80) exposed without HTTPS (443) — cleartext web traffic"},
            )

        if has_http and has_https:
            return ComplianceResult(
                check_type=self.check_type,
                status="warn",
                hipaa_control=self.hipaa_control,
                details={"message": "HTTP (80) and HTTPS (443) both open — ensure HTTP redirects to HTTPS"},
            )

        return ComplianceResult(
            check_type=self.check_type,
            status="pass",
            hipaa_control=self.hipaa_control,
            details={"message": "Web services properly encrypted or not exposed"},
        )


class TLSWebServicesCheck(ComplianceCheck):
    """Verify alternative web service ports use TLS.

    Application servers commonly run on 8080; the TLS counterpart is 8443.
    Having 8080 without 8443 suggests unencrypted application traffic.
    """

    @property
    def check_type(self) -> str:
        return "tls_web_services"

    @property
    def hipaa_control(self) -> Optional[str]:
        return "§164.312(a)(2)(iv)"

    @property
    def applicable_device_types(self) -> list[str]:
        return ["server"]

    async def run(self, device: Device) -> ComplianceResult:
        port_numbers = {p.port for p in device.open_ports}
        has_8080 = 8080 in port_numbers
        has_8443 = 8443 in port_numbers

        if has_8080 and not has_8443:
            return ComplianceResult(
                check_type=self.check_type,
                status="warn",
                hipaa_control=self.hipaa_control,
                details={"message": "HTTP alt (8080) without HTTPS alt (8443) — may lack TLS"},
            )

        return ComplianceResult(
            check_type=self.check_type,
            status="pass",
            hipaa_control=self.hipaa_control,
            details={"message": "Alternative web service ports properly secured or not exposed"},
        )


class DatabaseExposureCheck(ComplianceCheck):
    """Detect database services exposed on non-server devices.

    Database ports (MySQL, PostgreSQL, MSSQL, MongoDB, Redis, Cassandra)
    on workstations, network devices, or printers indicate misconfiguration
    or potential data exfiltration paths.
    """

    @property
    def check_type(self) -> str:
        return "database_exposure"

    @property
    def hipaa_control(self) -> Optional[str]:
        return "§164.312(a)(1)"

    @property
    def applicable_device_types(self) -> list[str]:
        return ["workstation", "network", "printer", "unknown"]

    async def run(self, device: Device) -> ComplianceResult:
        port_numbers = {p.port for p in device.open_ports}
        found = {p: DATABASE_PORTS[p] for p in port_numbers if p in DATABASE_PORTS}

        if found:
            return ComplianceResult(
                check_type=self.check_type,
                status="fail",
                hipaa_control=self.hipaa_control,
                details={
                    "exposed_databases": found,
                    "message": f"Database services on {device.device_type.value}: {', '.join(found.values())}",
                },
            )

        return ComplianceResult(
            check_type=self.check_type,
            status="pass",
            hipaa_control=self.hipaa_control,
            details={"message": "No unexpected database services exposed"},
        )


class SNMPSecurityCheck(ComplianceCheck):
    """Detect SNMP v1/v2 with cleartext community strings.

    SNMP ports 161/162 use community strings transmitted in cleartext.
    SNMPv3 provides authentication and encryption but cannot be distinguished
    by port alone — flag for review.
    """

    @property
    def check_type(self) -> str:
        return "snmp_security"

    @property
    def hipaa_control(self) -> Optional[str]:
        return "§164.312(a)(2)(i)"

    @property
    def applicable_device_types(self) -> list[str]:
        return ["workstation", "server", "network", "printer", "unknown"]

    async def run(self, device: Device) -> ComplianceResult:
        port_numbers = {p.port for p in device.open_ports}
        snmp_ports = port_numbers & {161, 162}

        if snmp_ports:
            return ComplianceResult(
                check_type=self.check_type,
                status="warn",
                hipaa_control=self.hipaa_control,
                details={
                    "snmp_ports": sorted(snmp_ports),
                    "message": "SNMP detected — verify SNMPv3 with authentication is in use",
                },
            )

        return ComplianceResult(
            check_type=self.check_type,
            status="pass",
            hipaa_control=self.hipaa_control,
            details={"message": "No SNMP services exposed"},
        )


class RDPExposureCheck(ComplianceCheck):
    """Detect RDP on devices where it shouldn't be exposed.

    RDP (3389) is expected on workstations for remote support but is a risk
    on servers (lateral movement), network devices, and printers.
    """

    @property
    def check_type(self) -> str:
        return "rdp_exposure"

    @property
    def hipaa_control(self) -> Optional[str]:
        return "§164.312(a)(1)"

    @property
    def applicable_device_types(self) -> list[str]:
        return ["server", "network", "printer", "unknown"]

    async def run(self, device: Device) -> ComplianceResult:
        port_numbers = {p.port for p in device.open_ports}

        if 3389 in port_numbers:
            return ComplianceResult(
                check_type=self.check_type,
                status="warn",
                hipaa_control=self.hipaa_control,
                details={
                    "message": f"RDP (3389) exposed on {device.device_type.value} — review access controls",
                },
            )

        return ComplianceResult(
            check_type=self.check_type,
            status="pass",
            hipaa_control=self.hipaa_control,
            details={"message": "RDP not exposed on non-workstation device"},
        )


class DeviceInventoryCheck(ComplianceCheck):
    """Verify device has been port-scanned for complete inventory.

    HIPAA requires asset inventory as part of risk analysis.
    ARP-only devices have no port data — flagged for deeper scanning.
    """

    @property
    def check_type(self) -> str:
        return "device_inventory"

    @property
    def hipaa_control(self) -> Optional[str]:
        return "§164.308(a)(1)(ii)(B)"

    @property
    def applicable_device_types(self) -> list[str]:
        return ["workstation", "server", "network", "printer", "unknown"]

    async def run(self, device: Device) -> ComplianceResult:
        if not device.open_ports:
            return ComplianceResult(
                check_type=self.check_type,
                status="warn",
                hipaa_control=self.hipaa_control,
                details={"message": "No port data — device needs nmap scan for complete inventory"},
            )

        return ComplianceResult(
            check_type=self.check_type,
            status="pass",
            hipaa_control=self.hipaa_control,
            details={
                "ports_found": len(device.open_ports),
                "message": f"Device inventoried with {len(device.open_ports)} open port(s)",
            },
        )


# All network compliance checks in evaluation order
ALL_NETWORK_CHECKS: list[ComplianceCheck] = [
    ProhibitedPortsCheck(),
    EncryptedServicesCheck(),
    TLSWebServicesCheck(),
    DatabaseExposureCheck(),
    SNMPSecurityCheck(),
    RDPExposureCheck(),
    DeviceInventoryCheck(),
]
