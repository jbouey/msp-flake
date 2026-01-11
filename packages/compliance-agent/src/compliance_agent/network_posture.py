"""
Network Posture Detector.

Host-centric network exposure detection and baseline alignment.
Detects listening ports, external bindings, prohibited services,
DNS resolvers, and reachability assertions.

Works with both Linux (via SSH) and Windows (via WinRM) targets.

Version: 1.0
"""

import asyncio
import logging
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field

import yaml

logger = logging.getLogger(__name__)


@dataclass
class ListeningPort:
    """A port listening on a host."""
    port: int
    protocol: str  # tcp, udp
    process: str
    pid: Optional[int] = None
    bind_address: str = "0.0.0.0"
    external: bool = False  # True if bound to 0.0.0.0 or public IP

    def __post_init__(self):
        # Determine if externally accessible
        self.external = self.bind_address in ("0.0.0.0", "::", "*") or \
                        not self.bind_address.startswith(("127.", "::1"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "port": self.port,
            "protocol": self.protocol,
            "process": self.process,
            "pid": self.pid,
            "bind_address": self.bind_address,
            "external": self.external,
        }


@dataclass
class NetworkPostureResult:
    """Result of network posture detection for a host."""
    target: str
    os_type: str  # linux, windows
    timestamp: str = ""

    # Findings
    listening_ports: List[ListeningPort] = field(default_factory=list)
    external_bindings: List[ListeningPort] = field(default_factory=list)
    prohibited_ports: List[Dict[str, Any]] = field(default_factory=list)
    baseline_violations: List[str] = field(default_factory=list)
    dns_resolvers: List[str] = field(default_factory=list)
    reachability_failures: List[Dict[str, Any]] = field(default_factory=list)

    # Compliance
    compliant: bool = True
    drift_items: List[Dict[str, Any]] = field(default_factory=list)
    hipaa_controls: List[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target": self.target,
            "os_type": self.os_type,
            "timestamp": self.timestamp,
            "compliant": self.compliant,
            "listening_ports": [p.to_dict() for p in self.listening_ports],
            "external_bindings": [p.to_dict() for p in self.external_bindings],
            "prohibited_ports": self.prohibited_ports,
            "baseline_violations": self.baseline_violations,
            "dns_resolvers": self.dns_resolvers,
            "reachability_failures": self.reachability_failures,
            "drift_items": self.drift_items,
            "hipaa_controls": self.hipaa_controls,
        }


class NetworkPostureDetector:
    """
    Detect network posture and exposure on Linux and Windows hosts.

    Checks:
    - Listening ports (ss/netstat)
    - External bindings (0.0.0.0)
    - Prohibited ports (telnet, FTP, etc.)
    - DNS resolvers
    - Reachability assertions

    Usage:
        detector = NetworkPostureDetector()

        # Linux via SSH
        from compliance_agent.runbooks.linux.executor import LinuxExecutor, LinuxTarget
        executor = LinuxExecutor()
        target = LinuxTarget(hostname="192.168.1.100", username="admin", password="...")
        result = await detector.detect_linux(executor, target)

        # Windows via WinRM
        from compliance_agent.runbooks.windows.executor import WindowsExecutor, WindowsTarget
        executor = WindowsExecutor()
        target = WindowsTarget(hostname="192.168.1.200", username="admin", password="...")
        result = await detector.detect_windows(executor, target)
    """

    def __init__(self, baseline_path: Optional[str] = None):
        """
        Initialize detector.

        Args:
            baseline_path: Path to network_posture.yaml
        """
        self.baseline = self._load_baseline(baseline_path)

    def _load_baseline(self, path: Optional[str] = None) -> Dict[str, Any]:
        """Load baseline configuration from YAML."""
        if path is None:
            base_dir = Path(__file__).parent / "baselines"
            path = base_dir / "network_posture.yaml"

        try:
            with open(path, "r") as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            logger.warning(f"Baseline not found at {path}, using defaults")
            return self._default_baseline()

    def _default_baseline(self) -> Dict[str, Any]:
        """Return minimal default baseline."""
        return {
            "version": "1.0",
            "prohibited_ports": [
                {"port": 21, "description": "FTP"},
                {"port": 23, "description": "Telnet"},
                {"port": 25, "description": "SMTP"},
            ],
            "allowed_external_ports": [
                {"port": 22, "protocol": "tcp"},
                {"port": 443, "protocol": "tcp"},
            ],
        }

    async def detect_linux(self, executor, target) -> NetworkPostureResult:
        """
        Detect network posture on Linux via SSH.

        Args:
            executor: LinuxExecutor instance
            target: LinuxTarget instance

        Returns:
            NetworkPostureResult with findings
        """
        result = NetworkPostureResult(
            target=target.hostname,
            os_type="linux"
        )

        # Get listening ports via ss
        ports_script = '''
        ss -tulpn 2>/dev/null | tail -n +2 | while read line; do
            proto=$(echo "$line" | awk '{print $1}')
            local=$(echo "$line" | awk '{print $5}')
            process=$(echo "$line" | awk '{print $7}' | sed 's/users:(("//' | sed 's/",.*//')

            # Parse address:port
            if echo "$local" | grep -q "\\["; then
                # IPv6
                addr=$(echo "$local" | sed 's/\\[\\(.*\\)\\]:.*/\\1/')
                port=$(echo "$local" | sed 's/.*\\]://')
            else
                # IPv4
                addr=$(echo "$local" | rev | cut -d: -f2- | rev)
                port=$(echo "$local" | rev | cut -d: -f1 | rev)
            fi

            echo "$proto|$addr|$port|$process"
        done
        '''

        exec_result = await executor.execute_script(target, ports_script, timeout=30)
        if exec_result.success:
            result.listening_ports = self._parse_linux_ports(exec_result.output.get("stdout", ""))

        # Get DNS resolvers
        dns_script = "grep -E '^nameserver' /etc/resolv.conf | awk '{print $2}'"
        exec_result = await executor.execute_script(target, dns_script, timeout=10)
        if exec_result.success:
            result.dns_resolvers = [
                line.strip() for line in exec_result.output.get("stdout", "").split("\n")
                if line.strip()
            ]

        # Check reachability
        result.reachability_failures = await self._check_reachability_linux(executor, target)

        # Analyze against baseline
        self._analyze_posture(result)

        return result

    async def detect_windows(self, executor, target) -> NetworkPostureResult:
        """
        Detect network posture on Windows via WinRM.

        Args:
            executor: WindowsExecutor instance
            target: WindowsTarget instance

        Returns:
            NetworkPostureResult with findings
        """
        result = NetworkPostureResult(
            target=target.hostname,
            os_type="windows"
        )

        # Get listening ports via netstat
        ports_script = r'''
        $ports = netstat -ano | Where-Object { $_ -match "LISTENING" } | ForEach-Object {
            $parts = $_ -split '\s+' | Where-Object { $_ }
            if ($parts.Count -ge 5) {
                $local = $parts[1]
                $pid = $parts[4]

                # Parse address:port
                $lastColon = $local.LastIndexOf(':')
                $addr = $local.Substring(0, $lastColon)
                $port = $local.Substring($lastColon + 1)

                # Get process name
                $proc = (Get-Process -Id $pid -ErrorAction SilentlyContinue).ProcessName

                @{
                    Protocol = $parts[0]
                    Address = $addr
                    Port = [int]$port
                    PID = [int]$pid
                    Process = $proc
                }
            }
        }
        $ports | ConvertTo-Json -Compress
        '''

        exec_result = await executor.execute_script(target, ports_script, timeout=30)
        if exec_result.success and exec_result.output.get("parsed"):
            result.listening_ports = self._parse_windows_ports(exec_result.output["parsed"])

        # Get DNS resolvers
        dns_script = '''
        Get-DnsClientServerAddress | Where-Object { $_.AddressFamily -eq 2 } |
            Select-Object -ExpandProperty ServerAddresses -Unique |
            ConvertTo-Json -Compress
        '''
        exec_result = await executor.execute_script(target, dns_script, timeout=10)
        if exec_result.success and exec_result.output.get("parsed"):
            dns = exec_result.output["parsed"]
            if isinstance(dns, list):
                result.dns_resolvers = dns
            elif isinstance(dns, str):
                result.dns_resolvers = [dns]

        # Check reachability
        result.reachability_failures = await self._check_reachability_windows(executor, target)

        # Analyze against baseline
        self._analyze_posture(result)

        return result

    def _parse_linux_ports(self, output: str) -> List[ListeningPort]:
        """Parse ss output into ListeningPort list."""
        ports = []
        for line in output.strip().split("\n"):
            if not line or "|" not in line:
                continue
            parts = line.split("|")
            if len(parts) >= 4:
                try:
                    proto = parts[0].lower()
                    addr = parts[1]
                    port = int(parts[2]) if parts[2].isdigit() else 0
                    process = parts[3] or "unknown"

                    if port > 0:
                        ports.append(ListeningPort(
                            port=port,
                            protocol=proto[:3],  # tcp or udp
                            process=process,
                            bind_address=addr
                        ))
                except (ValueError, IndexError):
                    continue
        return ports

    def _parse_windows_ports(self, data) -> List[ListeningPort]:
        """Parse Windows netstat JSON into ListeningPort list."""
        ports = []
        if not data:
            return ports

        if isinstance(data, dict):
            data = [data]

        for item in data:
            try:
                ports.append(ListeningPort(
                    port=item.get("Port", 0),
                    protocol=item.get("Protocol", "tcp").lower()[:3],
                    process=item.get("Process", "unknown") or "unknown",
                    pid=item.get("PID"),
                    bind_address=item.get("Address", "0.0.0.0")
                ))
            except (TypeError, KeyError):
                continue
        return ports

    async def _check_reachability_linux(self, executor, target) -> List[Dict[str, Any]]:
        """Check reachability assertions on Linux."""
        failures = []
        assertions = self.baseline.get("reachability_assertions", [])

        for assertion in assertions:
            if not assertion.get("required", True):
                continue

            check_target = assertion["target"]
            port = assertion["port"]

            # Use nc (netcat) to check connectivity
            script = f"timeout 5 bash -c 'echo > /dev/tcp/{check_target}/{port}' 2>/dev/null && echo SUCCESS || echo FAILED"

            result = await executor.execute_script(target, script, timeout=10)
            if "FAILED" in result.output.get("stdout", "") or not result.success:
                failures.append({
                    "target": check_target,
                    "port": port,
                    "description": assertion.get("description", ""),
                    "severity": assertion.get("failure_severity", "high"),
                })

        return failures

    async def _check_reachability_windows(self, executor, target) -> List[Dict[str, Any]]:
        """Check reachability assertions on Windows."""
        failures = []
        assertions = self.baseline.get("reachability_assertions", [])

        for assertion in assertions:
            if not assertion.get("required", True):
                continue

            check_target = assertion["target"]
            port = assertion["port"]

            script = f'''
            $result = Test-NetConnection -ComputerName "{check_target}" -Port {port} -InformationLevel Quiet
            if ($result) {{ "SUCCESS" }} else {{ "FAILED" }}
            '''

            result = await executor.execute_script(target, script, timeout=15)
            if "FAILED" in result.output.get("std_out", "") or not result.success:
                failures.append({
                    "target": check_target,
                    "port": port,
                    "description": assertion.get("description", ""),
                    "severity": assertion.get("failure_severity", "high"),
                })

        return failures

    def _analyze_posture(self, result: NetworkPostureResult):
        """Analyze posture against baseline and populate violations."""
        prohibited = {p["port"]: p for p in self.baseline.get("prohibited_ports", [])}
        allowed_external = {p["port"] for p in self.baseline.get("allowed_external_ports", [])}
        never_external = [
            p.get("process_pattern", "")
            for p in self.baseline.get("external_binding_policy", {}).get("never_external", [])
        ]

        for port in result.listening_ports:
            # Check for prohibited ports
            if port.port in prohibited:
                info = prohibited[port.port]
                result.prohibited_ports.append({
                    "port": port.port,
                    "protocol": port.protocol,
                    "process": port.process,
                    "description": info.get("description", ""),
                    "severity": info.get("severity", "high"),
                    "hipaa_control": info.get("hipaa_control", ""),
                })
                result.drift_items.append({
                    "type": "prohibited_port",
                    "port": port.port,
                    "process": port.process,
                    "severity": info.get("severity", "high"),
                })
                if info.get("hipaa_control"):
                    result.hipaa_controls.append(info["hipaa_control"])

            # Check for external bindings
            if port.external:
                result.external_bindings.append(port)

                # Check if this port should not be external
                if port.port not in allowed_external:
                    result.baseline_violations.append(
                        f"Port {port.port} ({port.process}) is externally accessible but not in allowed list"
                    )
                    result.drift_items.append({
                        "type": "unauthorized_external_port",
                        "port": port.port,
                        "process": port.process,
                        "severity": "high",
                    })

                # Check never_external patterns
                for pattern in never_external:
                    if pattern and pattern.lower() in port.process.lower():
                        result.baseline_violations.append(
                            f"Service {port.process} on port {port.port} should never be externally accessible"
                        )
                        result.drift_items.append({
                            "type": "never_external_violation",
                            "port": port.port,
                            "process": port.process,
                            "pattern": pattern,
                            "severity": "critical",
                        })

        # Check DNS resolvers
        dns_config = self.baseline.get("dns_resolvers", {})
        allowed_dns = set()
        prohibited_dns = set()

        for r in dns_config.get("allowed", []):
            if isinstance(r, dict):
                allowed_dns.add(r.get("ip", ""))
            else:
                allowed_dns.add(str(r))

        for r in dns_config.get("prohibited", []):
            if isinstance(r, dict):
                prohibited_dns.add(r.get("ip", ""))
            else:
                prohibited_dns.add(str(r))

        for resolver in result.dns_resolvers:
            if resolver in prohibited_dns:
                result.baseline_violations.append(f"Prohibited DNS resolver: {resolver}")
                result.drift_items.append({
                    "type": "prohibited_dns",
                    "resolver": resolver,
                    "severity": "high",
                })

        # Set overall compliance
        result.compliant = (
            len(result.prohibited_ports) == 0 and
            len(result.baseline_violations) == 0 and
            len(result.reachability_failures) == 0
        )

        # Deduplicate HIPAA controls
        result.hipaa_controls = list(set(result.hipaa_controls))

    async def remediate_prohibited_port(
        self,
        executor,
        target,
        port: int,
        os_type: str = "linux"
    ) -> bool:
        """
        Block a prohibited port via firewall.

        Args:
            executor: LinuxExecutor or WindowsExecutor
            target: Target instance
            port: Port number to block
            os_type: 'linux' or 'windows'

        Returns:
            True if remediation succeeded
        """
        if os_type == "linux":
            # Try ufw first, then iptables
            script = f'''
            if command -v ufw &>/dev/null; then
                ufw deny {port}/tcp
                ufw deny {port}/udp
                echo "BLOCKED_UFW"
            else
                iptables -A INPUT -p tcp --dport {port} -j DROP
                iptables -A INPUT -p udp --dport {port} -j DROP
                echo "BLOCKED_IPTABLES"
            fi
            '''
        else:
            script = f'''
            New-NetFirewallRule -DisplayName "Block Port {port}" -Direction Inbound -LocalPort {port} -Protocol TCP -Action Block
            New-NetFirewallRule -DisplayName "Block Port {port} UDP" -Direction Inbound -LocalPort {port} -Protocol UDP -Action Block
            "BLOCKED"
            '''

        result = await executor.execute_script(target, script, timeout=30)
        return result.success and "BLOCKED" in result.output.get("stdout", result.output.get("std_out", ""))

    def generate_evidence(self, results: List[NetworkPostureResult]) -> Dict[str, Any]:
        """
        Generate evidence bundle from posture detection results.

        Args:
            results: List of NetworkPostureResult

        Returns:
            Evidence bundle dictionary
        """
        timestamp = datetime.now(timezone.utc).isoformat()

        evidence = {
            "type": "network_posture_detection",
            "version": "1.0",
            "timestamp": timestamp,
            "baseline_version": self.baseline.get("version", "unknown"),
            "hosts_scanned": len(results),
            "compliant_count": sum(1 for r in results if r.compliant),
            "non_compliant_count": sum(1 for r in results if not r.compliant),
            "hosts": [r.to_dict() for r in results],
            "summary": {
                "total_prohibited_ports": sum(len(r.prohibited_ports) for r in results),
                "total_external_bindings": sum(len(r.external_bindings) for r in results),
                "total_violations": sum(len(r.baseline_violations) for r in results),
                "total_reachability_failures": sum(len(r.reachability_failures) for r in results),
                "all_hipaa_controls": list(set(
                    ctrl for r in results for ctrl in r.hipaa_controls
                )),
            }
        }

        # Add evidence hash
        evidence_str = json.dumps(evidence, sort_keys=True)
        evidence["hash"] = hashlib.sha256(evidence_str.encode()).hexdigest()

        return evidence

    def get_summary(self, results: List[NetworkPostureResult]) -> Dict[str, Any]:
        """Get summary of network posture results."""
        return {
            "hosts_scanned": len(results),
            "compliant": sum(1 for r in results if r.compliant),
            "non_compliant": sum(1 for r in results if not r.compliant),
            "issues": {
                "prohibited_ports": sum(len(r.prohibited_ports) for r in results),
                "unauthorized_external": sum(
                    len([v for v in r.drift_items if v.get("type") == "unauthorized_external_port"])
                    for r in results
                ),
                "never_external_violations": sum(
                    len([v for v in r.drift_items if v.get("type") == "never_external_violation"])
                    for r in results
                ),
                "dns_violations": sum(
                    len([v for v in r.drift_items if v.get("type") == "prohibited_dns"])
                    for r in results
                ),
                "reachability_failures": sum(len(r.reachability_failures) for r in results),
            },
            "hipaa_controls_affected": list(set(
                ctrl for r in results for ctrl in r.hipaa_controls
            )),
        }
