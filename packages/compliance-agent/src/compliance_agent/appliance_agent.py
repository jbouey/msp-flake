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
from .runbooks.windows.executor import WindowsTarget

logger = logging.getLogger(__name__)

VERSION = "1.0.0"


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
        self.running = False
        self._last_rules_sync = datetime.min.replace(tzinfo=timezone.utc)
        self._last_windows_scan = datetime.min.replace(tzinfo=timezone.utc)
        self._rules_sync_interval = 3600  # Sync rules every hour
        self._windows_scan_interval = 300  # Scan Windows every 5 minutes

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

        # Initialize drift checker if enabled
        if self.config.enable_drift_detection:
            try:
                self.drift_checker = SimpleDriftChecker()
                logger.info("Drift detection enabled")
            except Exception as e:
                logger.warning(f"Failed to initialize drift checker: {e}")

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

        checkin_ok = await self.client.checkin(
            hostname=get_hostname(),
            mac_address=get_mac_address(),
            ip_addresses=get_ip_addresses(),
            uptime_seconds=get_uptime_seconds(),
            agent_version=VERSION,
            nixos_version=get_nixos_version(),
            compliance_summary=compliance_summary
        )

        if checkin_ok:
            logger.debug(f"[{timestamp}] Checkin OK")
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

                bundle_hash = hashlib.sha256(
                    json.dumps(evidence_data, sort_keys=True).encode()
                ).hexdigest()

                # Upload to Central Command
                bundle_id = await self.client.submit_evidence(
                    bundle_hash=bundle_hash,
                    check_type=check_name,
                    check_result=check_result.get("status", "unknown"),
                    evidence_data=evidence_data,
                    hipaa_control=hipaa_controls.get(check_name)
                )

                if bundle_id:
                    logger.debug(f"Evidence uploaded: {check_name} -> {bundle_id}")

                    # Store locally as well
                    await self._store_local_evidence(bundle_id, evidence_data)

        except Exception as e:
            logger.error(f"Drift detection failed: {e}")

    async def _store_local_evidence(self, bundle_id: str, evidence_data: dict):
        """Store evidence bundle locally."""
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
                json.dump(evidence_data, f, indent=2)

            logger.debug(f"Local evidence stored: {bundle_path}")

        except Exception as e:
            logger.warning(f"Failed to store local evidence: {e}")

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

    async def _maybe_scan_windows(self):
        """Scan Windows targets if enough time has passed."""
        now = datetime.now(timezone.utc)
        elapsed = (now - self._last_windows_scan).total_seconds()

        if elapsed < self._windows_scan_interval:
            return

        logger.info(f"Scanning {len(self.windows_targets)} Windows targets...")

        for target in self.windows_targets:
            await self._scan_windows_target(target)

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
                ("windows_defender", "$status = Get-MpComputerStatus; @{Enabled=$status.AntivirusEnabled;Updated=$status.AntivirusSignatureLastUpdated} | ConvertTo-Json"),
                ("firewall_status", "Get-NetFirewallProfile | Select-Object Name,Enabled | ConvertTo-Json"),
                ("password_policy", "net accounts | Select-String 'password|lockout'"),
                ("bitlocker_status", "Get-BitLockerVolume -MountPoint C: -ErrorAction SilentlyContinue | Select-Object MountPoint,ProtectionStatus | ConvertTo-Json"),
                ("audit_policy", "auditpol /get /category:* | Select-String 'Success|Failure' | Select-Object -First 10"),
            ]

            for check_name, ps_cmd in checks:
                try:
                    check_result = session.run_ps(ps_cmd)
                    status = "pass" if check_result.status_code == 0 else "fail"
                    output = check_result.std_out.decode().strip()

                    # Submit as evidence
                    evidence_data = {
                        "check_name": check_name,
                        "target": target.hostname,
                        "computer_name": computer_name,
                        "status": status,
                        "output": output[:1000],  # Truncate large outputs
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }

                    bundle_hash = hashlib.sha256(
                        json.dumps(evidence_data, sort_keys=True).encode()
                    ).hexdigest()

                    await self.client.submit_evidence(
                        bundle_hash=bundle_hash,
                        check_type=f"windows_{check_name}",
                        check_result=status,
                        evidence_data=evidence_data,
                        host=target.hostname,
                        hipaa_control="164.312(b)"  # Audit controls
                    )

                    logger.debug(f"Windows check {check_name} on {target.hostname}: {status}")

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


def main():
    """Main entry point for appliance agent."""
    # Load config
    try:
        config = load_appliance_config()
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print("Create /var/lib/msp/config.yaml with site_id and api_key", file=sys.stderr)
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
