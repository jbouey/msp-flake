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
        self.running = False
        self._last_rules_sync = datetime.min.replace(tzinfo=timezone.utc)
        self._rules_sync_interval = 3600  # Sync rules every hour

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
                    evidence_data=evidence_data
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
