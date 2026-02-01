#!/usr/bin/env python3
"""
Appliance-mode compliance agent.

Simplified agent for standalone NixOS appliance deployment.
Uses YAML config and HTTPS + API key authentication.

Features:
- Phone-home to Central Command
- Basic compliance checks (services, time sync, disk)
- Evidence bundle generation and upload
- L1 rules sync from Central Command

Usage:
    python -m compliance_agent.appliance_agent
    # or via entry point:
    compliance-agent-appliance
"""

import asyncio
import hashlib
import json
import logging
import signal
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List

from fastapi import FastAPI
import uvicorn

from .appliance_config import load_appliance_config, ApplianceConfig
from .appliance_client import (
    CentralCommandClient,
    get_hostname,
    get_mac_address,
    get_ip_addresses,
    get_uptime_seconds,
    get_nixos_version,
)
from .crypto import Ed25519Signer, ensure_signing_key
from .runbooks.windows.executor import WindowsTarget, WindowsExecutor
from .runbooks.linux.executor import LinuxTarget, LinuxExecutor
from .linux_drift import LinuxDriftDetector
from .network_posture import NetworkPostureDetector

# Three-tier healing imports
from .incident_db import IncidentDatabase, Incident
from .auto_healer import AutoHealer, AutoHealerConfig
from .level1_deterministic import DeterministicEngine
from .level2_llm import Level2Planner, LLMConfig, LLMMode
from .level3_escalation import EscalationHandler, EscalationConfig
from .learning_loop import SelfLearningSystem, PromotionConfig
from .learning_sync import LearningSyncService
from .ntp_verify import NTPVerifier, verify_time_for_evidence

# Workstation discovery and compliance
from .workstation_discovery import WorkstationDiscovery, Workstation
from .workstation_checks import WorkstationComplianceChecker, WorkstationComplianceResult
from .workstation_evidence import WorkstationEvidenceGenerator, create_workstation_evidence

# Domain discovery for zero-friction deployment
from .domain_discovery import DomainDiscovery, DiscoveredDomain
from .ad_enumeration import ADEnumerator, EnumerationResult
from .agent_deployment import GoAgentDeployer, DeploymentResult

# Sensor API for dual-mode architecture (Windows)
from .sensor_api import (
    router as sensor_router,
    configure_healing as configure_sensor_healing,
    has_active_sensor,
    get_polling_hosts,
    get_sensor_hosts,
    get_dual_mode_stats,
    sensor_registry,
    SENSOR_TIMEOUT,
)

# Linux Sensor API for dual-mode architecture
from .sensor_linux import (
    router as linux_sensor_router,
    configure_linux_healing,
    has_active_linux_sensor,
    get_linux_sensor_hosts,
    get_linux_polling_hosts,
    get_linux_dual_mode_stats,
    get_combined_sensor_stats,
    set_sensor_scripts_dir,
)

# gRPC server for Go agent communication
from .grpc_server import (
    AgentRegistry,
    ComplianceAgentServicer,
    get_grpc_stats,
    GRPC_AVAILABLE,
    serve as grpc_serve,
)

# Import compliance_pb2 for HealCommand creation (if gRPC available)
try:
    from . import compliance_pb2
except ImportError:
    compliance_pb2 = None

logger = logging.getLogger(__name__)

VERSION = "1.0.52"


async def run_command(cmd: str, timeout: int = 30) -> tuple[int, str, str]:
    """Run a shell command and return (returncode, stdout, stderr)."""
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode, stdout.decode(), stderr.decode()
    except asyncio.TimeoutError:
        return -1, "", "Command timed out"
    except Exception as e:
        return -1, "", str(e)


class SimpleDriftChecker:
    """
    Simple drift checker for appliance mode.

    Performs basic compliance checks without requiring baseline config:
    - NixOS generation info
    - Critical services status
    - NTP sync status
    - Disk space
    - Firewall status
    """

    async def run_all_checks(self) -> Dict[str, Dict[str, Any]]:
        """Run all drift checks and return results."""
        results = {}

        checks = [
            ("nixos_generation", self._check_nixos_generation),
            ("ntp_sync", self._check_ntp_sync),
            ("critical_services", self._check_critical_services),
            ("disk_space", self._check_disk_space),
            ("firewall", self._check_firewall),
        ]

        for check_name, check_func in checks:
            try:
                results[check_name] = await check_func()
            except Exception as e:
                logger.error(f"Check {check_name} failed: {e}")
                results[check_name] = {
                    "status": "error",
                    "error": str(e)
                }

        return results

    async def _check_nixos_generation(self) -> Dict[str, Any]:
        """Check NixOS generation status."""
        code, stdout, _ = await run_command("readlink /run/current-system", timeout=5)
        current_system = stdout.strip() if code == 0 else "unknown"

        # Try to get generation number (may not work on minimal systems)
        current_gen = "unknown"
        # Method 1: nixos-rebuild list-generations (if available)
        code, stdout, _ = await run_command(
            "nixos-rebuild list-generations 2>/dev/null | tail -1 | awk '{print $1}'",
            timeout=10
        )
        if code == 0 and stdout.strip().isdigit():
            current_gen = stdout.strip()
        else:
            # Method 2: Parse from /nix/var/nix/profiles/system symlink
            code, stdout, _ = await run_command(
                "readlink /nix/var/nix/profiles/system 2>/dev/null | grep -oE '[0-9]+' | tail -1",
                timeout=5
            )
            if code == 0 and stdout.strip():
                current_gen = stdout.strip()

        # Pass if current-system exists and points to valid nix store path
        is_valid = current_system != "unknown" and "/nix/store/" in current_system

        return {
            "status": "pass" if is_valid else "warning",
            "details": {
                "current_system": current_system,
                "current_generation": current_gen,
            }
        }

    async def _check_ntp_sync(self) -> Dict[str, Any]:
        """Check NTP synchronization status."""
        code, stdout, _ = await run_command("timedatectl show --property=NTPSynchronized", timeout=5)

        synced = "yes" in stdout.lower() if code == 0 else False

        return {
            "status": "pass" if synced else "fail",
            "details": {
                "ntp_synchronized": synced,
            }
        }

    async def _check_critical_services(self) -> Dict[str, Any]:
        """Check critical services are running."""
        services = ["sshd", "chronyd", "nscd"]
        service_status = {}

        for svc in services:
            code, stdout, _ = await run_command(f"systemctl is-active {svc} 2>/dev/null", timeout=5)
            service_status[svc] = stdout.strip() if code == 0 else "inactive"

        all_active = all(s == "active" for s in service_status.values())

        return {
            "status": "pass" if all_active else "warning",
            "details": {
                "services": service_status,
            }
        }

    async def _check_disk_space(self) -> Dict[str, Any]:
        """Check disk space on key partitions."""
        code, stdout, _ = await run_command("df -h / /nix/store 2>/dev/null | tail -n +2", timeout=5)

        partitions = {}
        low_space = False

        if code == 0:
            for line in stdout.strip().split('\n'):
                parts = line.split()
                if len(parts) >= 5:
                    mount = parts[5]
                    use_pct = int(parts[4].rstrip('%'))
                    partitions[mount] = {
                        "size": parts[1],
                        "used": parts[2],
                        "available": parts[3],
                        "use_percent": use_pct
                    }
                    if use_pct > 90:
                        low_space = True

        return {
            "status": "fail" if low_space else "pass",
            "details": {
                "partitions": partitions,
            }
        }

    async def _check_firewall(self) -> Dict[str, Any]:
        """Check firewall status (supports both nftables and legacy iptables)."""
        # Try nftables first
        code, stdout, _ = await run_command("nft list tables 2>/dev/null | head -5", timeout=5)
        has_nft_rules = bool(stdout.strip()) if code == 0 else False

        # Fallback to iptables (NixOS often uses legacy iptables)
        has_iptables_rules = False
        if not has_nft_rules:
            code, stdout, _ = await run_command("iptables -L -n 2>/dev/null | grep -c Chain", timeout=5)
            try:
                chain_count = int(stdout.strip()) if code == 0 else 0
                has_iptables_rules = chain_count > 3  # More than default chains means rules exist
            except ValueError:
                has_iptables_rules = False

        has_rules = has_nft_rules or has_iptables_rules

        return {
            "status": "pass" if has_rules else "warning",
            "details": {
                "firewall_active": has_rules,
                "backend": "nftables" if has_nft_rules else ("iptables" if has_iptables_rules else "none"),
            }
        }


class ApplianceAgent:
    """
    Appliance-mode compliance agent.

    Runs in a loop performing:
    1. Phone-home checkin
    2. Drift detection
    3. Evidence generation
    4. L1 rules sync (periodic)
    """

    def __init__(self, config: ApplianceConfig):
        self.config = config
        self.client = CentralCommandClient(config)
        self.drift_checker: Optional[SimpleDriftChecker] = None
        self.windows_targets: List[WindowsTarget] = []
        self.linux_targets: List[LinuxTarget] = []
        self.linux_executor: Optional[LinuxExecutor] = None
        self.linux_drift_detector: Optional[LinuxDriftDetector] = None
        self.network_posture_detector: Optional[NetworkPostureDetector] = None
        self.windows_executor: Optional[WindowsExecutor] = None
        self.enabled_runbooks: List[str] = []  # Runbooks enabled for this appliance
        self.running = False
        self._last_rules_sync = datetime.min.replace(tzinfo=timezone.utc)
        self._last_windows_scan = datetime.min.replace(tzinfo=timezone.utc)
        self._last_linux_scan = datetime.min.replace(tzinfo=timezone.utc)
        self._last_network_posture_scan = datetime.min.replace(tzinfo=timezone.utc)
        self._rules_sync_interval = 3600  # Sync rules every hour
        self._linux_scan_interval = 300  # Scan Linux targets every 5 minutes
        self._network_posture_interval = 600  # Network posture scan every 10 minutes
        self._windows_scan_interval = 300  # Scan Windows every 5 minutes

        # Workstation discovery and compliance
        self.workstation_discovery: Optional[WorkstationDiscovery] = None
        self.workstation_checker: Optional[WorkstationComplianceChecker] = None
        self.workstations: List[Workstation] = []
        self._last_workstation_scan = datetime.min.replace(tzinfo=timezone.utc)
        self._last_workstation_discovery = datetime.min.replace(tzinfo=timezone.utc)
        self._workstation_scan_interval = 600  # Scan workstations every 10 minutes
        self._workstation_discovery_interval = 3600  # Discover from AD every hour
        self._workstation_enabled = getattr(config, 'workstation_enabled', True)
        self._domain_controller: Optional[str] = getattr(config, 'domain_controller', None)

        # Domain discovery for zero-friction deployment
        self.domain_discovery = DomainDiscovery()
        self.discovered_domain: Optional[DiscoveredDomain] = None
        self._domain_discovery_complete = False
        
        # AD enumeration for zero-friction deployment
        self.workstation_targets: List[Dict] = []  # Workstations for Go agent deployment
        self._last_enumeration = datetime.min.replace(tzinfo=timezone.utc)
        self._enumeration_interval = 3600  # Re-enumerate every hour

        # Three-tier healing components
        self.auto_healer: Optional[AutoHealer] = None
        self.incident_db: Optional[IncidentDatabase] = None
        self._healing_enabled = getattr(config, 'healing_enabled', True)
        self._healing_dry_run = getattr(config, 'healing_dry_run', True)

        # Learning system for L2->L1 promotion (data flywheel)
        self.learning_system: Optional[SelfLearningSystem] = None
        self._last_promotion_check = datetime.min.replace(tzinfo=timezone.utc)
        self._promotion_check_interval = getattr(config, 'promotion_check_interval', 3600)  # Hourly default
        self._auto_promote = getattr(config, 'auto_promote', False)  # Require human approval by default

        # Learning sync service for bidirectional server communication
        self.learning_sync: Optional[LearningSyncService] = None
        self._last_learning_sync = datetime.min.replace(tzinfo=timezone.utc)
        self._learning_sync_interval = getattr(config, 'learning_sync_interval', 14400)  # 4 hours default

        # Database pruning (daily cleanup to prevent disk space issues)
        self._last_prune_time = datetime.min.replace(tzinfo=timezone.utc)
        self._prune_interval = getattr(config, 'prune_interval', 86400)  # 24 hours default
        self._incident_retention_days = getattr(config, 'incident_retention_days', 30)  # Keep 30 days

        # Dual-mode sensor support
        self._sensor_enabled = getattr(config, 'sensor_enabled', True)
        self._sensor_port = getattr(config, 'sensor_port', 8080)
        self._sensor_server: Optional[uvicorn.Server] = None
        self._sensor_server_task: Optional[asyncio.Task] = None

        # gRPC server for Go agents (workstation-scale monitoring)
        self._grpc_enabled = getattr(config, 'grpc_enabled', GRPC_AVAILABLE)
        self._grpc_port = getattr(config, 'grpc_port', 50051)
        self._grpc_server_task: Optional[asyncio.Task] = None
        self.agent_registry: Optional[AgentRegistry] = None

        # Evidence signing
        self.signer: Optional[Ed25519Signer] = None
        self._signing_key_path = config.state_dir / "signing.key"

        # Evidence deduplication cache
        # Stores {check_type: (last_result, last_submit_time)}
        # Only submits on state change or hourly heartbeat to reduce storage by ~99%
        self._evidence_state_cache: Dict[str, tuple] = {}
        self._evidence_heartbeat_interval = 3600  # Hourly heartbeat even if no change

        # Initialize Windows targets from config
        for target_cfg in config.windows_targets:
            try:
                target = WindowsTarget(
                    hostname=target_cfg.get('hostname', target_cfg.get('ip', '')),
                    port=target_cfg.get('port', 5985),
                    username=target_cfg.get('username', ''),
                    password=target_cfg.get('password', ''),
                    use_ssl=target_cfg.get('use_ssl', False),
                    transport=target_cfg.get('transport', 'ntlm'),
                    ip_address=target_cfg.get('ip_address', target_cfg.get('ip', '')),
                )
                self.windows_targets.append(target)
                logger.info(f"Added Windows target: {target.hostname} (ip={target.ip_address})")
            except Exception as e:
                logger.warning(f"Invalid Windows target config: {e}")

        # Setup logging
        logging.basicConfig(
            level=getattr(logging, config.log_level),
            format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
            datefmt='%Y-%m-%dT%H:%M:%S%z'
        )

        # Ensure directories exist
        config.state_dir.mkdir(parents=True, exist_ok=True)
        config.evidence_dir.mkdir(parents=True, exist_ok=True)
        config.rules_dir.mkdir(parents=True, exist_ok=True)

    async def start(self):
        """Start the agent main loop."""
        logger.info(f"OsirisCare Compliance Agent v{VERSION}")
        logger.info(f"Site ID: {self.config.site_id}")
        logger.info(f"API Endpoint: {self.config.api_endpoint}")
        logger.info(f"Poll Interval: {self.config.poll_interval}s")
        logger.info("-" * 50)

        self.running = True

        # Initialize Ed25519 signing key (generate if not exists)
        try:
            was_generated, public_key_hex = ensure_signing_key(self._signing_key_path)
            self.signer = Ed25519Signer(self._signing_key_path)
            if was_generated:
                logger.info(f"Generated new signing key, public key: {public_key_hex[:16]}...")
            else:
                logger.info(f"Loaded signing key, public key: {public_key_hex[:16]}...")
        except Exception as e:
            logger.warning(f"Failed to initialize signing key: {e}")
            self.signer = None

        # Initialize drift checker if enabled
        if self.config.enable_drift_detection:
            try:
                self.drift_checker = SimpleDriftChecker()
                logger.info("Drift detection enabled")
            except Exception as e:
                logger.warning(f"Failed to initialize drift checker: {e}")

        # Initialize three-tier healing system
        if self._healing_enabled:
            try:
                await self._init_healing_system()
                mode = "DRY-RUN" if self._healing_dry_run else "ACTIVE"
                logger.info(f"Three-tier healing enabled ({mode})")

                # Configure sensor API with healing dependencies
                if self._sensor_enabled:
                    # Configure Windows sensor healing
                    configure_sensor_healing(
                        auto_healer=self.auto_healer,
                        windows_targets=self.windows_targets,
                        incident_db=self.incident_db,
                        config=self.config
                    )
                    # Configure Linux sensor healing
                    configure_linux_healing(
                        linux_healer=self.auto_healer,
                        linux_targets=self.linux_targets,
                        incident_db=self.incident_db,
                        config=self.config
                    )
                    logger.info(f"Sensor API configured for dual-mode operation (port {self._sensor_port})")
            except Exception as e:
                logger.warning(f"Failed to initialize healing system: {e}")
                self.auto_healer = None

        # Start sensor API web server for dual-mode architecture
        if self._sensor_enabled:
            await self._start_sensor_server()

        # Start gRPC server for Go agent communication
        if self._grpc_enabled and GRPC_AVAILABLE:
            await self._start_grpc_server()

        # Initial delay to let network settle
        await asyncio.sleep(5)

        # First-boot domain discovery (zero-friction deployment)
        if not self._domain_discovery_complete:
            await self._discover_domain_on_boot()

        # Main loop
        while self.running:
            try:
                await self._run_cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}")

            await asyncio.sleep(self.config.poll_interval)

        await self.client.close()
        logger.info("Agent stopped")

    async def stop(self):
        """Stop the agent gracefully."""
        logger.info("Stopping agent...")
        self.running = False

        # Stop sensor web server
        if self._sensor_server:
            self._sensor_server.should_exit = True
            if self._sensor_server_task:
                try:
                    self._sensor_server_task.cancel()
                    await asyncio.wait_for(self._sensor_server_task, timeout=5)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
            logger.info("Sensor API server stopped")

        # Stop gRPC server
        if self._grpc_server_task:
            try:
                self._grpc_server_task.cancel()
                await asyncio.wait_for(self._grpc_server_task, timeout=5)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            logger.info("gRPC server stopped")

    def _should_submit_evidence(self, check_type: str, result: str) -> bool:
        """
        Determine if evidence should be submitted based on deduplication rules.

        Only submits if:
        1. First submission for this check_type
        2. Result changed from last submission (state change)
        3. Hourly heartbeat interval elapsed (confirm state is still same)

        This reduces storage by ~99% by eliminating flapping duplicates.

        Args:
            check_type: Type of check (e.g., "windows_firewall_status", "linux_ntp_sync")
            result: Check result ("pass", "fail", "warn", etc.)

        Returns:
            True if evidence should be submitted, False to skip
        """
        now = datetime.now(timezone.utc)

        # Check cache for previous submission
        if check_type not in self._evidence_state_cache:
            # First submission for this check type - always submit
            self._evidence_state_cache[check_type] = (result, now)
            logger.debug(f"Evidence submit: {check_type} first submission")
            return True

        last_result, last_time = self._evidence_state_cache[check_type]

        # State changed - always submit
        if result != last_result:
            self._evidence_state_cache[check_type] = (result, now)
            logger.info(f"Evidence submit: {check_type} state changed {last_result} -> {result}")
            return True

        # Check if heartbeat interval elapsed
        elapsed = (now - last_time).total_seconds()
        if elapsed >= self._evidence_heartbeat_interval:
            self._evidence_state_cache[check_type] = (result, now)
            logger.debug(f"Evidence submit: {check_type} hourly heartbeat (elapsed: {elapsed:.0f}s)")
            return True

        # Skip - duplicate within heartbeat window
        logger.debug(f"Evidence skip: {check_type} duplicate (elapsed: {elapsed:.0f}s)")
        return False

    async def _start_sensor_server(self):
        """
        Start the sensor API web server for dual-mode architecture.

        Windows sensors push drift events to /api/sensor/* endpoints.
        Linux sensors push drift events to /sensor/* endpoints.
        """
        try:
            # Set Linux sensor scripts directory
            sensor_scripts_dir = Path(__file__).parent.parent.parent / "sensor" / "linux"
            set_sensor_scripts_dir(sensor_scripts_dir)

            # Create FastAPI app for sensor endpoints
            sensor_app = FastAPI(
                title="OsirisCare Sensor API",
                description="Receives drift events from Windows and Linux sensors",
                version=VERSION
            )

            # Include Windows sensor router (prefix: /api/sensor)
            sensor_app.include_router(sensor_router)

            # Include Linux sensor router (prefix: /sensor)
            sensor_app.include_router(linux_sensor_router)

            # Health check endpoint
            @sensor_app.get("/health")
            async def health():
                return {"status": "ok", "version": VERSION}

            # Configure uvicorn
            config = uvicorn.Config(
                sensor_app,
                host="0.0.0.0",
                port=self._sensor_port,
                log_level="warning",  # Reduce noise
                access_log=False
            )
            self._sensor_server = uvicorn.Server(config)

            # Start server in background task
            async def serve():
                try:
                    await self._sensor_server.serve()
                except asyncio.CancelledError:
                    pass

            self._sensor_server_task = asyncio.create_task(serve())
            logger.info(f"Sensor API server started on port {self._sensor_port}")

        except Exception as e:
            logger.warning(f"Failed to start sensor API server: {e}")
            self._sensor_enabled = False

    async def _start_grpc_server(self):
        """
        Start the gRPC server for Go agent communication.

        Go agents on Windows workstations connect via gRPC (port 50051)
        for persistent streaming of drift events. This solves the scalability
        problem of polling 25-50 workstations per site via WinRM.
        """
        if not GRPC_AVAILABLE:
            logger.warning("gRPC server disabled - grpcio not installed")
            return

        try:
            # Initialize agent registry
            self.agent_registry = AgentRegistry()

            # Start gRPC server in background task
            async def serve():
                try:
                    await grpc_serve(
                        port=self._grpc_port,
                        agent_registry=self.agent_registry,
                        healing_engine=self.auto_healer,
                        config=self.config,
                    )
                except asyncio.CancelledError:
                    pass

            self._grpc_server_task = asyncio.create_task(serve())
            logger.info(f"gRPC server started on port {self._grpc_port} (Go agents)")

        except Exception as e:
            logger.warning(f"Failed to start gRPC server: {e}")
            self._grpc_enabled = False

    async def _run_cycle(self):
        """Run one cycle of the agent loop."""
        timestamp = datetime.now(timezone.utc).isoformat()

        # 1. Phone-home checkin
        compliance_summary = None
        if self.drift_checker and self.config.enable_drift_detection:
            compliance_summary = await self._get_compliance_summary()

        checkin_response = await self.client.checkin(
            hostname=get_hostname(),
            mac_address=get_mac_address(),
            ip_addresses=get_ip_addresses(),
            uptime_seconds=get_uptime_seconds(),
            agent_version=VERSION,
            nixos_version=get_nixos_version(),
            compliance_summary=compliance_summary
        )

        if checkin_response is not None:
            logger.debug(f"[{timestamp}] Checkin OK")
            # Update Windows targets from server response (credential pull)
            await self._update_windows_targets_from_response(checkin_response)
            # Update Linux targets from server response (credential pull)
            await self._update_linux_targets_from_response(checkin_response)
            # Update enabled runbooks from server response (runbook config pull)
            self._update_enabled_runbooks_from_response(checkin_response)
            
            # Check if enumeration triggered (zero-friction deployment)
            if checkin_response.get('trigger_enumeration'):
                logger.info("Enumeration triggered from Central Command")
                await self._enumerate_ad_targets()
        else:
            logger.warning(f"[{timestamp}] Checkin failed")

        # 2. Run drift detection and upload evidence
        if self.drift_checker and self.config.enable_drift_detection:
            await self._run_drift_detection()

        # 3. Sync L1 rules (periodically)
        if self.config.enable_l1_sync:
            await self._maybe_sync_rules()

        # 4. Run Windows device scans (periodically)
        if self.windows_targets:
            await self._maybe_scan_windows()

        # 5. Run Linux device scans (periodically)
        if self.linux_targets:
            await self._maybe_scan_linux()

        # 6. Run network posture scan (periodically)
        await self._maybe_scan_network_posture()

        # 7. Run workstation compliance scan (periodically)
        if self._workstation_enabled and self._domain_controller:
            await self._maybe_scan_workstations()

        # 8. Deploy Go agents to workstations (if enumeration completed)
        if self.workstation_targets and self.discovered_domain:
            await self._maybe_deploy_go_agents()

        # 9. Check for L2->L1 promotion candidates (periodically)
        if self.learning_system:
            await self._maybe_check_promotions()

        # 10. Learning system sync (pattern stats + promoted rules)
        if self.learning_sync:
            await self._maybe_sync_learning()

        # 11. Database maintenance (pruning old incidents)
        await self._maybe_prune_database()

        # 12. Process pending orders (remote commands/updates)
        await self._process_pending_orders()

    async def _get_compliance_summary(self) -> dict:
        """Get summary of compliance status for checkin."""
        if not self.drift_checker:
            return {}

        try:
            # Quick summary - don't run full checks
            return {
                "agent_version": VERSION,
                "drift_enabled": True,
                "last_check": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            logger.warning(f"Failed to get compliance summary: {e}")
            return {}

    async def _run_drift_detection(self):
        """Run drift detection and upload results as evidence."""
        if not self.drift_checker:
            return

        # Perform multi-source NTP verification before signing evidence
        ntp_verification = None
        try:
            ntp_result = await verify_time_for_evidence(
                max_offset_ms=5000,  # 5 second threshold
                max_skew_ms=5000,    # 5 second skew between sources
                min_servers=3        # Need 3+ NTP servers to agree
            )
            if ntp_result.passed:
                ntp_verification = ntp_result.to_dict()
                logger.info(
                    f"NTP verification passed: {ntp_result.servers_responded} servers, "
                    f"median offset {ntp_result.median_offset_ms:.1f}ms"
                )
            else:
                logger.warning(f"NTP verification failed: {ntp_result.error}")
                # Still continue with evidence collection, but note the failure
                ntp_verification = ntp_result.to_dict()
        except Exception as e:
            logger.warning(f"NTP verification error: {e}")

        try:
            # Run all drift checks
            results = await self.drift_checker.run_all_checks()

            # HIPAA control mappings for NixOS drift checks
            hipaa_controls = {
                "nixos_generation": "164.312(c)(1)",  # Integrity
                "ntp_sync": "164.312(b)",             # Audit controls
                "services_running": "164.312(a)(1)",  # Access controls
                "disk_usage": "164.308(a)(7)",        # Contingency plan
                "firewall_enabled": "164.312(e)(1)", # Transmission security
            }

            for check_name, check_result in results.items():
                # Attempt healing if drift detected
                healing_result = await self._handle_drift_healing(check_name, check_result)

                if not self.config.enable_evidence_upload:
                    continue

                # Generate evidence bundle hash
                evidence_data = {
                    "check_name": check_name,
                    "status": check_result.get("status", "unknown"),
                    "details": check_result.get("details", {}),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "site_id": self.config.site_id,
                    "agent_version": VERSION,
                }

                # Add NTP verification to evidence for timestamp integrity
                if ntp_verification:
                    evidence_data["ntp_verification"] = ntp_verification

                # Add healing outcome to evidence if healing was attempted
                if healing_result:
                    evidence_data["healing"] = {
                        "attempted": True,
                        "incident_id": healing_result.get("incident_id"),
                        "resolution_level": healing_result.get("resolution_level"),
                        "action_taken": healing_result.get("action_taken"),
                        "success": healing_result.get("success"),
                        "dry_run": self._healing_dry_run,
                    }

                # Compute hash of the evidence data
                evidence_json = json.dumps(evidence_data, sort_keys=True)
                bundle_hash = hashlib.sha256(evidence_json.encode()).hexdigest()

                # Upload to Central Command (with deduplication)
                # Signing happens in client where full payload is constructed
                check_status = check_result.get("status", "unknown")
                if self._should_submit_evidence(check_name, check_status):
                    bundle_id = await self.client.submit_evidence(
                        bundle_hash=bundle_hash,
                        check_type=check_name,
                        check_result=check_status,
                        evidence_data=evidence_data,
                        hipaa_control=hipaa_controls.get(check_name),
                        signer=self.signer  # Pass signer to client for signing
                    )

                    if bundle_id:
                        logger.debug(f"Evidence uploaded: {check_name} -> {bundle_id} (signed={self.signer is not None})")

                    # Store locally as well
                    await self._store_local_evidence(bundle_id, evidence_data)

        except Exception as e:
            logger.error(f"Drift detection failed: {e}")

    async def _store_local_evidence(
        self,
        bundle_id: str,
        evidence_data: dict,
        agent_signature: Optional[str] = None
    ):
        """Store evidence bundle locally with optional signature."""
        try:
            date = datetime.now(timezone.utc)
            bundle_dir = (
                self.config.evidence_dir /
                f"{date.year:04d}" /
                f"{date.month:02d}" /
                f"{date.day:02d}" /
                bundle_id
            )
            bundle_dir.mkdir(parents=True, exist_ok=True)

            bundle_path = bundle_dir / "bundle.json"
            with open(bundle_path, 'w') as f:
                json.dump(evidence_data, f, indent=2, sort_keys=True)

            # Store signature if present
            if agent_signature:
                sig_path = bundle_dir / "bundle.sig"
                sig_path.write_text(agent_signature)
                logger.debug(f"Local evidence stored: {bundle_path} (with signature)")
            else:
                logger.debug(f"Local evidence stored: {bundle_path}")

        except Exception as e:
            logger.warning(f"Failed to store local evidence: {e}")

    # =========================================================================
    # Three-Tier Healing System
    # =========================================================================

    async def _init_healing_system(self):
        """Initialize the three-tier auto-healing system."""
        # Check if L2 is enabled and has an API key
        l2_enabled = getattr(self.config, 'l2_enabled', False) and bool(getattr(self.config, 'l2_api_key', ''))

        # Create config for AutoHealer
        healer_config = AutoHealerConfig(
            db_path=str(self.config.state_dir / "incidents.db"),
            rules_dir=self.config.rules_dir,
            enable_level1=True,
            enable_level2=l2_enabled,
            enable_level3=True,
            dry_run=self._healing_dry_run,
            # L2 LLM settings
            api_provider=getattr(self.config, 'l2_api_provider', 'anthropic'),
            api_model=getattr(self.config, 'l2_api_model', 'claude-3-5-haiku-latest'),
            api_key=getattr(self.config, 'l2_api_key', None),
        )

        # Create AutoHealer orchestrator (handles all initialization internally)
        self.auto_healer = AutoHealer(
            config=healer_config,
            action_executor=self._execute_healing_action
        )

        # Keep reference to incident database for later use
        self.incident_db = self.auto_healer.incident_db

        # Initialize learning system for L2->L1 promotion
        promotion_config = PromotionConfig(
            min_occurrences=5,
            min_l2_resolutions=3,
            min_success_rate=0.9,
            max_avg_resolution_time_ms=30000,
            check_interval_hours=self._promotion_check_interval // 3600,
            auto_promote=self._auto_promote,
            promotion_output_dir=self.config.rules_dir / "promoted"
        )
        self.learning_system = SelfLearningSystem(
            incident_db=self.incident_db,
            config=promotion_config
        )
        logger.info(
            f"Learning system initialized (auto_promote={self._auto_promote}, "
            f"check_interval={self._promotion_check_interval}s)"
        )

        # Initialize learning sync service for bidirectional server communication
        self.learning_sync = LearningSyncService(
            client=self.client,
            incident_db=self.incident_db,
            site_id=self.config.site_id,
            appliance_id=self._get_appliance_id(),
            promoted_rules_dir=self.config.rules_dir / "promoted"
        )
        logger.info(
            f"Learning sync service initialized (interval={self._learning_sync_interval}s)"
        )

        # Wire up learning_sync to auto_healer for execution telemetry
        self.auto_healer.learning_sync = self.learning_sync

        logger.info(f"Healing system initialized (L1 rules from {self.config.rules_dir})")

    async def _execute_healing_action(
        self,
        action: str,
        params: Dict[str, Any],
        site_id: Optional[str] = None,
        host_id: Optional[str] = None,
        incident: Optional[Incident] = None
    ) -> Dict[str, Any]:
        """
        Execute a healing action.

        This is the action executor passed to all healing tiers.
        In dry-run mode, just logs the action without executing.

        Args:
            action: The action type to execute
            params: Action parameters from the L1 rule
            site_id: Site ID for context
            host_id: Target host ID (for Windows runbooks)
            incident: Optional incident object for context
        """
        if self._healing_dry_run:
            logger.info(f"[DRY-RUN] Would execute: {action} with params: {params}")
            return {
                "dry_run": True,
                "action": action,
                "params": params,
                "status": "simulated_success"
            }

        # Add host_id to params so handlers can use it
        if host_id:
            params = {**params, "target_host": host_id}

        # Map action types to handlers
        action_handlers = {
            "restart_service": self._heal_restart_service,
            "run_command": self._heal_run_command,
            "run_windows_runbook": self._heal_run_windows_runbook,
            "run_linux_runbook": self._heal_run_linux_runbook,
            "escalate": self._heal_escalate,
        }

        # Map legacy action names to Windows runbook IDs
        legacy_action_runbooks = {
            "restore_firewall_baseline": "RB-WIN-SEC-001",  # Windows Firewall Enable
            "restore_audit_policy": "RB-WIN-SEC-002",       # Audit Policy
            "restore_defender": "RB-WIN-SEC-006",           # Defender Real-time
            "enable_bitlocker": "RB-WIN-SEC-005",           # BitLocker Status
            # Alert-style actions from L1 rules - FULL COVERAGE mapping
            # Core security
            "alert:firewall_disabled": "RB-WIN-FIREWALL-001",
            "alert:defender_disabled": "RB-WIN-SEC-006",
            "alert:bitlocker_disabled": "RB-WIN-SEC-005",
            # Policy compliance
            "alert:audit_policy_drift": "RB-WIN-SEC-002",
            "alert:password_policy_drift": "RB-WIN-SEC-004",
            "alert:lockout_policy_drift": "RB-WIN-SEC-003",
            "alert:screen_lock_drift": "RB-WIN-SEC-003",
            # Advanced security
            "alert:smb_signing_drift": "RB-WIN-SEC-007",
            "alert:ntlm_security_drift": "RB-WIN-SEC-008",
            "alert:unauthorized_admin": "RB-WIN-SEC-009",
            "alert:nla_disabled": "RB-WIN-SEC-010",
            "alert:uac_disabled": "RB-WIN-SEC-011",
            "alert:eventlog_protection_drift": "RB-WIN-SEC-012",
            "alert:credguard_disabled": "RB-WIN-SEC-013",
            # Services
            "alert:time_service_failed": "RB-WIN-SVC-004",
            "alert:dns_client_failed": "RB-WIN-NET-001",
            # Patching
            "alert:patches_missing": "RB-WIN-PATCH-001",
        }

        handler = action_handlers.get(action)
        if handler:
            return await handler(params, incident)
        elif action in legacy_action_runbooks:
            # Translate legacy action to Windows runbook
            runbook_id = legacy_action_runbooks[action]
            logger.info(f"Translating legacy action '{action}' to runbook {runbook_id}")
            runbook_params = {
                **params,
                "runbook_id": runbook_id,
                "phases": ["remediate", "verify"],
            }
            return await self._heal_run_windows_runbook(runbook_params, incident)
        elif action.startswith("run_runbook:"):
            # Auto-promoted L1 rules use run_runbook:<RUNBOOK_ID> format
            runbook_id = action.split(":", 1)[1]

            # Map AUTO-* format to proper RB-WIN-* runbook IDs
            # (Legacy promoted rules may use AUTO-<CHECK_TYPE> format)
            if runbook_id.startswith("AUTO-"):
                from .learning_loop import CHECK_TYPE_TO_RUNBOOK
                auto_check_type = runbook_id[5:].lower().replace("_", "")  # AUTO-BITLOCKER_STATUS -> bitlockerstatus
                # Try exact match first, then partial matches
                mapped_id = CHECK_TYPE_TO_RUNBOOK.get(auto_check_type)
                if not mapped_id:
                    # Try with underscores preserved
                    auto_check_type_underscore = runbook_id[5:].lower()  # AUTO-BITLOCKER_STATUS -> bitlocker_status
                    mapped_id = CHECK_TYPE_TO_RUNBOOK.get(auto_check_type_underscore)
                if mapped_id:
                    logger.info(f"Mapped legacy runbook '{runbook_id}' to '{mapped_id}'")
                    runbook_id = mapped_id
                else:
                    logger.warning(f"Could not map AUTO-* runbook '{runbook_id}' to valid runbook ID")

            logger.info(f"Executing auto-promoted rule runbook: {runbook_id}")
            runbook_params = {
                **params,
                "runbook_id": runbook_id,
                "phases": ["remediate", "verify"],
            }
            return await self._heal_run_windows_runbook(runbook_params, incident)
        else:
            logger.warning(f"Unknown healing action: {action}")
            return {"error": f"Unknown action: {action}"}

    async def _heal_restart_service(
        self, params: Dict[str, Any], incident: Optional[Incident]
    ) -> Dict[str, Any]:
        """Restart a systemd service."""
        service_name = params.get("service_name")
        if not service_name:
            return {"error": "service_name required"}

        code, stdout, stderr = await run_command(
            f"systemctl restart {service_name}",
            timeout=30
        )

        success = code == 0
        logger.info(f"Restarted service {service_name}: {'OK' if success else 'FAILED'}")

        return {
            "action": "restart_service",
            "service": service_name,
            "success": success,
            "stdout": stdout,
            "stderr": stderr
        }

    async def _heal_run_command(
        self, params: Dict[str, Any], incident: Optional[Incident]
    ) -> Dict[str, Any]:
        """Run a shell command for healing."""
        command = params.get("command")
        timeout = params.get("timeout", 30)

        if not command:
            return {"error": "command required"}

        code, stdout, stderr = await run_command(command, timeout=timeout)
        success = code == 0

        return {
            "action": "run_command",
            "command": command,
            "success": success,
            "exit_code": code,
            "stdout": stdout[:1000],  # Truncate
            "stderr": stderr[:500]
        }

    # Mapping from runbook IDs to Go agent heal commands
    # Only runbooks that the Go agent can execute locally
    GO_AGENT_RUNBOOK_MAP = {
        "RB-WIN-SEC-001": {"check_type": "firewall", "action": "enable"},
        "RB-WIN-SEC-003": {"check_type": "screenlock", "action": "configure"},
        "RB-WIN-SEC-005": {"check_type": "bitlocker", "action": "enable"},
        "RB-WIN-SEC-006": {"check_type": "defender", "action": "start"},
    }

    async def _heal_run_windows_runbook(
        self, params: Dict[str, Any], incident: Optional[Incident]
    ) -> Dict[str, Any]:
        """Execute a Windows runbook via Go agent (fast) or WinRM (fallback)."""
        runbook_id = params.get("runbook_id")
        target_host = params.get("target_host")
        phases = params.get("phases", ["remediate", "verify"])

        if not runbook_id:
            return {"error": "runbook_id required"}

        # === Go Agent Fast Path ===
        # Check if we can route this heal through a connected Go agent (~10ms vs ~8s)
        if (
            GRPC_AVAILABLE
            and compliance_pb2 is not None
            and self.agent_registry is not None
            and target_host
            and runbook_id in self.GO_AGENT_RUNBOOK_MAP
        ):
            # Check if a Go agent is connected for this hostname
            if self.agent_registry.has_agent_for_host(target_host):
                heal_spec = self.GO_AGENT_RUNBOOK_MAP[runbook_id]
                command_id = f"heal-{uuid.uuid4().hex[:12]}"

                # Create HealCommand protobuf message
                heal_command = compliance_pb2.HealCommand(
                    command_id=command_id,
                    check_type=heal_spec["check_type"],
                    action=heal_spec["action"],
                    params={},
                    timeout_seconds=60,
                )

                # Queue the command for delivery on next heartbeat
                queued = self.agent_registry.queue_heal_command(target_host, heal_command)
                if queued:
                    logger.info(
                        f"Routed heal via Go agent: {target_host}/{runbook_id} "
                        f"→ {heal_spec['check_type']}/{heal_spec['action']} "
                        f"(command_id={command_id})"
                    )
                    return {
                        "action": "run_windows_runbook",
                        "runbook_id": runbook_id,
                        "target": target_host,
                        "method": "go_agent",
                        "command_id": command_id,
                        "success": True,
                        "queued": True,
                        "note": "Heal command queued for Go agent (10ms vs 8s WinRM)",
                    }

        # === WinRM Fallback Path ===
        # No Go agent available, use WinRM (slower but works without agent deployment)

        # Find target - try to match by IP, hostname, or short name
        target = None
        if target_host:
            target_host_upper = target_host.upper()
            # Check if target_host looks like an IP address
            is_ip = all(c.isdigit() or c == '.' for c in target_host) and target_host.count('.') == 3
            target_host_short = target_host.split('.')[0].upper() if not is_ip else None

            for t in self.windows_targets:
                # Check IP address match (incidents typically use IPs)
                if t.ip_address and t.ip_address == target_host:
                    target = t
                    break
                # Check hostname match (exact)
                if t.hostname == target_host:
                    target = t
                    break
                # Check short name match (only for hostnames, not IPs)
                if target_host_short and t.hostname.split('.')[0].upper() == target_host_short:
                    target = t
                    break

        # Fallback to first available target if no match found
        if not target and self.windows_targets:
            target = self.windows_targets[0]
            if target_host:
                logger.warning(f"Target host '{target_host}' not found in windows_targets, using fallback: {target.hostname} (ip={target.ip_address})")

        if not target:
            return {"error": "No Windows target available"}

        # Import and execute runbook
        try:
            from .runbooks.windows.executor import WindowsExecutor

            # Create executor and run the runbook
            executor = WindowsExecutor(targets=[target])
            results = await executor.run_runbook(
                target=target,
                runbook_id=runbook_id,
                phases=phases,
                collect_evidence=True
            )

            # Check overall success - all phases must succeed
            overall_success = all(r.success for r in results)

            # Build response with phase details
            phase_details = []
            for result in results:
                phase_details.append({
                    "phase": result.phase,
                    "success": result.success,
                    "output": result.output,
                    "error": result.error,
                    "duration_seconds": result.duration_seconds
                })

            logger.info(
                f"Windows runbook {runbook_id} on {target.hostname}: "
                f"{'SUCCESS' if overall_success else 'FAILED'} - {len(results)} phases"
            )

            # Log details and extract error from failed phases
            first_error = None
            if not overall_success:
                for result in results:
                    if not result.success:
                        logger.warning(
                            f"  Phase '{result.phase}' failed: error={result.error}, "
                            f"output={str(result.output)[:200]}"
                        )
                        # Capture the first error for the return value
                        if first_error is None:
                            first_error = result.error or f"Phase '{result.phase}' failed"

            return {
                "action": "run_windows_runbook",
                "runbook_id": runbook_id,
                "target": target.hostname,
                "method": "winrm",  # Distinguish from go_agent path
                "phases": phases,
                "success": overall_success,
                "phase_details": phase_details,
                "error": first_error  # Include error for L1/L2 healing result
            }

        except ImportError as e:
            return {"error": f"Windows executor not available: {e}", "success": False}
        except Exception as e:
            logger.error(f"Windows runbook execution failed: {e}")
            return {"error": str(e), "success": False}

    async def _heal_run_linux_runbook(
        self, params: Dict[str, Any], incident: Optional[Incident]
    ) -> Dict[str, Any]:
        """Execute a Linux runbook via SSH."""
        runbook_id = params.get("runbook_id")
        target_host = params.get("target_host")
        phases = params.get("phases", ["remediate", "verify"])

        if not runbook_id:
            return {"error": "runbook_id required", "success": False}

        # Find target in linux_targets
        target = None
        if target_host:
            for t in self.linux_targets:
                if t.hostname == target_host or t.hostname.split('.')[0] == target_host:
                    target = t
                    break

        if not target:
            logger.warning(f"Linux target not found: {target_host}")
            return {"error": f"Linux target not found: {target_host}", "success": False}

        try:
            from .runbooks.linux.executor import LinuxExecutor

            executor = LinuxExecutor(targets=[target])

            logger.info(f"Executing Linux runbook {runbook_id} on {target.hostname}, phases={phases}")

            results = await executor.run_runbook(
                target,
                runbook_id,
                phases=phases,
                collect_evidence=True
            )

            # Analyze results
            overall_success = all(r.success for r in results if r.phase in phases)
            phase_details = [
                {
                    "phase": r.phase,
                    "success": r.success,
                    "output": r.output,
                    "error": r.error,
                    "duration": r.duration_seconds
                }
                for r in results
            ]

            # Log details and extract error from failed phases
            first_error = None
            if not overall_success:
                for result in results:
                    if not result.success:
                        logger.warning(
                            f"  Phase '{result.phase}' failed: error={result.error}, "
                            f"output={str(result.output)[:200]}"
                        )
                        if first_error is None:
                            first_error = result.error or f"Phase '{result.phase}' failed"

            return {
                "action": "run_linux_runbook",
                "runbook_id": runbook_id,
                "target": target.hostname,
                "method": "ssh",
                "phases": phases,
                "success": overall_success,
                "phase_details": phase_details,
                "error": first_error
            }

        except ImportError as e:
            return {"error": f"Linux executor not available: {e}", "success": False}
        except Exception as e:
            logger.error(f"Linux runbook execution failed: {e}")
            return {"error": str(e), "success": False}

    async def _heal_escalate(
        self, params: Dict[str, Any], incident: Optional[Incident]
    ) -> Dict[str, Any]:
        """Escalate to L3 (human intervention)."""
        reason = params.get("reason", "Escalation required")
        urgency = params.get("urgency", "medium")

        # Send to Central Command escalation endpoint
        try:
            escalation_data = {
                "incident_id": incident.id if incident else "unknown",
                "incident_type": incident.incident_type if incident else "unknown",
                "reason": reason,
                "urgency": urgency,
                "site_id": self.config.site_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            # Use the client to post escalation
            await self.client._request(
                "POST",
                "/api/escalations",
                json=escalation_data
            )

            logger.info(f"Escalated to L3: {reason}")
            return {"action": "escalate", "success": True, "reason": reason}

        except Exception as e:
            logger.error(f"Escalation failed: {e}")
            return {"action": "escalate", "success": False, "error": str(e)}

    async def _handle_drift_healing(
        self,
        check_name: str,
        check_result: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Convert drift detection result to incident and handle via AutoHealer.

        Returns healing result if healing was attempted, None otherwise.
        """
        if not self.auto_healer:
            return None

        # Only heal on failures
        status = check_result.get("status", "pass")
        if status == "pass":
            return None

        # Determine severity based on status
        severity_map = {
            "fail": "high",
            "warning": "medium",
            "error": "critical"
        }
        severity = severity_map.get(status, "low")

        # Build raw_data for healing (L1 rules check these fields)
        raw_data = {
            "check_type": check_name,
            "drift_detected": True,
            "status": status,
            "platform": "nixos",
            **check_result.get("details", {})
        }

        # Report incident to Central Command so it appears in dashboard
        try:
            await self.client.report_incident(
                incident_type=check_name,
                severity=severity,
                check_type=check_name,
                details={
                    "message": f"Drift detected: {check_name}",
                    "status": status,
                    **check_result.get("details", {})
                },
                pre_state=check_result.get("details", {}),
            )
        except Exception as e:
            logger.warning(f"Failed to report incident to Central Command: {e}")

        # Handle through three-tier system (heal() creates incident internally)
        try:
            result = await self.auto_healer.heal(
                site_id=self.config.site_id,
                host_id=get_hostname(),
                incident_type=check_name,
                severity=severity,
                raw_data=raw_data
            )

            # Log the outcome
            if result.success:
                logger.info(
                    f"Healing {check_name}: {result.resolution_level} → {result.action_taken} (SUCCESS)"
                )
                # Report pattern to learning flywheel for L1/L2 promotions
                if result.action_taken:
                    try:
                        await self.client.report_pattern(
                            check_type=check_name,
                            issue_signature=f"{check_name}:{get_hostname()}",
                            resolution_steps=[result.action_taken],
                            success=True,
                            execution_time_ms=result.resolution_time_ms,
                        )
                        logger.debug(f"Reported pattern for {check_name} to learning loop")
                    except Exception as e:
                        logger.debug(f"Pattern report failed (non-critical): {e}")
            else:
                logger.warning(
                    f"Healing {check_name}: {result.resolution_level} → {result.action_taken} (FAILED: {result.error})"
                )

            return {
                "incident_id": result.incident_id,
                "resolution_level": result.resolution_level,
                "action_taken": result.action_taken,
                "success": result.success,
                "duration_ms": result.resolution_time_ms,
                "error": result.error
            }

        except Exception as e:
            logger.error(f"Healing failed for {check_name}: {e}")
            return {
                "error": str(e),
                "success": False
            }

    async def _maybe_sync_rules(self):
        """Sync L1 rules if enough time has passed."""
        now = datetime.now(timezone.utc)
        elapsed = (now - self._last_rules_sync).total_seconds()

        if elapsed < self._rules_sync_interval:
            return

        try:
            rules = await self.client.sync_rules()
            if rules is not None:
                # Store rules locally
                rules_file = self.config.rules_dir / "l1_rules.json"
                with open(rules_file, 'w') as f:
                    json.dump(rules, f, indent=2)

                self._last_rules_sync = now
                logger.info(f"L1 rules synced: {len(rules)} rules")

        except Exception as e:
            logger.warning(f"Rules sync failed: {e}")

    async def _update_windows_targets_from_response(self, response: Dict):
        """
        Update Windows targets from server check-in response.

        This enables credential-pull architecture where credentials are fetched
        fresh from Central Command on each check-in cycle, rather than stored
        locally. Benefits:
        - No cached credentials on disk
        - Credential rotation picked up automatically
        - Stolen appliance doesn't expose credentials
        """
        windows_targets = response.get('windows_targets', [])

        if not windows_targets:
            return

        # Convert to WindowsTarget objects
        new_targets = []
        for target_cfg in windows_targets:
            hostname = target_cfg.get('hostname')
            if not hostname:
                continue  # Skip targets that need discovery

            try:
                target = WindowsTarget(
                    hostname=hostname,
                    username=target_cfg.get('username', ''),
                    password=target_cfg.get('password', ''),
                    use_ssl=target_cfg.get('use_ssl', False),
                    port=5986 if target_cfg.get('use_ssl') else 5985,
                    transport='ntlm',
                    ip_address=target_cfg.get('ip_address', target_cfg.get('ip', '')),
                )
                new_targets.append(target)
            except Exception as e:
                logger.warning(f"Invalid Windows target from server: {e}")

        if new_targets:
            # Replace targets with server-provided ones
            self.windows_targets = new_targets
            logger.info(f"Updated {len(new_targets)} Windows targets from Central Command")

    async def _update_linux_targets_from_response(self, response: Dict):
        """
        Update Linux targets from server check-in response.

        Uses credential-pull architecture just like Windows targets.
        Linux credentials can be SSH password or SSH key based.
        """
        linux_targets = response.get('linux_targets', [])

        if not linux_targets:
            return

        # Convert to LinuxTarget objects
        new_targets = []
        for target_cfg in linux_targets:
            hostname = target_cfg.get('hostname')
            if not hostname:
                continue

            try:
                target = LinuxTarget(
                    hostname=hostname,
                    port=target_cfg.get('port', 22),
                    username=target_cfg.get('username', 'root'),
                    password=target_cfg.get('password'),
                    private_key=target_cfg.get('private_key'),
                    distro=target_cfg.get('distro'),
                )
                new_targets.append(target)
            except Exception as e:
                logger.warning(f"Invalid Linux target from server: {e}")

        if new_targets:
            # Replace targets with server-provided ones
            self.linux_targets = new_targets

            # Recreate executor and drift detector with new targets
            self.linux_executor = LinuxExecutor(self.linux_targets)
            self.linux_drift_detector = LinuxDriftDetector(
                targets=self.linux_targets,
                executor=self.linux_executor
            )

            logger.info(f"Updated {len(new_targets)} Linux targets from Central Command")

    def _update_enabled_runbooks_from_response(self, response: Dict):
        """
        Update enabled runbooks from server check-in response.

        This enables runbook filtering where partners can enable/disable
        specific runbooks per site or appliance. Benefits:
        - Partners can customize remediation actions per client
        - Disruptive runbooks can be disabled for specific sites
        - New runbooks can be rolled out gradually

        Empty list means use defaults (all enabled). Non-empty list is the
        explicit set of enabled runbook IDs.
        """
        enabled_runbooks = response.get('enabled_runbooks', [])

        if enabled_runbooks:
            # Only log if the list actually changed
            if set(enabled_runbooks) != set(self.enabled_runbooks):
                self.enabled_runbooks = enabled_runbooks
                logger.info(f"Updated enabled runbooks: {len(enabled_runbooks)} runbooks active")
                logger.debug(f"Enabled runbooks: {', '.join(enabled_runbooks[:5])}{'...' if len(enabled_runbooks) > 5 else ''}")
        else:
            # Empty list = use defaults (don't clear)
            pass

    def is_runbook_enabled(self, runbook_id: str) -> bool:
        """
        Check if a runbook is enabled for this appliance.

        Returns True if:
        - enabled_runbooks is empty (use all runbooks by default)
        - runbook_id is in the enabled_runbooks list
        """
        if not self.enabled_runbooks:
            return True  # Default: all runbooks enabled
        return runbook_id in self.enabled_runbooks

    def _get_targets_needing_poll(self) -> List[WindowsTarget]:
        """
        Return Windows targets that need WinRM polling (no active sensor).

        Dual-mode architecture:
        - Hosts with active sensors: Events pushed via /api/sensor/drift
        - Hosts without sensors: Polled via WinRM
        """
        if not self._sensor_enabled:
            return self.windows_targets

        # Get hostnames with active sensors
        sensor_hosts = get_sensor_hosts()

        # Filter to targets without active sensors
        targets_to_poll = []
        for target in self.windows_targets:
            hostname = target.hostname.lower()
            # Check if any sensor matches this target
            has_sensor = any(
                sensor.lower() == hostname or
                sensor.lower() in hostname or
                hostname in sensor.lower()
                for sensor in sensor_hosts
            )
            if not has_sensor:
                targets_to_poll.append(target)

        return targets_to_poll

    async def _maybe_scan_windows(self):
        """
        Scan Windows targets if enough time has passed.

        Uses dual-mode logic: skips hosts with active sensors,
        only polls hosts without sensors.
        """
        now = datetime.now(timezone.utc)
        elapsed = (now - self._last_windows_scan).total_seconds()

        if elapsed < self._windows_scan_interval:
            return

        # Dual-mode: only poll targets without active sensors
        targets_to_poll = self._get_targets_needing_poll()
        sensor_count = len(self.windows_targets) - len(targets_to_poll)

        if self._sensor_enabled and sensor_count > 0:
            logger.info(
                f"Dual-mode: {sensor_count} sensors active, "
                f"polling {len(targets_to_poll)} hosts via WinRM"
            )

        if targets_to_poll:
            logger.info(f"Scanning {len(targets_to_poll)} Windows targets...")
            for target in targets_to_poll:
                await self._scan_windows_target(target)
        elif self.windows_targets:
            logger.debug("All Windows hosts have active sensors - skipping WinRM poll")

        self._last_windows_scan = now

    async def _scan_windows_target(self, target: WindowsTarget):
        """Run compliance checks on a single Windows target."""
        try:
            import winrm

            # Build WinRM endpoint
            protocol = "https" if target.use_ssl else "http"
            endpoint = f"{protocol}://{target.hostname}:{target.port}/wsman"

            session = winrm.Session(
                endpoint,
                auth=(target.username, target.password),
                transport=target.transport,
                server_cert_validation='ignore' if not target.use_ssl else 'validate',
            )

            # Test connectivity
            result = session.run_ps("$env:COMPUTERNAME")
            if result.status_code != 0:
                raise RuntimeError(f"WinRM failed: {result.std_err.decode()}")

            computer_name = result.std_out.decode().strip()
            logger.info(f"Connected to Windows: {computer_name}")

            # Run basic compliance checks
            checks = [
                ("windows_defender", "$status = Get-MpComputerStatus; @{Enabled=$status.AntivirusEnabled;RealTimeEnabled=$status.RealTimeProtectionEnabled;Updated=$status.AntivirusSignatureLastUpdated} | ConvertTo-Json"),
                ("firewall_status", "Get-NetFirewallProfile | Select-Object Name,Enabled | ConvertTo-Json"),
                ("password_policy", "net accounts | Select-String 'password|lockout'"),
                ("bitlocker_status", "Get-BitLockerVolume -MountPoint C: -ErrorAction SilentlyContinue | Select-Object MountPoint,ProtectionStatus | ConvertTo-Json"),
                ("audit_policy", "auditpol /get /subcategory:'Logon'"),
                # Critical Windows Services check
                ("service_w32time", "Get-Service W32Time -ErrorAction SilentlyContinue | Select-Object Name,Status,StartType | ConvertTo-Json"),
                ("service_dns", "Get-Service DNS -ErrorAction SilentlyContinue | Select-Object Name,Status,StartType | ConvertTo-Json"),
                ("service_spooler", "Get-Service Spooler -ErrorAction SilentlyContinue | Select-Object Name,Status,StartType | ConvertTo-Json"),
                # Windows Server Backup check (requires Windows Server Backup feature)
                ("backup_status", """
try {
    $wsb = Get-WBSummary -ErrorAction Stop
    if ($wsb.LastSuccessfulBackupTime) {
        $age = (Get-Date) - $wsb.LastSuccessfulBackupTime
        @{
            BackupType = 'WindowsServerBackup'
            LastBackup = $wsb.LastSuccessfulBackupTime.ToString('o')
            BackupAgeHours = [math]::Round($age.TotalHours, 1)
            LastResult = $wsb.LastBackupResultHR
            NextBackup = $wsb.NextBackupTime
        } | ConvertTo-Json
    } else {
        @{BackupType = 'WindowsServerBackup'; Error = 'NoBackupConfigured'} | ConvertTo-Json
    }
} catch {
    @{BackupType = 'NotInstalled'; Error = $_.Exception.Message} | ConvertTo-Json
}
"""),
            ]

            for check_name, ps_cmd in checks:
                try:
                    check_result = session.run_ps(ps_cmd)
                    output = check_result.std_out.decode().strip()

                    # Default: pass if command succeeded
                    status = "pass" if check_result.status_code == 0 else "fail"

                    # For specific checks, validate the actual output
                    if check_name == "firewall_status" and check_result.status_code == 0:
                        # Check if all firewall profiles are enabled
                        try:
                            import json as json_module
                            profiles = json_module.loads(output)
                            if isinstance(profiles, list):
                                all_enabled = all(p.get("Enabled", False) for p in profiles)
                            else:
                                all_enabled = profiles.get("Enabled", False)
                            status = "pass" if all_enabled else "fail"
                        except Exception:
                            pass  # Keep original status if parsing fails

                    elif check_name == "windows_defender" and check_result.status_code == 0:
                        # Check if Windows Defender is enabled AND real-time protection is on
                        try:
                            import json as json_module
                            defender = json_module.loads(output)
                            is_enabled = defender.get("Enabled", False)
                            realtime_enabled = defender.get("RealTimeEnabled", False)
                            # Both must be true for pass
                            status = "pass" if (is_enabled and realtime_enabled) else "fail"
                        except Exception:
                            # Fallback to string check for both
                            output_lower = output.lower()
                            status = "pass" if ('"enabled": true' in output_lower and '"realtimeenabled": true' in output_lower) else "fail"

                    elif check_name == "bitlocker_status" and check_result.status_code == 0:
                        # Check if BitLocker protection is on
                        try:
                            import json as json_module
                            bitlocker = json_module.loads(output)
                            # ProtectionStatus: 0=Off, 1=On, 2=Unknown
                            protection = bitlocker.get("ProtectionStatus", 0)
                            status = "pass" if protection == 1 else "fail"
                        except Exception:
                            # If no BitLocker volume found, that's a fail
                            status = "fail" if not output.strip() else status

                    elif check_name == "password_policy" and check_result.status_code == 0:
                        # Check for minimum password requirements
                        try:
                            # Look for minimum password length >= 8
                            import re
                            match = re.search(r'Minimum password length:\s*(\d+)', output, re.IGNORECASE)
                            if match:
                                min_len = int(match.group(1))
                                status = "pass" if min_len >= 8 else "fail"
                        except Exception:
                            pass

                    elif check_name == "audit_policy" and check_result.status_code == 0:
                        # Check that Logon auditing is enabled (Success and/or Failure)
                        # The query is for subcategory:'Logon' specifically
                        if "Success" in output or "Failure" in output:
                            # Make sure it's not "No Auditing" which would not contain Success/Failure
                            status = "pass"
                        else:
                            # "No Auditing" means Logon events are not being captured
                            status = "fail"

                    elif check_name == "backup_status":
                        # Check Windows Server Backup status
                        try:
                            import json as json_module
                            backup_data = json_module.loads(output)
                            backup_type = backup_data.get("BackupType", "Unknown")

                            if backup_type == "NotInstalled":
                                # Windows Server Backup feature not installed
                                status = "warning"
                            elif "Error" in backup_data and backup_data.get("Error") == "NoBackupConfigured":
                                # Feature installed but no backup policy configured
                                status = "fail"
                            elif "LastBackup" in backup_data:
                                # Check if backup is recent (within 24 hours)
                                backup_age = backup_data.get("BackupAgeHours", 999)
                                if backup_age <= 24:
                                    status = "pass"
                                elif backup_age <= 72:
                                    status = "warning"
                                else:
                                    status = "fail"
                            else:
                                status = "fail"
                        except Exception:
                            status = "fail" if check_result.status_code != 0 else status

                    elif check_name.startswith("service_") and check_result.status_code == 0:
                        # Check if critical Windows service is running
                        try:
                            import json as json_module
                            service_data = json_module.loads(output)
                            service_status = service_data.get("Status", 0)
                            # Status: 1=Stopped, 2=StartPending, 3=StopPending, 4=Running
                            if service_status == 4 or service_status == "Running":
                                status = "pass"
                            elif service_status in [0, 1, "Stopped"] or not service_data:
                                status = "fail"
                            else:
                                status = "warning"  # Starting/Stopping
                        except Exception:
                            # Service might not exist (e.g., DNS on non-DC)
                            if "Cannot find any service" in output or not output.strip():
                                status = "pass"  # Service not installed, not a failure
                            else:
                                status = "fail"

                    # Submit as evidence
                    evidence_data = {
                        "check_name": check_name,
                        "target": target.hostname,
                        "computer_name": computer_name,
                        "status": status,
                        "output": output[:1000],  # Truncate large outputs
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }

                    evidence_json = json.dumps(evidence_data, sort_keys=True)
                    bundle_hash = hashlib.sha256(evidence_json.encode()).hexdigest()

                    # Submit with deduplication (signing happens in client)
                    windows_check_type = f"windows_{check_name}"
                    if self._should_submit_evidence(windows_check_type, status):
                        await self.client.submit_evidence(
                            bundle_hash=bundle_hash,
                            check_type=windows_check_type,
                            check_result=status,
                            evidence_data=evidence_data,
                            host=target.hostname,
                            hipaa_control="164.312(b)",  # Audit controls
                            signer=self.signer
                        )

                        logger.debug(f"Windows check {check_name} on {target.hostname}: {status}")

                    # If check failed and healing is enabled, attempt remediation
                    # Note: AutoHealer respects dry_run mode internally
                    if status == "fail" and self.auto_healer:
                        try:
                            # Build raw_data with fields that L1 rules expect
                            raw_data = {
                                "check_type": check_name,  # L1 rules check this
                                "drift_detected": True,
                                "status": status,
                                "details": evidence_data,  # Nested evidence data
                                "host": target.hostname,
                                "output": output[:500],  # Include some output for context
                            }
                            logger.info(f"Windows check failed, attempting healing: {check_name} on {target.hostname}")
                            # AutoHealer.heal() creates the incident internally
                            heal_result = await self.auto_healer.heal(
                                site_id=self.config.site_id,
                                host_id=target.hostname,
                                incident_type=check_name,
                                severity="high",
                                raw_data=raw_data,
                            )
                            if heal_result.success:
                                logger.info(f"Healing {check_name}: {heal_result.resolution_level} → {heal_result.action_taken} (SUCCESS)")
                                # Report pattern to learning flywheel for L1/L2 promotions
                                if heal_result.action_taken:
                                    try:
                                        await self.client.report_pattern(
                                            check_type=check_name,
                                            issue_signature=f"{check_name}:{target.hostname}",
                                            resolution_steps=[heal_result.action_taken],
                                            success=True,
                                            execution_time_ms=heal_result.resolution_time_ms,
                                        )
                                        logger.debug(f"Reported pattern for {check_name} to learning loop")
                                    except Exception as e:
                                        logger.debug(f"Pattern report failed (non-critical): {e}")
                            else:
                                logger.warning(f"Healing failed: {heal_result.error}")
                        except Exception as heal_err:
                            logger.warning(f"Healing attempt failed for {check_name}: {heal_err}")

                except Exception as e:
                    logger.warning(f"Windows check {check_name} failed on {target.hostname}: {e}")

        except ImportError:
            logger.error("pywinrm not installed - Windows scanning disabled")
        except Exception as e:
            logger.error(f"Failed to scan Windows target {target.hostname}: {e}")

    # =========================================================================
    # Linux Scanning (SSH-based)
    # =========================================================================

    async def _maybe_scan_linux(self):
        """
        Scan Linux targets if enough time has passed.

        Uses LinuxDriftDetector with SSH via asyncssh for Linux/Unix servers.
        """
        now = datetime.now(timezone.utc)
        elapsed = (now - self._last_linux_scan).total_seconds()

        if elapsed < self._linux_scan_interval:
            return

        if not self.linux_targets:
            return

        if not self.linux_drift_detector:
            # Initialize detector if not already done
            self.linux_executor = LinuxExecutor(self.linux_targets)
            self.linux_drift_detector = LinuxDriftDetector(
                targets=self.linux_targets,
                executor=self.linux_executor
            )

        logger.info(f"Scanning {len(self.linux_targets)} Linux targets...")

        try:
            # Run drift detection on all targets
            drift_results = await self.linux_drift_detector.detect_all()

            # Submit evidence for each result
            for drift in drift_results:
                evidence_data = drift.to_dict()
                evidence_json = json.dumps(evidence_data, sort_keys=True)
                bundle_hash = hashlib.sha256(evidence_json.encode()).hexdigest()

                # Submit with deduplication (signing happens in client)
                linux_check_type = f"linux_{drift.check_type}"
                linux_result = "pass" if drift.compliant else "fail"
                if self._should_submit_evidence(linux_check_type, linux_result):
                    await self.client.submit_evidence(
                        bundle_hash=bundle_hash,
                        check_type=linux_check_type,
                        check_result=linux_result,
                        evidence_data=evidence_data,
                        host=drift.target,
                        hipaa_control=drift.hipaa_controls[0] if drift.hipaa_controls else "164.312(b)",
                        signer=self.signer
                    )

                    logger.debug(f"Linux {drift.runbook_id} on {drift.target}: {linux_result}")

                # If drift detected and L1-eligible, attempt auto-remediation
                if not drift.compliant and drift.l1_eligible and self.auto_healer:
                    try:
                        raw_data = {
                            "check_type": drift.check_type,
                            "drift_detected": True,
                            "status": "fail",
                            "details": evidence_data,
                            "host": drift.target,
                            "runbook_id": drift.runbook_id,
                            "distro": drift.distro,
                        }
                        logger.info(f"Linux drift detected, attempting healing: {drift.runbook_id} on {drift.target}")
                        heal_result = await self.auto_healer.heal(
                            site_id=self.config.site_id,
                            host_id=drift.target,
                            incident_type=drift.check_type,
                            severity=drift.severity,
                            raw_data=raw_data,
                        )
                        if heal_result.success:
                            logger.info(f"Linux healing {drift.check_type}: {heal_result.resolution_level} → {heal_result.action_taken} (SUCCESS)")
                            # Report pattern to learning flywheel for L1/L2 promotions
                            if heal_result.action_taken:
                                try:
                                    await self.client.report_pattern(
                                        check_type=drift.check_type,
                                        issue_signature=f"{drift.check_type}:{drift.target}",
                                        resolution_steps=[heal_result.action_taken],
                                        success=True,
                                        execution_time_ms=heal_result.resolution_time_ms,
                                    )
                                    logger.debug(f"Reported pattern for {drift.check_type} to learning loop")
                                except Exception as e:
                                    logger.debug(f"Pattern report failed (non-critical): {e}")
                        else:
                            logger.warning(f"Linux healing failed: {heal_result.error}")
                    except Exception as heal_err:
                        logger.warning(f"Linux healing attempt failed for {drift.runbook_id}: {heal_err}")

            # Log summary
            compliant_count = sum(1 for d in drift_results if d.compliant)
            drift_count = sum(1 for d in drift_results if not d.compliant)
            logger.info(f"Linux scan complete: {compliant_count} compliant, {drift_count} drifted")

        except Exception as e:
            logger.error(f"Linux drift detection failed: {e}")

        self._last_linux_scan = now

    # =========================================================================
    # Network Posture Scanning (Linux + Windows)
    # =========================================================================

    async def _maybe_scan_network_posture(self):
        """
        Scan network posture if enough time has passed.

        Checks listening ports, prohibited services, external bindings,
        and DNS resolvers on both Linux and Windows targets.
        """
        now = datetime.now(timezone.utc)
        elapsed = (now - self._last_network_posture_scan).total_seconds()

        if elapsed < self._network_posture_interval:
            return

        # Need at least one target type
        if not self.linux_targets and not self.windows_targets:
            return

        # Initialize detector if needed
        if not self.network_posture_detector:
            self.network_posture_detector = NetworkPostureDetector()

        logger.info("Starting network posture scan...")

        results = []

        # Scan Linux targets
        if self.linux_targets and self.linux_executor:
            for target in self.linux_targets:
                try:
                    result = await self.network_posture_detector.detect_linux(
                        self.linux_executor, target
                    )
                    results.append(result)
                except Exception as e:
                    logger.error(f"Network posture scan failed for Linux {target.hostname}: {e}")

        # Scan Windows targets
        if self.windows_targets:
            # Initialize Windows executor if needed
            if not self.windows_executor:
                self.windows_executor = WindowsExecutor(self.windows_targets)

            for target in self.windows_targets:
                try:
                    result = await self.network_posture_detector.detect_windows(
                        self.windows_executor, target
                    )
                    results.append(result)
                except Exception as e:
                    logger.error(f"Network posture scan failed for Windows {target.hostname}: {e}")

        # Submit evidence for each result
        for result in results:
            try:
                evidence_data = result.to_dict()
                evidence_json = json.dumps(evidence_data, sort_keys=True)
                bundle_hash = hashlib.sha256(evidence_json.encode()).hexdigest()

                # Submit with deduplication (signing happens in client)
                network_result = "pass" if result.compliant else "fail"
                if self._should_submit_evidence("network", network_result):
                    await self.client.submit_evidence(
                        bundle_hash=bundle_hash,
                        check_type="network",
                        check_result=network_result,
                        evidence_data=evidence_data,
                        host=result.target,
                        hipaa_control=result.hipaa_controls[0] if result.hipaa_controls else "164.312(e)(1)",
                        signer=self.signer
                    )

                status = "compliant" if result.compliant else f"drifted ({len(result.drift_items)} issues)"
                logger.debug(f"Network posture {result.target}: {status}")

                # If non-compliant and healing enabled, attempt remediation for prohibited ports
                if not result.compliant and result.prohibited_ports and self.auto_healer:
                    for prohibited in result.prohibited_ports:
                        try:
                            raw_data = {
                                "check_type": "network",
                                "drift_detected": True,
                                "status": "fail",
                                "details": {
                                    "port": prohibited.get("port"),
                                    "process": prohibited.get("process"),
                                    "description": prohibited.get("description"),
                                },
                                "host": result.target,
                                "os_type": result.os_type,
                            }
                            logger.info(f"Prohibited port detected, attempting healing: port {prohibited.get('port')} on {result.target}")
                            heal_result = await self.auto_healer.heal(
                                site_id=self.config.site_id,
                                host_id=result.target,
                                incident_type="prohibited_port",
                                severity="high",
                                raw_data=raw_data,
                            )
                            if heal_result.success:
                                logger.info(f"Network healing prohibited_port: {heal_result.resolution_level} → {heal_result.action_taken} (SUCCESS)")
                                # Report pattern to learning flywheel for L1/L2 promotions
                                if heal_result.action_taken:
                                    try:
                                        await self.client.report_pattern(
                                            check_type="prohibited_port",
                                            issue_signature=f"prohibited_port:{result.target}:{prohibited.get('port')}",
                                            resolution_steps=[heal_result.action_taken],
                                            success=True,
                                            execution_time_ms=heal_result.resolution_time_ms,
                                        )
                                        logger.debug(f"Reported pattern for prohibited_port to learning loop")
                                    except Exception as e:
                                        logger.debug(f"Pattern report failed (non-critical): {e}")
                            else:
                                logger.warning(f"Network posture healing failed: {heal_result.error}")
                        except Exception as heal_err:
                            logger.warning(f"Network posture healing attempt failed: {heal_err}")

            except Exception as e:
                logger.error(f"Failed to submit network posture evidence for {result.target}: {e}")

        # Log summary
        compliant_count = sum(1 for r in results if r.compliant)
        drift_count = sum(1 for r in results if not r.compliant)
        logger.info(f"Network posture scan complete: {compliant_count} compliant, {drift_count} with issues")

        self._last_network_posture_scan = now

    async def _discover_domain_on_boot(self):
        """
        Run domain discovery on first boot.
        Reports discovered domain to Central Command for credential prompt.
        """
        if self._domain_discovery_complete:
            return  # Already discovered
        
        logger.info("Running first-boot domain discovery...")
        try:
            self.discovered_domain = await self.domain_discovery.discover()
            
            if self.discovered_domain:
                # Report to Central Command
                await self._report_discovered_domain(self.discovered_domain)
                self._domain_discovery_complete = True
                logger.info(f"Domain discovery complete: {self.discovered_domain.domain_name}")
            else:
                logger.warning("No AD domain discovered - manual configuration required")
                self._domain_discovery_complete = True  # Mark complete even if failed
        except Exception as e:
            logger.error(f"Domain discovery error: {e}")
            self._domain_discovery_complete = True  # Mark complete to avoid retry loops

    async def _report_discovered_domain(self, domain: DiscoveredDomain):
        """Report discovered domain to Central Command for credential provisioning."""
        try:
            appliance_id = f"{self.config.site_id}-{get_mac_address()}"
            result = await self.client.report_discovered_domain(
                appliance_id=appliance_id,
                discovered_domain=domain.to_dict(),
                awaiting_credentials=True,
            )
            if result:
                logger.info(f"Reported discovered domain: {domain.domain_name}")
            else:
                logger.warning(f"Failed to report domain discovery")
        except Exception as e:
            logger.error(f"Error reporting domain: {e}")

    async def _maybe_scan_workstations(self):
        """
        Scan workstations if enough time has passed.

        Two-phase process:
        1. Discovery: Enumerate workstations from AD (hourly)
        2. Compliance: Run 5 checks on online workstations (every 10 min)

        Checks: BitLocker, Defender, Patches, Firewall, Screen Lock
        """
        now = datetime.now(timezone.utc)

        # Phase 1: Discovery (hourly)
        discovery_elapsed = (now - self._last_workstation_discovery).total_seconds()
        if discovery_elapsed >= self._workstation_discovery_interval:
            await self._discover_workstations()
            self._last_workstation_discovery = now

        # Phase 2: Compliance checks (every 10 min)
        scan_elapsed = (now - self._last_workstation_scan).total_seconds()
        if scan_elapsed < self._workstation_scan_interval:
            return

        if not self.workstations:
            return

        logger.info(f"Starting workstation compliance scan ({len(self.workstations)} workstations)...")

        # Initialize checker if needed
        if not self.workstation_checker:
            if not self.windows_executor:
                self.windows_executor = WindowsExecutor([])
            self.workstation_checker = WorkstationComplianceChecker(
                executor=self.windows_executor,
            )

        # Get credentials for workstation access
        workstation_creds = self._get_workstation_credentials()

        # Run checks on online workstations
        online_workstations = [ws for ws in self.workstations if ws.online]
        if not online_workstations:
            logger.info("No online workstations to scan")
            self._last_workstation_scan = now
            return

        results: List[WorkstationComplianceResult] = []
        for ws in online_workstations:
            try:
                target = ws.ip_address or ws.hostname
                result = await self.workstation_checker.run_all_checks(
                    target=target,
                    ip_address=ws.ip_address,
                    credentials=workstation_creds,
                )
                results.append(result)

                # Update workstation compliance status
                ws.compliance_status = result.overall_status.value
                ws.last_compliance_check = now

                status = "compliant" if result.overall_status.value == "compliant" else "drifted"
                logger.debug(f"Workstation {ws.hostname}: {status}")

            except Exception as e:
                logger.error(f"Workstation compliance check failed for {ws.hostname}: {e}")
                ws.compliance_status = "error"
                ws.last_compliance_check = now

        # Generate and submit evidence
        if results:
            try:
                evidence = create_workstation_evidence(
                    site_id=self.config.site_id,
                    compliance_results=results,
                    total_discovered=len(self.workstations),
                    online_count=len(online_workstations),
                )

                # Submit site summary
                summary = evidence.get("site_summary", {})
                summary_json = json.dumps(summary, sort_keys=True)
                summary_hash = hashlib.sha256(summary_json.encode()).hexdigest()

                # Submit with deduplication (signing happens in client)
                compliance_rate = summary.get("overall_compliance_rate", 0)
                check_result = "pass" if compliance_rate >= 80 else "fail"

                if self._should_submit_evidence("workstation", check_result):
                    await self.client.submit_evidence(
                        bundle_hash=summary_hash,
                        check_type="workstation",
                        check_result=check_result,
                        evidence_data=summary,
                        host=f"site:{self.config.site_id}",
                        hipaa_control="164.312(a)(2)(iv)",  # Encryption/decryption
                        signer=self.signer,
                    )

                # Log summary
                compliant = summary.get("compliant_workstations", 0)
                total = summary.get("online_workstations", 0)
                logger.info(
                    f"Workstation scan complete: {compliant}/{total} compliant "
                    f"({compliance_rate:.1f}%)"
                )

            except Exception as e:
                logger.error(f"Failed to generate workstation evidence: {e}")

        self._last_workstation_scan = now

    async def _discover_workstations(self):
        """Discover workstations from Active Directory."""
        if not self._domain_controller:
            return

        logger.info(f"Discovering workstations from AD via {self._domain_controller}...")

        try:
            # Get DC credentials from Windows targets
            dc_creds = self._get_dc_credentials()
            if not dc_creds:
                logger.warning("No DC credentials available for workstation discovery")
                return

            # Initialize Windows executor if needed
            if not self.windows_executor:
                self.windows_executor = WindowsExecutor([])

            # Initialize discovery
            if not self.workstation_discovery:
                self.workstation_discovery = WorkstationDiscovery(
                    executor=self.windows_executor,
                    domain_controller=self._domain_controller,
                    credentials=dc_creds,
                )

            # Discover and check online status
            workstations = await self.workstation_discovery.discover_and_check()
            self.workstations = workstations

            online = sum(1 for ws in workstations if ws.online)
            logger.info(f"Discovered {len(workstations)} workstations, {online} online")

        except Exception as e:
            logger.error(f"Workstation discovery failed: {e}")

    def _get_dc_credentials(self) -> Dict[str, str]:
        """Get domain controller credentials from config or Windows targets."""
        # Primary: use dedicated DC credentials from config
        dc_username = getattr(self.config, 'dc_username', None)
        dc_password = getattr(self.config, 'dc_password', None)
        if dc_username and dc_password:
            return {
                "username": dc_username,
                "password": dc_password,
            }

        # Fallback 1: Look for a Windows target that matches the DC
        for target in self.windows_targets:
            if self._domain_controller and (
                target.hostname == self._domain_controller or
                target.hostname.split('.')[0].upper() == self._domain_controller.split('.')[0].upper()
            ):
                return {
                    "username": target.username,
                    "password": target.password,
                }

        # Fallback 2: use first Windows target credentials
        if self.windows_targets:
            target = self.windows_targets[0]
            return {
                "username": target.username,
                "password": target.password,
            }

        return {}

    def _get_workstation_credentials(self) -> Dict[str, str]:
        """Get credentials for workstation access."""
        # Same as DC creds for now (domain admin can access workstations)
        return self._get_dc_credentials()
    
    async def _enumerate_ad_targets(self):
        """
        Enumerate servers and workstations from AD.
        
        Called when:
        1. trigger_enumeration flag is set (after credential submission)
        2. Periodically (hourly) to catch new machines
        """
        if not self.discovered_domain:
            logger.warning("Cannot enumerate AD: no domain discovered")
            return
        
        # Get domain credentials from Central Command
        creds = await self._get_domain_credentials()
        if not creds:
            logger.warning("No domain credentials available for enumeration")
            return
        
        logger.info(f"Starting AD enumeration for {self.discovered_domain.domain_name}")
        
        # Initialize Windows executor if needed
        if not self.windows_executor:
            self.windows_executor = WindowsExecutor([])
        
        # Create enumerator
        enumerator = ADEnumerator(
            domain_controller=self.discovered_domain.domain_controllers[0],
            username=creds['username'],
            password=creds['password'],
            domain=self.discovered_domain.domain_name,
            executor=self.windows_executor,
        )
        
        # Enumerate all computers
        servers, workstations = await enumerator.enumerate_all()
        
        # Test connectivity to each (with concurrency limit)
        result = EnumerationResult()
        result.servers = servers
        result.workstations = workstations
        result.enumeration_time = datetime.now(timezone.utc)
        
        # Test server connectivity (limit 5 concurrent)
        semaphore = asyncio.Semaphore(5)
        
        async def test_with_limit(computer):
            async with semaphore:
                return computer, await enumerator.test_connectivity(computer)
        
        # Test servers
        if servers:
            logger.info(f"Testing connectivity to {len(servers)} servers...")
            server_tests = await asyncio.gather(*[test_with_limit(s) for s in servers], return_exceptions=True)
            for test_result in server_tests:
                if isinstance(test_result, Exception):
                    continue
                computer, reachable = test_result
                if reachable:
                    result.reachable_servers.append(computer)
                else:
                    result.unreachable.append(computer)
        
        # Test workstations (only if Go agent deployment planned)
        # For now, just mark enabled ones as discovered
        result.reachable_workstations = [w for w in workstations if w.enabled]
        
        # Report results to Central Command
        await self._report_enumeration_results(result)
        
        # Update local target lists
        # Servers become Windows targets for compliance scanning
        new_windows_targets = []
        for s in result.reachable_servers:
            new_windows_targets.append({
                "hostname": s.fqdn or s.ip_address or s.hostname,
                "ip_address": s.ip_address or "",  # Store IP for target matching
                "username": creds['username'],
                "password": creds['password'],
            })
        
        # Merge with existing targets (don't overwrite manually configured ones)
        existing_hostnames = {t.hostname for t in self.windows_targets}
        for target_dict in new_windows_targets:
            if target_dict['hostname'] not in existing_hostnames:
                try:
                    target = WindowsTarget(
                        hostname=target_dict['hostname'],
                        username=target_dict['username'],
                        password=target_dict['password'],
                        use_ssl=False,
                        port=5985,
                        transport='ntlm',
                        ip_address=target_dict.get('ip_address', ''),
                    )
                    self.windows_targets.append(target)
                except Exception as e:
                    logger.warning(f"Failed to add enumerated target: {e}")
        
        # Store workstation targets for Go agent deployment
        self.workstation_targets = [
            {
                "hostname": w.fqdn or w.ip_address or w.hostname,
                "os": w.os_name,
            }
            for w in result.reachable_workstations
        ]
        
        logger.info(f"Enumeration complete: {len(result.reachable_servers)} servers, "
                    f"{len(result.reachable_workstations)} workstations")
        self._last_enumeration = datetime.now(timezone.utc)
    
    async def _get_domain_credentials(self) -> Optional[Dict]:
        """Fetch domain credentials from Central Command."""
        try:
            status, response = await self.client._request(
                'GET',
                f'/api/sites/{self.config.site_id}/domain-credentials',
            )
            if status == 200 and isinstance(response, dict):
                return response
        except Exception as e:
            logger.error(f"Failed to fetch domain credentials: {e}")
        return None
    
    async def _report_enumeration_results(self, result: EnumerationResult):
        """Report enumeration results to Central Command."""
        try:
            appliance_id = f"{self.config.site_id}-{get_mac_address()}"
            status, response = await self.client._request(
                'POST',
                '/api/appliances/enumeration-results',
                json_data={
                    "site_id": self.config.site_id,
                    "appliance_id": appliance_id,
                    "results": result.to_dict(),
                }
            )
            if status != 200:
                logger.error(f"Failed to report enumeration: {status}")
        except Exception as e:
            logger.error(f"Error reporting enumeration: {e}")
    
    async def _maybe_deploy_go_agents(self):
        """
        Deploy Go agents to workstations that don't have them.
        
        Runs after enumeration discovers new workstations.
        Only deploys to workstations that don't already have agents running.
        """
        if not self.workstation_targets:
            return
        
        # Get domain credentials
        creds = await self._get_domain_credentials()
        if not creds:
            logger.warning("No domain credentials available for Go agent deployment")
            return
        
        # Get appliance IP for gRPC address
        appliance_ips = get_ip_addresses()
        if not appliance_ips:
            logger.warning("Cannot determine appliance IP for Go agent config")
            return
        
        appliance_addr = f"{appliance_ips[0]}:{self._grpc_port}"
        
        # Initialize Windows executor if needed
        if not self.windows_executor:
            self.windows_executor = WindowsExecutor([])
        
        # Create deployer
        deployer = GoAgentDeployer(
            domain=self.discovered_domain.domain_name,
            username=creds['username'],
            password=creds['password'],
            appliance_addr=appliance_addr,
            executor=self.windows_executor,
        )
        
        # Check which workstations need agents
        workstations_needing_agent = []
        
        for ws in self.workstation_targets:
            hostname = ws.get('hostname')
            if not hostname:
                continue
            
            try:
                status = await deployer.check_agent_status(hostname)
                if not status.get('installed') or status.get('status') != 'Running':
                    workstations_needing_agent.append(ws)
            except Exception as e:
                logger.debug(f"Failed to check agent status on {hostname}: {e}")
                # Assume needs deployment if check fails
                workstations_needing_agent.append(ws)
        
        if workstations_needing_agent:
            logger.info(f"Deploying Go agents to {len(workstations_needing_agent)} workstations")
            results = await deployer.deploy_to_workstations(workstations_needing_agent)
            
            # Report deployment results
            await self._report_deployment_results(results)
        else:
            logger.debug("All workstations already have Go agents deployed")
    
    async def _report_deployment_results(self, results: List[DeploymentResult]):
        """Report Go agent deployment results to Central Command."""
        try:
            appliance_id = f"{self.config.site_id}-{get_mac_address()}"
            
            # Convert results to dict format
            deployment_data = [
                {
                    "hostname": r.hostname,
                    "success": r.success,
                    "method": r.method,
                    "error": r.error,
                    "agent_version": r.agent_version,
                    "deployed_at": r.deployed_at.isoformat() if r.deployed_at else None,
                }
                for r in results
            ]
            
            status, response = await self.client._request(
                'POST',
                '/api/appliances/agent-deployments',
                json_data={
                    "site_id": self.config.site_id,
                    "appliance_id": appliance_id,
                    "deployments": deployment_data,
                }
            )
            
            if status != 200:
                logger.error(f"Failed to report deployment results: {status}")
            else:
                successful = sum(1 for r in results if r.success)
                logger.info(f"Reported deployment results: {successful}/{len(results)} successful")
        except Exception as e:
            logger.error(f"Error reporting deployment results: {e}")

    # =========================================================================
    # Learning System - L2->L1 Promotion
    # =========================================================================

    async def _maybe_check_promotions(self):
        """Check for L2->L1 promotion candidates periodically."""
        now = datetime.now(timezone.utc)
        elapsed = (now - self._last_promotion_check).total_seconds()

        if elapsed < self._promotion_check_interval:
            return

        self._last_promotion_check = now
        logger.info("Running scheduled promotion check...")

        try:
            # Run the promotion check with notification callback
            report = self.learning_system.run_promotion_check(
                notify_callback=self._notify_promotion_candidates
            )

            # Report to Central Command if there are candidates
            if report["candidates_found"] > 0 or report.get("monitoring_report", {}).get("rollbacks_triggered"):
                await self._report_promotions_to_central(report)

        except Exception as e:
            logger.error(f"Promotion check failed: {e}")

    def _notify_promotion_candidates(self, report: Dict[str, Any]):
        """
        Callback for promotion notifications.

        Logs the notification and prepares it for Central Command.
        Email/webhook notifications are handled by Central Command.
        """
        if report["candidates_pending"] > 0:
            logger.info(
                f"📋 {report['candidates_pending']} patterns ready for promotion review. "
                f"Check Central Command dashboard."
            )

        if report["candidates_promoted"] > 0:
            logger.info(
                f"✅ Auto-promoted {report['candidates_promoted']} patterns to L1 rules."
            )

        rollbacks = report.get("monitoring_report", {}).get("rollbacks_triggered", [])
        if rollbacks:
            logger.warning(
                f"⚠️ Rolled back {len(rollbacks)} underperforming rules."
            )

    async def _report_promotions_to_central(self, report: Dict[str, Any]):
        """Report promotion status to Central Command."""
        try:
            # Build appliance identifier
            mac = get_mac_address()
            appliance_id = f"{self.config.site_id}-{mac}"

            # Prepare payload for Central Command
            payload = {
                "appliance_id": appliance_id,
                "site_id": self.config.site_id,
                "checked_at": report["checked_at"],
                "candidates_found": report["candidates_found"],
                "candidates_promoted": report["candidates_promoted"],
                "candidates_pending": report["candidates_pending"],
                "pending_candidates": report.get("pending_candidates", []),
                "promoted_rules": report.get("promoted_rules", []),
                "rollbacks": report.get("monitoring_report", {}).get("rollbacks_triggered", []),
                "errors": report.get("errors", [])
            }

            # POST to Central Command
            response = await self.client.post(
                "/api/learning/promotion-report",
                json=payload
            )

            if response and response.get("status") == "ok":
                logger.debug("Reported promotion status to Central Command")
            else:
                logger.warning(f"Failed to report promotion status: {response}")

        except Exception as e:
            logger.warning(f"Failed to report promotions to Central Command: {e}")

    def _get_appliance_id(self) -> str:
        """Get unique appliance identifier (site_id-mac)."""
        mac = get_mac_address()
        return f"{self.config.site_id}-{mac}"

    async def _maybe_sync_learning(self):
        """
        Sync learning system data with Central Command periodically.

        Syncs pattern_stats to server and fetches approved promoted rules.
        Default interval: 4 hours.
        """
        now = datetime.now(timezone.utc)
        elapsed = (now - self._last_learning_sync).total_seconds()

        if elapsed < self._learning_sync_interval:
            return

        self._last_learning_sync = now
        logger.info("Running scheduled learning system sync...")

        try:
            report = await self.learning_sync.sync()

            if report.get("patterns_synced"):
                logger.info(f"Pattern stats synced: {report.get('patterns_count', 0)} patterns")

            if report.get("rules_fetched") and report.get("rules_count", 0) > 0:
                logger.info(f"Fetched {report['rules_count']} promoted rules from server")
                # Reload L1 engine to pick up new rules
                if self.auto_healer and self.auto_healer.level1:
                    try:
                        self.auto_healer.level1.reload_rules()
                        logger.info("L1 rules reloaded after promoted rules sync")
                    except Exception as e:
                        logger.warning(f"Failed to reload L1 rules: {e}")

            if report.get("offline_queue_items", 0) > 0:
                logger.info(f"Processed {report['offline_queue_items']} items from offline queue")

            if report.get("errors"):
                for err in report["errors"]:
                    logger.warning(f"Learning sync error: {err}")

        except Exception as e:
            logger.error(f"Learning system sync failed: {e}")

    async def _maybe_prune_database(self):
        """
        Prune old incidents from database periodically to prevent disk space issues.

        Default interval: 24 hours
        Default retention: 30 days for resolved incidents
        """
        now = datetime.now(timezone.utc)
        elapsed = (now - self._last_prune_time).total_seconds()

        if elapsed < self._prune_interval:
            return

        self._last_prune_time = now

        if not self.incident_db:
            return

        try:
            # Get database stats before pruning
            stats_before = self.incident_db.get_database_stats()
            logger.info(
                f"Database maintenance: {stats_before.get('incidents_count', 0)} incidents, "
                f"{stats_before.get('file_size_mb', 0)}MB"
            )

            # Prune old resolved incidents (keep unresolved forever)
            result = self.incident_db.prune_old_incidents(
                retention_days=self._incident_retention_days,
                keep_unresolved=True
            )

            if result["incidents_deleted"] > 0:
                # Get stats after pruning
                stats_after = self.incident_db.get_database_stats()
                space_saved = stats_before.get('file_size_mb', 0) - stats_after.get('file_size_mb', 0)

                logger.info(
                    f"Database pruned: {result['incidents_deleted']} incidents deleted, "
                    f"{space_saved:.1f}MB reclaimed, {stats_after.get('incidents_count', 0)} remaining"
                )
            else:
                logger.debug("Database pruning: no old incidents to delete")

        except Exception as e:
            logger.error(f"Database pruning failed: {e}")

    # =========================================================================
    # Order Processing (remote commands and updates)
    # =========================================================================

    async def _process_pending_orders(self):
        """Fetch and process pending orders from Central Command."""
        try:
            # Build appliance ID (site_id-MAC)
            mac = get_mac_address()
            appliance_id = f"{self.config.site_id}-{mac}"

            orders = await self.client.fetch_pending_orders(appliance_id)

            for order in orders:
                order_id = order.get('order_id')
                order_type = order.get('order_type')

                if not order_id or not order_type:
                    continue

                logger.info(f"Processing order {order_id}: {order_type}")

                # Acknowledge order
                await self.client.acknowledge_order(order_id)

                # Execute order
                try:
                    result = await self._execute_order(order)
                    await self.client.complete_order(
                        order_id=order_id,
                        success=True,
                        result=result
                    )
                    logger.info(f"Order {order_id} completed successfully")
                except Exception as e:
                    logger.error(f"Order {order_id} failed: {e}")
                    await self.client.complete_order(
                        order_id=order_id,
                        success=False,
                        error_message=str(e)
                    )

        except Exception as e:
            logger.debug(f"Order processing skipped: {e}")

    async def _execute_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        """Execute an order based on its type."""
        order_type = order.get('order_type')
        parameters = order.get('parameters', {})

        handlers = {
            'force_checkin': self._handle_force_checkin,
            'run_drift': self._handle_run_drift,
            'sync_rules': self._handle_sync_rules,
            'restart_agent': self._handle_restart_agent,
            'update_agent': self._handle_update_agent,
            'update_iso': self._handle_update_iso,
            'view_logs': self._handle_view_logs,
            'deploy_sensor': self._handle_deploy_sensor,
            'remove_sensor': self._handle_remove_sensor,
            'deploy_linux_sensor': self._handle_deploy_linux_sensor,
            'remove_linux_sensor': self._handle_remove_linux_sensor,
            'sensor_status': self._handle_sensor_status,
            'sync_promoted_rule': self._handle_sync_promoted_rule,
        }

        handler = handlers.get(order_type)
        if handler:
            return await handler(parameters)
        else:
            raise ValueError(f"Unknown order type: {order_type}")

    async def _handle_force_checkin(self, params: Dict) -> Dict:
        """Force an immediate checkin."""
        await self.client.checkin(
            hostname=get_hostname(),
            mac_address=get_mac_address(),
            ip_addresses=get_ip_addresses(),
            uptime_seconds=get_uptime_seconds(),
            agent_version=VERSION,
            nixos_version=get_nixos_version(),
        )
        return {"status": "checkin_complete"}

    async def _handle_run_drift(self, params: Dict) -> Dict:
        """Run drift detection immediately."""
        if self.drift_checker:
            results = await self.drift_checker.run_all_checks()
            return {"drift_results": results}
        return {"error": "drift_checker not initialized"}

    async def _handle_sync_rules(self, params: Dict) -> Dict:
        """Force L1 rules sync."""
        rules = await self.client.sync_rules()
        if rules is not None:
            rules_file = self.config.rules_dir / "l1_rules.json"
            with open(rules_file, 'w') as f:
                json.dump(rules, f, indent=2)
            return {"rules_synced": len(rules)}
        return {"error": "rules_sync_failed"}

    async def _handle_restart_agent(self, params: Dict) -> Dict:
        """Schedule agent restart."""
        # Return success, then restart after a delay
        logger.info("Agent restart requested, restarting in 5 seconds...")
        asyncio.get_event_loop().call_later(5, self._do_restart)
        return {"status": "restart_scheduled"}

    def _do_restart(self):
        """Execute agent restart via systemctl."""
        import os
        os.system("systemctl restart compliance-agent")

    async def _handle_update_agent(self, params: Dict) -> Dict:
        """
        Download and apply agent update.

        Parameters:
            package_url: URL to download agent package tarball
            version: Expected version string
        """
        package_url = params.get('package_url')
        version = params.get('version', 'unknown')

        if not package_url:
            raise ValueError("package_url is required for update_agent")

        logger.info(f"Updating agent to version {version} from {package_url}")

        # Download package
        update_dir = Path("/var/lib/msp/agent-updates")
        update_dir.mkdir(parents=True, exist_ok=True)

        tarball_path = update_dir / f"agent-{version}.tar.gz"
        if not await self.client.download_file(package_url, str(tarball_path)):
            raise RuntimeError("Failed to download update package")

        # Extract to overlay directory
        overlay_dir = Path("/var/lib/msp/agent-overlay")
        overlay_dir.mkdir(parents=True, exist_ok=True)

        # Clear old overlay
        import shutil
        if overlay_dir.exists():
            shutil.rmtree(overlay_dir)
        overlay_dir.mkdir(parents=True, exist_ok=True)

        # Extract tarball
        import tarfile
        with tarfile.open(tarball_path, 'r:gz') as tar:
            tar.extractall(overlay_dir)

        logger.info(f"Agent update extracted to {overlay_dir}")

        # Create/update systemd drop-in to set PYTHONPATH
        dropin_dir = Path("/etc/systemd/system/compliance-agent.service.d")
        try:
            dropin_dir.mkdir(parents=True, exist_ok=True)
            dropin_file = dropin_dir / "overlay.conf"
            dropin_file.write_text(f'''[Service]
Environment="PYTHONPATH={overlay_dir}"
''')
            logger.info("Created systemd PYTHONPATH override")
        except PermissionError:
            # NixOS read-only filesystem - use alternative approach
            logger.warning("Cannot write systemd drop-in (read-only fs), update will apply on next ISO deploy")
            return {
                "status": "update_downloaded",
                "version": version,
                "note": "Filesystem read-only, manual restart with PYTHONPATH required"
            }

        # Reload systemd and schedule restart
        import os
        os.system("systemctl daemon-reload")
        asyncio.get_event_loop().call_later(3, self._do_restart)

        return {
            "status": "update_applied",
            "version": version,
            "restart_scheduled": True
        }

    async def _handle_view_logs(self, params: Dict) -> Dict:
        """Return recent agent logs."""
        lines = params.get('lines', 50)
        code, stdout, stderr = await run_command(
            f"journalctl -u compliance-agent --no-pager -n {lines}",
            timeout=10
        )
        return {
            "logs": stdout if code == 0 else stderr,
            "lines": lines
        }

    async def _handle_deploy_sensor(self, params: Dict) -> Dict:
        """
        Deploy Windows sensor to a target host.

        Parameters:
            hostname: Target Windows hostname
        """
        hostname = params.get('hostname')
        if not hostname:
            return {"error": "hostname is required"}

        # Find matching target with credentials
        target = None
        for t in self.windows_targets:
            if (t.hostname.lower() == hostname.lower() or
                hostname.lower() in t.hostname.lower()):
                target = t
                break

        if not target:
            return {"error": f"No credentials found for {hostname}"}

        # Import deployment script
        try:
            from pathlib import Path
            import sys

            # Add windows directory to path
            windows_dir = Path(__file__).parent.parent.parent / "windows"
            if str(windows_dir) not in sys.path:
                sys.path.insert(0, str(windows_dir))

            from deploy_sensor import deploy_sensor

            # Get appliance IP for sensor to report to
            appliance_ip = get_ip_addresses()[0] if get_ip_addresses() else "127.0.0.1"

            success = deploy_sensor(
                target_host=target.hostname,
                target_port=target.port,
                username=target.username,
                password=target.password,
                appliance_ip=appliance_ip,
                appliance_port=self._sensor_port,
                use_ssl=target.use_ssl,
            )

            if success:
                logger.info(f"Sensor deployed to {hostname}")
                return {"status": "deployed", "hostname": hostname}
            else:
                return {"error": f"Deployment failed for {hostname}"}

        except ImportError as e:
            return {"error": f"Deployment module not available: {e}"}
        except Exception as e:
            logger.error(f"Sensor deployment failed: {e}")
            return {"error": str(e)}

    async def _handle_remove_sensor(self, params: Dict) -> Dict:
        """
        Remove Windows sensor from a target host.

        Parameters:
            hostname: Target Windows hostname
        """
        hostname = params.get('hostname')
        if not hostname:
            return {"error": "hostname is required"}

        # Find matching target with credentials
        target = None
        for t in self.windows_targets:
            if (t.hostname.lower() == hostname.lower() or
                hostname.lower() in t.hostname.lower()):
                target = t
                break

        if not target:
            return {"error": f"No credentials found for {hostname}"}

        try:
            from pathlib import Path
            import sys

            windows_dir = Path(__file__).parent.parent.parent / "windows"
            if str(windows_dir) not in sys.path:
                sys.path.insert(0, str(windows_dir))

            from deploy_sensor import remove_sensor

            success = remove_sensor(
                target_host=target.hostname,
                target_port=target.port,
                username=target.username,
                password=target.password,
                use_ssl=target.use_ssl,
            )

            if success:
                logger.info(f"Sensor removed from {hostname}")
                # Clear from sensor registry
                hostname_lower = hostname.lower()
                for key in list(sensor_registry.keys()):
                    if key.lower() == hostname_lower:
                        del sensor_registry[key]
                return {"status": "removed", "hostname": hostname}
            else:
                return {"error": f"Removal failed for {hostname}"}

        except ImportError as e:
            return {"error": f"Deployment module not available: {e}"}
        except Exception as e:
            logger.error(f"Sensor removal failed: {e}")
            return {"error": str(e)}

    async def _handle_deploy_linux_sensor(self, params: Dict) -> Dict:
        """
        Deploy Linux sensor to a target host via SSH.

        Parameters:
            hostname: Target Linux hostname
        """
        hostname = params.get('hostname')
        if not hostname:
            return {"error": "hostname is required"}

        # Find matching target with credentials
        target = None
        for t in self.linux_targets:
            if (t.hostname.lower() == hostname.lower() or
                hostname.lower() in t.hostname.lower()):
                target = t
                break

        if not target:
            return {"error": f"No SSH credentials found for {hostname}"}

        try:
            # Get appliance IP for sensor to report to
            appliance_ip = get_ip_addresses()[0] if get_ip_addresses() else "127.0.0.1"
            appliance_url = f"https://{appliance_ip}:{self._sensor_port}"

            # Generate sensor credentials
            from .sensor_linux import generate_sensor_credentials
            sensor_id, api_key = generate_sensor_credentials()

            # Build install command
            install_cmd = (
                f"curl -sSL --insecure {appliance_url}/sensor/install.sh | "
                f"bash -s -- --sensor-id {sensor_id} --api-key {api_key} "
                f"--appliance-url {appliance_url}"
            )

            # Execute via SSH
            result = await self.linux_executor.run_command(target, install_cmd)

            if result.exit_code == 0:
                logger.info(f"Linux sensor deployed to {hostname} (sensor_id: {sensor_id})")
                return {
                    "status": "deployed",
                    "hostname": hostname,
                    "sensor_id": sensor_id
                }
            else:
                return {
                    "error": f"Deployment failed: {result.stderr}",
                    "stdout": result.stdout
                }

        except Exception as e:
            logger.error(f"Linux sensor deployment failed: {e}")
            return {"error": str(e)}

    async def _handle_remove_linux_sensor(self, params: Dict) -> Dict:
        """
        Remove Linux sensor from a target host via SSH.

        Parameters:
            hostname: Target Linux hostname
        """
        hostname = params.get('hostname')
        if not hostname:
            return {"error": "hostname is required"}

        # Find matching target with credentials
        target = None
        for t in self.linux_targets:
            if (t.hostname.lower() == hostname.lower() or
                hostname.lower() in t.hostname.lower()):
                target = t
                break

        if not target:
            return {"error": f"No SSH credentials found for {hostname}"}

        try:
            # Get appliance URL
            appliance_ip = get_ip_addresses()[0] if get_ip_addresses() else "127.0.0.1"
            appliance_url = f"https://{appliance_ip}:{self._sensor_port}"

            # Build uninstall command (non-interactive)
            uninstall_cmd = (
                f"curl -sSL --insecure {appliance_url}/sensor/uninstall.sh | "
                f"bash -s -- --force"
            )

            # Execute via SSH
            result = await self.linux_executor.run_command(target, uninstall_cmd)

            if result.exit_code == 0:
                logger.info(f"Linux sensor removed from {hostname}")
                # Clear from sensor registry
                from .sensor_linux import linux_sensor_registry
                for sensor_id in list(linux_sensor_registry.keys()):
                    if linux_sensor_registry[sensor_id].hostname.lower() == hostname.lower():
                        del linux_sensor_registry[sensor_id]
                        break
                return {"status": "removed", "hostname": hostname}
            else:
                return {
                    "error": f"Removal failed: {result.stderr}",
                    "stdout": result.stdout
                }

        except Exception as e:
            logger.error(f"Linux sensor removal failed: {e}")
            return {"error": str(e)}

    async def _handle_sensor_status(self, params: Dict) -> Dict:
        """Return current sensor status for both Windows and Linux."""
        combined_stats = get_combined_sensor_stats()
        windows_stats = combined_stats.get("windows", {})
        linux_stats = combined_stats.get("linux", {})

        return {
            "windows": {
                "sensors": windows_stats,
                "targets_with_sensors": len(windows_stats.get("sensor_hostnames", [])),
                "targets_needing_poll": len(self._get_targets_needing_poll()),
                "total_targets": len(self.windows_targets),
            },
            "linux": {
                "sensors": linux_stats,
                "targets_with_sensors": len(linux_stats.get("sensor_hostnames", [])),
                "targets_needing_poll": len(get_linux_polling_hosts([t.hostname for t in self.linux_targets])),
                "total_targets": len(self.linux_targets),
            },
            "total_active_sensors": combined_stats.get("total_active_sensors", 0),
            "all_sensor_hostnames": combined_stats.get("all_sensor_hostnames", []),
        }

    async def _handle_sync_promoted_rule(self, params: Dict) -> Dict:
        """
        Handle server-pushed promoted rule deployment.

        This is called when Central Command approves an L2→L1 promotion
        and pushes the new rule to this appliance.

        Parameters:
            rule_id: Unique rule identifier (e.g., L1-PROMOTED-ABC12345)
            pattern_signature: SHA256[:16] of the pattern
            rule_yaml: Full YAML content of the rule
            promoted_at: ISO timestamp when promoted
            promoted_by: Email of approver
        """
        rule_id = params.get('rule_id')
        rule_yaml = params.get('rule_yaml')

        if not rule_id or not rule_yaml:
            raise ValueError("rule_id and rule_yaml are required")

        # Deploy rule to promoted rules directory
        promoted_dir = self.config.rules_dir / "promoted"
        promoted_dir.mkdir(parents=True, exist_ok=True)

        rule_file = promoted_dir / f"{rule_id}.yaml"

        # Check if already deployed
        if rule_file.exists():
            logger.info(f"Promoted rule {rule_id} already exists, skipping")
            return {"status": "already_deployed", "rule_id": rule_id}

        # Write the rule
        rule_file.write_text(rule_yaml)
        logger.info(f"Deployed promoted rule: {rule_id} to {rule_file}")

        # Reload L1 engine if available
        if self.auto_healer and hasattr(self.auto_healer, 'level1'):
            try:
                self.auto_healer.level1.reload_rules()
                logger.info(f"Reloaded L1 rules after deploying {rule_id}")
            except Exception as e:
                logger.warning(f"Failed to reload L1 rules: {e}")

        return {
            "status": "deployed",
            "rule_id": rule_id,
            "pattern_signature": params.get('pattern_signature'),
            "promoted_at": params.get('promoted_at'),
            "promoted_by": params.get('promoted_by'),
        }

    async def _handle_update_iso(self, params: Dict) -> Dict:
        """Handle A/B partition ISO update via order.

        This is triggered by Central Command's Fleet Updates feature.
        The update agent handles download, verification, and staging.

        Parameters:
            update_id: Unique update identifier
            rollout_id: Rollout campaign ID
            version: Version string
            iso_url: URL to download ISO
            sha256: Expected checksum
            size_bytes: Optional file size
            maintenance_window: Dict with days, start, end times
        """
        from .update_agent import UpdateAgent, UpdateInfo

        # Read API key from file if configured
        api_key = ""
        if self.config.mcp_api_key_file and self.config.mcp_api_key_file.exists():
            api_key = self.config.mcp_api_key_file.read_text().strip()

        # Create update agent with config
        update_agent = UpdateAgent(
            api_base_url=self.config.mcp_url,
            api_key=api_key,
            appliance_id=self.config.host_id,
        )

        # Create UpdateInfo from params
        update = UpdateInfo(
            update_id=params.get('update_id', 'unknown'),
            rollout_id=params.get('rollout_id', 'unknown'),
            version=params.get('version', 'unknown'),
            iso_url=params.get('iso_url'),
            sha256=params.get('sha256'),
            size_bytes=params.get('size_bytes'),
            maintenance_window=params.get('maintenance_window', {}),
            current_status='notified',
        )

        if not update.iso_url or not update.sha256:
            return {"error": "iso_url and sha256 are required"}

        logger.info(f"ISO update requested: {update.version}")

        # Download ISO
        iso_path = await update_agent.download_iso(update)
        if not iso_path:
            return {"error": "Download failed", "status": "failed"}

        # Verify checksum
        if not update_agent.verify_checksum(iso_path, update.sha256):
            iso_path.unlink()
            return {"error": "Checksum verification failed", "status": "failed"}

        # Apply update (write to standby partition, set next boot)
        if not await update_agent.apply_update(update, iso_path):
            return {"error": "Failed to apply update", "status": "failed"}

        # Clean up download
        iso_path.unlink()

        # Wait for maintenance window if configured
        if update.maintenance_window:
            logger.info(f"Waiting for maintenance window: {update.maintenance_window}")
            if not await update_agent.wait_for_maintenance_window(update.maintenance_window):
                logger.warning("Maintenance window wait timed out, proceeding anyway")

        # Report rebooting status
        await update_agent.report_status("rebooting")

        # Schedule reboot
        logger.info(f"Scheduling reboot for ISO update to version {update.version}")
        asyncio.get_event_loop().call_later(5, self._do_reboot)

        return {
            "status": "rebooting",
            "version": update.version,
            "message": "Update applied, rebooting in 5 seconds",
        }

    def _do_reboot(self):
        """Execute system reboot."""
        import os
        os.system("systemctl reboot")


def main():
    """Main entry point for appliance agent."""
    import argparse

    parser = argparse.ArgumentParser(description="OsirisCare Compliance Agent")
    parser.add_argument('--provision', metavar='CODE',
                        help='Provision appliance with code')
    parser.add_argument('--provision-interactive', action='store_true',
                        help='Run interactive provisioning')
    args = parser.parse_args()

    # Check for provisioning mode
    from .provisioning import needs_provisioning, run_provisioning_cli, run_provisioning_auto

    # Handle explicit provisioning arguments
    if args.provision:
        if run_provisioning_auto(args.provision):
            print("Provisioning complete. Restarting agent...")
            import os
            os.execv(sys.executable, [sys.executable] + sys.argv[:1])
        else:
            sys.exit(1)

    if args.provision_interactive:
        if run_provisioning_cli():
            print("Provisioning complete. Restarting agent...")
            import os
            os.execv(sys.executable, [sys.executable] + sys.argv[:1])
        else:
            sys.exit(1)

    # Auto-detect provisioning mode if config doesn't exist
    if needs_provisioning():
        print("\n" + "=" * 60)
        print("  No configuration found - entering provisioning mode")
        print("=" * 60 + "\n")
        if run_provisioning_cli():
            print("Provisioning complete. Starting agent...\n")
        else:
            print("Provisioning cancelled. Agent cannot start without config.")
            sys.exit(1)

    # Load config
    try:
        config = load_appliance_config()
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print("Run with --provision-interactive to provision this appliance", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Invalid config: {e}", file=sys.stderr)
        sys.exit(1)

    # Create agent
    agent = ApplianceAgent(config)

    # Setup signal handlers
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def signal_handler():
        loop.create_task(agent.stop())

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)

    # Run agent
    try:
        loop.run_until_complete(agent.start())
    except KeyboardInterrupt:
        loop.run_until_complete(agent.stop())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
