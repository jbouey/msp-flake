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
from .runbooks.windows.executor import WindowsTarget

# Three-tier healing imports
from .incident_db import IncidentDatabase, Incident
from .auto_healer import AutoHealer, AutoHealerConfig
from .level1_deterministic import DeterministicEngine
from .level2_llm import Level2Planner, LLMConfig, LLMMode
from .level3_escalation import EscalationHandler, EscalationConfig
from .learning_loop import SelfLearningSystem, PromotionConfig
from .ntp_verify import NTPVerifier, verify_time_for_evidence

# Sensor API for dual-mode architecture
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

logger = logging.getLogger(__name__)

VERSION = "1.0.19"


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

        code, stdout, _ = await run_command(
            "nixos-rebuild list-generations 2>/dev/null | tail -1 | awk '{print $1}'",
            timeout=10
        )
        current_gen = stdout.strip() if code == 0 else "unknown"

        return {
            "status": "pass" if current_gen != "unknown" else "warning",
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
        """Check firewall status."""
        code, stdout, _ = await run_command("nft list tables 2>/dev/null | head -5", timeout=5)

        has_rules = bool(stdout.strip()) if code == 0 else False

        return {
            "status": "pass" if has_rules else "warning",
            "details": {
                "firewall_active": has_rules,
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
        self.enabled_runbooks: List[str] = []  # Runbooks enabled for this appliance
        self.running = False
        self._last_rules_sync = datetime.min.replace(tzinfo=timezone.utc)
        self._last_windows_scan = datetime.min.replace(tzinfo=timezone.utc)
        self._rules_sync_interval = 3600  # Sync rules every hour
        self._windows_scan_interval = 300  # Scan Windows every 5 minutes

        # Three-tier healing components
        self.auto_healer: Optional[AutoHealer] = None
        self.incident_db: Optional[IncidentDatabase] = None
        self._healing_enabled = getattr(config, 'healing_enabled', True)
        self._healing_dry_run = getattr(config, 'healing_dry_run', True)

        # Dual-mode sensor support
        self._sensor_enabled = getattr(config, 'sensor_enabled', True)
        self._sensor_port = getattr(config, 'sensor_port', 8080)

        # Evidence signing
        self.signer: Optional[Ed25519Signer] = None
        self._signing_key_path = config.state_dir / "signing.key"

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
                )
                self.windows_targets.append(target)
                logger.info(f"Added Windows target: {target.hostname}")
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
                    configure_sensor_healing(
                        auto_healer=self.auto_healer,
                        windows_targets=self.windows_targets,
                        incident_db=self.incident_db,
                        config=self.config
                    )
                    logger.info(f"Sensor API configured for dual-mode operation (port {self._sensor_port})")
            except Exception as e:
                logger.warning(f"Failed to initialize healing system: {e}")
                self.auto_healer = None

        # Initial delay to let network settle
        await asyncio.sleep(5)

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
            # Update enabled runbooks from server response (runbook config pull)
            self._update_enabled_runbooks_from_response(checkin_response)
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

        # 5. Process pending orders (remote commands/updates)
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

                # Compute hash and sign the evidence data
                evidence_json = json.dumps(evidence_data, sort_keys=True)
                bundle_hash = hashlib.sha256(evidence_json.encode()).hexdigest()

                # Sign the bundle if signer is available
                agent_signature = None
                if self.signer:
                    try:
                        signature_bytes = self.signer.sign(evidence_json)
                        agent_signature = signature_bytes.hex()
                    except Exception as e:
                        logger.warning(f"Failed to sign evidence bundle: {e}")

                # Upload to Central Command
                bundle_id = await self.client.submit_evidence(
                    bundle_hash=bundle_hash,
                    check_type=check_name,
                    check_result=check_result.get("status", "unknown"),
                    evidence_data=evidence_data,
                    hipaa_control=hipaa_controls.get(check_name),
                    agent_signature=agent_signature
                )

                if bundle_id:
                    logger.debug(f"Evidence uploaded: {check_name} -> {bundle_id} (signed={agent_signature is not None})")

                    # Store locally as well (with signature)
                    await self._store_local_evidence(bundle_id, evidence_data, agent_signature)

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
        # Create config for AutoHealer
        healer_config = AutoHealerConfig(
            db_path=str(self.config.state_dir / "incidents.db"),
            rules_dir=self.config.rules_dir,
            enable_level1=True,
            enable_level2=False,  # No local LLM on appliance
            enable_level3=True,
            dry_run=self._healing_dry_run,
        )

        # Create AutoHealer orchestrator (handles all initialization internally)
        self.auto_healer = AutoHealer(
            config=healer_config,
            action_executor=self._execute_healing_action
        )

        # Keep reference to incident database for later use
        self.incident_db = self.auto_healer.incident_db

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
            "escalate": self._heal_escalate,
        }

        handler = action_handlers.get(action)
        if handler:
            return await handler(params, incident)
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

    async def _heal_run_windows_runbook(
        self, params: Dict[str, Any], incident: Optional[Incident]
    ) -> Dict[str, Any]:
        """Execute a Windows runbook via WinRM."""
        runbook_id = params.get("runbook_id")
        target_host = params.get("target_host")
        phases = params.get("phases", ["remediate", "verify"])

        if not runbook_id:
            return {"error": "runbook_id required"}

        # Find target - use first Windows target if not specified
        target = None
        if target_host:
            for t in self.windows_targets:
                if t.hostname == target_host:
                    target = t
                    break
        elif self.windows_targets:
            target = self.windows_targets[0]

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

            return {
                "action": "run_windows_runbook",
                "runbook_id": runbook_id,
                "target": target.hostname,
                "phases": phases,
                "success": overall_success,
                "phase_details": phase_details
            }

        except ImportError as e:
            return {"error": f"Windows executor not available: {e}", "success": False}
        except Exception as e:
            logger.error(f"Windows runbook execution failed: {e}")
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
                )
                new_targets.append(target)
            except Exception as e:
                logger.warning(f"Invalid Windows target from server: {e}")

        if new_targets:
            # Replace targets with server-provided ones
            self.windows_targets = new_targets
            logger.info(f"Updated {len(new_targets)} Windows targets from Central Command")

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

                    # Sign if signer available
                    agent_signature = None
                    if self.signer:
                        try:
                            signature_bytes = self.signer.sign(evidence_json)
                            agent_signature = signature_bytes.hex()
                        except Exception as e:
                            logger.warning(f"Failed to sign Windows evidence: {e}")

                    await self.client.submit_evidence(
                        bundle_hash=bundle_hash,
                        check_type=f"windows_{check_name}",
                        check_result=status,
                        evidence_data=evidence_data,
                        host=target.hostname,
                        hipaa_control="164.312(b)",  # Audit controls
                        agent_signature=agent_signature
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
                                logger.info(f"Healing succeeded: {heal_result.resolution_level} - {heal_result.action_taken}")
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
            'view_logs': self._handle_view_logs,
            'deploy_sensor': self._handle_deploy_sensor,
            'remove_sensor': self._handle_remove_sensor,
            'sensor_status': self._handle_sensor_status,
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

    async def _handle_sensor_status(self, params: Dict) -> Dict:
        """Return current sensor status."""
        stats = get_dual_mode_stats()
        return {
            "sensors": stats,
            "targets_with_sensors": len(stats["sensor_hostnames"]),
            "targets_needing_poll": len(self._get_targets_needing_poll()),
            "total_targets": len(self.windows_targets),
        }


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
